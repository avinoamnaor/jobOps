"""API shapes for documents."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.enums import DocumentKind
from app.schemas.base import StrictModel


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: DocumentKind
    label: str | None
    original_filename: str | None
    content_hash: str
    content_type: str | None
    size_bytes: int
    notes: str | None
    created_at: datetime
    archived_at: datetime | None

    # `stored_path` is deliberately NOT exposed. It is an internal storage
    # detail, and publishing filesystem layout to clients invites them to depend
    # on it — or to probe it.


class AttachSubmittedCvRequest(StrictModel):
    document_id: int = Field(gt=0)
    note: str | None = None
    occurred_at: datetime | None = None
