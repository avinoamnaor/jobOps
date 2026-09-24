"""The email processing pipeline (Phase 6.2C-2).

`FakeClassifier` implements the `EmailClassifier` protocol, so everything real
runs — the direction guard, the sanitiser, grounding, the matcher, the policy,
and persistence — with no provider, no key and no network.

Besides the happy paths, these pin down what must stay impossible: sending the
user's own mail, sending unsanitised text, paying twice for one email, leaving
a half-written suggestion, and executing anything that was proposed.
"""

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import EmailBatchTooLarge, EmailClassificationFailed, InvalidSuggestionPlan
from app.enums import (
    ApplicationStatus,
    ClassificationConfidence,
    DocumentKind,
    EmailMessageType,
    ProposedActionType,
    SuggestionKind,
    SuggestionPlanOutcome,
    SuggestionState,
)
from app.models.application import Application
from app.models.application_event import ApplicationEvent
from app.models.email_message import EmailMessage
from app.models.suggestion import Suggestion, SuggestionAction
from app.schemas.application import ApplicationCreate
from app.schemas.classification import EmailClassification
from app.services import email_processing
from app.services.applications import create_application
from app.services.documents import store_document
from app.services.email_classifier import ClassificationInput
from app.services.email_processing import (
    MAX_MESSAGES_PER_BATCH,
    ProcessingStatus,
    process_email_message,
    process_email_messages,
)
from app.services.suggestions import get_email_plan
from tests.conftest import requires_database

pytestmark = requires_database

BODY = (
    "Thank you for applying for the Junior SW Engineer role at Harmonic.\n\n"
    "After careful consideration, we have decided to move forward with other "
    "candidates.\n\n"
    "Questions? Write to recruiter.person@example.org or call +972-50-123-4567."
)
EVIDENCE = "we have decided to move forward with other candidates"


class FakeClassifier:
    """Records what it was handed and returns a configurable verdict."""

    def __init__(
        self,
        *,
        message_type: EmailMessageType = EmailMessageType.REJECTION,
        company_name: str | None = "Harmonic",
        role_title: str | None = "Junior SW Engineer",
        evidence: list[str] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.calls: list[ClassificationInput] = []
        self.message_type = message_type
        self.company_name = company_name
        self.role_title = role_title
        self.evidence = [EVIDENCE] if evidence is None else evidence
        self.raises = raises
        self.last_usage = type("U", (), {"input_tokens": 1200, "output_tokens": 50})()

    def classify(self, message: ClassificationInput) -> EmailClassification:
        self.calls.append(message)
        if self.raises is not None:
            raise self.raises
        return EmailClassification(
            message_type=self.message_type,
            company_name=self.company_name,
            role_title=self.role_title,
            confidence=ClassificationConfidence.HIGH,
            evidence=self.evidence,
        )


def _email(
    db: Session,
    *,
    gmail_id: str = "m1",
    direction: str = "incoming",
    body: str = BODY,
) -> EmailMessage:
    message = EmailMessage(
        gmail_message_id=gmail_id,
        thread_id=f"thread-{gmail_id}",
        sender="Talent Team <talent.team@harmonic.example>",
        subject="Your application",
        received_at=datetime(2026, 1, 1, tzinfo=UTC),
        body_text=body,
        direction=direction,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def _applied_application(db: Session, role_title: str = "Junior SW Engineer") -> Application:
    document, _ = store_document(
        db, kind=DocumentKind.CV, content=f"%PDF-1.4 {role_title}\n%%EOF".encode()
    )
    return create_application(
        db,
        ApplicationCreate(
            company_name="Harmonic",
            role_title=role_title,
            status=ApplicationStatus.APPLIED,
            submitted_cv_document_id=document.id,
        ),
    )


def _counts(db: Session) -> tuple[int, int, int, int]:
    """(applications, events, suggestions, actions)."""
    return (
        db.execute(select(func.count()).select_from(Application)).scalar_one(),
        db.execute(select(func.count()).select_from(ApplicationEvent)).scalar_one(),
        db.execute(select(func.count()).select_from(Suggestion)).scalar_one(),
        db.execute(select(func.count()).select_from(SuggestionAction)).scalar_one(),
    )


# --- one email --------------------------------------------------------------


class TestMatchedEmail:
    def test_persists_a_pending_plan_and_executes_nothing(self, db_session: Session) -> None:
        application = _applied_application(db_session)
        message = _email(db_session)
        apps_before, events_before, _, _ = _counts(db_session)

        outcome = process_email_message(db_session, FakeClassifier(), message.id)

        assert outcome.status is ProcessingStatus.CREATED
        assert outcome.classifier_called is True
        assert outcome.plan_outcome == SuggestionPlanOutcome.PROPOSE_ACTIONS
        assert outcome.action_count == 2

        suggestion = get_email_plan(db_session, message.id)
        assert suggestion is not None
        assert suggestion.id == outcome.suggestion_id
        assert suggestion.kind == SuggestionKind.EMAIL_PLAN
        assert suggestion.application_id == application.id
        assert suggestion.state == SuggestionState.PENDING
        assert [a.action_type for a in suggestion.actions] == [
            ProposedActionType.RECORD_EVENT,
            ProposedActionType.CHANGE_STATUS,
        ]

        # Proposed, not executed.
        db_session.refresh(application)
        assert application.status == ApplicationStatus.APPLIED
        apps_after, events_after, _, _ = _counts(db_session)
        assert (apps_after, events_after) == (apps_before, events_before)


class TestUnmatchedAndAmbiguous:
    def test_ambiguous_match_is_persisted_as_review_required(self, db_session: Session) -> None:
        first = _applied_application(db_session, "Junior SW Engineer")
        second = _applied_application(db_session, "Data Engineer")
        message = _email(db_session)
        # No role extracted: the company alone matches two applications, and the
        # matcher must not pick one.
        classifier = FakeClassifier(role_title=None)

        outcome = process_email_message(db_session, classifier, message.id)

        assert outcome.status is ProcessingStatus.CREATED
        suggestion = get_email_plan(db_session, message.id)
        assert suggestion is not None
        assert suggestion.outcome == SuggestionPlanOutcome.REVIEW_REQUIRED
        assert suggestion.application_id is None
        assert suggestion.actions == []
        assert suggestion.plan_details is not None
        assert set(suggestion.plan_details["candidate_application_ids"]) == {
            first.id,
            second.id,
        }

    def test_no_match_rejection_is_review_only(self, db_session: Session) -> None:
        message = _email(db_session)
        classifier = FakeClassifier(company_name="Unknown Corp")

        outcome = process_email_message(db_session, classifier, message.id)

        assert outcome.status is ProcessingStatus.CREATED
        assert outcome.plan_outcome == SuggestionPlanOutcome.REVIEW_REQUIRED
        assert outcome.action_count == 0

    def test_create_application_is_proposed_not_executed(self, db_session: Session) -> None:
        message = _email(
            db_session,
            body="We have received your application for Backend Engineer at Acme.",
        )
        classifier = FakeClassifier(
            message_type=EmailMessageType.APPLICATION_RECEIVED,
            company_name="Acme",
            role_title="Backend Engineer",
            evidence=["We have received your application"],
        )

        outcome = process_email_message(db_session, classifier, message.id)

        assert outcome.status is ProcessingStatus.CREATED
        suggestion = get_email_plan(db_session, message.id)
        assert suggestion is not None
        assert [a.action_type for a in suggestion.actions] == [
            ProposedActionType.CREATE_APPLICATION,
            ProposedActionType.RECORD_EVENT,
        ]
        assert suggestion.actions[0].company_name == "Acme"
        assert suggestion.actions[1].targets_new_application is True
        # Nothing was created.
        applications, events, _, _ = _counts(db_session)
        assert (applications, events) == (0, 0)

    def test_no_action_type_is_persisted_consistently(self, db_session: Session) -> None:
        message = _email(db_session, body="New jobs matching your alert.")
        classifier = FakeClassifier(
            message_type=EmailMessageType.JOB_ALERT,
            evidence=["New jobs matching your alert"],
        )

        outcome = process_email_message(db_session, classifier, message.id)

        assert outcome.status is ProcessingStatus.CREATED
        assert outcome.plan_outcome == SuggestionPlanOutcome.NO_ACTION
        assert outcome.action_count == 0


class TestEligibilityAndPrivacy:
    def test_outgoing_is_skipped_before_the_classifier(self, db_session: Session) -> None:
        message = _email(db_session, direction="outgoing")
        classifier = FakeClassifier()

        outcome = process_email_message(db_session, classifier, message.id)

        assert outcome.status is ProcessingStatus.SKIPPED
        assert outcome.classifier_called is False
        assert classifier.calls == []
        assert _counts(db_session)[2] == 0

    def test_unknown_direction_is_processed(self, db_session: Session) -> None:
        _applied_application(db_session)
        message = _email(db_session, direction="unknown")

        outcome = process_email_message(db_session, FakeClassifier(), message.id)

        assert outcome.status is ProcessingStatus.CREATED

    def test_the_classifier_only_sees_sanitised_text(self, db_session: Session) -> None:
        message = _email(db_session)
        classifier = FakeClassifier()

        process_email_message(db_session, classifier, message.id)

        (sent,) = classifier.calls
        assert "recruiter.person@example.org" not in (sent.body_text or "")
        assert "123-4567" not in (sent.body_text or "")
        assert "talent.team@harmonic.example" not in sent.sender
        # The meaning survives sanitisation.
        assert EVIDENCE in (sent.body_text or "")

    def test_the_stored_email_is_not_modified(self, db_session: Session) -> None:
        message = _email(db_session)

        process_email_message(db_session, FakeClassifier(), message.id)

        db_session.refresh(message)
        assert message.body_text == BODY

    def test_unknown_id_is_reported(self, db_session: Session) -> None:
        classifier = FakeClassifier()

        outcome = process_email_message(db_session, classifier, 999999)

        assert outcome.status is ProcessingStatus.NOT_FOUND
        assert classifier.calls == []


class TestIdempotency:
    def test_an_already_processed_email_is_not_sent_again(self, db_session: Session) -> None:
        _applied_application(db_session)
        message = _email(db_session)
        classifier = FakeClassifier()

        first = process_email_message(db_session, classifier, message.id)
        second = process_email_message(db_session, classifier, message.id)

        assert first.status is ProcessingStatus.CREATED
        assert second.status is ProcessingStatus.ALREADY_PROCESSED
        assert second.suggestion_id == first.suggestion_id
        assert second.classifier_called is False
        assert len(classifier.calls) == 1
        _, _, suggestions, actions = _counts(db_session)
        assert (suggestions, actions) == (1, 2)


class TestFailuresLeaveNothing:
    def test_classifier_failure_writes_nothing(self, db_session: Session) -> None:
        message = _email(db_session)
        classifier = FakeClassifier(raises=EmailClassificationFailed("provider timeout"))

        outcome = process_email_message(db_session, classifier, message.id)

        assert outcome.status is ProcessingStatus.FAILED
        assert outcome.detail is not None and "timeout" in outcome.detail
        assert _counts(db_session)[2:] == (0, 0)

    def test_a_failed_email_can_be_retried(self, db_session: Session) -> None:
        _applied_application(db_session)
        message = _email(db_session)
        process_email_message(
            db_session, FakeClassifier(raises=EmailClassificationFailed("x")), message.id
        )

        retry = process_email_message(db_session, FakeClassifier(), message.id)

        assert retry.status is ProcessingStatus.CREATED

    def test_ungrounded_evidence_is_not_persisted(self, db_session: Session) -> None:
        # A classifier that does not enforce grounding itself still cannot get
        # an invented quote into a stored suggestion.
        message = _email(db_session)
        classifier = FakeClassifier(evidence=["we would love to offer you the job"])

        outcome = process_email_message(db_session, classifier, message.id)

        assert outcome.status is ProcessingStatus.FAILED
        assert "does not appear" in (outcome.detail or "")
        assert _counts(db_session)[2:] == (0, 0)

    def test_persistence_refusal_writes_nothing(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        message = _email(db_session)

        def refuse(*_: object, **__: object) -> None:
            raise InvalidSuggestionPlan("stale snapshot")

        monkeypatch.setattr(email_processing, "persist_email_plan", refuse)

        outcome = process_email_message(db_session, FakeClassifier(), message.id)

        assert outcome.status is ProcessingStatus.FAILED
        assert "stale snapshot" in (outcome.detail or "")
        assert _counts(db_session)[2:] == (0, 0)


# --- batches ----------------------------------------------------------------


class TestBatch:
    def test_a_failure_does_not_stop_the_batch(self, db_session: Session) -> None:
        _applied_application(db_session)
        first = _email(db_session, gmail_id="a")
        outgoing = _email(db_session, gmail_id="b", direction="outgoing")
        third = _email(db_session, gmail_id="c")

        class FailsFirst(FakeClassifier):
            def classify(self, message: ClassificationInput) -> EmailClassification:
                if not self.calls:
                    self.calls.append(message)
                    raise EmailClassificationFailed("transient")
                return super().classify(message)

        outcomes = process_email_messages(
            db_session, FailsFirst(), [first.id, outgoing.id, 999999, third.id]
        )

        assert [o.status for o in outcomes] == [
            ProcessingStatus.FAILED,
            ProcessingStatus.SKIPPED,
            ProcessingStatus.NOT_FOUND,
            ProcessingStatus.CREATED,
        ]
        assert get_email_plan(db_session, first.id) is None
        assert get_email_plan(db_session, third.id) is not None

    def test_duplicate_ids_are_processed_once(self, db_session: Session) -> None:
        _applied_application(db_session)
        message = _email(db_session)
        classifier = FakeClassifier()

        outcomes = process_email_messages(db_session, classifier, [message.id, message.id])

        assert len(outcomes) == 1
        assert len(classifier.calls) == 1

    def test_an_oversized_batch_is_refused_before_anything_runs(
        self, db_session: Session
    ) -> None:
        classifier = FakeClassifier()

        with pytest.raises(EmailBatchTooLarge):
            process_email_messages(
                db_session, classifier, list(range(1, MAX_MESSAGES_PER_BATCH + 2))
            )
        assert classifier.calls == []

    def test_an_empty_batch_does_nothing(self, db_session: Session) -> None:
        classifier = FakeClassifier()
        assert process_email_messages(db_session, classifier, []) == []
        assert classifier.calls == []

    def test_an_unexpected_error_stops_the_batch(self, db_session: Session) -> None:
        # A bug is not a per-email failure: it propagates rather than being
        # repeated across every remaining email.
        first = _email(db_session, gmail_id="a")
        second = _email(db_session, gmail_id="b")
        classifier = FakeClassifier(raises=RuntimeError("bug"))

        with pytest.raises(RuntimeError):
            process_email_messages(db_session, classifier, [first.id, second.id])
        assert len(classifier.calls) == 1
        assert _counts(db_session)[2] == 0


# --- CLI --------------------------------------------------------------------


def _load_cli():
    path = Path(__file__).resolve().parents[1] / "scripts" / "process_email_messages.py"
    spec = importlib.util.spec_from_file_location("process_email_messages", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestCli:
    def test_processes_named_ids_and_never_prints_the_body(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _applied_application(db_session)
        message = _email(db_session)
        cli = _load_cli()
        fake = FakeClassifier()
        # Test database session, fake provider, fake key: nothing real is touched.
        monkeypatch.setattr(cli, "SessionLocal", lambda: db_session)
        monkeypatch.setattr(cli, "configured_api_key", lambda _provider: "test-key")
        monkeypatch.setattr(
            cli.StructuredEmailClassifier, "from_settings", classmethod(lambda _cls, _p: fake)
        )
        monkeypatch.setattr(sys, "argv", ["process", "--ids", str(message.id)])

        cli.main()

        printed = capsys.readouterr().out
        assert "created" in printed
        assert "actions executed   0" in printed
        assert EVIDENCE not in printed
        assert "recruiter.person@example.org" not in printed
        assert get_email_plan(db_session, message.id) is not None

    def test_refuses_more_than_the_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cli = _load_cli()
        ids = [str(i) for i in range(1, MAX_MESSAGES_PER_BATCH + 2)]
        monkeypatch.setattr(sys, "argv", ["process", "--ids", *ids])

        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1

    def test_refuses_to_run_without_a_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cli = _load_cli()
        monkeypatch.setattr(cli, "configured_api_key", lambda _provider: None)
        monkeypatch.setattr(sys, "argv", ["process", "--ids", "1"])

        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1
