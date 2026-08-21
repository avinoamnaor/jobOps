"""Targeted tests for the Suggestion review workflow (Phase 5 Part 1).

These test the service layer directly, because the guarantees being checked —
"creating never mutates the application", "accept routes through the real
status service", "a resolved suggestion is final" — are domain guarantees, not
API behaviour.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import SubmittedCvRequired, SuggestionAlreadyResolved, SuggestionNotFound
from app.enums import (
    ApplicationStatus,
    DocumentKind,
    EventType,
    SuggestionConfidence,
    SuggestionSource,
    SuggestionState,
)
from app.models.application_event import ApplicationEvent
from app.schemas.application import ApplicationCreate
from app.services.applications import create_application
from app.services.documents import store_document
from app.services.suggestions import (
    accept_suggestion,
    create_suggestion,
    list_suggestions,
    reject_suggestion,
)
from tests.conftest import requires_database

pytestmark = requires_database


def _new_application(db: Session, **overrides: object) -> object:
    payload = {"company_name": "Harmonic", "role_title": "Junior SW Engineer"}
    payload.update(overrides)
    return create_application(db, ApplicationCreate(**payload))


def _with_cv(db: Session, application: object) -> object:
    """Attach a submitted CV as a precondition (directly, no extra event)."""
    document, _ = store_document(db, kind=DocumentKind.CV, content=b"%PDF-1.4 cv\n%%EOF")
    application.submitted_cv_document_id = document.id  # type: ignore[attr-defined]
    db.commit()
    db.refresh(application)
    return application


def _applied_application_with_cv(db: Session) -> object:
    """An application already 'applied', with a submitted CV attached.

    A submitted-state status requires a CV even at creation time (the Phase 4
    rule), so the document must exist and be passed in on creation — not
    attached afterwards.
    """
    document, _ = store_document(db, kind=DocumentKind.CV, content=b"%PDF-1.4 cv\n%%EOF")
    return create_application(
        db,
        ApplicationCreate(
            company_name="Harmonic",
            role_title="Junior SW Engineer",
            status=ApplicationStatus.APPLIED,
            submitted_cv_document_id=document.id,
        ),
    )


def _suggest(db: Session, application, **overrides: object):
    payload = {
        "application_id": application.id,
        "proposed_status": ApplicationStatus.TECHNICAL_INTERVIEW,
        "source": SuggestionSource.MANUAL,
        "confidence": SuggestionConfidence.HIGH,
        "rationale": "A future integration detected a technical-interview invitation",
    }
    payload.update(overrides)
    return create_suggestion(db, **payload)


class TestCreateSuggestionNeverMutatesTheApplication:
    def test_status_and_updated_at_are_unchanged(self, db_session: Session) -> None:
        application = _applied_application_with_cv(db_session)
        original_status = application.status
        original_updated_at = application.updated_at

        _suggest(db_session, application)

        db_session.refresh(application)
        assert application.status == original_status
        assert application.updated_at == original_updated_at

    def test_no_timeline_event_is_written(self, db_session: Session) -> None:
        application = _new_application(db_session)

        _suggest(db_session, application)

        events = db_session.execute(
            select(ApplicationEvent).where(ApplicationEvent.application_id == application.id)
        ).scalars().all()
        assert [event.event_type for event in events] == [EventType.CREATED]  # only from creation

    def test_suggestion_starts_pending(self, db_session: Session) -> None:
        application = _new_application(db_session)

        suggestion = _suggest(db_session, application)

        assert suggestion.state == SuggestionState.PENDING
        assert suggestion.resolved_at is None


class TestAcceptRoutesThroughTheRealStatusService:
    def test_accept_changes_status_and_writes_a_status_changed_event(
        self, db_session: Session
    ) -> None:
        application = _applied_application_with_cv(db_session)
        suggestion = _suggest(
            db_session, application, proposed_status=ApplicationStatus.TECHNICAL_INTERVIEW
        )

        accepted = accept_suggestion(db_session, suggestion.id)

        assert accepted.state == SuggestionState.ACCEPTED
        assert accepted.resolved_at is not None

        db_session.refresh(application)
        assert application.status == ApplicationStatus.TECHNICAL_INTERVIEW

        events = db_session.execute(
            select(ApplicationEvent)
            .where(ApplicationEvent.application_id == application.id)
            .order_by(ApplicationEvent.id)
        ).scalars().all()
        # `_applied_application_with_cv` attaches the CV at creation, which
        # writes its own document_attached event alongside `created`.
        assert [event.event_type for event in events] == [
            EventType.CREATED,
            EventType.DOCUMENT_ATTACHED,
            EventType.STATUS_CHANGED,
        ]
        status_event = events[-1]
        assert status_event.previous_status == ApplicationStatus.APPLIED
        assert status_event.new_status == ApplicationStatus.TECHNICAL_INTERVIEW
        assert suggestion.rationale in (status_event.body or "")

    def test_existing_rules_still_apply_on_accept(self, db_session: Session) -> None:
        """Accepting a suggestion that needs a CV, with none attached, must fail
        exactly like a manual status change would — the rule is not bypassed."""
        application = _new_application(db_session)  # no CV attached
        suggestion = _suggest(db_session, application, proposed_status=ApplicationStatus.APPLIED)

        with pytest.raises(SubmittedCvRequired):
            accept_suggestion(db_session, suggestion.id)

        # The failed accept must not resolve the suggestion.
        db_session.refresh(suggestion)
        assert suggestion.state == SuggestionState.PENDING


class TestRejectLeavesTheApplicationUnchanged:
    def test_reject_marks_the_suggestion_only(self, db_session: Session) -> None:
        application = _applied_application_with_cv(db_session)
        original_status = application.status
        suggestion = _suggest(db_session, application)

        rejected = reject_suggestion(db_session, suggestion.id)

        assert rejected.state == SuggestionState.REJECTED
        assert rejected.resolved_at is not None

        db_session.refresh(application)
        assert application.status == original_status

        events = db_session.execute(
            select(ApplicationEvent).where(ApplicationEvent.application_id == application.id)
        ).scalars().all()
        # `_applied_application_with_cv` attaches the CV at creation (its own
        # document_attached event); rejecting the suggestion adds nothing new.
        assert [event.event_type for event in events] == [
            EventType.CREATED,
            EventType.DOCUMENT_ATTACHED,
        ]


class TestResolvedSuggestionCannotBeProcessedTwice:
    def test_accepting_twice_is_refused(self, db_session: Session) -> None:
        application = _applied_application_with_cv(db_session)
        suggestion = _suggest(db_session, application)

        accept_suggestion(db_session, suggestion.id)

        with pytest.raises(SuggestionAlreadyResolved):
            accept_suggestion(db_session, suggestion.id)

    def test_rejecting_an_accepted_suggestion_is_refused(self, db_session: Session) -> None:
        application = _applied_application_with_cv(db_session)
        suggestion = _suggest(db_session, application)

        accept_suggestion(db_session, suggestion.id)

        with pytest.raises(SuggestionAlreadyResolved):
            reject_suggestion(db_session, suggestion.id)

    def test_rejecting_twice_is_refused(self, db_session: Session) -> None:
        application = _new_application(db_session)
        suggestion = _suggest(db_session, application)

        reject_suggestion(db_session, suggestion.id)

        with pytest.raises(SuggestionAlreadyResolved):
            reject_suggestion(db_session, suggestion.id)

    def test_unknown_suggestion_raises(self, db_session: Session) -> None:
        with pytest.raises(SuggestionNotFound):
            accept_suggestion(db_session, 999_999)
        with pytest.raises(SuggestionNotFound):
            reject_suggestion(db_session, 999_999)


class TestListSuggestions:
    def test_pending_filter_excludes_resolved_ones(self, db_session: Session) -> None:
        application = _new_application(db_session)
        pending = _suggest(db_session, application, proposed_status=ApplicationStatus.HR_INTERVIEW)
        resolved = _suggest(db_session, application, proposed_status=ApplicationStatus.REJECTED)
        reject_suggestion(db_session, resolved.id)

        results = list_suggestions(db_session, state=SuggestionState.PENDING)

        assert [item.id for item in results] == [pending.id]

    def test_no_filter_returns_everything_newest_first(self, db_session: Session) -> None:
        application = _new_application(db_session)
        first = _suggest(db_session, application, proposed_status=ApplicationStatus.HR_INTERVIEW)
        second = _suggest(db_session, application, proposed_status=ApplicationStatus.REJECTED)

        results = list_suggestions(db_session)

        assert [item.id for item in results] == [second.id, first.id]

    def test_application_relationship_is_available_for_the_review_ui(
        self, db_session: Session
    ) -> None:
        application = _new_application(db_session, company_name="Harmonic Inc.")
        _suggest(db_session, application)

        [result] = list_suggestions(db_session, state=SuggestionState.PENDING)

        assert result.application.company_name == "Harmonic Inc."
