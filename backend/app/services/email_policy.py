"""What JobOps should PROPOSE about a classified, matched email.

Phase 6.2C-0. This module decides; it never acts. Nothing here writes a row,
appends an event, changes a status, creates an application, or persists a
suggestion — and it takes no `Session`, so none of that is even reachable.

The third of three separated responsibilities:

    classification  what does this email mean?
    matching        which application is it about?
    policy          what should we propose doing about it?      <- here

Deliberately deterministic. Model confidence describes how sure a model is about
language; it must never be the thing that decides a product action, or a
confidently-worded email would move a real application on its own. Everything
below is a table a person can read and disagree with.

Two guarantees hold throughout:

  * **Nothing is proposed that would fail if executed.** Status proposals are
    checked against `applications.status_change_blocker` — the same function
    `change_status` itself uses — so a plan can never contain an action the
    service would refuse.
  * **Nothing is proposed against an uncertain target.** Ambiguous identity, low
    identity confidence, and absent identity all route to review, never to a
    mutation.
"""

from dataclasses import dataclass, field
from datetime import datetime

from app.enums import (
    NO_ACTION_MESSAGE_TYPES,
    STAGE_ORDER,
    ApplicationChannel,
    ApplicationStatus,
    EmailMessageType,
    EventSource,
    EventType,
    MatchConfidence,
    MatchStatus,
    ProposedActionType,
    SuggestionPlanOutcome,
)
from app.services.applications import status_change_blocker

# The timeline event each message type would append.
#
# Mapped onto the *existing* `EventType` vocabulary rather than inventing
# members for Gmail. Three map exactly; the rest fall back to `NOTE_ADDED`,
# which carries the meaning in its summary. That fallback is a real gap in the
# event vocabulary, not a design choice — see MISSING_EVENT_TYPES below.
EVENT_TYPE_FOR_MESSAGE: dict[EmailMessageType, EventType] = {
    EmailMessageType.INTERVIEW_SCHEDULED: EventType.INTERVIEW_SCHEDULED,
    EmailMessageType.ASSESSMENT_REQUESTED: EventType.ASSIGNMENT_RECEIVED,
    EmailMessageType.OFFER_RECEIVED: EventType.OFFER_RECEIVED,
    EmailMessageType.APPLICATION_RECEIVED: EventType.NOTE_ADDED,
    EmailMessageType.REJECTION: EventType.NOTE_ADDED,
    EmailMessageType.INTERVIEW_INVITATION: EventType.NOTE_ADDED,
    EmailMessageType.REFERRAL_OR_RECOMMENDATION: EventType.NOTE_ADDED,
    EmailMessageType.RECRUITER_OUTREACH: EventType.NOTE_ADDED,
    EmailMessageType.PROCESS_UPDATE: EventType.NOTE_ADDED,
    EmailMessageType.OTHER_RECRUITMENT: EventType.NOTE_ADDED,
}

# Message types whose proposed event has no matching `EventType` member and is
# therefore recorded as a generic note. Listed explicitly so the gap is visible
# and can be closed deliberately in the persistence slice.
MISSING_EVENT_TYPES: frozenset[EmailMessageType] = frozenset(
    message_type
    for message_type, event_type in EVENT_TYPE_FOR_MESSAGE.items()
    if event_type is EventType.NOTE_ADDED
)

# The human summary each proposed event would carry.
EVENT_SUMMARY_FOR_MESSAGE: dict[EmailMessageType, str] = {
    EmailMessageType.APPLICATION_RECEIVED: "Application confirmed by email",
    EmailMessageType.REJECTION: "Rejection received by email",
    EmailMessageType.INTERVIEW_INVITATION: "Interview invitation received by email",
    EmailMessageType.INTERVIEW_SCHEDULED: "Interview scheduled, confirmed by email",
    EmailMessageType.ASSESSMENT_REQUESTED: "Assessment requested by email",
    EmailMessageType.OFFER_RECEIVED: "Offer received by email",
    EmailMessageType.REFERRAL_OR_RECOMMENDATION: "Referral or recommendation received by email",
    EmailMessageType.RECRUITER_OUTREACH: "Recruiter outreach received by email",
    EmailMessageType.PROCESS_UPDATE: "Process update received by email",
    EmailMessageType.OTHER_RECRUITMENT: "Recruitment email received",
}

# The status each message type would propose, when the current state allows it.
#
# Only three. Everything else proposes an event and no status, because the
# existing lifecycle has no member that clearly corresponds:
#
#   * interview_invitation — an invitation is not an interview. There is no
#     "invited" status, and guessing between hr_interview and
#     technical_interview from an email is exactly the invention to avoid.
#   * interview_scheduled — same problem: which interview? The email says a time,
#     not a stage.
#   * assessment_requested — `take_home` exists, but a screening form is not a
#     take-home, and the two are not reliably distinguishable from an email.
#   * recruiter_outreach — `recruiter_contact` exists, but generic outreach about
#     an application already being tracked is not evidence the process advanced.
#   * process_update — explicitly not a stronger signal. "Moving forward" is not
#     an interview and not an offer.
STATUS_FOR_MESSAGE: dict[EmailMessageType, ApplicationStatus] = {
    EmailMessageType.APPLICATION_RECEIVED: ApplicationStatus.APPLIED,
    EmailMessageType.REJECTION: ApplicationStatus.REJECTED,
    EmailMessageType.OFFER_RECEIVED: ApplicationStatus.OFFER,
}


@dataclass(frozen=True)
class ApplicationContext:
    """The minimum application state a policy decision needs.

    Deliberately three fields. No CV, no description, no notes, no URL — the
    policy has no use for them, and a struct that cannot carry them cannot leak
    them into a decision or a log line.

    `has_submitted_cv` is here because it is a real precondition: several
    statuses cannot be recorded without one, so a proposal that ignored it would
    be an action the service refuses.
    """

    application_id: int
    status: ApplicationStatus
    has_submitted_cv: bool


@dataclass(frozen=True)
class PolicyInput:
    """Everything the policy considers, and nothing else."""

    message_type: EmailMessageType
    match_status: MatchStatus
    match_confidence: MatchConfidence | None = None
    application: ApplicationContext | None = None
    # Carried only for a create-application proposal's prefill.
    company_name: str | None = None
    role_title: str | None = None
    # Preserved from classification; never invented here.
    event_datetime: datetime | None = None
    # Review context for an ambiguous match. Never chosen between.
    candidate_application_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class ProposedAction:
    """One thing the user could accept. Nothing here has happened."""

    action_type: ProposedActionType
    summary: str
    application_id: int | None = None
    # CHANGE_STATUS only.
    to_status: ApplicationStatus | None = None
    # RECORD_EVENT only.
    event_type: EventType | None = None
    occurred_at: datetime | None = None
    # CREATE_APPLICATION only: safe prefill, never invented.
    company_name: str | None = None
    role_title: str | None = None
    application_channel: ApplicationChannel | None = None

    # Target the application the CREATE_APPLICATION action in this same plan
    # would produce, rather than an existing one.
    #
    # Needed because `application_id=None` is otherwise ambiguous — it means
    # both "no application to act on" and "the one we are about to create". A
    # plan's actions are ordered, so an executor applies the creation first and
    # then routes every action carrying this flag to the new row. Nothing is
    # executed in this phase; this only makes the sequence expressible.
    targets_new_application: bool = False

    # The user may take this or leave it. Distinct from the rest of a plan,
    # which is what JobOps thinks should happen: recruiter outreach about an
    # untracked role is worth *offering* to track, not recommending.
    optional: bool = False

    # Provenance for when this is eventually executed. `EventSource.GMAIL` has
    # existed since Phase 1 and was reserved for exactly this — so an
    # email-derived event is recorded honestly rather than appearing to be
    # something the user typed.
    event_source: EventSource = EventSource.GMAIL


@dataclass(frozen=True)
class SuggestionPlan:
    """What JobOps proposes about one email."""

    outcome: SuggestionPlanOutcome
    reason: str
    message_type: EmailMessageType
    actions: tuple[ProposedAction, ...] = ()
    application_id: int | None = None
    candidate_application_ids: tuple[int, ...] = ()
    field_notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def review_required(self) -> bool:
        return self.outcome is SuggestionPlanOutcome.REVIEW_REQUIRED

    @property
    def mutates(self) -> bool:
        """Whether accepting this plan would change anything."""
        return bool(self.actions)


def _no_action(message_type: EmailMessageType, reason: str) -> SuggestionPlan:
    return SuggestionPlan(
        outcome=SuggestionPlanOutcome.NO_ACTION, reason=reason, message_type=message_type
    )


def _review(
    message_type: EmailMessageType,
    reason: str,
    *,
    candidates: tuple[int, ...] = (),
) -> SuggestionPlan:
    return SuggestionPlan(
        outcome=SuggestionPlanOutcome.REVIEW_REQUIRED,
        reason=reason,
        message_type=message_type,
        candidate_application_ids=candidates,
    )


def decide(policy_input: PolicyInput) -> SuggestionPlan:
    """Decide what to propose. Pure — no session, no I/O, no provider.

    Order matters, and the refusals come first: a message type that never
    warrants action is dismissed before identity is considered at all, and an
    uncertain identity is dismissed before any action is composed. Nothing
    downstream has to remember to check.
    """
    message_type = policy_input.message_type

    # 1. Types that never warrant action, whatever the match says.
    if message_type in NO_ACTION_MESSAGE_TYPES:
        return _no_action(
            message_type,
            f"{message_type.value} is not evidence about an application process",
        )

    # 2. Three types can propose creating an application when nothing matches.
    #    They differ in how strong the evidence is that an application exists:
    #
    #      application_received — the employer has confirmed one does. The
    #        strongest evidence available, stronger than a referral.
    #      referral_or_recommendation — someone put the candidate forward, which
    #        is not the same as applying, but is worth tracking.
    #      recruiter_outreach — someone made contact about a role. Worth
    #        offering to track; not worth recommending.
    #
    #    Every other type with no match stays review-only: inventing an
    #    application from a rejection would create a record whose only content
    #    is that it already ended.
    if policy_input.match_status is MatchStatus.NO_MATCH:
        if message_type is EmailMessageType.APPLICATION_RECEIVED:
            return _confirmation_without_application(policy_input)
        if message_type is EmailMessageType.REFERRAL_OR_RECOMMENDATION:
            return _referral_without_application(policy_input)
        if message_type is EmailMessageType.RECRUITER_OUTREACH:
            return _outreach_without_application(policy_input)

    # 3. Identity must be settled before anything may target an application.
    identity = _identity_problem(policy_input)
    if identity is not None:
        return identity

    application = policy_input.application
    assert application is not None  # guaranteed by _identity_problem

    return _plan_for_matched(policy_input, application)


def _identity_problem(policy_input: PolicyInput) -> SuggestionPlan | None:
    """Refuse to target an application unless identity is properly established."""
    message_type = policy_input.message_type

    if policy_input.match_status is MatchStatus.AMBIGUOUS:
        return _review(
            message_type,
            "Several applications could be the subject of this email; "
            "choosing one is not the matcher's to make",
            candidates=policy_input.candidate_application_ids,
        )

    if policy_input.match_status is MatchStatus.NO_MATCH:
        # Surfaced, not discarded: a real rejection with no tracked application
        # is still worth seeing, it just has nothing to attach to.
        return _review(
            message_type,
            "No application matches this email, so there is nothing to update — "
            "review it or create the application first",
        )

    if policy_input.application is None:
        # A matched status with no context is a caller error, not a decision.
        return _review(
            message_type,
            "The email was matched but no application state was supplied",
        )

    if policy_input.match_confidence is MatchConfidence.LOW:
        # LOW means the company matched and nothing else did — the role was
        # missing entirely. That is enough to show someone; it is not enough to
        # move their application.
        return _review(
            message_type,
            "The application was identified by company alone, which is too weak "
            "to act on without confirmation",
            candidates=(policy_input.application.application_id,),
        )

    return None


def _creation_action(
    policy_input: PolicyInput,
    *,
    channel: ApplicationChannel | None,
    optional: bool = False,
) -> ProposedAction:
    """A create-application proposal prefilled only from what the email said."""
    return ProposedAction(
        action_type=ProposedActionType.CREATE_APPLICATION,
        summary="Create application from this email",
        company_name=policy_input.company_name,
        role_title=policy_input.role_title,
        application_channel=channel,
        optional=optional,
    )


def _missing_role_note(policy_input: PolicyInput) -> tuple[str, ...]:
    if policy_input.role_title:
        return ()
    return ("No role was named; the created application would need one added",)


def _cannot_create(policy_input: PolicyInput, what: str) -> SuggestionPlan:
    """Review instead of creating, when there is nothing to prefill with."""
    return _review(
        policy_input.message_type,
        f"{what} but no company could be extracted, so there is nothing to "
        "prefill an application with",
    )


def _confirmation_without_application(policy_input: PolicyInput) -> SuggestionPlan:
    """An employer confirmed an application JobOps is not tracking.

    The strongest creation evidence there is: not "someone thinks you would fit"
    but "we have your application". So the plan proposes creating the
    application *and* recording the confirmation against it — the second action
    targeting the row the first would produce.

    The channel is deliberately left unset. The email proves an application
    exists; it says nothing about whether it was submitted through a careers
    site, LinkedIn or a job board, and picking one would be inventing a fact the
    user would then have to notice and correct.
    """
    if not policy_input.company_name:
        return _cannot_create(policy_input, "An application confirmation arrived")

    return SuggestionPlan(
        outcome=SuggestionPlanOutcome.PROPOSE_ACTIONS,
        reason=(
            "An employer confirmed an application that JobOps is not tracking — "
            "create it and record the confirmation"
        ),
        message_type=policy_input.message_type,
        actions=(
            _creation_action(policy_input, channel=None),
            ProposedAction(
                action_type=ProposedActionType.RECORD_EVENT,
                summary=EVENT_SUMMARY_FOR_MESSAGE[EmailMessageType.APPLICATION_RECEIVED],
                event_type=EVENT_TYPE_FOR_MESSAGE[EmailMessageType.APPLICATION_RECEIVED],
                targets_new_application=True,
            ),
        ),
        field_notes=_missing_role_note(policy_input)
        + (
            "The application channel is unknown from the email and is left for "
            "you to set",
        ),
    )


def _referral_without_application(policy_input: PolicyInput) -> SuggestionPlan:
    """Someone put the candidate forward for an untracked role.

    Worth tracking, but weaker evidence than a confirmation: being referred is
    not applying, so no confirmation event accompanies the creation.
    """
    if not policy_input.company_name:
        return _cannot_create(policy_input, "A referral was received")

    return SuggestionPlan(
        outcome=SuggestionPlanOutcome.PROPOSE_ACTIONS,
        reason=(
            "You were referred for a role at a company with no tracked "
            "application — create one to follow it"
        ),
        message_type=policy_input.message_type,
        actions=(
            # Referral is how this opportunity arrived, which is exactly what
            # the channel field means.
            _creation_action(policy_input, channel=ApplicationChannel.REFERRAL),
        ),
        field_notes=_missing_role_note(policy_input),
    )


def _outreach_without_application(policy_input: PolicyInput) -> SuggestionPlan:
    """A recruiter made contact about a role JobOps is not tracking.

    News first, action second. There is no application yet and possibly never
    will be — the candidate has not applied and may not want to — so the
    creation is offered rather than recommended, and the outcome says
    "informational" rather than dressing an unread message up as a task.
    """
    if not policy_input.company_name:
        return _cannot_create(policy_input, "A recruiter made contact")

    return SuggestionPlan(
        outcome=SuggestionPlanOutcome.INFORMATIONAL,
        reason=(
            "A recruiter contacted you about a role with no tracked application "
            "— track it if you want to pursue it"
        ),
        message_type=policy_input.message_type,
        actions=(
            # A recruiter is how this arrived; unlike a confirmation, the
            # channel here is something the email actually establishes.
            _creation_action(
                policy_input, channel=ApplicationChannel.RECRUITER, optional=True
            ),
        ),
        field_notes=_missing_role_note(policy_input),
    )


def _plan_for_matched(
    policy_input: PolicyInput, application: ApplicationContext
) -> SuggestionPlan:
    """Compose the plan for an email whose application is established."""
    message_type = policy_input.message_type
    actions: list[ProposedAction] = []
    notes: list[str] = []

    # --- the event ---------------------------------------------------------
    event_type = EVENT_TYPE_FOR_MESSAGE.get(message_type, EventType.NOTE_ADDED)
    summary = EVENT_SUMMARY_FOR_MESSAGE.get(message_type, "Recruitment email received")

    # Only carried when classification established one. A scheduled interview
    # with no stated time gets an event without a time, not an invented one.
    occurred_at = (
        policy_input.event_datetime
        if message_type is EmailMessageType.INTERVIEW_SCHEDULED
        else None
    )
    if message_type is EmailMessageType.INTERVIEW_SCHEDULED and occurred_at is None:
        notes.append("The email did not state a timezone-aware time, so none is proposed")

    actions.append(
        ProposedAction(
            action_type=ProposedActionType.RECORD_EVENT,
            summary=summary,
            application_id=application.application_id,
            event_type=event_type,
            occurred_at=occurred_at,
        )
    )

    # --- the status change, if any -----------------------------------------
    proposed_status = STATUS_FOR_MESSAGE.get(message_type)
    if proposed_status is not None:
        status_note = _consider_status_change(application, proposed_status, actions)
        if status_note:
            notes.append(status_note)

    # A referral against a tracked application is news, not a change: being
    # referred does not mean having applied.
    informational = message_type in {
        EmailMessageType.REFERRAL_OR_RECOMMENDATION,
        EmailMessageType.RECRUITER_OUTREACH,
        EmailMessageType.PROCESS_UPDATE,
    }
    outcome = (
        SuggestionPlanOutcome.INFORMATIONAL
        if informational
        else SuggestionPlanOutcome.PROPOSE_ACTIONS
    )

    return SuggestionPlan(
        outcome=outcome,
        reason=_reason_for(message_type, actions),
        message_type=message_type,
        actions=tuple(actions),
        application_id=application.application_id,
        field_notes=tuple(notes),
    )


def _consider_status_change(
    application: ApplicationContext,
    proposed_status: ApplicationStatus,
    actions: list[ProposedAction],
) -> str | None:
    """Append a status proposal if it is both sensible and executable.

    Two independent checks, and both must pass:

    *Would it move backwards?* `STAGE_ORDER` is the project's existing notion of
    how far through a process a status is. A late confirmation email must not
    drag an application in `hr_interview` back to `applied` — the email is true,
    but the status is already further along and the event alone records it.
    Statuses outside `STAGE_ORDER` are terminal or on-hold; those are never
    reopened by an inbound email either.

    *Would it be refused?* Asked of `status_change_blocker`, the same function
    `change_status` uses, so this cannot drift from the real rule.
    """
    current = ApplicationStatus(application.status)

    if proposed_status is not ApplicationStatus.REJECTED:
        # A rejection is an ending rather than a stage, so it is never
        # "backwards"; everything else must be forward progress.
        current_stage = STAGE_ORDER.get(current)
        proposed_stage = STAGE_ORDER.get(proposed_status)
        if current_stage is None:
            return (
                f"No status change proposed: '{current.value}' is a terminal or hold "
                "status, and an inbound email should not reopen it"
            )
        if proposed_stage is not None and current_stage >= proposed_stage:
            return (
                f"No status change proposed: the application is already at "
                f"'{current.value}', which is at or beyond '{proposed_status.value}'"
            )

    blocker = status_change_blocker(
        current_status=application.status.value,
        to_status=proposed_status,
        has_submitted_cv=application.has_submitted_cv,
    )
    if blocker is not None:
        return (
            f"No status change proposed: moving to '{proposed_status.value}' would be "
            f"refused ({blocker.value})"
        )

    actions.append(
        ProposedAction(
            action_type=ProposedActionType.CHANGE_STATUS,
            summary=f"Change status to '{proposed_status.value}'",
            application_id=application.application_id,
            to_status=proposed_status,
        )
    )
    return None


def _reason_for(message_type: EmailMessageType, actions: list[ProposedAction]) -> str:
    kinds = {action.action_type for action in actions}
    if ProposedActionType.CHANGE_STATUS in kinds:
        return (
            f"The email is a {message_type.value.replace('_', ' ')} for this application; "
            "record it and move the application on"
        )
    return (
        f"The email is a {message_type.value.replace('_', ' ')} for this application; "
        "record it on the timeline"
    )


def idempotency_key(email_message_id: int, action: ProposedAction) -> str:
    """The stable identity a later persistence layer should deduplicate on.

    Recommendation, not yet enforced — nothing is persisted in this slice.

    Built from the internal `EmailMessage.id` rather than the Gmail message id:
    the internal id is already the stable handle everything else uses, and
    keeping the provider id out of derived records means a future provider
    change is not a data migration.

    Action type and target are included because one email can legitimately
    propose two things about the same application — a confirmation event *and* a
    move to applied — and those must dedupe independently rather than collapsing
    into one.
    """
    target = action.application_id if action.application_id is not None else "new"
    return f"email:{email_message_id}:{action.action_type.value}:{target}"
