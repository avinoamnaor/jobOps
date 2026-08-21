"""API shapes for applications."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.enums import ApplicationChannel, ApplicationStatus
from app.schemas.base import StrictModel
from app.schemas.document import DocumentRead
from app.schemas.event import EventRead


class ApplicationCreate(StrictModel):
    company_name: str = Field(min_length=1, max_length=200)
    role_title: str = Field(min_length=1, max_length=200)

    status: ApplicationStatus = ApplicationStatus.SAVED
    application_channel: ApplicationChannel = ApplicationChannel.OTHER

    job_url: str | None = None
    job_description: str | None = None
    location: str | None = Field(default=None, max_length=200)
    work_mode: str | None = Field(default=None, max_length=32)
    notes: str | None = None
    applied_at: datetime | None = None
    # Optional: record the CV submitted, so an application can be created as
    # `applied` in one step. Must reference a document with kind == 'cv'.
    submitted_cv_document_id: int | None = Field(default=None, gt=0)


class ApplicationUpdate(StrictModel):
    """Everything that may be edited freely.

    `status` is deliberately absent. Allowing it here would let a PATCH change
    the status without writing a timeline event, silently breaking the guarantee
    that the event log explains every status the application has ever held.

    Because this is a `StrictModel`, sending `status` returns 422 rather than
    quietly ignoring it. Status changes go through
    POST /applications/{id}/status, and the error message says so.
    """

    company_name: str | None = Field(default=None, min_length=1, max_length=200)
    role_title: str | None = Field(default=None, min_length=1, max_length=200)
    application_channel: ApplicationChannel | None = None
    job_url: str | None = None
    job_description: str | None = None
    location: str | None = Field(default=None, max_length=200)
    work_mode: str | None = Field(default=None, max_length=32)
    notes: str | None = None
    applied_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_status_field(cls, data: Any) -> Any:
        """Give `status` a better error than the generic "extra field" one.

        `extra="forbid"` alone would already reject it, but with a message that
        does not say what to do instead. `status` is the field most likely to be
        sent here by mistake, so it earns a pointer to the right endpoint.
        """
        if isinstance(data, dict) and "status" in data:
            raise ValueError(
                "'status' cannot be changed through this endpoint. "
                "Use POST /applications/{id}/status, which records the change "
                "on the application timeline."
            )
        return data


class StatusChangeRequest(StrictModel):
    to: ApplicationStatus
    note: str | None = None
    # Lets you record on Wednesday that the status really changed on Monday.
    occurred_at: datetime | None = None


class ApplicationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_name: str
    company_key: str
    role_title: str
    role_key: str
    status: ApplicationStatus
    application_channel: ApplicationChannel
    job_url: str | None
    job_url_canonical: str | None
    job_description: str | None
    location: str | None
    work_mode: str | None
    notes: str | None
    submitted_cv_document_id: int | None
    applied_at: datetime | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApplicationDetail(ApplicationRead):
    """A single application together with its full timeline."""

    events: list[EventRead]
    # The full document, so the UI can show "CV: Fullstack v3 (PDF, 84 KB)"
    # without a second request.
    submitted_cv: DocumentRead | None


class ApplicationFolderExport(BaseModel):
    """Result of preparing an application's local export folder."""

    folder: str
    path: str


class DraftFolderRequest(StrictModel):
    """Prepare a submission folder before an application row exists."""

    company_name: str = Field(min_length=1, max_length=200)
    role_title: str = Field(min_length=1, max_length=200)
    document_id: int = Field(gt=0)


class DuplicateCheckRequest(StrictModel):
    """Candidate job data to check against existing applications (advisory)."""

    company_name: str = Field(min_length=1, max_length=200)
    role_title: str = Field(min_length=1, max_length=200)
    job_url: str | None = None
    job_description: str | None = None


class DuplicateMatch(BaseModel):
    """One possibly-duplicate existing application."""

    application_id: int
    company_name: str
    role_title: str
    status: ApplicationStatus
    applied_at: datetime | None
    confidence: str
    reason: str


class ApplicationPage(BaseModel):
    """One page of results.

    `total` is the number of rows matching the filters, not the page size, so the
    UI can render "showing 1-25 of 137".
    """

    items: list[ApplicationRead]
    total: int
    page: int
    page_size: int
