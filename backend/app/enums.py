"""Domain vocabulary: the fixed sets of values the system understands.

These are `StrEnum`, so every member *is* a string (`ApplicationStatus.APPLIED
== "applied"` is True). That means they serialise to JSON, compare to database
values, and print in log messages without any conversion.

They are stored in PostgreSQL as VARCHAR guarded by a CHECK constraint, not as a
native PostgreSQL ENUM type. Native enums are painful to change — adding a value
requires ALTER TYPE, and removing one is worse. A CHECK constraint is edited by
an ordinary migration. We expect this vocabulary to grow, so we optimise for
"easy to change".
"""

from enum import StrEnum


class ApplicationStatus(StrEnum):
    """Where an application currently stands."""

    SAVED = "saved"
    APPLIED = "applied"
    RECRUITER_CONTACT = "recruiter_contact"
    HR_INTERVIEW = "hr_interview"
    TECHNICAL_INTERVIEW = "technical_interview"
    TAKE_HOME = "take_home"
    FINAL_INTERVIEW = "final_interview"
    OFFER = "offer"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    ON_HOLD = "on_hold"


class DocumentKind(StrEnum):
    """What a stored file is.

    The `Document` entity is deliberately generic. CVs are the main kind today,
    but take-home submissions and cover letters are the same thing from the
    storage layer's point of view: an immutable blob of bytes with metadata.
    """

    CV = "cv"
    COVER_LETTER = "cover_letter"
    TAKE_HOME = "take_home"
    PORTFOLIO = "portfolio"
    OTHER = "other"


class EventType(StrEnum):
    """What kind of thing happened on an application's timeline."""

    CREATED = "created"
    STATUS_CHANGED = "status_changed"
    NOTE_ADDED = "note_added"
    DOCUMENT_ATTACHED = "document_attached"
    INTERVIEW_SCHEDULED = "interview_scheduled"
    INTERVIEW_COMPLETED = "interview_completed"
    ASSIGNMENT_RECEIVED = "assignment_received"
    ASSIGNMENT_SUBMITTED = "assignment_submitted"
    OFFER_RECEIVED = "offer_received"
    FOLLOWED_UP = "followed_up"
    IMPORTED = "imported"


class EventSource(StrEnum):
    """Which part of the system produced an event.

    Everything is `MANUAL` in Phase 1. The other values exist so that later
    integrations are recorded honestly rather than pretending to be you.
    """

    MANUAL = "manual"
    EXTENSION = "extension"
    GMAIL = "gmail"
    SYSTEM = "system"


class ApplicationChannel(StrEnum):
    """The channel through which the application was actually submitted.

    Deliberately NOT "where I discovered the job" — finding a role on LinkedIn
    but submitting on the company's own site is `COMPANY_SITE`.
    """

    LINKEDIN = "linkedin"
    COMPANY_SITE = "company_site"
    RECRUITER = "recruiter"
    REFERRAL = "referral"
    JOB_BOARD = "job_board"
    OTHER = "other"


# --- Derived groupings ----------------------------------------------------
# Kept in Python rather than the database: they are business rules, they change
# more often than the schema, and expressing them here keeps them testable.

TERMINAL_STATUSES: frozenset[ApplicationStatus] = frozenset(
    {
        ApplicationStatus.ACCEPTED,
        ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN,
    }
)

ACTIVE_STATUSES: frozenset[ApplicationStatus] = frozenset(ApplicationStatus) - TERMINAL_STATUSES

# How far through a hiring process each stage is. Used to answer "what is the
# furthest stage I reached?" by replaying the event log — which is why we do not
# need a separate `outcome` column. Terminal statuses are absent on purpose:
# "rejected" is not a stage, it is an ending, and the stage it ended at is
# recorded in the rejecting event's `previous_status`.
STAGE_ORDER: dict[ApplicationStatus, int] = {
    ApplicationStatus.SAVED: 0,
    ApplicationStatus.APPLIED: 1,
    ApplicationStatus.RECRUITER_CONTACT: 2,
    ApplicationStatus.HR_INTERVIEW: 3,
    ApplicationStatus.TAKE_HOME: 4,
    ApplicationStatus.TECHNICAL_INTERVIEW: 5,
    ApplicationStatus.FINAL_INTERVIEW: 6,
    ApplicationStatus.OFFER: 7,
}

# Statuses that mean the application has actually been submitted, and therefore
# require a recorded submitted CV. `saved` is deliberately excluded — a posting
# may be saved before applying. The terminal/hold statuses (rejected, withdrawn,
# on_hold) are also excluded: you can reach them from `saved` without ever having
# submitted (e.g. deciding not to apply, or a posting freezing before you did).
STATUSES_REQUIRING_SUBMITTED_CV: frozenset[ApplicationStatus] = frozenset(
    {
        ApplicationStatus.APPLIED,
        ApplicationStatus.RECRUITER_CONTACT,
        ApplicationStatus.HR_INTERVIEW,
        ApplicationStatus.TECHNICAL_INTERVIEW,
        ApplicationStatus.TAKE_HOME,
        ApplicationStatus.FINAL_INTERVIEW,
        ApplicationStatus.OFFER,
        ApplicationStatus.ACCEPTED,
    }
)

# Events that carry a status. Replaying these reconstructs `Application.status`.
STATUS_BEARING_EVENT_TYPES: frozenset[EventType] = frozenset(
    {EventType.CREATED, EventType.STATUS_CHANGED}
)

# Event types a human may add by hand. Status-bearing types are excluded: those
# are produced only by the service layer, so that a hand-written event can never
# silently rewrite an application's status.
MANUAL_EVENT_TYPES: frozenset[EventType] = frozenset(EventType) - STATUS_BEARING_EVENT_TYPES


class SuggestionSource(StrEnum):
    """Where a proposed status change came from.

    `MANUAL` is what makes suggestions creatable and testable now, before any
    integration exists. `GMAIL` and `CLAUDE` are reserved for later phases —
    adding this vocabulary now, rather than when Gmail/Claude land, is what
    "keep this architecture ready" means: those phases add a producer, not a
    schema change.
    """

    MANUAL = "manual"
    GMAIL = "gmail"
    CLAUDE = "claude"


class SuggestionConfidence(StrEnum):
    """How sure the producer is. Advisory — never affects what accepting does."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SuggestionState(StrEnum):
    """A suggestion's place in its (short) lifecycle.

    PENDING is the only state a suggestion can be acted on from. ACCEPTED and
    REJECTED are both final — resolved once, never re-processed.
    """

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class SuggestionKind(StrEnum):
    """Which shape a suggestion row has.

    `STATUS_CHANGE` is the original Phase 5 suggestion: one proposed status for
    one existing application. Every row that existed before email plans did is
    this kind, and it is the only kind the existing review endpoints handle.

    `EMAIL_PLAN` is the persisted form of an `email_policy.SuggestionPlan`: one
    row per email, carrying the plan's outcome, with its ordered actions in
    `suggestion_actions`. It may have no application (a review-only plan, or one
    proposing to create one) and no status at all (an event-only plan).
    """

    STATUS_CHANGE = "status_change"
    EMAIL_PLAN = "email_plan"


class EmailDirection(StrEnum):
    """Who authored a stored Gmail message: the account owner, or someone else.

    Determined from Gmail's own `labelIds` metadata, never from the sender
    address: parsing "is this me?" out of a `From:` header would mean hardcoding
    a personal address into the codebase, and would break on aliases, plus
    addressing, and display-name changes. `SENT` and `DRAFT` are system labels
    Gmail applies itself, so they are authoritative and cost nothing to read.

    OUTGOING means "the account owner wrote it" — sent mail *and* unsent drafts.
    Slightly broader than the word suggests, and deliberately so: what the rest
    of the system does with this field is decide whether the text is the user's
    own words or an employer's, and an unsent draft is just as much the user's
    own words as a sent reply.

    INCOMING means "it arrived from someone else". Note this is NOT "it is in
    the inbox": `INBOX` disappears the moment a message is archived, so keying
    off it would misfile every archived recruitment email. See
    `core.gmail_parse.direction_from_labels` for the full rule.

    UNKNOWN is a real answer, not a failure — but a narrow one. It means no
    label evidence at all, plus rows imported before this column existed. A
    confident blank beats a confident wrong answer, the same principle the
    capture extension uses.

    Product policy this enables (Phase 6.2A/6.2B, deliberately not implemented
    here): an `outgoing` message is never sent to the classifier and never
    produces a suggestion by itself. A reply the user wrote is evidence about the
    user, not about the employer's decision. Outgoing mail may later become
    useful as *thread context*, but that is a separate phase.
    """

    INCOMING = "incoming"
    OUTGOING = "outgoing"
    UNKNOWN = "unknown"


class EmailBodySource(StrEnum):
    """Where a stored message's `body_text` actually came from.

    Recorded because "why is this body only 200 characters?" was, for a while,
    unanswerable. A Gmail `snippet` and a genuinely short email look identical
    once stored, so there was no way to tell which rows were previews and which
    were complete — and therefore no safe way to improve the previews without
    risking overwriting good content.

    Ordered by quality (see `BODY_SOURCE_QUALITY`), which is what lets a later
    sync upgrade a row without ever downgrading one.
    """

    # A real text/plain MIME part: what the sender wrote, as they wrote it.
    PLAIN = "plain"
    # Converted locally from text/html because no usable text/plain part existed.
    HTML = "html"
    # Gmail's ~200-character preview. A last resort: it is truncated mid-
    # sentence and routinely contains only the polite opening of a message
    # whose actual decision comes later.
    SNIPPET = "snippet"
    # No body at all.
    NONE = "none"
    # Imported before this column existed, so the source was never recorded.
    UNKNOWN = "unknown"


# How good each body source is. A sync may replace a stored body only with one
# that ranks strictly higher, which is what makes enrichment safe to run
# repeatedly: a good body can never be overwritten by a worse one.
BODY_SOURCE_QUALITY: dict[EmailBodySource, int] = {
    EmailBodySource.PLAIN: 3,
    EmailBodySource.HTML: 2,
    EmailBodySource.SNIPPET: 1,
    EmailBodySource.NONE: 0,
    # Unknown ranks lowest so that any freshly determined source is an
    # improvement on "we never recorded it".
    EmailBodySource.UNKNOWN: 0,
}

# Sources worth re-fetching a stored message for. Once a row holds plain or
# html text there is nothing better to find, so it is never fetched again and a
# steady-state sync costs exactly what it did before enrichment existed.
UPGRADABLE_BODY_SOURCES: frozenset[EmailBodySource] = frozenset(
    {EmailBodySource.UNKNOWN, EmailBodySource.SNIPPET, EmailBodySource.NONE}
)


class EmailMessageType(StrEnum):
    """What a recruitment email *means* — the classifier's semantic vocabulary.

    This is an interpretation contract, not an action policy. A value here says
    what the message is, never what JobOps should do about it: no status change,
    no suggestion, no application match is implied by any member. Those are
    deterministic product decisions made later, from this value plus context the
    classifier never sees.

    Ordering below is roughly "least to most engaged", which is a reading aid
    only — nothing depends on member order.
    """

    # Not meaningfully related to a job or recruitment process at all.
    IRRELEVANT = "irrelevant"

    # Job recommendations or saved-search alerts. These often contain real
    # company and role names, which makes them easy to mistake for evidence of
    # an application — they are not. No application exists because of an alert.
    JOB_ALERT = "job_alert"

    # Confirmation that an application or submission was received.
    APPLICATION_RECEIVED = "application_received"

    # The candidate was referred or recommended by someone else, or another
    # person submitted their details. Deliberately distinct from
    # APPLICATION_RECEIVED: being referred does NOT mean the candidate
    # personally applied, and later phases must not assume `applied` from it.
    REFERRAL_OR_RECOMMENDATION = "referral_or_recommendation"

    # A recruiter initiates meaningful contact about a role but has not yet
    # asked to arrange a call. The distinction from INTERVIEW_INVITATION is the
    # ask: "we have a role that fits you" is outreach; "when are you free?" is
    # an invitation.
    RECRUITER_OUTREACH = "recruiter_outreach"

    # The company asks the candidate to arrange, or to supply availability for,
    # a call or interview — but no final date/time is settled yet.
    INTERVIEW_INVITATION = "interview_invitation"

    # A concrete interview/call date and time is established. This is the only
    # type that routinely justifies a non-null `event_datetime`.
    INTERVIEW_SCHEDULED = "interview_scheduled"

    # A candidate action is requested: technical questions, take-home task,
    # coding assessment, screening form, or similar.
    ASSESSMENT_REQUESTED = "assessment_requested"

    # Recruitment-process information that fits no more specific type above.
    # A fallback, NOT a catch-all: if the message actually schedules an
    # interview or rejects the candidate, it is that type, not this one.
    PROCESS_UPDATE = "process_update"

    # The company communicates that the candidacy will not continue.
    #
    # Subject lines mislead here more than anywhere else: "Thank you for
    # applying" and "Update on your application" are both common rejection
    # subjects. Classification must read the body, never the subject alone.
    REJECTION = "rejection"

    # An actual job offer is communicated — not generic positive progress, and
    # not "we would like to move you to the next round".
    OFFER_RECEIVED = "offer_received"

    # GDPR/privacy/data-retention consent or similar administrative notices,
    # typically automated from an ATS.
    PRIVACY_OR_RETENTION_NOTICE = "privacy_or_retention_notice"

    # A request to rate or give feedback about an interview experience. Often
    # evidence that an interview happened, but semantically distinct from the
    # interview itself — kept separate so later phases can decide what, if
    # anything, to infer from it.
    POST_INTERVIEW_SURVEY = "post_interview_survey"

    # Clearly recruitment-related, but none of the specific types above.
    OTHER_RECRUITMENT = "other_recruitment"


class ClassificationConfidence(StrEnum):
    """How sure the classifier is about its interpretation.

    Coarse buckets on purpose. An LLM's numeric self-reported probability
    ("0.97") is not calibrated and reads as far more precise than it is; three
    named levels are honest about what the signal actually supports.

    Deliberately NOT `SuggestionConfidence`, despite sharing values today. That
    enum describes how sure the producer of a *status proposal* is; this one
    describes certainty about a *semantic reading* of an email. Keeping them
    apart is what lets classification stay independent of suggestion policy —
    the entire point of splitting these phases — and lets either vocabulary
    change without dragging the other with it.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# Message types that are recruitment-related. Derived from the enum rather than
# stored as a separate `recruitment_related` field on the classifier's output:
# two fields that can contradict each other is a bug waiting to happen, so
# `message_type` stays the single source of truth and this is computed from it.
RECRUITMENT_MESSAGE_TYPES: frozenset[EmailMessageType] = frozenset(EmailMessageType) - frozenset(
    {EmailMessageType.IRRELEVANT}
)

# Directions whose messages are eligible to be classified at all.
#
# Policy captured here, enforced in a later phase: outgoing mail is the user's
# own writing (sent or drafted), so classifying it would produce statements
# about the user's intent dressed up as statements about an employer's decision.
#
# `unknown` is included, which is safe because of how narrow it now is: every
# message the account owner authored carries SENT or DRAFT, so `unknown` cannot
# be the user's own writing — it is only a message with no label evidence at
# all, which in practice means a degenerate API response rather than anything
# the user wrote.
CLASSIFIABLE_EMAIL_DIRECTIONS: frozenset[EmailDirection] = frozenset(
    {EmailDirection.INCOMING, EmailDirection.UNKNOWN}
)


class MatchStatus(StrEnum):
    """Whether a classified email could be tied to an existing Application.

    Three outcomes, and the middle one is load-bearing. Without AMBIGUOUS the
    matcher would have to choose between inventing a match it cannot justify and
    reporting nothing at all — when the useful answer is often "here are the two
    applications this could be, you decide".

    The guiding rule is that a false NO_MATCH costs a manual link, while a false
    MATCHED silently files an employer's rejection against the wrong job. Those
    are not comparable, so the matcher declines whenever the evidence is thin.
    """

    MATCHED = "matched"
    AMBIGUOUS = "ambiguous"
    NO_MATCH = "no_match"


class MatchConfidence(StrEnum):
    """How sure the matcher is about *identity*.

    Deliberately not `ClassificationConfidence`. That one says how sure a model
    is about what an email means; this says how sure deterministic logic is about
    which application it belongs to. A message can be an unmistakable rejection
    (classification: high) whose employer matches four saved applications
    (matching: ambiguous) — collapsing the two would destroy exactly the
    distinction a reviewer needs.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CompanySignal(StrEnum):
    """How the email's company related to the matched application's."""

    # The names were already identical, ignoring case and surrounding space.
    EXACT = "exact"
    # They agreed only after normalisation — a legal suffix, punctuation, or
    # accents differed. Recorded separately because "we transformed the input to
    # make it fit" is weaker evidence than "they were the same".
    NORMALIZED = "normalized"
    # A company was extracted, but no application matched it.
    NONE = "none"
    # The classifier did not extract a company at all.
    MISSING = "missing"


class RoleSignal(StrEnum):
    """How the email's role related to the matched application's."""

    EXACT = "exact"
    NORMALIZED = "normalized"
    NONE = "none"
    MISSING = "missing"


class SuggestionPlanOutcome(StrEnum):
    """What kind of response an email warrants, before anything is executed.

    A plan is a *proposal*. Nothing in this vocabulary implies that JobOps has
    acted, only what it would put in front of the user.
    """

    # Concrete proposals the user can accept: record an event, change a status,
    # create an application.
    PROPOSE_ACTIONS = "propose_actions"
    # Worth showing, but nothing to accept — the email carries news rather than
    # a change to make.
    INFORMATIONAL = "informational"
    # Meaningful, but the target is not established. Never carries a mutation.
    REVIEW_REQUIRED = "review_required"
    # Not worth the user's attention at all.
    NO_ACTION = "no_action"


class ProposedActionType(StrEnum):
    """The kinds of thing a plan may propose."""

    # Append a timeline event to an existing application.
    RECORD_EVENT = "record_event"
    # Move an existing application to a different status.
    CHANGE_STATUS = "change_status"
    # Create a new application, prefilled from the email.
    CREATE_APPLICATION = "create_application"


# Message types that never warrant an action, however confidently matched.
#
# Each is a deliberate v1 decision rather than an oversight: a job alert is not
# evidence of an application; a data-retention notice is administrative noise;
# a post-interview survey implies an interview happened but inferring
# retrospective history from it was explicitly deferred.
NO_ACTION_MESSAGE_TYPES: frozenset[EmailMessageType] = frozenset(
    {
        EmailMessageType.IRRELEVANT,
        EmailMessageType.JOB_ALERT,
        EmailMessageType.PRIVACY_OR_RETENTION_NOTICE,
        EmailMessageType.POST_INTERVIEW_SURVEY,
    }
)


def sql_value_list(enum_cls: type[StrEnum]) -> str:
    """Render an enum as a SQL literal list, for CHECK constraints."""
    return ", ".join(f"'{member.value}'" for member in enum_cls)
