"""The `suggestions` table — proposed status changes awaiting review.

A Suggestion is advisory only: creating one never touches the Application it
refers to. Accepting one routes through `services.applications.change_status` —
the SAME function every manual status change uses — so the normal
`status_changed` timeline event is written and every existing rule (the
submitted-CV requirement, etc.) still applies. Rejecting only marks the row
rejected. A resolved suggestion (accepted or rejected) can never be processed
again.

This table is where later Gmail/Claude integrations will write; nothing here
assumes a particular producer, which is what "ready for Gmail/Claude later"
means in practice.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.enums import (
    ApplicationStatus,
    SuggestionConfidence,
    SuggestionSource,
    SuggestionState,
    sql_value_list,
)

if TYPE_CHECKING:
    from app.models.application import Application


class Suggestion(Base):
    __tablename__ = "suggestions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    application_id: Mapped[int] = mapped_column(
        BigInteger,
        # No application-deletion endpoint exists today (only soft-delete), so
        # CASCADE is dormant in practice — but it is the correct behaviour if a
        # row is ever hard-deleted, matching application_events. Named
        # explicitly so a future migration can reference it reliably.
        ForeignKey(
            "applications.id",
            ondelete="CASCADE",
            name="fk_suggestions_application_id",
        ),
    )

    proposed_status: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[str] = mapped_column(String(10))
    rationale: Mapped[str] = mapped_column(Text)

    state: Mapped[str] = mapped_column(String(20), default=SuggestionState.PENDING.value)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Set the moment a suggestion is accepted or rejected; null while pending.
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # One-directional: Application does not declare a matching `suggestions`
    # relationship, since nothing today needs to navigate that way.
    application: Mapped[Application] = relationship()

    __table_args__ = (
        CheckConstraint(
            f"proposed_status IN ({sql_value_list(ApplicationStatus)})",
            name="ck_suggestions_proposed_status",
        ),
        CheckConstraint(
            f"source IN ({sql_value_list(SuggestionSource)})",
            name="ck_suggestions_source",
        ),
        CheckConstraint(
            f"confidence IN ({sql_value_list(SuggestionConfidence)})",
            name="ck_suggestions_confidence",
        ),
        CheckConstraint(
            f"state IN ({sql_value_list(SuggestionState)})",
            name="ck_suggestions_state",
        ),
        Index("ix_suggestions_application_id", "application_id"),
        # Supports "list pending suggestions, newest first" — the review page's
        # only real query.
        Index("ix_suggestions_state_created", "state", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<Suggestion {self.id} app={self.application_id} "
            f"-> {self.proposed_status} [{self.state}]>"
        )
