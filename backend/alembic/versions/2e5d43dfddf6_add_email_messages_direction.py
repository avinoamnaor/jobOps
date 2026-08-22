"""add email_messages.direction

Revision ID: 2e5d43dfddf6
Revises: 9b53b1296e00
Create Date: 2026-08-21

Phase 6.2A-0. Purely additive: one column on `email_messages`. No existing
column is altered, dropped or rewritten, and no row's imported Gmail data is
touched — this migration only appends a field that did not exist before.

`direction` records whether a message was received or sent, read at import time
from Gmail's own `labelIds` (SENT/INBOX). It is deliberately NOT derived from
the sender address: that would mean hardcoding a personal address into the
codebase and would still be wrong for aliases and plus-addressing.

This migration makes no network call and cannot: it has no Gmail credentials, no
client, and nothing here reads a message body. Rows imported before this column
existed therefore stay `unknown` — an honest "not determined" rather than a
guess made from data the migration does not have. Later syncs populate the
column correctly, and re-syncing does not backfill old rows because dedupe by
`gmail_message_id` skips them; that is acceptable, since `unknown` is a valid
member of the enum and later phases treat it as classifiable.

Reviewed and confirmed:
  * NOT NULL with a server_default, so the column can be added to a table that
    already holds imported rows in a single statement — no nullable-then-
    backfill-then-alter dance, and no window where the column is null.
  * The server_default is kept permanently rather than dropped after backfill.
    It costs nothing, and it means a raw INSERT that omits `direction` gets the
    honest `unknown` instead of failing.
  * The CHECK constraint is named explicitly (`ck_email_messages_direction`),
    per CLAUDE.md's migration invariant — autogenerate emits unnamed constraints
    that then cannot be dropped reliably in downgrade().
  * VARCHAR + CHECK rather than a native PostgreSQL ENUM, consistent with every
    other enum-backed column in this project: this vocabulary should stay easy
    to extend with an ordinary migration.
  * downgrade() drops the constraint before the column, the reverse of upgrade.
    It loses the recorded directions, which is correct for a downgrade — the
    data is re-derivable on a later sync, and nothing else references it.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2e5d43dfddf6"
down_revision: Union[str, Sequence[str], None] = "9b53b1296e00"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CK_EMAIL_MESSAGES_DIRECTION = "ck_email_messages_direction"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "email_messages",
        sa.Column(
            "direction",
            sa.String(length=20),
            server_default="unknown",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        CK_EMAIL_MESSAGES_DIRECTION,
        "email_messages",
        "direction IN ('incoming', 'outgoing', 'unknown')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(CK_EMAIL_MESSAGES_DIRECTION, "email_messages", type_="check")
    op.drop_column("email_messages", "direction")
