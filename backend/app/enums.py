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

# Events that carry a status. Replaying these reconstructs `Application.status`.
STATUS_BEARING_EVENT_TYPES: frozenset[EventType] = frozenset(
    {EventType.CREATED, EventType.STATUS_CHANGED}
)

# Event types a human may add by hand. Status-bearing types are excluded: those
# are produced only by the service layer, so that a hand-written event can never
# silently rewrite an application's status.
MANUAL_EVENT_TYPES: frozenset[EventType] = frozenset(EventType) - STATUS_BEARING_EVENT_TYPES


def sql_value_list(enum_cls: type[StrEnum]) -> str:
    """Render an enum as a SQL literal list, for CHECK constraints."""
    return ", ".join(f"'{member.value}'" for member in enum_cls)
