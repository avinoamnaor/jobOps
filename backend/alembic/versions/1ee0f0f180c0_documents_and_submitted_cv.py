"""documents and submitted cv

Revision ID: 1ee0f0f180c0
Revises: 33e3e4c22650
Create Date: 2026-08-17

Phase 2. Purely additive: creates `documents` and adds two nullable foreign key
columns. No existing column is altered and no data is rewritten, so this applies
cleanly to a database that already holds Phase 1 applications.

MANUAL CORRECTION AFTER AUTOGENERATE
------------------------------------
Autogenerate emitted `op.create_foreign_key(None, ...)` for both new foreign
keys, and correspondingly `op.drop_constraint(None, ...)` in downgrade().

On upgrade that works — PostgreSQL invents a name. On DOWNGRADE it is broken:
you cannot drop a constraint called `None`, so the downgrade would fail with a
confusing error at exactly the moment you are trying to undo something.

Both constraints are therefore named explicitly here (and in the models, so a
future autogenerate run compares like with like). This is precisely why
generated migrations get read line by line before being applied.

Reviewed and confirmed:
  * `documents.content_hash` is UNIQUE — deduplication is a database guarantee,
    not merely an application-code check
  * both FKs use ON DELETE RESTRICT, so a document that an application claims it
    submitted cannot be deleted out from under it
  * both new columns are NULLABLE, which is what makes this safe to apply to
    existing rows
  * downgrade() drops in reverse dependency order and now names its constraints
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1ee0f0f180c0"
down_revision: Union[str, Sequence[str], None] = "33e3e4c22650"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FK_EVENT_DOCUMENT = "fk_application_events_document_id"
FK_APPLICATION_SUBMITTED_CV = "fk_applications_submitted_cv_document_id"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "documents",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("stored_path", sa.String(length=500), nullable=False),
        sa.Column("content_type", sa.String(length=150), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('cv', 'cover_letter', 'take_home', 'portfolio', 'other')",
            name="ck_documents_kind",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_hash", name="uq_documents_content_hash"),
    )

    op.add_column("application_events", sa.Column("document_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        FK_EVENT_DOCUMENT,
        "application_events",
        "documents",
        ["document_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.add_column(
        "applications", sa.Column("submitted_cv_document_id", sa.BigInteger(), nullable=True)
    )
    op.create_index(
        "ix_applications_submitted_cv_document_id",
        "applications",
        ["submitted_cv_document_id"],
        unique=False,
    )
    op.create_foreign_key(
        FK_APPLICATION_SUBMITTED_CV,
        "applications",
        "documents",
        ["submitted_cv_document_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(FK_APPLICATION_SUBMITTED_CV, "applications", type_="foreignkey")
    op.drop_index("ix_applications_submitted_cv_document_id", table_name="applications")
    op.drop_column("applications", "submitted_cv_document_id")

    op.drop_constraint(FK_EVENT_DOCUMENT, "application_events", type_="foreignkey")
    op.drop_column("application_events", "document_id")

    op.drop_table("documents")
