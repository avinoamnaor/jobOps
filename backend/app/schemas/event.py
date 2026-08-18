"""API shapes for timeline events.

Kept separate from the ORM models on purpose. The ORM describes how data is
*stored*; these describe how it is *sent and received*. Keeping them apart means
we can change one without the other, and it lets us refuse input the database
would technically accept — see `EventCreate` below.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.enums import MANUAL_EVENT_TYPES, EventType
from app.schemas.base import StrictModel


class EventCreate(StrictModel):
    """A manually added timeline event.

    Note what is *absent*: no `previous_status`, no `new_status`, no `source`.
    Those are decided by the service layer. Because this inherits `StrictModel`,
    sending them is a 422 rather than a silent no-op — a hand-written event
    cannot rewrite an application's status, and a caller who tries is told so.
    """

    event_type: EventType
    summary: str = Field(min_length=1, max_length=300)
    body: str | None = None
    occurred_at: datetime | None = None
    scheduled_for: datetime | None = None

    @field_validator("event_type")
    @classmethod
    def reject_status_bearing_types(cls, value: EventType) -> EventType:
        if value not in MANUAL_EVENT_TYPES:
            allowed = ", ".join(sorted(MANUAL_EVENT_TYPES))
            raise ValueError(
                f"'{value}' is produced by the system, not by hand. "
                f"Use POST /applications/{{id}}/status to change status. Allowed: {allowed}"
            )
        return value


class EventRead(BaseModel):
    # from_attributes lets Pydantic build this straight from an ORM object
    # instead of requiring a dict.
    model_config = ConfigDict(from_attributes=True)

    id: int
    application_id: int
    event_type: EventType
    occurred_at: datetime
    scheduled_for: datetime | None
    source: str
    previous_status: str | None
    new_status: str | None
    # Set on document_attached events, so the timeline can link to the file.
    document_id: int | None
    summary: str
    body: str | None
    payload: dict[str, Any] | None
    created_at: datetime
