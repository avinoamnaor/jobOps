"""The `suggestions` table — proposals awaiting review — and their actions.

A Suggestion is advisory only: creating one never touches the Application it
refers to. Accepting one routes through `services.applications.change_status` —
the SAME function every manual status change uses — so the normal
`status_changed` timeline event is written and every existing rule (the
submitted-CV requirement, etc.) still applies. Rejecting only marks the row
rejected. A resolved suggestion (accepted or rejected) can never be processed
again.

Two shapes share this table, distinguished by `kind`:

  * `status_change` — the original shape: one proposed status for one existing
    application. `application_id`, `proposed_status` and `confidence` are set;
    nothing email-related is.
  * `email_plan` — a persisted `email_policy.SuggestionPlan`: one row per email
    (`email_message_id` is UNIQUE, which is what makes persisting a plan
    idempotent), carrying the plan's outcome, with zero or more ordered
    `SuggestionAction` rows. `application_id` is set only when the email was
    matched; `proposed_status` is never set — status changes live in actions.

`ck_suggestions_kind_shape` enforces both shapes in PostgreSQL, so a row that is
half one and half the other cannot exist whoever writes it.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.enums import (
    ApplicationChannel,
    ApplicationStatus,
    EmailMessageType,
    EventSource,
    EventType,
    ProposedActionType,
    SuggestionConfidence,
    SuggestionKind,
    SuggestionPlanOutcome,
    SuggestionSource,
    SuggestionState,
    sql_value_list,
)

if TYPE_CHECKING:
    from app.models.application import Application
    from app.models.email_message import EmailMessage


# Each kind's required shape. Kept as SQL text beside the model so the migration
# and the model state the same rule; `test_suggestion_plans` exercises both.
SUGGESTION_KIND_SHAPE_SQL = (
    "(kind = 'status_change'"
    " AND application_id IS NOT NULL"
    " AND proposed_status IS NOT NULL"
    " AND confidence IS NOT NULL"
    " AND email_message_id IS NULL"
    " AND outcome IS NULL"
    " AND message_type IS NULL)"
    " OR "
    "(kind = 'email_plan'"
    " AND email_message_id IS NOT NULL"
    " AND outcome IS NOT NULL"
    " AND message_type IS NOT NULL"
    " AND proposed_status IS NULL)"
)

# Each action type's required shape.
SUGGESTION_ACTION_SHAPE_SQL = (
    "(action_type = 'change_status'"
    " AND to_status IS NOT NULL AND event_type IS NULL AND company_name IS NULL)"
    " OR "
    "(action_type = 'record_event'"
    " AND event_type IS NOT NULL AND to_status IS NULL AND company_name IS NULL)"
    " OR "
    "(action_type = 'create_application'"
    " AND company_name IS NOT NULL AND to_status IS NULL AND event_type IS NULL"
    " AND application_id IS NULL AND NOT targets_new_application)"
)


class Suggestion(Base):
    __tablename__ = "suggestions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    kind: Mapped[str] = mapped_column(
        String(20),
        # Kept permanently: rows written before `kind` existed, and any raw
        # INSERT that omits it, are the original status-change shape.
        server_default=SuggestionKind.STATUS_CHANGE.value,
        default=SuggestionKind.STATUS_CHANGE.value,
    )

    # Required for `status_change`; for `email_plan`, set only when the email
    # was matched to an application.
    application_id: Mapped[int | None] = mapped_column(
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
        default=None,
    )

    # `status_change` only. An email plan's status change is one of its actions.
    proposed_status: Mapped[str | None] = mapped_column(String(32), default=None)
    source: Mapped[str] = mapped_column(String(20))
    # `status_change` only. The email policy deliberately produces no
    # confidence — it is deterministic — so none is invented for its plans.
    confidence: Mapped[str | None] = mapped_column(String(10), default=None)
    rationale: Mapped[str] = mapped_column(Text)

    state: Mapped[str] = mapped_column(String(20), default=SuggestionState.PENDING.value)

    # --- email_plan only ---------------------------------------------------

    # The internal email that produced this plan. UNIQUE: one plan per email,
    # so re-processing the same email can never create a second one. (PostgreSQL
    # UNIQUE ignores NULLs, so status-change rows are unaffected.)
    email_message_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "email_messages.id",
            ondelete="CASCADE",
            name="fk_suggestions_email_message_id",
        ),
        default=None,
    )
    outcome: Mapped[str | None] = mapped_column(String(20), default=None)
    message_type: Mapped[str | None] = mapped_column(String(40), default=None)
    # Review context that is shown, never filtered on: candidate application ids
    # for an ambiguous match, and the plan's field notes.
    plan_details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Set the moment a suggestion is accepted or rejected; null while pending.
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # One-directional: Application does not declare a matching `suggestions`
    # relationship, since nothing today needs to navigate that way.
    application: Mapped[Application | None] = relationship()
    email_message: Mapped[EmailMessage | None] = relationship()

    # Always in plan order. Written once with the suggestion and never edited.
    actions: Mapped[list[SuggestionAction]] = relationship(
        back_populates="suggestion",
        order_by="SuggestionAction.position",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            f"kind IN ({sql_value_list(SuggestionKind)})",
            name="ck_suggestions_kind",
        ),
        CheckConstraint(SUGGESTION_KIND_SHAPE_SQL, name="ck_suggestions_kind_shape"),
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
        CheckConstraint(
            f"outcome IN ({sql_value_list(SuggestionPlanOutcome)})",
            name="ck_suggestions_outcome",
        ),
        CheckConstraint(
            f"message_type IN ({sql_value_list(EmailMessageType)})",
            name="ck_suggestions_message_type",
        ),
        UniqueConstraint("email_message_id", name="uq_suggestions_email_message_id"),
        Index("ix_suggestions_application_id", "application_id"),
        # Supports "list pending suggestions, newest first" — the review page's
        # only real query.
        Index("ix_suggestions_state_created", "state", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<Suggestion {self.id} {self.kind} app={self.application_id} "
            f"-> {self.proposed_status or self.outcome} [{self.state}]>"
        )


class SuggestionAction(Base):
    """One proposed action of an email plan, in plan order. Nothing here has happened.

    A direct image of `email_policy.ProposedAction`. `position` preserves the
    order the plan was composed in, which matters: a plan that creates an
    application and then records an event against it (`targets_new_application`)
    only makes sense executed in that order.
    """

    __tablename__ = "suggestion_actions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    suggestion_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "suggestions.id",
            ondelete="CASCADE",
            name="fk_suggestion_actions_suggestion_id",
        ),
    )
    # 0-based order within the plan.
    position: Mapped[int] = mapped_column(Integer)

    action_type: Mapped[str] = mapped_column(String(30))
    summary: Mapped[str] = mapped_column(String(300))

    # The existing application acted on. Null for a creation, and for an action
    # that targets the application the plan's creation would produce.
    application_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "applications.id",
            ondelete="CASCADE",
            name="fk_suggestion_actions_application_id",
        ),
        default=None,
    )
    targets_new_application: Mapped[bool] = mapped_column(Boolean, default=False)

    # change_status only.
    to_status: Mapped[str | None] = mapped_column(String(32), default=None)
    # record_event only.
    event_type: Mapped[str | None] = mapped_column(String(40), default=None)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # create_application only: prefill taken from the email, never invented.
    company_name: Mapped[str | None] = mapped_column(String(200), default=None)
    role_title: Mapped[str | None] = mapped_column(String(200), default=None)
    application_channel: Mapped[str | None] = mapped_column(String(32), default=None)

    optional: Mapped[bool] = mapped_column(Boolean, default=False)
    # Provenance the executed event will carry.
    event_source: Mapped[str] = mapped_column(String(20))

    suggestion: Mapped[Suggestion] = relationship(back_populates="actions")

    __table_args__ = (
        UniqueConstraint(
            "suggestion_id", "position", name="uq_suggestion_actions_suggestion_position"
        ),
        CheckConstraint("position >= 0", name="ck_suggestion_actions_position"),
        CheckConstraint(
            f"action_type IN ({sql_value_list(ProposedActionType)})",
            name="ck_suggestion_actions_action_type",
        ),
        CheckConstraint(SUGGESTION_ACTION_SHAPE_SQL, name="ck_suggestion_actions_shape"),
        CheckConstraint(
            "NOT (targets_new_application AND application_id IS NOT NULL)",
            name="ck_suggestion_actions_single_target",
        ),
        CheckConstraint(
            f"to_status IN ({sql_value_list(ApplicationStatus)})",
            name="ck_suggestion_actions_to_status",
        ),
        CheckConstraint(
            f"event_type IN ({sql_value_list(EventType)})",
            name="ck_suggestion_actions_event_type",
        ),
        CheckConstraint(
            f"application_channel IN ({sql_value_list(ApplicationChannel)})",
            name="ck_suggestion_actions_application_channel",
        ),
        CheckConstraint(
            f"event_source IN ({sql_value_list(EventSource)})",
            name="ck_suggestion_actions_event_source",
        ),
        Index("ix_suggestion_actions_application_id", "application_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<SuggestionAction {self.id} s={self.suggestion_id}#{self.position} "
            f"{self.action_type}>"
        )
