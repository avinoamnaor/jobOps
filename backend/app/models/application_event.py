"""The `application_events` table — the append-only history.

This table is the source of truth. `applications.status` is derived from it.
Nothing in the application updates or deletes rows here.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.enums import EventSource, EventType, sql_value_list

if TYPE_CHECKING:
    from app.models.application import Application


class ApplicationEvent(Base):
    __tablename__ = "application_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    application_id: Mapped[int] = mapped_column(
        BigInteger,
        # ondelete="CASCADE" is enforced by PostgreSQL itself, so orphaned events
        # cannot exist even if rows are deleted outside the ORM.
        ForeignKey("applications.id", ondelete="CASCADE"),
    )

    event_type: Mapped[str] = mapped_column(String(40))

    # When the thing actually happened. Backdatable — you might log Monday's
    # recruiter call on Wednesday.
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # When a *future* thing will happen ("interview Tuesday 14:00"). This is what
    # makes "what is coming up this week?" a single cheap query instead of a
    # separate calendar feature.
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    source: Mapped[str] = mapped_column(String(20))

    # Populated only on status-bearing events. `previous_status` is what makes
    # "at which stage do I usually get rejected?" answerable.
    previous_status: Mapped[str | None] = mapped_column(String(32), default=None)
    new_status: Mapped[str | None] = mapped_column(String(32), default=None)

    # Set on document_attached events, so the timeline can link straight to the
    # exact file involved. Phase 1 deliberately left this out because the
    # documents table did not exist yet; adding it now is a purely additive
    # migration, which is the payoff for having designed it that way.
    document_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "documents.id",
            ondelete="RESTRICT",
            name="fk_application_events_document_id",
        ),
        default=None,
    )

    summary: Mapped[str] = mapped_column(String(300))
    body: Mapped[str | None] = mapped_column(Text, default=None)

    # Free-form structured data from later integrations, so that adding a Gmail
    # field does not require a migration. Rule: anything we filter or sort on
    # gets promoted to a real column.
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)

    # Distinct from occurred_at: this is when the row was written.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    application: Mapped[Application] = relationship(back_populates="events")

    __table_args__ = (
        CheckConstraint(
            f"event_type IN ({sql_value_list(EventType)})",
            name="ck_application_events_event_type",
        ),
        CheckConstraint(
            f"source IN ({sql_value_list(EventSource)})",
            name="ck_application_events_source",
        ),
        Index("ix_application_events_application_occurred", "application_id", "occurred_at"),
        # Partial index: only rows that actually have a scheduled_for are
        # indexed, which keeps it tiny while making upcoming-item queries fast.
        Index(
            "ix_application_events_scheduled_for",
            "scheduled_for",
            postgresql_where=text("scheduled_for IS NOT NULL"),
        ),
    )

    def __repr__(self) -> str:
        return f"<ApplicationEvent {self.id} app={self.application_id} {self.event_type}>"
