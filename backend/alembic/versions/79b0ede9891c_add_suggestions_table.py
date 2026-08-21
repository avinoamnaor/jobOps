"""add suggestions table

Revision ID: 79b0ede9891c
Revises: 1ee0f0f180c0
Create Date: 2026-08-20

Phase 5 Part 1. Purely additive: creates `suggestions`. No existing table is
altered, so this applies cleanly to a database that already holds applications,
events and documents.

The foreign key is named explicitly (autogenerate left it unnamed) so it can be
referenced reliably by any future migration — the same reason every other FK in
this project is named, per CLAUDE.md's migration invariant.

Reviewed and confirmed:
  * `application_id` uses ON DELETE CASCADE, matching `application_events` — a
    suggestion cannot outlive the application it refers to. No endpoint deletes
    an application row today (only soft-delete), so this is currently dormant,
    but it is the correct behaviour if that ever changes.
  * `proposed_status`, `source`, `confidence` and `state` are each guarded by a
    CHECK against the current enum values, consistent with every other status
    column in the project — VARCHAR + CHECK, not a native PostgreSQL ENUM, so
    the vocabulary stays easy to extend.
  * `rationale` is NOT NULL: a suggestion with no reason is not useful, and the
    review UI has nothing to show without one.
  * `resolved_at` is nullable — null while pending, set once on accept/reject.
  * downgrade() drops in reverse dependency order (indexes, then the table,
    which implicitly drops the FK with it).
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "79b0ede9891c"
down_revision: Union[str, Sequence[str], None] = "1ee0f0f180c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FK_SUGGESTIONS_APPLICATION_ID = "fk_suggestions_application_id"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "suggestions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("application_id", sa.BigInteger(), nullable=False),
        sa.Column("proposed_status", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.String(length=10), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "confidence IN ('high', 'medium', 'low')",
            name="ck_suggestions_confidence",
        ),
        sa.CheckConstraint(
            "proposed_status IN ('saved', 'applied', 'recruiter_contact', 'hr_interview', "
            "'technical_interview', 'take_home', 'final_interview', 'offer', 'accepted', "
            "'rejected', 'withdrawn', 'on_hold')",
            name="ck_suggestions_proposed_status",
        ),
        sa.CheckConstraint(
            "source IN ('manual', 'gmail', 'claude')",
            name="ck_suggestions_source",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'accepted', 'rejected')",
            name="ck_suggestions_state",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=FK_SUGGESTIONS_APPLICATION_ID,
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_suggestions_application_id", "suggestions", ["application_id"])
    op.create_index("ix_suggestions_state_created", "suggestions", ["state", "created_at"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_suggestions_state_created", table_name="suggestions")
    op.drop_index("ix_suggestions_application_id", table_name="suggestions")
    op.drop_table("suggestions")
