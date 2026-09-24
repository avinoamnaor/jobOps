"""Approving and dismissing email-plan suggestions (Phase 6.2D).

Plans come from the real policy and are stored by the real persistence, so the
executor is tested against exactly what processing produces. Approval must be
all or nothing: every failure test checks that nothing an earlier action did
survives, and that the suggestion stays pending and retryable.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import (
    EmailPlanNotExecutable,
    StatusUnchanged,
    SubmittedCvRequired,
    SuggestionAlreadyResolved,
    SuggestionApprovalInputInvalid,
)
from app.enums import (
    ApplicationChannel,
    ApplicationStatus,
    DocumentKind,
    EmailMessageType,
    EventSource,
    EventType,
    MatchConfidence,
    MatchStatus,
    SuggestionState,
)
from app.models.application import Application
from app.models.application_event import ApplicationEvent
from app.models.email_message import EmailMessage
from app.models.suggestion import Suggestion
from app.schemas.application import ApplicationCreate
from app.services import suggestions as suggestion_service
from app.services.applications import (
    change_status,
    create_application,
    find_status_inconsistencies,
)
from app.services.documents import store_document
from app.services.email_policy import ApplicationContext, PolicyInput, decide
from app.services.suggestions import (
    accept_suggestion,
    persist_email_plan,
    reject_suggestion,
)
from tests.conftest import requires_database

pytestmark = requires_database

RECEIVED_AT = datetime(2026, 1, 5, 9, 30, tzinfo=UTC)


# --- helpers --------------------------------------------------------------


def _email(db: Session, gmail_id: str = "m1") -> EmailMessage:
    message = EmailMessage(
        gmail_message_id=gmail_id,
        thread_id=f"thread-{gmail_id}",
        sender="Talent <talent@example.org>",
        subject="Your application",
        received_at=RECEIVED_AT,
        body_text="Body",
        direction="incoming",
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def _applied(db: Session) -> Application:
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


def _matched(
    db: Session, application: Application, message_type: EmailMessageType, **extra: object
) -> Suggestion:
    plan = decide(
        PolicyInput(
            message_type=message_type,
            match_status=MatchStatus.MATCHED,
            match_confidence=MatchConfidence.HIGH,
            application=ApplicationContext(
                application_id=application.id,
                status=ApplicationStatus(application.status),
                has_submitted_cv=application.submitted_cv_document_id is not None,
            ),
            company_name=application.company_name,
            role_title=application.role_title,
            **extra,  # type: ignore[arg-type]
        )
    )
    suggestion, _ = persist_email_plan(db, email_message_id=_email(db).id, plan=plan)
    return suggestion


def _unmatched(
    db: Session, message_type: EmailMessageType, *, role_title: str | None = "Backend Engineer"
) -> Suggestion:
    plan = decide(
        PolicyInput(
            message_type=message_type,
            match_status=MatchStatus.NO_MATCH,
            company_name="Acme",
            role_title=role_title,
        )
    )
    suggestion, _ = persist_email_plan(db, email_message_id=_email(db).id, plan=plan)
    return suggestion


def _gmail_events(db: Session, application_id: int) -> list[ApplicationEvent]:
    return list(
        db.execute(
            select(ApplicationEvent)
            .where(
                ApplicationEvent.application_id == application_id,
                ApplicationEvent.source == EventSource.GMAIL.value,
            )
            .order_by(ApplicationEvent.id)
        ).scalars()
    )


def _counts(db: Session) -> tuple[int, int]:
    """(applications, events)."""
    return (
        db.execute(select(func.count()).select_from(Application)).scalar_one(),
        db.execute(select(func.count()).select_from(ApplicationEvent)).scalar_one(),
    )


def _state(db: Session, suggestion: Suggestion) -> str:
    db.expire_all()
    return db.get(Suggestion, suggestion.id).state  # type: ignore[union-attr]


# --- successful approvals -------------------------------------------------


class TestApprovals:
    def test_event_only_plan(self, db_session: Session) -> None:
        application = _applied(db_session)
        interview_at = datetime(2026, 1, 12, 14, 0, tzinfo=UTC)
        suggestion = _matched(
            db_session,
            application,
            EmailMessageType.INTERVIEW_SCHEDULED,
            event_datetime=interview_at,
        )

        approved = accept_suggestion(db_session, suggestion.id)

        assert approved.state == SuggestionState.ACCEPTED
        assert approved.resolved_at is not None
        (event,) = _gmail_events(db_session, application.id)
        assert event.event_type == EventType.INTERVIEW_SCHEDULED
        assert event.summary == suggestion.actions[0].summary
        # The interview time is when it will happen; the event is when the
        # news arrived.
        assert event.scheduled_for == interview_at
        assert event.occurred_at == RECEIVED_AT
        db_session.refresh(application)
        assert application.status == ApplicationStatus.APPLIED

    def test_rejection_plan_records_event_then_status_in_order(
        self, db_session: Session
    ) -> None:
        application = _applied(db_session)
        suggestion = _matched(db_session, application, EmailMessageType.REJECTION)

        accept_suggestion(db_session, suggestion.id)

        note, status_event = _gmail_events(db_session, application.id)
        # Position order preserved: action 0 (event) before action 1 (status).
        assert note.event_type == EventType.NOTE_ADDED
        assert status_event.event_type == EventType.STATUS_CHANGED
        assert status_event.previous_status == ApplicationStatus.APPLIED
        assert status_event.new_status == ApplicationStatus.REJECTED
        db_session.refresh(application)
        assert application.status == ApplicationStatus.REJECTED
        assert application.closed_at is not None
        # The cached status still agrees with the replayed event log.
        assert find_status_inconsistencies(db_session) == []

    def test_offer_plan(self, db_session: Session) -> None:
        application = _applied(db_session)
        suggestion = _matched(db_session, application, EmailMessageType.OFFER_RECEIVED)

        accept_suggestion(db_session, suggestion.id, note="Great news")

        events = _gmail_events(db_session, application.id)
        assert [e.event_type for e in events] == [
            EventType.OFFER_RECEIVED,
            EventType.STATUS_CHANGED,
        ]
        assert events[1].body == "Great news"
        db_session.refresh(application)
        assert application.status == ApplicationStatus.OFFER

    def test_create_then_event_targets_the_new_application(self, db_session: Session) -> None:
        suggestion = _unmatched(db_session, EmailMessageType.APPLICATION_RECEIVED)

        approved = accept_suggestion(
            db_session,
            suggestion.id,
            application_channel=ApplicationChannel.COMPANY_SITE,
        )

        (application,) = db_session.execute(select(Application)).scalars()
        assert application.company_name == "Acme"
        assert application.role_title == "Backend Engineer"
        assert application.application_channel == ApplicationChannel.COMPANY_SITE
        # Created as saved: an email never tells us which CV was sent.
        assert application.status == ApplicationStatus.SAVED
        assert application.submitted_cv_document_id is None
        created, note = _gmail_events(db_session, application.id)
        assert created.event_type == EventType.CREATED
        assert note.event_type == EventType.NOTE_ADDED
        assert approved.application_id == application.id
        assert approved.state == SuggestionState.ACCEPTED

    def test_optional_outreach_creation_uses_the_stated_channel(
        self, db_session: Session
    ) -> None:
        suggestion = _unmatched(db_session, EmailMessageType.RECRUITER_OUTREACH)

        accept_suggestion(db_session, suggestion.id)

        (application,) = db_session.execute(select(Application)).scalars()
        assert application.application_channel == ApplicationChannel.RECRUITER

    def test_a_missing_role_is_supplied_at_approval(self, db_session: Session) -> None:
        suggestion = _unmatched(
            db_session, EmailMessageType.REFERRAL_OR_RECOMMENDATION, role_title=None
        )

        with pytest.raises(SuggestionApprovalInputInvalid, match="role_title"):
            accept_suggestion(db_session, suggestion.id)
        assert _counts(db_session) == (0, 0)
        assert _state(db_session, suggestion) == SuggestionState.PENDING

        accept_suggestion(db_session, suggestion.id, role_title="Platform Engineer")

        (application,) = db_session.execute(select(Application)).scalars()
        assert application.role_title == "Platform Engineer"
        assert application.application_channel == ApplicationChannel.REFERRAL


# --- approval-time re-validation and atomicity ------------------------------


class TestAtomicity:
    def test_missing_channel_creates_nothing(self, db_session: Session) -> None:
        suggestion = _unmatched(db_session, EmailMessageType.APPLICATION_RECEIVED)

        with pytest.raises(SuggestionApprovalInputInvalid, match="application_channel"):
            accept_suggestion(db_session, suggestion.id)

        assert _counts(db_session) == (0, 0)
        assert _state(db_session, suggestion) == SuggestionState.PENDING

    def test_stale_status_fails_and_rolls_back_the_earlier_event(
        self, db_session: Session
    ) -> None:
        application = _applied(db_session)
        suggestion = _matched(db_session, application, EmailMessageType.REJECTION)
        # Since planning, the user recorded the rejection by hand.
        change_status(db_session, application.id, to_status=ApplicationStatus.REJECTED)
        before = _counts(db_session)

        with pytest.raises(StatusUnchanged):
            accept_suggestion(db_session, suggestion.id)

        # Action 0 (the note event) did not survive action 1's failure.
        assert _counts(db_session) == before
        assert _gmail_events(db_session, application.id) == []
        assert _state(db_session, suggestion) == SuggestionState.PENDING

    def test_a_closed_application_is_not_reopened(self, db_session: Session) -> None:
        application = _applied(db_session)
        suggestion = _matched(db_session, application, EmailMessageType.OFFER_RECEIVED)
        change_status(db_session, application.id, to_status=ApplicationStatus.WITHDRAWN)
        before = _counts(db_session)

        with pytest.raises(EmailPlanNotExecutable, match="terminal"):
            accept_suggestion(db_session, suggestion.id)

        assert _counts(db_session) == before
        db_session.refresh(application)
        assert application.status == ApplicationStatus.WITHDRAWN
        assert _state(db_session, suggestion) == SuggestionState.PENDING

    def test_submitted_cv_rule_is_enforced_at_approval(self, db_session: Session) -> None:
        application = _applied(db_session)
        suggestion = _matched(db_session, application, EmailMessageType.OFFER_RECEIVED)
        # The CV was detached since planning (a direct edit standing in for any
        # route by which the precondition stopped holding).
        application.submitted_cv_document_id = None
        db_session.commit()
        before = _counts(db_session)

        with pytest.raises(SubmittedCvRequired):
            accept_suggestion(db_session, suggestion.id)

        assert _counts(db_session) == before
        db_session.refresh(application)
        assert application.status == ApplicationStatus.APPLIED
        assert _state(db_session, suggestion) == SuggestionState.PENDING

    def test_failure_after_creation_removes_the_created_application(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        suggestion = _unmatched(db_session, EmailMessageType.APPLICATION_RECEIVED)

        def boom(*_: object, **__: object) -> None:
            raise RuntimeError("event store unavailable")

        monkeypatch.setattr(suggestion_service, "append_event", boom)

        with pytest.raises(RuntimeError):
            accept_suggestion(
                db_session, suggestion.id, application_channel=ApplicationChannel.OTHER
            )

        # Action 0's application (and its `created` event) are gone.
        assert _counts(db_session) == (0, 0)
        assert _state(db_session, suggestion) == SuggestionState.PENDING
        db_session.expire_all()
        assert db_session.get(Suggestion, suggestion.id).application_id is None  # type: ignore[union-attr]

    def test_a_failed_approval_can_be_retried(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        application = _applied(db_session)
        suggestion = _matched(db_session, application, EmailMessageType.REJECTION)
        real_append = suggestion_service.append_event
        calls = {"n": 0}

        def fails_once(*args: object, **kwargs: object) -> object:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient")
            return real_append(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(suggestion_service, "append_event", fails_once)

        with pytest.raises(RuntimeError):
            accept_suggestion(db_session, suggestion.id)
        approved = accept_suggestion(db_session, suggestion.id)

        assert approved.state == SuggestionState.ACCEPTED
        assert len(_gmail_events(db_session, application.id)) == 2

    def test_an_actionless_plan_cannot_be_approved(self, db_session: Session) -> None:
        suggestion = _unmatched(db_session, EmailMessageType.REJECTION)  # review_required

        with pytest.raises(EmailPlanNotExecutable, match="dismiss"):
            accept_suggestion(db_session, suggestion.id)
        assert _state(db_session, suggestion) == SuggestionState.PENDING


# --- dismissal and repeated calls -----------------------------------------


class TestLifecycle:
    def test_dismissal_executes_nothing(self, db_session: Session) -> None:
        application = _applied(db_session)
        suggestion = _matched(db_session, application, EmailMessageType.REJECTION)
        before = _counts(db_session)

        dismissed = reject_suggestion(db_session, suggestion.id)

        assert dismissed.state == SuggestionState.REJECTED
        assert dismissed.resolved_at is not None
        assert _counts(db_session) == before
        db_session.refresh(application)
        assert application.status == ApplicationStatus.APPLIED

    def test_repeated_approval_is_refused_and_changes_nothing(
        self, db_session: Session
    ) -> None:
        application = _applied(db_session)
        suggestion = _matched(db_session, application, EmailMessageType.REJECTION)
        accept_suggestion(db_session, suggestion.id)
        after_first = _counts(db_session)

        with pytest.raises(SuggestionAlreadyResolved):
            accept_suggestion(db_session, suggestion.id)
        with pytest.raises(SuggestionAlreadyResolved):
            reject_suggestion(db_session, suggestion.id)

        assert _counts(db_session) == after_first
        assert _state(db_session, suggestion) == SuggestionState.ACCEPTED

    def test_a_dismissed_plan_cannot_be_approved(self, db_session: Session) -> None:
        application = _applied(db_session)
        suggestion = _matched(db_session, application, EmailMessageType.REJECTION)
        reject_suggestion(db_session, suggestion.id)

        with pytest.raises(SuggestionAlreadyResolved):
            accept_suggestion(db_session, suggestion.id)
        with pytest.raises(SuggestionAlreadyResolved):
            reject_suggestion(db_session, suggestion.id)
        db_session.refresh(application)
        assert application.status == ApplicationStatus.APPLIED

    def test_no_provider_or_gmail_call_is_possible(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.services.email_classifier as classifier_module
        import app.services.gmail as gmail_module

        def forbidden(*_: object, **__: object) -> None:
            raise AssertionError("approval must not reach a provider or Gmail")

        monkeypatch.setattr(classifier_module, "build_completer", forbidden)
        monkeypatch.setattr(gmail_module.GmailClient, "from_stored_credentials", forbidden)
        application = _applied(db_session)
        suggestion = _matched(db_session, application, EmailMessageType.OFFER_RECEIVED)

        assert accept_suggestion(db_session, suggestion.id).state == SuggestionState.ACCEPTED


# --- HTTP contract --------------------------------------------------------


class TestApi:
    def test_accept_returns_the_plan_shape(self, client: TestClient, db_session: Session) -> None:
        suggestion = _unmatched(db_session, EmailMessageType.APPLICATION_RECEIVED)

        response = client.post(
            f"/suggestions/{suggestion.id}/accept",
            json={"application_channel": "linkedin"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "accepted"
        assert body["kind"] == "email_plan"
        assert body["application_id"] is not None
        assert [a["action_type"] for a in body["actions"]] == [
            "create_application",
            "record_event",
        ]

    def test_missing_input_is_422_and_repeat_is_409(
        self, client: TestClient, db_session: Session
    ) -> None:
        suggestion = _unmatched(db_session, EmailMessageType.APPLICATION_RECEIVED)

        missing = client.post(f"/suggestions/{suggestion.id}/accept", json={})
        assert missing.status_code == 422
        assert "application_channel" in missing.json()["detail"]

        ok = client.post(
            f"/suggestions/{suggestion.id}/accept", json={"application_channel": "other"}
        )
        assert ok.status_code == 200
        again = client.post(
            f"/suggestions/{suggestion.id}/accept", json={"application_channel": "other"}
        )
        assert again.status_code == 409

    def test_stale_plan_is_409(self, client: TestClient, db_session: Session) -> None:
        application = _applied(db_session)
        suggestion = _matched(db_session, application, EmailMessageType.OFFER_RECEIVED)
        change_status(db_session, application.id, to_status=ApplicationStatus.WITHDRAWN)

        response = client.post(f"/suggestions/{suggestion.id}/accept", json={})

        assert response.status_code == 409
        assert _state(db_session, suggestion) == SuggestionState.PENDING

    def test_dismiss_returns_the_plan_shape(
        self, client: TestClient, db_session: Session
    ) -> None:
        suggestion = _unmatched(db_session, EmailMessageType.RECRUITER_OUTREACH)

        response = client.post(f"/suggestions/{suggestion.id}/reject")

        assert response.status_code == 200
        assert response.json()["state"] == "rejected"
        assert _counts(db_session) == (0, 0)
