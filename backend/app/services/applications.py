"""Application business logic.

This module owns every rule about applications. Routers translate HTTP to these
functions and back; they contain no logic of their own. Later callers (the
Chrome extension endpoint, the Gmail suggestion queue) will call exactly these
same functions, which is what keeps the rules in one place.

The central invariant of the whole system lives here:

    `_record_status` is the only function that assigns `Application.status`,
    and it always writes a matching timeline event.

Because both changes are made on the same SQLAlchemy Session and committed once,
PostgreSQL applies them as a single transaction: either the column and the event
both land, or neither does.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import (
    ApplicationNotFound,
    DocumentKindNotAllowed,
    StatusUnchanged,
    SubmittedCvRequired,
    SubmittedCvUnchanged,
)
from app.core.normalize import canonicalize_url, normalize_company, normalize_role
from app.enums import (
    STATUSES_REQUIRING_SUBMITTED_CV,
    TERMINAL_STATUSES,
    ApplicationChannel,
    ApplicationStatus,
    DocumentKind,
    EventSource,
    EventType,
)
from app.models.application import Application
from app.models.application_event import ApplicationEvent
from app.schemas.application import ApplicationCreate, ApplicationUpdate
from app.services.documents import get_document


def _utcnow() -> datetime:
    """Timezone-aware UTC now.

    Always timezone-aware, always UTC. Naive datetimes are how "the interview is
    at 14:00" quietly becomes the wrong hour. Conversion to local time happens in
    the UI, never in storage.
    """
    return datetime.now(UTC)


# --- The one place that writes Application.status -------------------------


def _record_status(
    db: Session,
    application: Application,
    *,
    new_status: ApplicationStatus,
    event_type: EventType,
    source: EventSource,
    summary: str,
    note: str | None,
    occurred_at: datetime,
) -> ApplicationEvent:
    """Set the cached status and append the event that explains it.

    Private by convention (leading underscore): callers use `create_application`
    or `change_status`. Keeping the assignment in a single private function is
    what makes the invariant enforceable rather than merely intended — there is
    exactly one line in the codebase that writes `application.status`.

    Note there is no `db.commit()` here. Committing is the caller's decision, so
    that a caller can bundle several changes into one transaction if it needs to.
    """
    previous_status = application.status if event_type is EventType.STATUS_CHANGED else None

    application.status = new_status.value

    # Record when you actually applied, the first time you reach `applied`.
    if new_status is ApplicationStatus.APPLIED and application.applied_at is None:
        application.applied_at = occurred_at

    # Terminal statuses close the application; moving back out reopens it (which
    # does happen — a "rejected" role gets un-frozen and the recruiter returns).
    application.closed_at = occurred_at if new_status in TERMINAL_STATUSES else None

    event = ApplicationEvent(
        application_id=application.id,
        event_type=event_type.value,
        occurred_at=occurred_at,
        source=source.value,
        previous_status=previous_status,
        new_status=new_status.value,
        summary=summary,
        body=note,
    )
    db.add(event)
    return event


# --- Reads ----------------------------------------------------------------


def get_application(db: Session, application_id: int, *, with_events: bool = False) -> Application:
    """Fetch one application, or raise `ApplicationNotFound`."""
    stmt = select(Application).where(
        Application.id == application_id,
        Application.deleted_at.is_(None),
    )
    if with_events:
        # selectinload fetches all events in ONE extra query. Without it,
        # serialising the timeline would fire a separate query per event
        # (the "N+1 query problem").
        #
        # `submitted_cv` is declared lazy="raise_on_sql", so it MUST be loaded
        # explicitly here. That is the point: the relationship refuses to load
        # itself behind your back, which turns an invisible N+1 into a loud
        # error the first time someone forgets.
        stmt = stmt.options(
            selectinload(Application.events),
            selectinload(Application.submitted_cv),
        )

    application = db.execute(stmt).scalar_one_or_none()
    if application is None:
        raise ApplicationNotFound(application_id)
    return application


def list_applications(
    db: Session,
    *,
    status: ApplicationStatus | None = None,
    channel: ApplicationChannel | None = None,
    query: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[Sequence[Application], int]:
    """Return one page of applications plus the total number of matches."""
    filters = [Application.deleted_at.is_(None)]

    if status is not None:
        filters.append(Application.status == status.value)
    if channel is not None:
        filters.append(Application.application_channel == channel.value)
    if query:
        # ILIKE is PostgreSQL's case-insensitive LIKE. At a few hundred rows this
        # is instant; full-text search would be complexity we cannot yet justify.
        pattern = f"%{query.strip()}%"
        filters.append(
            or_(Application.company_name.ilike(pattern), Application.role_title.ilike(pattern))
        )

    total = db.execute(select(func.count()).select_from(Application).where(*filters)).scalar_one()

    stmt = (
        select(Application)
        .where(*filters)
        # `id` is a tiebreaker, not decoration: with offset pagination and a
        # non-unique sort key, rows can otherwise repeat or vanish across pages.
        .order_by(Application.created_at.desc(), Application.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return db.execute(stmt).scalars().all(), total


# --- Writes ---------------------------------------------------------------


def create_application(db: Session, data: ApplicationCreate) -> Application:
    """Create an application and open its timeline with a `created` event.

    The `created` event carries `new_status`, so replaying the log reproduces the
    status from the very first entry. Emitting a separate `status_changed` event
    on creation would mean two rows saying the same thing.

    A submitted CV may be supplied so a real application can be recorded in one
    step (fill details -> choose Applied -> pick the CV -> create). It is
    validated the same way `attach_submitted_cv` validates, and the application,
    its `created` event, and the `document_attached` event all commit together.
    """
    # Validate the CV up front (if given), so we never create anything on a bad
    # reference. get_document raises DocumentNotFound; kind is checked here.
    cv_document = None
    if data.submitted_cv_document_id is not None:
        cv_document = get_document(db, data.submitted_cv_document_id)
        if cv_document.kind != DocumentKind.CV:
            raise DocumentKindNotAllowed(cv_document.id, cv_document.kind, DocumentKind.CV.value)

    # A submitted-state status requires a CV. `saved` may have one, but need not.
    if data.status in STATUSES_REQUIRING_SUBMITTED_CV and cv_document is None:
        raise SubmittedCvRequired(data.status.value, on_create=True)

    now = _utcnow()

    application = Application(
        company_name=data.company_name.strip(),
        company_key=normalize_company(data.company_name),
        role_title=data.role_title.strip(),
        role_key=normalize_role(data.role_title),
        # Placeholder: `_record_status` below sets the real value. The column is
        # NOT NULL, so it needs something before the flush.
        status=data.status.value,
        application_channel=data.application_channel.value,
        job_url=data.job_url,
        job_url_canonical=canonicalize_url(data.job_url),
        job_description=data.job_description,
        location=data.location,
        work_mode=data.work_mode,
        notes=data.notes,
        submitted_cv_document_id=data.submitted_cv_document_id,
    )
    db.add(application)
    # flush() sends the INSERT so PostgreSQL assigns an id we can reference from
    # the event — but does NOT commit. Everything stays in one transaction.
    db.flush()

    _record_status(
        db,
        application,
        new_status=data.status,
        event_type=EventType.CREATED,
        source=EventSource.MANUAL,
        summary=f"Application created with status '{data.status.value}'",
        note=None,
        occurred_at=now,
    )

    # Record the submitted CV on the timeline too, matching attach_submitted_cv.
    if cv_document is not None:
        document_name = (
            cv_document.label or cv_document.original_filename or f"document {cv_document.id}"
        )
        db.add(
            ApplicationEvent(
                application_id=application.id,
                event_type=EventType.DOCUMENT_ATTACHED.value,
                occurred_at=now,
                source=EventSource.MANUAL.value,
                document_id=cv_document.id,
                summary=f"Submitted CV attached: {document_name}"[:300],
            )
        )

    # An explicitly supplied applied_at wins over the one inferred above.
    if data.applied_at is not None:
        application.applied_at = data.applied_at

    db.commit()
    db.refresh(application)
    return application


def change_status(
    db: Session,
    application_id: int,
    *,
    to_status: ApplicationStatus,
    note: str | None = None,
    occurred_at: datetime | None = None,
    source: EventSource = EventSource.MANUAL,
) -> Application:
    """The single entry point for changing an application's status.

    Any transition is allowed. Real hiring processes skip stages, go backwards,
    and reopen — a strict state machine would fight reality every week. The event
    log records what actually happened, which is more useful than a rule that
    says it could not have.
    """
    application = get_application(db, application_id)

    if application.status == to_status.value:
        raise StatusUnchanged(application.status)

    # A submitted-state status may not be recorded without knowing which CV was
    # sent. `saved` and the terminal/hold statuses are exempt.
    if (
        to_status in STATUSES_REQUIRING_SUBMITTED_CV
        and application.submitted_cv_document_id is None
    ):
        raise SubmittedCvRequired(to_status.value)

    previous_status = application.status
    when = occurred_at or _utcnow()

    _record_status(
        db,
        application,
        new_status=to_status,
        event_type=EventType.STATUS_CHANGED,
        source=source,
        summary=f"Status changed from '{previous_status}' to '{to_status.value}'",
        note=note,
        occurred_at=when,
    )

    # One commit -> one transaction -> the column update and the event insert are
    # applied together or not at all.
    db.commit()
    db.refresh(application)
    return application


def attach_submitted_cv(
    db: Session,
    application_id: int,
    document_id: int,
    *,
    note: str | None = None,
    occurred_at: datetime | None = None,
    source: EventSource = EventSource.MANUAL,
) -> Application:
    """Record which exact CV was submitted for this application.

    The `kind == "cv"` rule is enforced HERE, in the service layer, not in the
    router. The router is only one of several future callers — the Chrome
    extension endpoint and the Gmail suggestion queue will call this same
    function — and a rule enforced at one entry point is a rule that holds until
    someone adds a second entry point.

    (PostgreSQL could enforce this too, via a composite foreign key on
    `documents (id, kind)` plus a pinned column on `applications`. That was
    considered and rejected: it needs a redundant column and a more complex
    schema to express a rule that one line of Python states clearly.)
    """
    application = get_application(db, application_id)
    document = get_document(db, document_id)

    if document.kind != DocumentKind.CV:
        raise DocumentKindNotAllowed(document.id, document.kind, DocumentKind.CV.value)

    if application.submitted_cv_document_id == document.id:
        raise SubmittedCvUnchanged(document.id)

    previous_document_id = application.submitted_cv_document_id
    application.submitted_cv_document_id = document.id

    document_name = document.label or document.original_filename or f"document {document.id}"
    if previous_document_id is None:
        summary = f"Submitted CV attached: {document_name}"
    else:
        summary = f"Submitted CV changed to: {document_name}"

    db.add(
        ApplicationEvent(
            application_id=application.id,
            event_type=EventType.DOCUMENT_ATTACHED.value,
            occurred_at=occurred_at or _utcnow(),
            source=source.value,
            document_id=document.id,
            summary=summary[:300],
            body=note,
        )
    )

    # One commit: the column and the timeline entry land together, exactly as
    # with status changes.
    db.commit()
    db.refresh(application)
    return application


def update_application(db: Session, application_id: int, data: ApplicationUpdate) -> Application:
    """Edit descriptive fields. Cannot touch status — see `ApplicationUpdate`."""
    application = get_application(db, application_id)

    # exclude_unset distinguishes "field omitted" from "field explicitly set to
    # null", so a PATCH that mentions three fields only changes those three.
    changes = data.model_dump(exclude_unset=True)

    for field, value in changes.items():
        setattr(application, field, value)

    # Keep derived columns consistent with the text they are derived from.
    if "company_name" in changes:
        application.company_key = normalize_company(application.company_name)
    if "role_title" in changes:
        application.role_key = normalize_role(application.role_title)
    if "job_url" in changes:
        application.job_url_canonical = canonicalize_url(application.job_url)

    db.commit()
    db.refresh(application)
    return application


def soft_delete_application(db: Session, application_id: int) -> None:
    """Hide an application without destroying its history."""
    application = get_application(db, application_id)
    application.deleted_at = _utcnow()
    db.commit()


# --- Consistency check ----------------------------------------------------


def rebuild_status_from_events(db: Session, application_id: int) -> str | None:
    """Recompute status purely from the event log.

    The replay rule is deliberately simple: the current status is the
    `new_status` of the most recent event that carries one. Returns None if the
    application has no status-bearing events at all.

    This is the safety net for caching status on the application row. If this
    ever disagrees with `applications.status`, something wrote that column
    without going through `_record_status` — which is exactly the bug that
    denormalisation risks, now detectable instead of silent.
    """
    stmt = (
        select(ApplicationEvent.new_status)
        .where(
            ApplicationEvent.application_id == application_id,
            ApplicationEvent.new_status.is_not(None),
        )
        .order_by(ApplicationEvent.occurred_at.desc(), ApplicationEvent.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def find_status_inconsistencies(db: Session) -> list[tuple[int, str, str | None]]:
    """Every application whose cached status disagrees with its event log.

    Returns (application_id, stored_status, replayed_status). An empty list means
    the cache is trustworthy.
    """
    inconsistencies = []
    application_ids = db.execute(select(Application.id)).scalars().all()

    for application_id in application_ids:
        stored = db.execute(
            select(Application.status).where(Application.id == application_id)
        ).scalar_one()
        replayed = rebuild_status_from_events(db, application_id)
        if stored != replayed:
            inconsistencies.append((application_id, stored, replayed))

    return inconsistencies
