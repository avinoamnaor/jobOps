"""API shapes for suggestions."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.enums import (
    ApplicationChannel,
    ApplicationStatus,
    EmailMessageType,
    EventSource,
    EventType,
    ProposedActionType,
    SuggestionConfidence,
    SuggestionKind,
    SuggestionPlanOutcome,
    SuggestionSource,
    SuggestionState,
)
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
    """Accept a suggestion.

    `role_title` and `application_channel` are only for an email plan that
    creates an application, and are required there only when the email did not
    state them — JobOps never invents either. Sending them for any other
    suggestion is refused (422) rather than silently ignored.
    """

    note: str | None = None
    role_title: str | None = Field(default=None, min_length=1, max_length=200)
    application_channel: ApplicationChannel | None = None


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


class SuggestionActionRead(BaseModel):
    """One ordered action of an email plan."""

    model_config = ConfigDict(from_attributes=True)

    position: int
    action_type: ProposedActionType
    summary: str
    application_id: int | None
    targets_new_application: bool
    to_status: ApplicationStatus | None
    event_type: EventType | None
    occurred_at: datetime | None
    company_name: str | None
    role_title: str | None
    application_channel: ApplicationChannel | None
    optional: bool
    event_source: EventSource


class EmailPlanRead(BaseModel):
    """An email-derived suggestion: the plan and its ordered actions.

    `application_id` is the matched application, or — once a plan that creates
    one is approved — the application it created. Null for review-only plans.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: SuggestionKind
    email_message_id: int
    application_id: int | None
    outcome: SuggestionPlanOutcome
    message_type: EmailMessageType
    source: SuggestionSource
    rationale: str
    state: SuggestionState
    plan_details: dict[str, Any] | None
    actions: list[SuggestionActionRead]
    created_at: datetime
    resolved_at: datetime | None
