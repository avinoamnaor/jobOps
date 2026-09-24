"""Suggestion review workflow.

A Suggestion proposes a status change but changes nothing by itself. Accepting
one calls `services.applications.change_status` — the SAME function every other
status change goes through — so a `status_changed` timeline event is written and
every existing rule (the submitted-CV requirement, etc.) still applies exactly as
it would for a manual change. Rejecting only marks the row rejected. A resolved
suggestion (accepted or rejected) can never be processed again.

`create_suggestion` has no idea where a suggestion came from. Email-derived
suggestions arrive differently: `persist_email_plan` stores a whole
`email_policy.SuggestionPlan` — one row per email, with its ordered actions — and
executes none of it. Accepting one runs `approve_email_plan`, which executes
every action in order inside a single transaction (all or nothing); rejecting
one is dismissal and executes nothing. Both kinds share the same states and the
same "resolved is final" rule.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import (
    EmailMessageNotFound,
    EmailPlanNotExecutable,
    InvalidSuggestionPlan,
    OutgoingMessageNotClassifiable,
    SuggestionAlreadyResolved,
    SuggestionApprovalInputInvalid,
    SuggestionNotFound,
)
from app.enums import (
    MANUAL_EVENT_TYPES,
    ApplicationChannel,
    ApplicationStatus,
    EmailDirection,
    EventSource,
    EventType,
    ProposedActionType,
    SuggestionConfidence,
    SuggestionKind,
    SuggestionPlanOutcome,
    SuggestionSource,
    SuggestionState,
)
from app.models.email_message import EmailMessage
from app.models.suggestion import Suggestion, SuggestionAction
from app.schemas.application import ApplicationCreate
from app.services.applications import (
    apply_status_change,
    change_status,
    create_application_in_transaction,
    get_application,
    status_change_blocker,
)
from app.services.email_policy import ProposedAction, SuggestionPlan, status_progression_problem
from app.services.events import append_event

# application_events.source has no "claude" value yet (Claude integration does
# not exist). Until it does, a Claude-produced suggestion's acceptance is
# attributed to SYSTEM rather than inventing a timeline source that nothing else
# recognises.
_EVENT_SOURCE_BY_SUGGESTION_SOURCE: dict[SuggestionSource, EventSource] = {
    SuggestionSource.MANUAL: EventSource.MANUAL,
    SuggestionSource.GMAIL: EventSource.GMAIL,
    SuggestionSource.CLAUDE: EventSource.SYSTEM,
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def create_suggestion(
    db: Session,
    *,
    application_id: int,
    proposed_status: ApplicationStatus,
    source: SuggestionSource,
    confidence: SuggestionConfidence,
    rationale: str,
) -> Suggestion:
    """Record a proposed status change. Writes ONLY the suggestion row.

    Confirms the application exists (raises `ApplicationNotFound` otherwise, via
    `get_application`) but never modifies it — no event, no status write. This is
    the invariant the whole feature rests on.
    """
    get_application(db, application_id)

    suggestion = Suggestion(
        application_id=application_id,
        proposed_status=proposed_status.value,
        source=source.value,
        confidence=confidence.value,
        rationale=rationale,
        state=SuggestionState.PENDING.value,
    )
    db.add(suggestion)
    db.commit()
    db.refresh(suggestion)
    return suggestion


def get_suggestion(db: Session, suggestion_id: int) -> Suggestion:
    suggestion = db.execute(
        select(Suggestion).where(Suggestion.id == suggestion_id)
    ).scalar_one_or_none()
    if suggestion is None:
        raise SuggestionNotFound(suggestion_id)
    return suggestion


def list_suggestions(
    db: Session,
    *,
    state: SuggestionState | None = None,
    kind: SuggestionKind | None = None,
) -> Sequence[Suggestion]:
    """List suggestions, optionally filtered by state and kind.

    `state=pending` covers both "list pending suggestions" and the review page;
    omitting it returns everything (useful for a future history view).
    `selectinload` avoids an N+1 when the caller reads `.application` for each
    row (the review UI needs company/role/current status alongside each one).
    """
    stmt = (
        select(Suggestion)
        .options(selectinload(Suggestion.application))
        .order_by(Suggestion.created_at.desc(), Suggestion.id.desc())
    )
    if state is not None:
        stmt = stmt.where(Suggestion.state == state.value)
    if kind is not None:
        stmt = stmt.where(Suggestion.kind == kind.value)
    return db.execute(stmt).scalars().all()


def _require_pending(suggestion: Suggestion) -> None:
    if suggestion.state != SuggestionState.PENDING.value:
        raise SuggestionAlreadyResolved(suggestion.id, suggestion.state)


def _lock_suggestion(db: Session, suggestion_id: int) -> Suggestion:
    """Fetch a suggestion and hold a row lock on it until the transaction ends.

    Two concurrent approve/dismiss requests for the same suggestion serialise
    here: the second waits, then sees the first's resolved state and is refused
    by `_require_pending`, instead of both executing. `populate_existing` makes
    sure the state checked is the database's, not a stale identity-map copy.
    """
    suggestion = db.execute(
        select(Suggestion)
        .where(Suggestion.id == suggestion_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if suggestion is None:
        raise SuggestionNotFound(suggestion_id)
    return suggestion


def accept_suggestion(
    db: Session,
    suggestion_id: int,
    *,
    note: str | None = None,
    role_title: str | None = None,
    application_channel: ApplicationChannel | None = None,
) -> Suggestion:
    """Accept a suggestion of either kind.

    An email plan is executed by `approve_email_plan` (one transaction, all or
    nothing). `role_title` and `application_channel` apply only to an email
    plan that creates an application; supplying them anywhere else is refused.
    """
    suggestion = get_suggestion(db, suggestion_id)
    if suggestion.kind == SuggestionKind.EMAIL_PLAN.value:
        return approve_email_plan(
            db,
            suggestion_id,
            note=note,
            role_title=role_title,
            application_channel=application_channel,
        )
    if role_title is not None or application_channel is not None:
        raise SuggestionApprovalInputInvalid(
            "role_title and application_channel apply only to email suggestions "
            "that create an application"
        )
    return _accept_status_change(db, suggestion, note=note)


def _accept_status_change(
    db: Session, suggestion: Suggestion, *, note: str | None
) -> Suggestion:
    """Accept a status-change suggestion: the real status change, then resolve it.

    These are two separate commits (`change_status` commits internally, as every
    other caller of it relies on). If the process died between them you would see
    a changed application with a still-pending suggestion — recoverable, since
    re-accepting then fails with a clear "already in that status" rather than
    silently double-applying anything. Unchanged since Phase 5: email plans,
    which can carry several actions, get the single-transaction executor below.
    """
    _require_pending(suggestion)

    # Guaranteed non-null for this kind by `ck_suggestions_kind_shape`.
    assert suggestion.application_id is not None
    assert suggestion.proposed_status is not None
    event_source = _EVENT_SOURCE_BY_SUGGESTION_SOURCE[SuggestionSource(suggestion.source)]
    change_status(
        db,
        suggestion.application_id,
        to_status=ApplicationStatus(suggestion.proposed_status),
        note=note or f"Accepted suggestion: {suggestion.rationale}",
        source=event_source,
    )

    suggestion.state = SuggestionState.ACCEPTED.value
    suggestion.resolved_at = _utcnow()
    db.commit()
    db.refresh(suggestion)
    return suggestion


def reject_suggestion(db: Session, suggestion_id: int) -> Suggestion:
    """Reject (dismiss): marks the row only, for either kind. Nothing is executed.

    For an email plan this is dismissal: no application created, no event, no
    status change. The row lock makes a dismissal racing an approval safe — one
    of them resolves the suggestion and the other is refused.
    """
    suggestion = _lock_suggestion(db, suggestion_id)
    _require_pending(suggestion)

    suggestion.state = SuggestionState.REJECTED.value
    suggestion.resolved_at = _utcnow()
    db.commit()
    db.refresh(suggestion)
    return suggestion


def list_email_plans(
    db: Session, *, state: SuggestionState | None = None
) -> Sequence[Suggestion]:
    """Email-plan suggestions with their actions, newest first."""
    stmt = (
        select(Suggestion)
        .options(selectinload(Suggestion.actions))
        .where(Suggestion.kind == SuggestionKind.EMAIL_PLAN.value)
        .order_by(Suggestion.created_at.desc(), Suggestion.id.desc())
    )
    if state is not None:
        stmt = stmt.where(Suggestion.state == state.value)
    return db.execute(stmt).scalars().all()


# --- Email-plan approval ---------------------------------------------------


def approve_email_plan(
    db: Session,
    suggestion_id: int,
    *,
    note: str | None = None,
    role_title: str | None = None,
    application_channel: ApplicationChannel | None = None,
) -> Suggestion:
    """Execute an email plan's actions in order, in ONE transaction, then accept it.

    All or nothing. Every write goes through a non-committing service core —
    `create_application_in_transaction`, `append_event`, `apply_status_change` —
    so the plan's creation, events and status change, and the suggestion's own
    `accepted` state, are committed together by the single `db.commit()` below.
    If any action raises, everything is rolled back: no application, no event,
    no status change survives, and the suggestion stays `pending` and can be
    retried or dismissed.

    Nothing from planning time is trusted. Each action is re-checked against
    the application as it is *now*: it must still exist, a status change must
    still pass `status_change_blocker` (inside `apply_status_change`, so the
    submitted-CV rule cannot be bypassed) and must still be forward progress
    per the policy's own rule. No provider or Gmail call is made.

    Optional actions are executed too: approving a plan is the explicit choice
    to take it. (Today `optional` appears only on recruiter-outreach plans whose
    single action is the optional creation, so there is nothing to choose
    between.)
    """
    try:
        suggestion = _lock_suggestion(db, suggestion_id)
        if suggestion.kind != SuggestionKind.EMAIL_PLAN.value:
            raise SuggestionApprovalInputInvalid(
                f"Suggestion {suggestion_id} is not an email plan"
            )
        _require_pending(suggestion)
        if not suggestion.actions:
            # `accepted` means "what it proposed was done". A review-only or
            # no-action plan proposes nothing, so accepting it would record an
            # execution that never happened; it is dismissed instead.
            raise EmailPlanNotExecutable(
                suggestion.id,
                f"a '{suggestion.outcome}' plan has no actions to approve; dismiss it instead",
            )
        _execute_plan(
            db,
            suggestion,
            note=note,
            role_title=role_title,
            application_channel=application_channel,
        )
        suggestion.state = SuggestionState.ACCEPTED.value
        suggestion.resolved_at = _utcnow()
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(suggestion)
    return suggestion


def _execute_plan(
    db: Session,
    suggestion: Suggestion,
    *,
    note: str | None,
    role_title: str | None,
    application_channel: ApplicationChannel | None,
) -> None:
    """Apply each action in `position` order. Never commits."""
    actions = sorted(suggestion.actions, key=lambda action: action.position)
    creates = [a for a in actions if a.action_type == ProposedActionType.CREATE_APPLICATION]
    if (role_title is not None or application_channel is not None) and not creates:
        raise SuggestionApprovalInputInvalid(
            "role_title and application_channel apply only to a plan that creates "
            "an application; this one does not"
        )

    email = suggestion.email_message
    assert email is not None  # guaranteed for email plans by ck_suggestions_kind_shape
    new_application_id: int | None = None

    for action in actions:
        action_type = ProposedActionType(action.action_type)

        if action_type is ProposedActionType.CREATE_APPLICATION:
            application = create_application_in_transaction(
                db,
                _creation_data(action, role_title, application_channel),
                source=EventSource(action.event_source),
            )
            new_application_id = application.id
            # The plan is now about this application; recorded so the approved
            # suggestion points at what it produced.
            suggestion.application_id = application.id
            continue

        target_id = _target_of(suggestion, action, new_application_id)

        if action_type is ProposedActionType.RECORD_EVENT:
            assert action.event_type is not None  # ck_suggestion_actions_shape
            append_event(
                db,
                target_id,
                event_type=EventType(action.event_type),
                summary=action.summary,
                source=EventSource(action.event_source),
                # When the news arrived. A stated future time (an interview)
                # goes to `scheduled_for`, the timeline's field for exactly that.
                occurred_at=email.received_at,
                scheduled_for=action.occurred_at,
            )
        elif action_type is ProposedActionType.CHANGE_STATUS:
            assert action.to_status is not None  # ck_suggestion_actions_shape
            to_status = ApplicationStatus(action.to_status)
            current = get_application(db, target_id)
            problem = status_progression_problem(ApplicationStatus(current.status), to_status)
            if problem is not None:
                raise EmailPlanNotExecutable(suggestion.id, problem)
            # Dated at approval, not at email time: status is replayed from the
            # event log ordered by `occurred_at`, so a backdated status event
            # could sort before a later manual change and make the replayed
            # status disagree with the cached one.
            apply_status_change(
                db,
                target_id,
                to_status=to_status,
                note=note or f"Approved email suggestion: {suggestion.rationale}",
                source=EventSource(action.event_source),
            )


def _target_of(
    suggestion: Suggestion, action: SuggestionAction, new_application_id: int | None
) -> int:
    if action.targets_new_application:
        if new_application_id is None:
            raise EmailPlanNotExecutable(
                suggestion.id,
                f"action {action.position} targets an application no earlier action creates",
            )
        return new_application_id
    if action.application_id is None:
        raise EmailPlanNotExecutable(
            suggestion.id, f"action {action.position} has no target application"
        )
    return action.application_id


def _creation_data(
    action: SuggestionAction,
    role_title: str | None,
    application_channel: ApplicationChannel | None,
) -> ApplicationCreate:
    """What to create: the email's prefill, completed only by what the user supplied.

    Status is left at the default `saved` and no CV is attached: an email proves
    an application exists, never which CV was sent. User-supplied values win
    over the prefill — they are the user's own correction, not an inference.
    """
    role = role_title if role_title is not None else action.role_title
    channel = application_channel
    if channel is None and action.application_channel is not None:
        channel = ApplicationChannel(action.application_channel)
    missing = [
        name
        for name, value in (("role_title", role), ("application_channel", channel))
        if not value
    ]
    if missing:
        raise SuggestionApprovalInputInvalid(
            "The email did not state " + " or ".join(missing) + " for the application "
            "this plan creates; supply it when approving"
        )
    assert action.company_name is not None  # ck_suggestion_actions_shape
    assert role is not None and channel is not None
    return ApplicationCreate(
        company_name=action.company_name,
        role_title=role,
        application_channel=channel,
    )


# --- Email-derived plans ---------------------------------------------------

# Outcomes whose plans may never carry an action. The policy guarantees this;
# persistence re-checks it, because a review-required plan that somehow carried
# a mutation is exactly the thing that must not reach an approval button.
_ACTIONLESS_OUTCOMES: frozenset[SuggestionPlanOutcome] = frozenset(
    {SuggestionPlanOutcome.REVIEW_REQUIRED, SuggestionPlanOutcome.NO_ACTION}
)


def get_email_plan(db: Session, email_message_id: int) -> Suggestion | None:
    """The persisted plan for one email, with its actions, or None."""
    return db.execute(
        select(Suggestion)
        .options(selectinload(Suggestion.actions))
        .where(Suggestion.email_message_id == email_message_id)
    ).scalar_one_or_none()


def persist_email_plan(
    db: Session,
    *,
    email_message_id: int,
    plan: SuggestionPlan,
) -> tuple[Suggestion, bool]:
    """Store what the policy proposed about one email. Executes nothing.

    Returns `(suggestion, created)`. Idempotent: an email has at most one plan
    (`uq_suggestions_email_message_id`), so calling this again for the same
    email returns the stored plan with `created=False` and writes nothing —
    whatever state it is in, and even if the new plan differs. First write wins:
    a stored plan is never silently rewritten, and one the user already
    resolved is never resurrected by re-processing the email.

    Writes only `suggestions` and `suggestion_actions`. No application is
    created, no event appended, no status changed — every action stays a
    proposal until an approval step executes it through the normal services.

    The plan is re-validated against the database before storing (see
    `_validate_plan`), so a stale or malformed plan is refused with
    `InvalidSuggestionPlan` rather than persisted as something that would fail,
    or break a rule, on approval.
    """
    existing = get_email_plan(db, email_message_id)
    if existing is not None:
        return existing, False

    message = db.execute(
        select(EmailMessage).where(EmailMessage.id == email_message_id)
    ).scalar_one_or_none()
    if message is None:
        raise EmailMessageNotFound(email_message_id)
    # The user's own writing is never classified, so it can never have a plan.
    # Re-checked here because this is a second entry point.
    if message.direction == EmailDirection.OUTGOING.value:
        raise OutgoingMessageNotClassifiable()

    _validate_plan(db, plan)

    suggestion = Suggestion(
        kind=SuggestionKind.EMAIL_PLAN.value,
        email_message_id=email_message_id,
        application_id=plan.application_id,
        source=SuggestionSource.GMAIL.value,
        rationale=plan.reason,
        outcome=plan.outcome.value,
        message_type=plan.message_type.value,
        plan_details={
            "candidate_application_ids": list(plan.candidate_application_ids),
            "field_notes": list(plan.field_notes),
        },
        state=SuggestionState.PENDING.value,
        actions=[
            _action_row(position, action) for position, action in enumerate(plan.actions)
        ],
    )
    db.add(suggestion)
    try:
        # One commit: the suggestion and all its actions land together or not
        # at all, so a plan can never be stored with some actions missing.
        db.commit()
    except IntegrityError:
        # A concurrent caller may have stored this email's plan between the
        # lookup above and this insert. If so, theirs is the plan; anything
        # else (a CHECK violation) is a real error and propagates.
        db.rollback()
        existing = get_email_plan(db, email_message_id)
        if existing is None:
            raise
        return existing, False

    db.refresh(suggestion)
    return suggestion, True


def _action_row(position: int, action: ProposedAction) -> SuggestionAction:
    return SuggestionAction(
        position=position,
        action_type=action.action_type.value,
        summary=action.summary[:300],
        application_id=action.application_id,
        targets_new_application=action.targets_new_application,
        to_status=action.to_status.value if action.to_status is not None else None,
        event_type=action.event_type.value if action.event_type is not None else None,
        occurred_at=action.occurred_at,
        company_name=action.company_name,
        role_title=action.role_title,
        application_channel=(
            action.application_channel.value if action.application_channel is not None else None
        ),
        optional=action.optional,
        event_source=action.event_source.value,
    )


def _validate_plan(db: Session, plan: SuggestionPlan) -> None:
    """Refuse a plan that could not be approved without breaking a rule.

    The policy already produces only executable plans; this re-checks against
    the database *now*, because the plan was decided from a snapshot and this is
    a separate entry point. The checks:

      * review-required and no-action plans carry no actions;
      * every targeted application exists (and is not soft-deleted), and every
        action on an existing application targets the plan's application;
      * at most one creation, and "the new application" is targeted only after it;
      * no action records a status-bearing event — those come only from the
        status service, which is what keeps the event log authoritative;
      * every status change passes `status_change_blocker`, the same check
        `change_status` makes. A change aimed at a not-yet-created application
        is checked as if from `saved` with no CV — so a submitted-state status
        is refused there, because a submitted CV is never inferred.
    """
    if plan.outcome in _ACTIONLESS_OUTCOMES and plan.actions:
        raise InvalidSuggestionPlan(f"a '{plan.outcome.value}' plan cannot carry actions")

    target = None
    if plan.application_id is not None:
        target = get_application(db, plan.application_id)

    created = False
    for position, action in enumerate(plan.actions):
        where = f"action {position} ({action.action_type.value})"

        if action.action_type is ProposedActionType.CREATE_APPLICATION:
            if created:
                raise InvalidSuggestionPlan(f"{where}: a plan may create only one application")
            if not action.company_name:
                raise InvalidSuggestionPlan(f"{where}: no company to create the application with")
            created = True
            continue

        if action.targets_new_application:
            if not created:
                raise InvalidSuggestionPlan(
                    f"{where}: targets the new application before any creation"
                )
        elif target is None or action.application_id != target.id:
            raise InvalidSuggestionPlan(
                f"{where}: must target the plan's application or the one it creates"
            )

        if action.action_type is ProposedActionType.RECORD_EVENT:
            if action.event_type is None or action.event_type not in MANUAL_EVENT_TYPES:
                raise InvalidSuggestionPlan(
                    f"{where}: '{action.event_type}' events come only from the status service"
                )
        elif action.action_type is ProposedActionType.CHANGE_STATUS:
            if action.to_status is None:
                raise InvalidSuggestionPlan(f"{where}: no status to change to")
            if action.targets_new_application or target is None:
                current_status, has_cv = ApplicationStatus.SAVED.value, False
            else:
                current_status = target.status
                has_cv = target.submitted_cv_document_id is not None
            blocker = status_change_blocker(
                current_status=current_status,
                to_status=action.to_status,
                has_submitted_cv=has_cv,
            )
            if blocker is not None:
                raise InvalidSuggestionPlan(
                    f"{where}: moving to '{action.to_status.value}' would be refused "
                    f"({blocker.value})"
                )
