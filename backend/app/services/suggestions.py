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
executes none of it. The accept/reject flow here handles only the original
single-status-change kind; email plans are refused by it until plan approval
exists, rather than being half-executed.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import (
    EmailMessageNotFound,
    InvalidSuggestionPlan,
    OutgoingMessageNotClassifiable,
    SuggestionAlreadyResolved,
    SuggestionKindNotSupported,
    SuggestionNotFound,
)
from app.enums import (
    MANUAL_EVENT_TYPES,
    ApplicationStatus,
    EmailDirection,
    EventSource,
    ProposedActionType,
    SuggestionConfidence,
    SuggestionKind,
    SuggestionPlanOutcome,
    SuggestionSource,
    SuggestionState,
)
from app.models.email_message import EmailMessage
from app.models.suggestion import Suggestion, SuggestionAction
from app.services.applications import change_status, get_application, status_change_blocker
from app.services.email_policy import ProposedAction, SuggestionPlan

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


def _require_status_change_kind(suggestion: Suggestion) -> None:
    """Accept/reject below understand one shape only: a single status change.

    Checked before the state, so an email plan is refused for what it is rather
    than for being resolved.
    """
    if suggestion.kind != SuggestionKind.STATUS_CHANGE.value:
        raise SuggestionKindNotSupported(suggestion.id, suggestion.kind)


def accept_suggestion(db: Session, suggestion_id: int, *, note: str | None = None) -> Suggestion:
    """Accept: perform the real status change, then resolve the suggestion.

    These are two separate commits (`change_status` commits internally, as every
    other caller of it relies on). If the process died between them you would see
    a changed application with a still-pending suggestion — recoverable, since
    re-accepting then fails with a clear "already in that status" rather than
    silently double-applying anything. Full single-transaction atomicity would
    require changing `change_status`'s contract for every other caller, which is
    more than this MVP slice needs.
    """
    suggestion = get_suggestion(db, suggestion_id)
    _require_status_change_kind(suggestion)
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
    """Reject: marks the row only. The application is never touched."""
    suggestion = get_suggestion(db, suggestion_id)
    _require_status_change_kind(suggestion)
    _require_pending(suggestion)

    suggestion.state = SuggestionState.REJECTED.value
    suggestion.resolved_at = _utcnow()
    db.commit()
    db.refresh(suggestion)
    return suggestion


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
