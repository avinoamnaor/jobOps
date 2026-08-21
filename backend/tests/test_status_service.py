"""Tests for the status/timeline invariant — the core of Phase 1.

These test the service layer directly rather than through HTTP, because the
guarantees being checked are domain guarantees, not API behaviour.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import (
    ApplicationNotFound,
    DocumentKindNotAllowed,
    StatusUnchanged,
    SubmittedCvRequired,
)
from app.enums import ApplicationStatus, DocumentKind, EventSource, EventType
from app.models.application_event import ApplicationEvent
from app.schemas.application import ApplicationCreate
from app.services.applications import (
    change_status,
    create_application,
    find_status_inconsistencies,
    rebuild_status_from_events,
)
from app.services.documents import store_document
from tests.conftest import requires_database

pytestmark = requires_database


def _new_application(db: Session, **overrides: object) -> object:
    payload = {"company_name": "ProgrammaticX", "role_title": "Fullstack Developer"}
    payload.update(overrides)
    return create_application(db, ApplicationCreate(**payload))


def _with_cv(db: Session, application: object) -> object:
    """Attach a submitted CV as a precondition, without adding a timeline event.

    Sets the column directly (rather than via attach_submitted_cv) so tests that
    assert exact event counts are unaffected by the CV requirement.
    """
    document, _ = store_document(db, kind=DocumentKind.CV, content=b"%PDF-1.4 demo cv\n%%EOF")
    application.submitted_cv_document_id = document.id  # type: ignore[attr-defined]
    db.commit()
    db.refresh(application)
    return application


class TestCreateApplication:
    def test_opens_the_timeline_with_a_created_event(self, db_session: Session) -> None:
        application = _new_application(db_session)

        events = db_session.execute(
            select(ApplicationEvent).where(ApplicationEvent.application_id == application.id)
        ).scalars().all()

        assert len(events) == 1
        assert events[0].event_type == EventType.CREATED
        # The created event carries the status, so a replay works from entry one.
        assert events[0].new_status == ApplicationStatus.SAVED
        assert events[0].previous_status is None
        assert events[0].source == EventSource.MANUAL

    def test_derived_keys_are_computed(self, db_session: Session) -> None:
        application = _new_application(
            db_session,
            company_name="ProgrammaticX Ltd.",
            role_title="Fullstack Developer (m/f/d)",
            job_url="https://www.example.com/jobs/42/?utm_source=linkedin",
        )

        assert application.company_key == "programmaticx"
        assert application.role_key == "fullstack developer"
        assert application.job_url_canonical == "https://example.com/jobs/42"

    def test_saved_application_has_no_applied_date(self, db_session: Session) -> None:
        """A saved job has not been applied to; inventing a date would be a lie."""
        application = _new_application(db_session, status=ApplicationStatus.SAVED)
        assert application.applied_at is None

    def test_moving_to_applied_sets_applied_at(self, db_session: Session) -> None:
        # Create-as-applied is no longer permitted without a CV, so the applied
        # date is set when the application transitions to `applied` (with a CV).
        application = _new_application(db_session)
        _with_cv(db_session, application)
        change_status(db_session, application.id, to_status=ApplicationStatus.APPLIED)
        db_session.refresh(application)
        assert application.applied_at is not None


class TestChangeStatus:
    def test_updates_column_and_writes_event_together(self, db_session: Session) -> None:
        application = _new_application(db_session)
        _with_cv(db_session, application)

        change_status(db_session, application.id, to_status=ApplicationStatus.APPLIED)

        db_session.refresh(application)
        assert application.status == ApplicationStatus.APPLIED

        events = db_session.execute(
            select(ApplicationEvent)
            .where(ApplicationEvent.application_id == application.id)
            .order_by(ApplicationEvent.id)
        ).scalars().all()

        assert len(events) == 2
        status_event = events[-1]
        assert status_event.event_type == EventType.STATUS_CHANGED
        assert status_event.previous_status == ApplicationStatus.SAVED
        assert status_event.new_status == ApplicationStatus.APPLIED

    def test_both_changes_are_in_one_transaction(self, db_session: Session) -> None:
        """Roll back before committing: neither change may survive.

        This is what "same transaction" actually means. `_record_status` does not
        commit, so until the caller commits, the column update and the event
        insert are a single all-or-nothing unit.
        """
        application = _new_application(db_session)
        original_status = application.status

        from app.services.applications import _record_status

        _record_status(
            db_session,
            application,
            new_status=ApplicationStatus.OFFER,
            event_type=EventType.STATUS_CHANGED,
            source=EventSource.MANUAL,
            summary="deliberately abandoned",
            note=None,
            occurred_at=datetime.now(UTC),
        )
        db_session.rollback()

        db_session.refresh(application)
        assert application.status == original_status

        event_types = db_session.execute(
            select(ApplicationEvent.event_type).where(
                ApplicationEvent.application_id == application.id
            )
        ).scalars().all()
        assert EventType.STATUS_CHANGED not in event_types

    def test_terminal_status_sets_closed_at(self, db_session: Session) -> None:
        application = _new_application(db_session)

        change_status(db_session, application.id, to_status=ApplicationStatus.REJECTED)

        db_session.refresh(application)
        assert application.closed_at is not None

    def test_reopening_clears_closed_at(self, db_session: Session) -> None:
        """Rejected roles do get un-frozen and recruiters do come back."""
        application = _new_application(db_session)
        _with_cv(db_session, application)
        change_status(db_session, application.id, to_status=ApplicationStatus.REJECTED)
        change_status(db_session, application.id, to_status=ApplicationStatus.HR_INTERVIEW)

        db_session.refresh(application)
        assert application.closed_at is None

    def test_any_transition_is_allowed(self, db_session: Session) -> None:
        """Real processes skip stages; a strict state machine would fight reality."""
        application = _new_application(db_session)
        _with_cv(db_session, application)

        change_status(db_session, application.id, to_status=ApplicationStatus.FINAL_INTERVIEW)

        db_session.refresh(application)
        assert application.status == ApplicationStatus.FINAL_INTERVIEW

    def test_changing_to_the_same_status_is_refused(self, db_session: Session) -> None:
        application = _new_application(db_session)

        with pytest.raises(StatusUnchanged):
            change_status(db_session, application.id, to_status=ApplicationStatus.SAVED)

    def test_unknown_application_raises(self, db_session: Session) -> None:
        with pytest.raises(ApplicationNotFound):
            change_status(db_session, 999_999, to_status=ApplicationStatus.APPLIED)

    def test_note_is_stored_on_the_event(self, db_session: Session) -> None:
        application = _new_application(db_session)
        _with_cv(db_session, application)

        change_status(
            db_session,
            application.id,
            to_status=ApplicationStatus.RECRUITER_CONTACT,
            note="Recruiter called, asked about notice period",
        )

        event = db_session.execute(
            select(ApplicationEvent)
            .where(ApplicationEvent.application_id == application.id)
            .order_by(ApplicationEvent.id.desc())
            .limit(1)
        ).scalar_one()
        assert event.body == "Recruiter called, asked about notice period"

    def test_backdated_change_is_recorded_at_the_given_time(self, db_session: Session) -> None:
        application = _new_application(db_session)
        _with_cv(db_session, application)
        monday = datetime.now(UTC) - timedelta(days=2)

        change_status(
            db_session,
            application.id,
            to_status=ApplicationStatus.APPLIED,
            occurred_at=monday,
        )

        event = db_session.execute(
            select(ApplicationEvent)
            .where(ApplicationEvent.application_id == application.id)
            .order_by(ApplicationEvent.id.desc())
            .limit(1)
        ).scalar_one()
        assert event.occurred_at == monday


class TestStatusReplay:
    def test_replay_matches_stored_status_through_a_full_lifecycle(
        self, db_session: Session
    ) -> None:
        """The safety net for caching status on the application row."""
        application = _new_application(db_session)
        _with_cv(db_session, application)

        for target in [
            ApplicationStatus.APPLIED,
            ApplicationStatus.RECRUITER_CONTACT,
            ApplicationStatus.HR_INTERVIEW,
            ApplicationStatus.TECHNICAL_INTERVIEW,
            ApplicationStatus.OFFER,
            ApplicationStatus.ACCEPTED,
        ]:
            change_status(db_session, application.id, to_status=target)
            db_session.refresh(application)
            assert rebuild_status_from_events(db_session, application.id) == application.status

        assert application.status == ApplicationStatus.ACCEPTED

    def test_no_inconsistencies_across_all_applications(self, db_session: Session) -> None:
        first = _new_application(db_session)
        _with_cv(db_session, first)
        second = _new_application(db_session, company_name="Acme", role_title="Backend Engineer")

        change_status(db_session, first.id, to_status=ApplicationStatus.APPLIED)
        change_status(db_session, second.id, to_status=ApplicationStatus.REJECTED)

        assert find_status_inconsistencies(db_session) == []

    def test_inconsistency_is_detected_when_the_column_is_written_directly(
        self, db_session: Session
    ) -> None:
        """Proves the safety net actually catches the bug it exists for."""
        application = _new_application(db_session)

        # Deliberately bypass the service layer — the thing we forbid everywhere else.
        application.status = ApplicationStatus.OFFER.value
        db_session.commit()

        inconsistencies = find_status_inconsistencies(db_session)
        assert inconsistencies == [(application.id, "offer", "saved")]


class TestSubmittedCvRequired:
    """A submitted-state status requires a CV; `saved` and terminal ones do not."""

    def test_moving_to_applied_without_cv_is_refused(self, db_session: Session) -> None:
        application = _new_application(db_session)
        with pytest.raises(SubmittedCvRequired):
            change_status(db_session, application.id, to_status=ApplicationStatus.APPLIED)

    def test_moving_to_a_later_stage_without_cv_is_refused(self, db_session: Session) -> None:
        application = _new_application(db_session)
        with pytest.raises(SubmittedCvRequired):
            change_status(db_session, application.id, to_status=ApplicationStatus.HR_INTERVIEW)

    def test_refused_transition_leaves_status_unchanged(self, db_session: Session) -> None:
        application = _new_application(db_session)
        with pytest.raises(SubmittedCvRequired):
            change_status(db_session, application.id, to_status=ApplicationStatus.APPLIED)
        db_session.rollback()
        db_session.refresh(application)
        assert application.status == ApplicationStatus.SAVED

    def test_moving_to_applied_with_cv_succeeds(self, db_session: Session) -> None:
        application = _new_application(db_session)
        _with_cv(db_session, application)
        result = change_status(db_session, application.id, to_status=ApplicationStatus.APPLIED)
        assert result.status == ApplicationStatus.APPLIED

    def test_creating_as_a_submitted_status_is_refused(self, db_session: Session) -> None:
        with pytest.raises(SubmittedCvRequired):
            _new_application(db_session, status=ApplicationStatus.APPLIED)

    def test_saved_without_cv_is_allowed(self, db_session: Session) -> None:
        application = _new_application(db_session, status=ApplicationStatus.SAVED)
        assert application.status == ApplicationStatus.SAVED

    def test_terminal_status_from_saved_without_cv_is_allowed(self, db_session: Session) -> None:
        """You may withdraw or record a rejection for a role you never submitted."""
        application = _new_application(db_session)
        result = change_status(db_session, application.id, to_status=ApplicationStatus.WITHDRAWN)
        assert result.status == ApplicationStatus.WITHDRAWN


class TestCreateWithSubmittedCv:
    """Creating a real application in one step: choose Applied + supply the CV."""

    def _cv(self, db: Session) -> object:
        document, _ = store_document(db, kind=DocumentKind.CV, content=b"%PDF-1.4 cv\n%%EOF")
        return document

    def test_creating_applied_with_cv_succeeds_and_stores_the_link(
        self, db_session: Session
    ) -> None:
        cv = self._cv(db_session)
        application = _new_application(
            db_session, status=ApplicationStatus.APPLIED, submitted_cv_document_id=cv.id
        )
        assert application.status == ApplicationStatus.APPLIED
        assert application.submitted_cv_document_id == cv.id
        assert application.applied_at is not None

    def test_creating_with_cv_records_a_document_attached_event(
        self, db_session: Session
    ) -> None:
        cv = self._cv(db_session)
        application = _new_application(
            db_session, status=ApplicationStatus.APPLIED, submitted_cv_document_id=cv.id
        )
        events = db_session.execute(
            select(ApplicationEvent)
            .where(ApplicationEvent.application_id == application.id)
            .order_by(ApplicationEvent.id)
        ).scalars().all()
        assert [e.event_type for e in events] == [EventType.CREATED, EventType.DOCUMENT_ATTACHED]
        assert events[-1].document_id == cv.id

    def test_creating_with_a_non_cv_document_is_rejected(self, db_session: Session) -> None:
        letter, _ = store_document(
            db_session, kind=DocumentKind.COVER_LETTER, content=b"%PDF-1.4 letter\n%%EOF"
        )
        with pytest.raises(DocumentKindNotAllowed):
            _new_application(
                db_session,
                status=ApplicationStatus.APPLIED,
                submitted_cv_document_id=letter.id,
            )

    def test_creating_saved_with_cv_is_allowed(self, db_session: Session) -> None:
        cv = self._cv(db_session)
        application = _new_application(
            db_session, status=ApplicationStatus.SAVED, submitted_cv_document_id=cv.id
        )
        assert application.submitted_cv_document_id == cv.id
