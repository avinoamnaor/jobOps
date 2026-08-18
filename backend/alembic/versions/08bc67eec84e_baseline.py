"""baseline — empty starting point for the migration history.

Revision ID: 08bc67eec84e
Revises:
Create Date: 2026-08-17

This migration creates nothing on purpose. It exists so that:

  * the migration chain has a fixed root that later revisions attach to;
  * `alembic upgrade head` does observable work on a fresh database (it creates
    the `alembic_version` table and stamps this revision id into it), which
    proves the whole toolchain is wired up correctly before any real schema
    exists.

`alembic_version` is a one-row, one-column table recording which revision the
database is currently at. That row is the entire mechanism: Alembic compares it
to the migration files on disk to decide what still needs to run.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '08bc67eec84e'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
