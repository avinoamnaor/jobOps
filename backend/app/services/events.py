"""Timeline event logic for events a human adds by hand.

Status-bearing events are not created here — they come from
`services.applications`, which is the only module allowed to produce them.
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import MANUAL_EVENT_TYPES, EventSource, EventType
from app.models.application_event import ApplicationEvent
from app.schemas.event import EventCreate
from app.services.applications import _utcnow, get_application


def list_events(db: Session, application_id: int) -> Sequence[ApplicationEvent]:
    """The full timeline, newest first."""
    # Confirms the application exists (and is not deleted) before returning rows.
    get_application(db, application_id)

    stmt = (
        select(ApplicationEvent)
        .where(ApplicationEvent.application_id == application_id)
        .order_by(ApplicationEvent.occurred_at.desc(), ApplicationEvent.id.desc())
    )
    return db.execute(stmt).scalars().all()


def add_manual_event(db: Session, application_id: int, data: EventCreate) -> ApplicationEvent:
    """Append a descriptive event: a note, a scheduled interview, a follow-up.

    `previous_status` and `new_status` are left NULL. The schema already refuses
    status-bearing event types, so this function cannot change an application's
    status even by accident.
    """
    event = append_event(
        db,
        application_id,
        event_type=data.event_type,
        summary=data.summary,
        body=data.body,
        occurred_at=data.occurred_at,
        scheduled_for=data.scheduled_for,
        source=EventSource.MANUAL,
    )
    db.commit()
    db.refresh(event)
    return event


def append_event(
    db: Session,
    application_id: int,
    *,
    event_type: EventType,
    summary: str,
    source: EventSource,
    body: str | None = None,
    occurred_at: datetime | None = None,
    scheduled_for: datetime | None = None,
) -> ApplicationEvent:
    """Append one descriptive event without committing — the caller owns the transaction.

    Refuses status-bearing event types outright. `EventCreate` already does so
    for the HTTP path; this is the same rule for internal callers (the email-plan
    executor), so no path can append an event that silently rewrites status.
    """
    if event_type not in MANUAL_EVENT_TYPES:
        raise ValueError(f"'{event_type.value}' events are produced only by the status service")

    application = get_application(db, application_id)

    event = ApplicationEvent(
        application_id=application.id,
        event_type=event_type.value,
        occurred_at=occurred_at or _utcnow(),
        scheduled_for=scheduled_for,
        source=source.value,
        summary=summary[:300],
        body=body,
    )
    db.add(event)
    db.flush()
    return event
