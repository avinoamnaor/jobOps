"""Suggestion review workflow.

A Suggestion proposes a status change but changes nothing by itself. Accepting
one calls `services.applications.change_status` — the SAME function every other
status change goes through — so a `status_changed` timeline event is written and
every existing rule (the submitted-CV requirement, etc.) still applies exactly as
it would for a manual change. Rejecting only marks the row rejected. A resolved
suggestion (accepted or rejected) can never be processed again.

This module has no idea where a suggestion came from. Gmail and Claude will later
call `create_suggestion` with `source=gmail` / `source=claude`; nothing here needs
to change for that.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import SuggestionAlreadyResolved, SuggestionNotFound
from app.enums import (
    ApplicationStatus,
    EventSource,
    SuggestionConfidence,
    SuggestionSource,
    SuggestionState,
)
from app.models.suggestion import Suggestion
from app.services.applications import change_status, get_application

# application_events.source has no "claude" value yet (Claude integration does
# not exist). Until it does, a Claude-produced suggestion's acceptance is
# attributed to SYSTEM rather than inventing a timeline source that nothing else
# recognises.
_EVENT_SOURCE_BY_SUGGESTION_SOURCE: dict[SuggestionSource, EventSource] = {
    SuggestionSource.MANUAL: EventSource.MANUAL,
    SuggestionSource.GMAIL: EventSource.GMAIL,
    SuggestionSource.CLAUDE: EventSource.SYSTEM,
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def create_suggestion(
    db: Session,
    *,
    application_id: int,
    proposed_status: ApplicationStatus,
    source: SuggestionSource,
    confidence: SuggestionConfidence,
    rationale: str,
) -> Suggestion:
    """Record a proposed status change. Writes ONLY the suggestion row.

    Confirms the application exists (raises `ApplicationNotFound` otherwise, via
    `get_application`) but never modifies it — no event, no status write. This is
    the invariant the whole feature rests on.
    """
    get_application(db, application_id)

    suggestion = Suggestion(
        application_id=application_id,
        proposed_status=proposed_status.value,
        source=source.value,
        confidence=confidence.value,
        rationale=rationale,
        state=SuggestionState.PENDING.value,
    )
    db.add(suggestion)
    db.commit()
    db.refresh(suggestion)
    return suggestion


def get_suggestion(db: Session, suggestion_id: int) -> Suggestion:
    suggestion = db.execute(
        select(Suggestion).where(Suggestion.id == suggestion_id)
    ).scalar_one_or_none()
    if suggestion is None:
        raise SuggestionNotFound(suggestion_id)
    return suggestion


def list_suggestions(db: Session, *, state: SuggestionState | None = None) -> Sequence[Suggestion]:
    """List suggestions, optionally filtered by state.

    `state=pending` covers both "list pending suggestions" and the review page;
    omitting it returns everything (useful for a future history view).
    `selectinload` avoids an N+1 when the caller reads `.application` for each
    row (the review UI needs company/role/current status alongside each one).
    """
    stmt = (
        select(Suggestion)
        .options(selectinload(Suggestion.application))
        .order_by(Suggestion.created_at.desc(), Suggestion.id.desc())
    )
    if state is not None:
        stmt = stmt.where(Suggestion.state == state.value)
    return db.execute(stmt).scalars().all()


def _require_pending(suggestion: Suggestion) -> None:
    if suggestion.state != SuggestionState.PENDING.value:
        raise SuggestionAlreadyResolved(suggestion.id, suggestion.state)


def accept_suggestion(db: Session, suggestion_id: int, *, note: str | None = None) -> Suggestion:
    """Accept: perform the real status change, then resolve the suggestion.

    These are two separate commits (`change_status` commits internally, as every
    other caller of it relies on). If the process died between them you would see
    a changed application with a still-pending suggestion — recoverable, since
    re-accepting then fails with a clear "already in that status" rather than
    silently double-applying anything. Full single-transaction atomicity would
    require changing `change_status`'s contract for every other caller, which is
    more than this MVP slice needs.
    """
    suggestion = get_suggestion(db, suggestion_id)
    _require_pending(suggestion)

    event_source = _EVENT_SOURCE_BY_SUGGESTION_SOURCE[SuggestionSource(suggestion.source)]
    change_status(
        db,
        suggestion.application_id,
        to_status=ApplicationStatus(suggestion.proposed_status),
        note=note or f"Accepted suggestion: {suggestion.rationale}",
        source=event_source,
    )

    suggestion.state = SuggestionState.ACCEPTED.value
    suggestion.resolved_at = _utcnow()
    db.commit()
    db.refresh(suggestion)
    return suggestion


def reject_suggestion(db: Session, suggestion_id: int) -> Suggestion:
    """Reject: marks the row only. The application is never touched."""
    suggestion = get_suggestion(db, suggestion_id)
    _require_pending(suggestion)

    suggestion.state = SuggestionState.REJECTED.value
    suggestion.resolved_at = _utcnow()
    db.commit()
    db.refresh(suggestion)
    return suggestion
