"""Timeline event logic for events a human adds by hand.

Status-bearing events are not created here — they come from
`services.applications`, which is the only module allowed to produce them.
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import EventSource
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
    application = get_application(db, application_id)

    event = ApplicationEvent(
        application_id=application.id,
        event_type=data.event_type.value,
        occurred_at=data.occurred_at or _utcnow(),
        scheduled_for=data.scheduled_for,
        source=EventSource.MANUAL.value,
        summary=data.summary,
        body=data.body,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event
