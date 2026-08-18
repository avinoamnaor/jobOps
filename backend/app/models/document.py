"""The `documents` table — the document library.

One row per distinct set of bytes ever uploaded. Rows are never mutated in a way
that changes what the file is: editing your CV produces a NEW document, which is
what makes "which exact CV did I submit to ProgrammaticX?" answerable a year
later.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.enums import DocumentKind, sql_value_list


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    kind: Mapped[str] = mapped_column(String(32))

    # Optional human name, because "CV.pdf" tells you nothing six months later.
    # Not unique: two documents may reasonably share a working title.
    label: Mapped[str | None] = mapped_column(String(200), default=None)

    # Kept purely as metadata, for display and for the download filename.
    # It is NEVER used to build a filesystem path — see app/core/storage.py.
    original_filename: Mapped[str | None] = mapped_column(String(255), default=None)

    # SHA-256 hex digest of the file's contents, and the file's identity.
    # UNIQUE is what makes deduplication a database guarantee rather than a
    # hopeful check in application code: even a race between two simultaneous
    # uploads of the same file cannot produce two rows.
    content_hash: Mapped[str] = mapped_column(String(64))

    # Relative to DOCUMENTS_ROOT, never absolute. Storing it relative is what
    # lets the whole data directory move to another machine — or to object
    # storage later — without rewriting a single row.
    stored_path: Mapped[str] = mapped_column(String(500))

    # Client-supplied and therefore untrusted; useful for serving downloads,
    # never used to make a security decision.
    content_type: Mapped[str | None] = mapped_column(String(150), default=None)
    size_bytes: Mapped[int] = mapped_column(BigInteger)

    notes: Mapped[str | None] = mapped_column(Text, default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Documents are archived, never deleted: an application may reference this
    # row forever. No endpoint sets this yet; list queries already respect it.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        CheckConstraint(
            f"kind IN ({sql_value_list(DocumentKind)})",
            name="ck_documents_kind",
        ),
        UniqueConstraint("content_hash", name="uq_documents_content_hash"),
    )

    def __repr__(self) -> str:
        return f"<Document {self.id} [{self.kind}] {self.original_filename}>"
