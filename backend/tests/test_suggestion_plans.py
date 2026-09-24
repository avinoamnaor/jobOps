"""Persisting email-derived suggestion plans (Phase 6.2C-1).

Plans are produced by the real `email_policy.decide`, so these tests exercise
the policy -> persistence seam as it will actually be used. Hand-built plans
appear only where the point is that persistence refuses something the policy
would never produce.

Every test also checks the negative that makes this phase safe: persisting a
plan executes none of it — no application created, no event appended, no
status changed.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import (
    ApplicationNotFound,
    EmailMessageNotFound,
    InvalidSuggestionPlan,
    OutgoingMessageNotClassifiable,
    SuggestionKindNotSupported,
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
    ProposedActionType,
    SuggestionConfidence,
    SuggestionKind,
    SuggestionPlanOutcome,
    SuggestionSource,
    SuggestionState,
)
from app.models.application import Application
from app.models.application_event import ApplicationEvent
from app.models.email_message import EmailMessage
from app.models.suggestion import Suggestion, SuggestionAction
from app.schemas.application import ApplicationCreate
from app.services.applications import create_application
from app.services.documents import store_document
from app.services.email_policy import (
    ApplicationContext,
    PolicyInput,
    ProposedAction,
    SuggestionPlan,
    decide,
)
from app.services.suggestions import (
    accept_suggestion,
    create_suggestion,
    list_suggestions,
    persist_email_plan,
    reject_suggestion,
)
from tests.conftest import requires_database

pytestmark = requires_database


# --- helpers --------------------------------------------------------------


def _email(db: Session, *, gmail_id: str = "m1", direction: str = "incoming") -> EmailMessage:
    message = EmailMessage(
        gmail_message_id=gmail_id,
        thread_id=f"thread-{gmail_id}",
        sender="Talent Team <careers@example.org>",
        subject="About your application",
        received_at=datetime(2026, 1, 1, tzinfo=UTC),
        body_text="Body",
        direction=direction,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def _saved_application(db: Session) -> Application:
    return create_application(
        db, ApplicationCreate(company_name="Harmonic", role_title="Junior SW Engineer")
    )


def _applied_application(db: Session) -> Application:
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


def _matched_plan(
    application: Application,
    message_type: EmailMessageType,
    **overrides: object,
) -> SuggestionPlan:
    params: dict[str, object] = {
        "message_type": message_type,
        "match_status": MatchStatus.MATCHED,
        "match_confidence": MatchConfidence.HIGH,
        "application": ApplicationContext(
            application_id=application.id,
            status=ApplicationStatus(application.status),
            has_submitted_cv=application.submitted_cv_document_id is not None,
        ),
        "company_name": application.company_name,
        "role_title": application.role_title,
    }
    params.update(overrides)
    return decide(PolicyInput(**params))  # type: ignore[arg-type]


def _unmatched_plan(message_type: EmailMessageType, **overrides: object) -> SuggestionPlan:
    params: dict[str, object] = {
        "message_type": message_type,
        "match_status": MatchStatus.NO_MATCH,
        "company_name": "Acme",
        "role_title": "Backend Engineer",
    }
    params.update(overrides)
    return decide(PolicyInput(**params))  # type: ignore[arg-type]


def _counts(db: Session) -> tuple[int, int, int, int]:
    """(applications, events, suggestions, actions)."""
    return (
        db.execute(select(func.count()).select_from(Application)).scalar_one(),
        db.execute(select(func.count()).select_from(ApplicationEvent)).scalar_one(),
        db.execute(select(func.count()).select_from(Suggestion)).scalar_one(),
        db.execute(select(func.count()).select_from(SuggestionAction)).scalar_one(),
    )


# --- matched applications -------------------------------------------------


class TestMatchedPlans:
    def test_multi_action_plan_is_stored_in_order_and_executes_nothing(
        self, db_session: Session
    ) -> None:
        application = _applied_application(db_session)
        message = _email(db_session)
        plan = _matched_plan(application, EmailMessageType.REJECTION)
        apps_before, events_before, _, _ = _counts(db_session)

        suggestion, created = persist_email_plan(
            db_session, email_message_id=message.id, plan=plan
        )

        assert created is True
        assert suggestion.kind == SuggestionKind.EMAIL_PLAN
        assert suggestion.email_message_id == message.id
        assert suggestion.application_id == application.id
        assert suggestion.outcome == SuggestionPlanOutcome.PROPOSE_ACTIONS
        assert suggestion.message_type == EmailMessageType.REJECTION
        assert suggestion.source == SuggestionSource.GMAIL
        assert suggestion.state == SuggestionState.PENDING
        assert suggestion.rationale == plan.reason
        # Deterministic policy: no confidence is invented for it.
        assert suggestion.confidence is None
        assert suggestion.proposed_status is None

        actions = suggestion.actions
        assert [a.position for a in actions] == [0, 1]
        assert [a.action_type for a in actions] == [
            ProposedActionType.RECORD_EVENT,
            ProposedActionType.CHANGE_STATUS,
        ]
        assert actions[0].event_type == EventType.NOTE_ADDED
        assert actions[1].to_status == ApplicationStatus.REJECTED
        assert all(a.application_id == application.id for a in actions)
        assert all(a.event_source == EventSource.GMAIL for a in actions)

        # Nothing executed.
        db_session.refresh(application)
        assert application.status == ApplicationStatus.APPLIED
        apps_after, events_after, _, _ = _counts(db_session)
        assert (apps_after, events_after) == (apps_before, events_before)

    def test_event_only_plan_keeps_the_stated_time(self, db_session: Session) -> None:
        application = _applied_application(db_session)
        message = _email(db_session)
        when = datetime(2026, 3, 2, 14, 0, tzinfo=UTC)
        plan = _matched_plan(
            application, EmailMessageType.INTERVIEW_SCHEDULED, event_datetime=when
        )

        suggestion, _ = persist_email_plan(db_session, email_message_id=message.id, plan=plan)

        assert len(suggestion.actions) == 1
        action = suggestion.actions[0]
        assert action.action_type == ProposedActionType.RECORD_EVENT
        assert action.event_type == EventType.INTERVIEW_SCHEDULED
        assert action.occurred_at == when
        assert action.to_status is None

    def test_no_cv_means_no_submitted_status_is_stored(self, db_session: Session) -> None:
        # A confirmation for a `saved` application without a CV: moving it to
        # `applied` would violate the submitted-CV rule, so only the event is
        # proposed — and nothing invents a CV to make the move possible.
        application = _saved_application(db_session)
        message = _email(db_session)
        plan = _matched_plan(application, EmailMessageType.APPLICATION_RECEIVED)

        suggestion, _ = persist_email_plan(db_session, email_message_id=message.id, plan=plan)

        assert [a.action_type for a in suggestion.actions] == [ProposedActionType.RECORD_EVENT]
        assert suggestion.plan_details is not None
        notes = suggestion.plan_details["field_notes"]
        assert any("submitted_cv_required" in note for note in notes)
        db_session.refresh(application)
        assert application.submitted_cv_document_id is None
        assert application.status == ApplicationStatus.SAVED


# --- creation proposals ---------------------------------------------------


class TestCreateApplicationPlans:
    def test_create_then_event_is_stored_without_creating_anything(
        self, db_session: Session
    ) -> None:
        message = _email(db_session)
        plan = _unmatched_plan(EmailMessageType.APPLICATION_RECEIVED)
        apps_before, events_before, _, _ = _counts(db_session)

        suggestion, created = persist_email_plan(
            db_session, email_message_id=message.id, plan=plan
        )

        assert created is True
        assert suggestion.application_id is None
        assert suggestion.outcome == SuggestionPlanOutcome.PROPOSE_ACTIONS
        create, event = suggestion.actions
        assert create.action_type == ProposedActionType.CREATE_APPLICATION
        assert create.company_name == "Acme"
        assert create.role_title == "Backend Engineer"
        # The email does not say how the application was submitted.
        assert create.application_channel is None
        assert create.application_id is None
        assert event.action_type == ProposedActionType.RECORD_EVENT
        assert event.targets_new_application is True
        assert event.application_id is None

        apps_after, events_after, _, _ = _counts(db_session)
        assert (apps_after, events_after) == (apps_before, events_before)

    def test_informational_outreach_offers_an_optional_creation(
        self, db_session: Session
    ) -> None:
        message = _email(db_session)
        plan = _unmatched_plan(EmailMessageType.RECRUITER_OUTREACH)

        suggestion, _ = persist_email_plan(db_session, email_message_id=message.id, plan=plan)

        assert suggestion.outcome == SuggestionPlanOutcome.INFORMATIONAL
        (action,) = suggestion.actions
        assert action.action_type == ProposedActionType.CREATE_APPLICATION
        assert action.optional is True
        assert action.application_channel == ApplicationChannel.RECRUITER
        assert _counts(db_session)[0] == 0


# --- actionless outcomes --------------------------------------------------


class TestActionlessPlans:
    def test_review_required_keeps_candidates_and_has_no_actions(
        self, db_session: Session
    ) -> None:
        first = _saved_application(db_session)
        second = create_application(
            db_session, ApplicationCreate(company_name="Harmonic", role_title="Data Engineer")
        )
        message = _email(db_session)
        plan = decide(
            PolicyInput(
                message_type=EmailMessageType.REJECTION,
                match_status=MatchStatus.AMBIGUOUS,
                candidate_application_ids=(first.id, second.id),
            )
        )

        suggestion, _ = persist_email_plan(db_session, email_message_id=message.id, plan=plan)

        assert suggestion.outcome == SuggestionPlanOutcome.REVIEW_REQUIRED
        assert suggestion.application_id is None
        assert suggestion.actions == []
        assert suggestion.plan_details is not None
        assert suggestion.plan_details["candidate_application_ids"] == [first.id, second.id]

    def test_no_action_outcome_is_recorded(self, db_session: Session) -> None:
        message = _email(db_session)
        plan = _unmatched_plan(EmailMessageType.JOB_ALERT)

        suggestion, created = persist_email_plan(
            db_session, email_message_id=message.id, plan=plan
        )

        assert created is True
        assert suggestion.outcome == SuggestionPlanOutcome.NO_ACTION
        assert suggestion.actions == []


# --- idempotency ----------------------------------------------------------


class TestIdempotency:
    def test_persisting_the_same_email_twice_creates_one_plan(self, db_session: Session) -> None:
        application = _applied_application(db_session)
        message = _email(db_session)
        plan = _matched_plan(application, EmailMessageType.REJECTION)

        first, first_created = persist_email_plan(
            db_session, email_message_id=message.id, plan=plan
        )
        second, second_created = persist_email_plan(
            db_session, email_message_id=message.id, plan=plan
        )

        assert (first_created, second_created) == (True, False)
        assert second.id == first.id
        _, _, suggestions, actions = _counts(db_session)
        assert (suggestions, actions) == (1, 2)

    def test_a_different_later_plan_does_not_overwrite_the_first(
        self, db_session: Session
    ) -> None:
        application = _applied_application(db_session)
        message = _email(db_session)
        persist_email_plan(
            db_session,
            email_message_id=message.id,
            plan=_matched_plan(application, EmailMessageType.REJECTION),
        )

        stored, created = persist_email_plan(
            db_session,
            email_message_id=message.id,
            plan=_unmatched_plan(EmailMessageType.JOB_ALERT),
        )

        assert created is False
        assert stored.outcome == SuggestionPlanOutcome.PROPOSE_ACTIONS
        assert stored.message_type == EmailMessageType.REJECTION
        assert len(stored.actions) == 2

    def test_a_resolved_plan_is_not_resurrected(self, db_session: Session) -> None:
        message = _email(db_session)
        plan = _unmatched_plan(EmailMessageType.RECRUITER_OUTREACH)
        suggestion, _ = persist_email_plan(db_session, email_message_id=message.id, plan=plan)
        # No dismissal flow exists yet; set the state directly to stand in for it.
        suggestion.state = SuggestionState.REJECTED.value
        db_session.commit()

        again, created = persist_email_plan(db_session, email_message_id=message.id, plan=plan)

        assert created is False
        assert again.id == suggestion.id
        assert again.state == SuggestionState.REJECTED
        assert _counts(db_session)[2] == 1

    def test_database_refuses_a_second_plan_for_one_email(self, db_session: Session) -> None:
        # The guarantee under a concurrent-insert race is the UNIQUE constraint,
        # not the lookup in front of it.
        message = _email(db_session)
        persist_email_plan(
            db_session,
            email_message_id=message.id,
            plan=_unmatched_plan(EmailMessageType.JOB_ALERT),
        )

        db_session.add(
            Suggestion(
                kind=SuggestionKind.EMAIL_PLAN.value,
                email_message_id=message.id,
                source=SuggestionSource.GMAIL.value,
                rationale="duplicate",
                outcome=SuggestionPlanOutcome.NO_ACTION.value,
                message_type=EmailMessageType.JOB_ALERT.value,
            )
        )
        with pytest.raises(IntegrityError, match="uq_suggestions_email_message_id"):
            db_session.commit()
        db_session.rollback()


# --- refusals -------------------------------------------------------------


class TestRefusals:
    def test_status_change_blocked_by_missing_cv_is_refused(self, db_session: Session) -> None:
        application = _saved_application(db_session)
        message = _email(db_session)
        plan = SuggestionPlan(
            outcome=SuggestionPlanOutcome.PROPOSE_ACTIONS,
            reason="hand-built",
            message_type=EmailMessageType.APPLICATION_RECEIVED,
            application_id=application.id,
            actions=(
                ProposedAction(
                    action_type=ProposedActionType.CHANGE_STATUS,
                    summary="Change status to 'applied'",
                    application_id=application.id,
                    to_status=ApplicationStatus.APPLIED,
                ),
            ),
        )

        with pytest.raises(InvalidSuggestionPlan, match="submitted_cv_required"):
            persist_email_plan(db_session, email_message_id=message.id, plan=plan)
        assert _counts(db_session)[2:] == (0, 0)

    def test_submitted_status_for_a_new_application_is_refused(
        self, db_session: Session
    ) -> None:
        # A created application has no CV, so it can never be proposed straight
        # into a submitted-state status.
        message = _email(db_session)
        plan = SuggestionPlan(
            outcome=SuggestionPlanOutcome.PROPOSE_ACTIONS,
            reason="hand-built",
            message_type=EmailMessageType.APPLICATION_RECEIVED,
            actions=(
                ProposedAction(
                    action_type=ProposedActionType.CREATE_APPLICATION,
                    summary="Create",
                    company_name="Acme",
                ),
                ProposedAction(
                    action_type=ProposedActionType.CHANGE_STATUS,
                    summary="Change status to 'applied'",
                    to_status=ApplicationStatus.APPLIED,
                    targets_new_application=True,
                ),
            ),
        )

        with pytest.raises(InvalidSuggestionPlan, match="submitted_cv_required"):
            persist_email_plan(db_session, email_message_id=message.id, plan=plan)

    def test_targeting_the_new_application_before_creating_it_is_refused(
        self, db_session: Session
    ) -> None:
        message = _email(db_session)
        plan = SuggestionPlan(
            outcome=SuggestionPlanOutcome.PROPOSE_ACTIONS,
            reason="hand-built",
            message_type=EmailMessageType.APPLICATION_RECEIVED,
            actions=(
                ProposedAction(
                    action_type=ProposedActionType.RECORD_EVENT,
                    summary="Application confirmed by email",
                    event_type=EventType.NOTE_ADDED,
                    targets_new_application=True,
                ),
                ProposedAction(
                    action_type=ProposedActionType.CREATE_APPLICATION,
                    summary="Create",
                    company_name="Acme",
                ),
            ),
        )

        with pytest.raises(InvalidSuggestionPlan, match="before any creation"):
            persist_email_plan(db_session, email_message_id=message.id, plan=plan)

    def test_review_required_plan_with_actions_is_refused(self, db_session: Session) -> None:
        application = _applied_application(db_session)
        message = _email(db_session)
        plan = SuggestionPlan(
            outcome=SuggestionPlanOutcome.REVIEW_REQUIRED,
            reason="hand-built",
            message_type=EmailMessageType.REJECTION,
            application_id=application.id,
            actions=(
                ProposedAction(
                    action_type=ProposedActionType.CHANGE_STATUS,
                    summary="Change status to 'rejected'",
                    application_id=application.id,
                    to_status=ApplicationStatus.REJECTED,
                ),
            ),
        )

        with pytest.raises(InvalidSuggestionPlan, match="cannot carry actions"):
            persist_email_plan(db_session, email_message_id=message.id, plan=plan)

    def test_status_bearing_event_is_refused(self, db_session: Session) -> None:
        application = _applied_application(db_session)
        message = _email(db_session)
        plan = SuggestionPlan(
            outcome=SuggestionPlanOutcome.PROPOSE_ACTIONS,
            reason="hand-built",
            message_type=EmailMessageType.REJECTION,
            application_id=application.id,
            actions=(
                ProposedAction(
                    action_type=ProposedActionType.RECORD_EVENT,
                    summary="sneaky",
                    application_id=application.id,
                    event_type=EventType.STATUS_CHANGED,
                ),
            ),
        )

        with pytest.raises(InvalidSuggestionPlan, match="status service"):
            persist_email_plan(db_session, email_message_id=message.id, plan=plan)

    def test_unknown_application_is_refused(self, db_session: Session) -> None:
        message = _email(db_session)
        plan = SuggestionPlan(
            outcome=SuggestionPlanOutcome.PROPOSE_ACTIONS,
            reason="hand-built",
            message_type=EmailMessageType.REJECTION,
            application_id=999,
        )

        with pytest.raises(ApplicationNotFound):
            persist_email_plan(db_session, email_message_id=message.id, plan=plan)

    def test_outgoing_email_is_refused(self, db_session: Session) -> None:
        message = _email(db_session, direction="outgoing")

        with pytest.raises(OutgoingMessageNotClassifiable):
            persist_email_plan(
                db_session,
                email_message_id=message.id,
                plan=_unmatched_plan(EmailMessageType.JOB_ALERT),
            )
        assert _counts(db_session)[2] == 0

    def test_unknown_email_is_refused(self, db_session: Session) -> None:
        with pytest.raises(EmailMessageNotFound):
            persist_email_plan(
                db_session,
                email_message_id=12345,
                plan=_unmatched_plan(EmailMessageType.JOB_ALERT),
            )


# --- backward compatibility -----------------------------------------------


class TestBackwardCompatibility:
    def test_status_change_suggestions_keep_their_shape(self, db_session: Session) -> None:
        application = _applied_application(db_session)

        suggestion = create_suggestion(
            db_session,
            application_id=application.id,
            proposed_status=ApplicationStatus.HR_INTERVIEW,
            source=SuggestionSource.MANUAL,
            confidence=SuggestionConfidence.HIGH,
            rationale="Recruiter called",
        )

        assert suggestion.kind == SuggestionKind.STATUS_CHANGE
        assert suggestion.email_message_id is None
        assert suggestion.actions == []
        accepted = accept_suggestion(db_session, suggestion.id)
        assert accepted.state == SuggestionState.ACCEPTED
        db_session.refresh(application)
        assert application.status == ApplicationStatus.HR_INTERVIEW

    def test_a_row_written_without_kind_is_a_status_change(self, db_session: Session) -> None:
        # What every pre-migration row looks like: the server default fills it.
        application = _applied_application(db_session)
        db_session.execute(
            text(
                "INSERT INTO suggestions "
                "(application_id, proposed_status, source, confidence, rationale, state) "
                "VALUES (:app, 'offer', 'manual', 'high', 'legacy row', 'pending')"
            ),
            {"app": application.id},
        )
        db_session.commit()

        (row,) = list_suggestions(db_session)
        assert row.kind == SuggestionKind.STATUS_CHANGE

    def test_status_change_shape_is_still_enforced_by_the_database(
        self, db_session: Session
    ) -> None:
        db_session.add(
            Suggestion(
                kind=SuggestionKind.STATUS_CHANGE.value,
                source=SuggestionSource.MANUAL.value,
                confidence=SuggestionConfidence.HIGH.value,
                rationale="no application, no status",
            )
        )
        with pytest.raises(IntegrityError, match="ck_suggestions_kind_shape"):
            db_session.commit()
        db_session.rollback()

    def test_accept_and_reject_refuse_email_plans(self, db_session: Session) -> None:
        application = _applied_application(db_session)
        message = _email(db_session)
        suggestion, _ = persist_email_plan(
            db_session,
            email_message_id=message.id,
            plan=_matched_plan(application, EmailMessageType.REJECTION),
        )

        with pytest.raises(SuggestionKindNotSupported):
            accept_suggestion(db_session, suggestion.id)
        with pytest.raises(SuggestionKindNotSupported):
            reject_suggestion(db_session, suggestion.id)

        db_session.refresh(suggestion)
        db_session.refresh(application)
        assert suggestion.state == SuggestionState.PENDING
        assert application.status == ApplicationStatus.APPLIED

    def test_api_lists_only_status_change_suggestions(
        self, client: TestClient, db_session: Session
    ) -> None:
        application = _applied_application(db_session)
        legacy = create_suggestion(
            db_session,
            application_id=application.id,
            proposed_status=ApplicationStatus.HR_INTERVIEW,
            source=SuggestionSource.MANUAL,
            confidence=SuggestionConfidence.HIGH,
            rationale="Recruiter called",
        )
        message = _email(db_session)
        plan_row, _ = persist_email_plan(
            db_session,
            email_message_id=message.id,
            plan=_unmatched_plan(EmailMessageType.RECRUITER_OUTREACH),
        )

        response = client.get("/suggestions", params={"state": "pending"})
        assert response.status_code == 200
        assert [item["id"] for item in response.json()] == [legacy.id]

        accept = client.post(f"/suggestions/{plan_row.id}/accept", json={})
        assert accept.status_code == 409
        reject = client.post(f"/suggestions/{plan_row.id}/reject")
        assert reject.status_code == 409
