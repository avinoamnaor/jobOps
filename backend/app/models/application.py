"""The `applications` table — the aggregate root of the whole system."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.enums import ApplicationChannel, ApplicationStatus, sql_value_list

if TYPE_CHECKING:
    from app.models.application_event import ApplicationEvent
    from app.models.document import Document


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # `*_name`/`*_title` are what you typed and what the UI shows.
    # `*_key` is the normalised form used for matching. Two columns because
    # display text and comparison text have different jobs, and deriving the key
    # on every query would be both slow and un-indexable.
    company_name: Mapped[str] = mapped_column(String(200))
    company_key: Mapped[str] = mapped_column(String(200))
    role_title: Mapped[str] = mapped_column(String(200))
    role_key: Mapped[str] = mapped_column(String(200))

    # The cached current status. This is a *projection* of the event log, not an
    # independent fact. Only `services.applications._record_status` writes it.
    status: Mapped[str] = mapped_column(String(32))

    application_channel: Mapped[str] = mapped_column(String(32))

    job_url: Mapped[str | None] = mapped_column(Text, default=None)
    job_url_canonical: Mapped[str | None] = mapped_column(Text, default=None)
    # The posting will eventually disappear from the internet. This will not.
    job_description: Mapped[str | None] = mapped_column(Text, default=None)
    location: Mapped[str | None] = mapped_column(String(200), default=None)
    work_mode: Mapped[str | None] = mapped_column(String(32), default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)

    # Nullable on purpose: a `saved` job has not been applied to, and inventing a
    # date would corrupt every "how long since I applied" question later.
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Soft delete: keeps the row (and its history) but hides it from listings.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # server_default=func.now() means PostgreSQL fills these in, so the value is
    # correct even if a row is ever inserted by something other than this app.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # The one CV actually submitted for this role. Singular by design: in
    # practice one CV goes out per application, and modelling a many-to-many
    # relationship for a case that does not happen would be complexity with no
    # payoff. If take-home files or cover letters need attaching later, that is
    # an additive change — a join table, or a document_id on the event.
    #
    # ondelete="RESTRICT": PostgreSQL refuses to delete a document that an
    # application still claims it submitted. Losing that link would destroy the
    # exact thing this feature exists to record.
    submitted_cv_document_id: Mapped[int | None] = mapped_column(
        BigInteger,
        # Named explicitly so it can be dropped by name in a downgrade. An
        # unnamed constraint gets a database-generated name that migrations
        # cannot refer to reliably.
        ForeignKey(
            "documents.id",
            ondelete="RESTRICT",
            name="fk_applications_submitted_cv_document_id",
        ),
        default=None,
    )

    events: Mapped[list[ApplicationEvent]] = relationship(
        back_populates="application",
        # If an application row is ever hard-deleted, its events go with it.
        cascade="all, delete-orphan",
        order_by="ApplicationEvent.occurred_at.desc(), ApplicationEvent.id.desc()",
    )

    submitted_cv: Mapped[Document | None] = relationship(lazy="raise_on_sql")

    __table_args__ = (
        # The database refuses an unknown status even if a bug slips past the
        # Python layer. Cheap, and it means the data can always be trusted.
        CheckConstraint(
            f"status IN ({sql_value_list(ApplicationStatus)})",
            name="ck_applications_status",
        ),
        CheckConstraint(
            f"application_channel IN ({sql_value_list(ApplicationChannel)})",
            name="ck_applications_application_channel",
        ),
        Index("ix_applications_company_role", "company_key", "role_key"),
        Index("ix_applications_status_applied_at", "status", "applied_at"),
        Index("ix_applications_job_url_canonical", "job_url_canonical"),
        # Supports the reverse lookup: "which applications did I send this CV to?"
        Index("ix_applications_submitted_cv_document_id", "submitted_cv_document_id"),
    )

    def __repr__(self) -> str:
        return f"<Application {self.id} {self.company_name} / {self.role_title} [{self.status}]>"
