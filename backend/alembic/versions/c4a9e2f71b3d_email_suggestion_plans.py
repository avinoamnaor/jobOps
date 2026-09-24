"""email suggestion plans: extend suggestions, add suggestion_actions

Revision ID: c4a9e2f71b3d
Revises: 147c319c1082
Create Date: 2026-09-24

Phase 6.2C-1. Persists `email_policy.SuggestionPlan`s by extending the existing
`suggestions` table rather than creating a parallel one. Existing rows are not
rewritten: every one of them is a status-change suggestion, which is exactly
what the new `kind` column's server_default says.

Changes to `suggestions`:
  * `kind` — NOT NULL, server_default 'status_change' (kept permanently), so
    existing rows backfill in the same statement that adds the column.
  * `email_message_id` — FK to `email_messages` (ON DELETE CASCADE: a plan is
    meaningless without the email it describes), UNIQUE so one email can only
    ever produce one plan. That constraint is the idempotency guarantee.
  * `outcome`, `message_type` — the plan's outcome and the email's type, each
    guarded by a CHECK against the current enum values.
  * `plan_details` — JSONB review context (candidate ids, field notes). Display
    only; nothing filters on it.
  * `application_id`, `proposed_status`, `confidence` become nullable, but
    `ck_suggestions_kind_shape` re-imposes NOT NULL on all three for
    `status_change` rows, so the original shape is exactly as strict as before.

New table `suggestion_actions`: one row per proposed action, ordered by
`position` (UNIQUE per suggestion), with a CHECK per action type's shape.

Reviewed and confirmed:
  * Every constraint and index is named explicitly, per CLAUDE.md.
  * Enum lists are literals frozen at this revision, as in every other
    migration here, so a later enum edit cannot silently change this one.
  * downgrade() is lossy by necessity: email-plan rows cannot satisfy the
    restored NOT NULL columns, so they (and, by cascade, their actions) are
    deleted first. Status-change rows survive untouched.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4a9e2f71b3d"
down_revision: Union[str, Sequence[str], None] = "147c319c1082"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FK_SUGGESTIONS_EMAIL_MESSAGE_ID = "fk_suggestions_email_message_id"
UQ_SUGGESTIONS_EMAIL_MESSAGE_ID = "uq_suggestions_email_message_id"
CK_SUGGESTIONS_KIND = "ck_suggestions_kind"
CK_SUGGESTIONS_KIND_SHAPE = "ck_suggestions_kind_shape"
CK_SUGGESTIONS_OUTCOME = "ck_suggestions_outcome"
CK_SUGGESTIONS_MESSAGE_TYPE = "ck_suggestions_message_type"

KIND_SHAPE_SQL = (
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

ACTION_SHAPE_SQL = (
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

APPLICATION_STATUSES = (
    "'saved', 'applied', 'recruiter_contact', 'hr_interview', 'technical_interview', "
    "'take_home', 'final_interview', 'offer', 'accepted', 'rejected', 'withdrawn', "
    "'on_hold'"
)


def upgrade() -> None:
    """Upgrade schema."""
    # --- suggestions -------------------------------------------------------
    op.add_column(
        "suggestions",
        sa.Column(
            "kind",
            sa.String(length=20),
            server_default="status_change",
            nullable=False,
        ),
    )
    op.add_column("suggestions", sa.Column("email_message_id", sa.BigInteger(), nullable=True))
    op.add_column("suggestions", sa.Column("outcome", sa.String(length=20), nullable=True))
    op.add_column("suggestions", sa.Column("message_type", sa.String(length=40), nullable=True))
    op.add_column(
        "suggestions",
        sa.Column("plan_details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    op.alter_column("suggestions", "application_id", existing_type=sa.BigInteger(), nullable=True)
    op.alter_column(
        "suggestions", "proposed_status", existing_type=sa.String(length=32), nullable=True
    )
    op.alter_column("suggestions", "confidence", existing_type=sa.String(length=10), nullable=True)

    op.create_foreign_key(
        FK_SUGGESTIONS_EMAIL_MESSAGE_ID,
        "suggestions",
        "email_messages",
        ["email_message_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        UQ_SUGGESTIONS_EMAIL_MESSAGE_ID, "suggestions", ["email_message_id"]
    )
    op.create_check_constraint(
        CK_SUGGESTIONS_KIND, "suggestions", "kind IN ('status_change', 'email_plan')"
    )
    op.create_check_constraint(CK_SUGGESTIONS_KIND_SHAPE, "suggestions", KIND_SHAPE_SQL)
    op.create_check_constraint(
        CK_SUGGESTIONS_OUTCOME,
        "suggestions",
        "outcome IN ('propose_actions', 'informational', 'review_required', 'no_action')",
    )
    op.create_check_constraint(
        CK_SUGGESTIONS_MESSAGE_TYPE,
        "suggestions",
        "message_type IN ('irrelevant', 'job_alert', 'application_received', "
        "'referral_or_recommendation', 'recruiter_outreach', 'interview_invitation', "
        "'interview_scheduled', 'assessment_requested', 'process_update', 'rejection', "
        "'offer_received', 'privacy_or_retention_notice', 'post_interview_survey', "
        "'other_recruitment')",
    )

    # --- suggestion_actions -----------------------------------------------
    op.create_table(
        "suggestion_actions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("suggestion_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("action_type", sa.String(length=30), nullable=False),
        sa.Column("summary", sa.String(length=300), nullable=False),
        sa.Column("application_id", sa.BigInteger(), nullable=True),
        sa.Column("targets_new_application", sa.Boolean(), nullable=False),
        sa.Column("to_status", sa.String(length=32), nullable=True),
        sa.Column("event_type", sa.String(length=40), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("company_name", sa.String(length=200), nullable=True),
        sa.Column("role_title", sa.String(length=200), nullable=True),
        sa.Column("application_channel", sa.String(length=32), nullable=True),
        sa.Column("optional", sa.Boolean(), nullable=False),
        sa.Column("event_source", sa.String(length=20), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_suggestion_actions_position"),
        sa.CheckConstraint(
            "action_type IN ('record_event', 'change_status', 'create_application')",
            name="ck_suggestion_actions_action_type",
        ),
        sa.CheckConstraint(ACTION_SHAPE_SQL, name="ck_suggestion_actions_shape"),
        sa.CheckConstraint(
            "NOT (targets_new_application AND application_id IS NOT NULL)",
            name="ck_suggestion_actions_single_target",
        ),
        sa.CheckConstraint(
            f"to_status IN ({APPLICATION_STATUSES})",
            name="ck_suggestion_actions_to_status",
        ),
        sa.CheckConstraint(
            "event_type IN ('created', 'status_changed', 'note_added', 'document_attached', "
            "'interview_scheduled', 'interview_completed', 'assignment_received', "
            "'assignment_submitted', 'offer_received', 'followed_up', 'imported')",
            name="ck_suggestion_actions_event_type",
        ),
        sa.CheckConstraint(
            "application_channel IN ('linkedin', 'company_site', 'recruiter', 'referral', "
            "'job_board', 'other')",
            name="ck_suggestion_actions_application_channel",
        ),
        sa.CheckConstraint(
            "event_source IN ('manual', 'extension', 'gmail', 'system')",
            name="ck_suggestion_actions_event_source",
        ),
        sa.ForeignKeyConstraint(
            ["suggestion_id"],
            ["suggestions.id"],
            name="fk_suggestion_actions_suggestion_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name="fk_suggestion_actions_application_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "suggestion_id", "position", name="uq_suggestion_actions_suggestion_position"
        ),
    )
    op.create_index(
        "ix_suggestion_actions_application_id", "suggestion_actions", ["application_id"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_suggestion_actions_application_id", table_name="suggestion_actions")
    op.drop_table("suggestion_actions")

    # Email-plan rows cannot satisfy the restored NOT NULL columns below.
    op.execute("DELETE FROM suggestions WHERE kind = 'email_plan'")

    op.drop_constraint(CK_SUGGESTIONS_MESSAGE_TYPE, "suggestions", type_="check")
    op.drop_constraint(CK_SUGGESTIONS_OUTCOME, "suggestions", type_="check")
    op.drop_constraint(CK_SUGGESTIONS_KIND_SHAPE, "suggestions", type_="check")
    op.drop_constraint(CK_SUGGESTIONS_KIND, "suggestions", type_="check")
    op.drop_constraint(UQ_SUGGESTIONS_EMAIL_MESSAGE_ID, "suggestions", type_="unique")
    op.drop_constraint(FK_SUGGESTIONS_EMAIL_MESSAGE_ID, "suggestions", type_="foreignkey")

    op.alter_column("suggestions", "confidence", existing_type=sa.String(length=10), nullable=False)
    op.alter_column(
        "suggestions", "proposed_status", existing_type=sa.String(length=32), nullable=False
    )
    op.alter_column("suggestions", "application_id", existing_type=sa.BigInteger(), nullable=False)

    op.drop_column("suggestions", "plan_details")
    op.drop_column("suggestions", "message_type")
    op.drop_column("suggestions", "outcome")
    op.drop_column("suggestions", "email_message_id")
    op.drop_column("suggestions", "kind")
