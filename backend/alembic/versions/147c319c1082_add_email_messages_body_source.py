"""add email_messages.body_source

Revision ID: 147c319c1082
Revises: 2e5d43dfddf6
Create Date: 2026-08-23

Phase 6.2A-3. Purely additive: one column on `email_messages`. No existing
column is altered and no stored body is rewritten — this migration only appends
a field recording where each body came from.

The column exists to make a previously unanswerable question answerable. A
Gmail `snippet` and a genuinely short email are indistinguishable once stored,
so there was no safe way to identify the ~200-character previews and improve
them without risking overwriting good content. Recording the source turns that
into a decidable check, which `services.gmail` then uses to upgrade only the
rows that can actually be improved.

Existing rows read `unknown`, which ranks lowest in `BODY_SOURCE_QUALITY` — so
any source determined by a later sync counts as an improvement, and a good body
can never be replaced by a worse one. No network call is made or possible here:
determining a real source requires re-fetching the message from Gmail, which is
sync's job, not a migration's.

Reviewed and confirmed:
  * NOT NULL with a server_default, so the column is added to a table already
    holding 250 rows in one statement, with no window where it is null.
  * The server_default is kept permanently: a raw INSERT omitting the column
    gets the honest `unknown` rather than failing.
  * The CHECK constraint is named explicitly (`ck_email_messages_body_source`),
    per CLAUDE.md's migration invariant.
  * VARCHAR + CHECK rather than a native PostgreSQL ENUM, consistent with every
    other enum-backed column here.
  * downgrade() drops the constraint before the column. It loses the recorded
    sources, which is correct: they are re-derivable on a later sync, and no
    stored body is touched either way.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "147c319c1082"
down_revision: Union[str, Sequence[str], None] = "2e5d43dfddf6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CK_EMAIL_MESSAGES_BODY_SOURCE = "ck_email_messages_body_source"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "email_messages",
        sa.Column(
            "body_source",
            sa.String(length=20),
            server_default="unknown",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        CK_EMAIL_MESSAGES_BODY_SOURCE,
        "email_messages",
        "body_source IN ('plain', 'html', 'snippet', 'none', 'unknown')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(CK_EMAIL_MESSAGES_BODY_SOURCE, "email_messages", type_="check")
    op.drop_column("email_messages", "body_source")
