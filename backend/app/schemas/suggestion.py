"""API shapes for suggestions."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.enums import ApplicationStatus, SuggestionConfidence, SuggestionSource, SuggestionState
from app.schemas.base import StrictModel


class SuggestionCreate(StrictModel):
    """Propose a status change for an application.

    This is the same primitive later Gmail/Claude integrations will call (with
    `source=gmail` / `source=claude`); `source=manual` (the default) is what
    makes suggestions creatable and testable today, before either exists.
    Creating one writes only this row — it never touches the application.
    """

    application_id: int = Field(gt=0)
    proposed_status: ApplicationStatus
    source: SuggestionSource = SuggestionSource.MANUAL
    confidence: SuggestionConfidence
    rationale: str = Field(min_length=1, max_length=2000)


class SuggestionAcceptRequest(StrictModel):
    note: str | None = None


class SuggestionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    application_id: int
    proposed_status: ApplicationStatus
    source: SuggestionSource
    confidence: SuggestionConfidence
    rationale: str
    state: SuggestionState
    created_at: datetime
    resolved_at: datetime | None


class SuggestionWithApplication(SuggestionRead):
    """A suggestion plus just enough application context for the review UI."""

    company_name: str
    role_title: str
    current_status: ApplicationStatus
