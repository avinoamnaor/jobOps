"""HTTP layer for suggestions.

Every function here does the same three things: read the request, call one
service function, return the result. Every response carries just enough
application context (company, role, current status) for the review UI to render
without a second request per row.
"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.enums import SuggestionKind, SuggestionState
from app.models.suggestion import Suggestion
from app.schemas.suggestion import (
    EmailPlanRead,
    SuggestionAcceptRequest,
    SuggestionCreate,
    SuggestionRead,
    SuggestionWithApplication,
)
from app.services import suggestions as suggestion_service

router = APIRouter(prefix="/suggestions", tags=["suggestions"])


def _read(suggestion: Suggestion) -> SuggestionWithApplication | EmailPlanRead:
    """Each kind in its own shape: status changes exactly as before, plans with actions."""
    if suggestion.kind == SuggestionKind.EMAIL_PLAN.value:
        return EmailPlanRead.model_validate(suggestion)
    return _with_application(suggestion)


def _with_application(suggestion: Suggestion) -> SuggestionWithApplication:
    return SuggestionWithApplication(
        **SuggestionRead.model_validate(suggestion).model_dump(),
        company_name=suggestion.application.company_name,
        role_title=suggestion.application.role_title,
        current_status=suggestion.application.status,
    )


@router.post("", response_model=SuggestionWithApplication, status_code=status.HTTP_201_CREATED)
def create_suggestion(payload: SuggestionCreate, db: Session = Depends(get_db)) -> object:
    """Propose a status change. Writes only the suggestion row.

    The primitive later Gmail/Claude integrations call; `source=manual` (the
    default) is what makes this usable and testable before either exists.
    """
    suggestion = suggestion_service.create_suggestion(
        db,
        application_id=payload.application_id,
        proposed_status=payload.proposed_status,
        source=payload.source,
        confidence=payload.confidence,
        rationale=payload.rationale,
    )
    return _with_application(suggestion)


@router.get("", response_model=list[SuggestionWithApplication])
def list_suggestions(
    state: SuggestionState | None = Query(default=None),
    db: Session = Depends(get_db),
) -> object:
    """List suggestions, optionally filtered by state.

    `?state=pending` serves both the nav badge count and the review page —
    omitting the filter returns every suggestion, for a future history view.
    """
    # Status-change suggestions only: this response shape (and the review UI
    # built on it) assumes one application and one proposed status per row,
    # which email-derived plans do not have.
    suggestions = suggestion_service.list_suggestions(
        db, state=state, kind=SuggestionKind.STATUS_CHANGE
    )
    return [_with_application(item) for item in suggestions]


@router.get("/email-plans", response_model=list[EmailPlanRead])
def list_email_plans(
    state: SuggestionState | None = Query(default=None),
    db: Session = Depends(get_db),
) -> object:
    """Email-derived suggestions with their ordered actions. Reads only."""
    return [
        EmailPlanRead.model_validate(item)
        for item in suggestion_service.list_email_plans(db, state=state)
    ]


@router.post(
    "/{suggestion_id}/accept",
    response_model=SuggestionWithApplication | EmailPlanRead,
)
def accept_suggestion(
    suggestion_id: int,
    payload: SuggestionAcceptRequest,
    db: Session = Depends(get_db),
) -> object:
    """Accept a suggestion (see services/suggestions.py).

    A status-change suggestion routes through the real status-change service, as
    before. An email plan executes all its actions in order in one transaction —
    all or nothing; any failure leaves it pending and changes nothing. Either
    way, an accept that would break a rule fails instead of resolving.
    """
    suggestion = suggestion_service.accept_suggestion(
        db,
        suggestion_id,
        note=payload.note,
        role_title=payload.role_title,
        application_channel=payload.application_channel,
    )
    return _read(suggestion)


@router.post(
    "/{suggestion_id}/reject",
    response_model=SuggestionWithApplication | EmailPlanRead,
)
def reject_suggestion(suggestion_id: int, db: Session = Depends(get_db)) -> object:
    """Reject/dismiss: marks the suggestion only. Nothing is executed."""
    suggestion = suggestion_service.reject_suggestion(db, suggestion_id)
    return _read(suggestion)
