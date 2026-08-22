"""The `email_messages` table — Gmail messages imported for later matching.

Phase 6.1 scope: this table is filled by a read-only sync, and nothing reads
from it yet. No row here has ever changed an Application, created a Suggestion,
or been seen by an LLM — later phases build that on top of what this one stores.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.enums import EmailDirection, sql_value_list


class EmailMessage(Base):
    __tablename__ = "email_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # The dedupe key: re-running sync must never import the same Gmail message
    # twice. UNIQUE makes that a database guarantee, not just an application-code
    # check (the same pattern as `documents.content_hash`).
    gmail_message_id: Mapped[str] = mapped_column(String(64))
    thread_id: Mapped[str] = mapped_column(String(64))

    # Raw header value ("Jane Doe <jane@example.com>") — kept as-is; later
    # phases can parse it further if they need the bare address.
    sender: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str | None] = mapped_column(String(500), default=None)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # Decoded plain-text body, or Gmail's own snippet as a fallback — never the
    # raw MIME message, which nothing in this project has a use for.
    body_text: Mapped[str | None] = mapped_column(Text, default=None)

    # Received or sent, read from Gmail's own `labelIds` (SENT/INBOX) — never
    # guessed from the sender address, which would mean hardcoding a personal
    # address. Rows imported before this column existed are `unknown`.
    #
    # Stored rather than derived because the raw `labelIds` are not kept: this
    # is the one bit of that metadata later phases need, and re-fetching a
    # message from Gmail just to learn its direction would make an offline
    # question depend on the network.
    direction: Mapped[str] = mapped_column(
        String(20),
        server_default=EmailDirection.UNKNOWN.value,
        default=EmailDirection.UNKNOWN.value,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("gmail_message_id", name="uq_email_messages_gmail_message_id"),
        CheckConstraint(
            f"direction IN ({sql_value_list(EmailDirection)})",
            name="ck_email_messages_direction",
        ),
        Index("ix_email_messages_received_at", "received_at"),
    )

    def __repr__(self) -> str:
        return f"<EmailMessage {self.id} gmail={self.gmail_message_id} subject={self.subject!r}>"
