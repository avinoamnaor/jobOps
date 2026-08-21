"""add email_messages table

Revision ID: 9b53b1296e00
Revises: 79b0ede9891c
Create Date: 2026-08-20

Phase 6.1. Purely additive: creates `email_messages`. No existing table is
altered, so this applies cleanly to a database that already holds applications,
events, documents and suggestions.

This table has no foreign key to anything — Gmail messages are imported
independently of any application, and nothing in this phase matches one to the
other. That matching is later work; this phase only authenticates, reads,
parses, deduplicates and stores.

Reviewed and confirmed:
  * `gmail_message_id` is UNIQUE (named explicitly) — re-running a sync can
    never import the same Gmail message twice; this is a database guarantee,
    not just an application-code check, the same pattern as
    `documents.content_hash`.
  * `sender`, `subject`, `body_text` hold decoded plain text, never raw MIME.
  * `received_at` is indexed — the natural "recent first" ordering for any
    later screen or query over this table.
  * downgrade() drops the index before the table (though dropping the table
    alone would take the index with it; explicit is clearer to read).
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9b53b1296e00"
down_revision: Union[str, Sequence[str], None] = "79b0ede9891c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "email_messages",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("gmail_message_id", sa.String(length=64), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("sender", sa.String(length=320), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("gmail_message_id", name="uq_email_messages_gmail_message_id"),
    )
    op.create_index("ix_email_messages_received_at", "email_messages", ["received_at"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_email_messages_received_at", table_name="email_messages")
    op.drop_table("email_messages")
