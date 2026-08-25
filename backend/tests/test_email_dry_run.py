"""Boundary tests for the read-only real-message dry run (Phase 6.2A-2).

`FakeClassifier` implements the `EmailClassifier` protocol, so the real
`dry_run_classify` logic runs — id selection, the outgoing guard, sanitisation,
grounding — with no provider, no key, and no network.

The point of most of these is not that the happy path works. It is that the
things this tool must NEVER do stay impossible: reading messages nobody asked
for, sending a message the user wrote, sending unsanitised text, printing a
body, or writing anything at all.
"""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.errors import EmailClassificationFailed
from app.enums import ClassificationConfidence, EmailMessageType
from app.models.application import Application
from app.models.application_event import ApplicationEvent
from app.models.email_message import EmailMessage
from app.models.suggestion import Suggestion
from app.schemas.classification import EmailClassification
from app.services.email_classifier import ClassificationInput
from app.services.email_dry_run import dry_run_classify
from tests.conftest import requires_database

pytestmark = requires_database

# A body carrying every category the sanitiser removes, plus the wording that
# must survive it.
RAW_BODY = (
    "Thank you for applying for the Backend Engineer role at Brightpath Systems.\n\n"
    "After careful consideration, we have decided to move forward with other "
    "candidates.\n\n"
    "Questions? Write to recruiter.person@example.org or call +972-50-123-4567.\n"
    "https://brightpath.example.com/apply?token=aB3xY9zQ7mN2pL5kJ8hG4dF6s&utm_source=ats"
)


def _load_cli():
    """Import the CLI script by path — scripts/ is not an installed package."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "classify_email_messages.py"
    spec = importlib.util.spec_from_file_location("classify_email_messages", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _store(
    db: Session,
    *,
    gmail_message_id: str = "m1",
    direction: str = "incoming",
    sender: str = "Talent Team <careers@example.org>",
    subject: str = "Thank you for your interest",
    body_text: str = RAW_BODY,
) -> EmailMessage:
    message = EmailMessage(
        gmail_message_id=gmail_message_id,
        thread_id=f"thread-{gmail_message_id}",
        sender=sender,
        subject=subject,
        received_at=datetime(2026, 1, 1, tzinfo=UTC),
        body_text=body_text,
        direction=direction,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


class FakeClassifier:
    """Records exactly what it was handed, and returns a fixed verdict."""

    def __init__(self, evidence: list[str] | None = None, raises: Exception | None = None):
        self.calls: list[ClassificationInput] = []
        self._raises = raises
        self._evidence = (
            ["we have decided to move forward with other candidates"]
            if evidence is None
            else evidence
        )
        self.last_usage = type("U", (), {"input_tokens": 1500, "output_tokens": 60})()

    def classify(self, message: ClassificationInput) -> EmailClassification:
        self.calls.append(message)
        if self._raises is not None:
            raise self._raises
        return EmailClassification(
            message_type=EmailMessageType.REJECTION,
            company_name="Brightpath Systems",
            role_title="Backend Engineer",
            confidence=ClassificationConfidence.HIGH,
            evidence=self._evidence,
        )


class TestOnlySelectedMessagesAreRead:
    def test_only_the_named_ids_are_classified(self, db_session: Session) -> None:
        wanted = _store(db_session, gmail_message_id="wanted")
        _store(db_session, gmail_message_id="other-1")
        _store(db_session, gmail_message_id="other-2")

        fake = FakeClassifier()
        outcomes = dry_run_classify(db_session, fake, [wanted.id])

        assert len(outcomes) == 1
        assert outcomes[0].message_id == wanted.id
        # The other two rows exist and were never sent anywhere.
        assert len(fake.calls) == 1

    def test_there_is_no_classify_everything_path(self, db_session: Session) -> None:
        _store(db_session, gmail_message_id="a")
        _store(db_session, gmail_message_id="b")

        fake = FakeClassifier()
        assert dry_run_classify(db_session, fake, []) == []
        assert fake.calls == []

    def test_a_missing_id_is_reported_not_raised(self, db_session: Session) -> None:
        fake = FakeClassifier()
        outcomes = dry_run_classify(db_session, fake, [999999])

        assert len(outcomes) == 1
        assert outcomes[0].found is False
        assert outcomes[0].classification is None
        assert fake.calls == []

    def test_a_missing_id_does_not_stop_the_others(self, db_session: Session) -> None:
        message = _store(db_session)
        fake = FakeClassifier()

        outcomes = dry_run_classify(db_session, fake, [999999, message.id])

        assert [o.found for o in outcomes] == [False, True]
        assert outcomes[1].classified


class TestOutgoingIsSkippedBeforeAnything:
    def test_an_outgoing_message_is_never_sent(self, db_session: Session) -> None:
        message = _store(db_session, direction="outgoing")
        fake = FakeClassifier()

        outcomes = dry_run_classify(db_session, fake, [message.id])

        assert outcomes[0].skipped
        assert "outgoing" in outcomes[0].skipped_reason
        assert fake.calls == []

    def test_the_skip_happens_before_sanitisation(self, db_session: Session) -> None:
        """Nothing is even read into a request.

        The outcome carries no sender, subject or counts, which is the
        observable consequence of skipping first: there was nothing to sanitise
        because the message never entered the pipeline.
        """
        message = _store(db_session, direction="outgoing")

        outcome = dry_run_classify(db_session, FakeClassifier(), [message.id])[0]

        assert outcome.sender is None
        assert outcome.subject is None
        assert outcome.counts is None

    def test_incoming_and_unknown_are_classified(self, db_session: Session) -> None:
        incoming = _store(db_session, gmail_message_id="in", direction="incoming")
        unknown = _store(db_session, gmail_message_id="unk", direction="unknown")

        fake = FakeClassifier()
        outcomes = dry_run_classify(db_session, fake, [incoming.id, unknown.id])

        assert all(o.classified for o in outcomes)
        assert len(fake.calls) == 2


class TestTheProviderOnlySeesSanitisedContent:
    def test_the_classifier_receives_sanitised_text(self, db_session: Session) -> None:
        message = _store(db_session)
        fake = FakeClassifier()

        dry_run_classify(db_session, fake, [message.id])

        sent = fake.calls[0]
        assert "recruiter.person@example.org" not in sent.body_text
        assert "+972-50-123-4567" not in sent.body_text
        assert "utm_source" not in sent.body_text
        assert "aB3xY9zQ7mN2pL5kJ8hG4dF6s" not in sent.body_text

    def test_the_original_body_is_never_sent(self, db_session: Session) -> None:
        message = _store(db_session)
        fake = FakeClassifier()

        dry_run_classify(db_session, fake, [message.id])

        assert fake.calls[0].body_text != RAW_BODY

    def test_semantic_content_survives_into_the_request(self, db_session: Session) -> None:
        # Sanitising into meaninglessness would defeat the purpose of sending.
        message = _store(db_session)
        fake = FakeClassifier()

        dry_run_classify(db_session, fake, [message.id])

        sent = fake.calls[0].body_text
        assert "Backend Engineer" in sent
        assert "Brightpath Systems" in sent
        assert "we have decided to move forward with other candidates" in sent

    def test_the_sender_address_is_sanitised(self, db_session: Session) -> None:
        message = _store(db_session)
        fake = FakeClassifier()

        dry_run_classify(db_session, fake, [message.id])

        assert "careers@example.org" not in fake.calls[0].sender

    def test_only_the_intended_classifier_fields_are_passed(
        self, db_session: Session
    ) -> None:
        message = _store(db_session)
        fake = FakeClassifier()

        dry_run_classify(db_session, fake, [message.id])

        sent = fake.calls[0]
        assert isinstance(sent, ClassificationInput)
        # No JobOps id, no Gmail id, no thread id — the input type has no field
        # for any of them.
        assert set(type(sent).__dataclass_fields__) == {
            "sender",
            "subject",
            "body_text",
            "received_at",
            "direction",
        }
        rendered = f"{sent.sender} {sent.subject} {sent.body_text}"
        assert str(message.id) not in rendered
        assert message.gmail_message_id not in rendered
        assert message.thread_id not in rendered

    def test_the_stored_row_is_not_modified(self, db_session: Session) -> None:
        message = _store(db_session)
        original = (message.sender, message.subject, message.body_text)

        dry_run_classify(db_session, FakeClassifier(), [message.id])

        db_session.refresh(message)
        assert (message.sender, message.subject, message.body_text) == original


class TestGroundingUsesTheSanitisedSource:
    def test_evidence_quoted_from_sanitised_text_is_grounded(
        self, db_session: Session
    ) -> None:
        message = _store(db_session)
        fake = FakeClassifier(
            evidence=["we have decided to move forward with other candidates"]
        )

        outcome = dry_run_classify(db_session, fake, [message.id])[0]

        assert outcome.grounded is True

    def test_evidence_quoting_redacted_text_is_not_grounded(
        self, db_session: Session
    ) -> None:
        """The check must reflect what the model actually saw.

        The address is in the ORIGINAL body, so verifying against that would
        pass — but the model was shown `[EMAIL]` and could not have read it.
        Grounding has to fail, or "source-grounded" would be a claim about a
        source the model never received.
        """
        message = _store(db_session)
        # Bypasses the classifier's own grounding check so the dry run's
        # independent verification is what is being tested.
        fake = FakeClassifier(evidence=["recruiter.person@example.org"])

        outcome = dry_run_classify(db_session, fake, [message.id])[0]

        assert outcome.grounded is False
        assert "recruiter.person@example.org" in message.body_text


class TestFailuresAreReportedWithoutWriting:
    def test_a_classifier_error_is_captured(self, db_session: Session) -> None:
        message = _store(db_session)
        fake = FakeClassifier(raises=EmailClassificationFailed("provider timeout"))

        outcome = dry_run_classify(db_session, fake, [message.id])[0]

        assert outcome.error is not None
        assert "timeout" in outcome.error
        assert outcome.classification is None

    def test_an_error_does_not_stop_later_messages(self, db_session: Session) -> None:
        first = _store(db_session, gmail_message_id="a")
        second = _store(db_session, gmail_message_id="b")

        class FailsOnce(FakeClassifier):
            def classify(self, message: ClassificationInput) -> EmailClassification:
                if not self.calls:
                    self.calls.append(message)
                    raise EmailClassificationFailed("transient")
                return super().classify(message)

        outcomes = dry_run_classify(db_session, FailsOnce(), [first.id, second.id])

        assert outcomes[0].error is not None
        assert outcomes[1].classified


class TestNothingIsPersisted:
    def test_no_rows_are_created_anywhere(self, db_session: Session) -> None:
        message = _store(db_session)
        before = db_session.execute(select(EmailMessage)).scalars().all()

        dry_run_classify(db_session, FakeClassifier(), [message.id])

        assert len(db_session.execute(select(EmailMessage)).scalars().all()) == len(before)
        assert db_session.execute(select(Application)).scalars().all() == []
        assert db_session.execute(select(ApplicationEvent)).scalars().all() == []
        assert db_session.execute(select(Suggestion)).scalars().all() == []

    def test_no_classification_table_exists_yet(self, db_session: Session) -> None:
        """This phase deliberately stores nothing.

        Persistence is the next design decision, and a table appearing before
        that decision is made would be the wrong kind of momentum.
        """
        rows = db_session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name LIKE '%classification%'"
            )
        ).all()
        assert rows == []

    def test_the_email_message_model_has_no_classification_columns(self) -> None:
        columns = set(EmailMessage.__table__.c.keys())
        for forbidden in ("message_type", "classification", "confidence", "classified_at"):
            assert forbidden not in columns


class TestPrintingDoesNotLeak:
    def test_the_outcome_object_carries_no_original_body(self, db_session: Session) -> None:
        """Structural, not behavioural: there is no field to leak.

        A printing mistake cannot expose the body if the body never reaches the
        object being printed.
        """
        message = _store(db_session)
        outcome = dry_run_classify(db_session, FakeClassifier(), [message.id])[0]

        assert not hasattr(outcome, "body_text")
        assert not hasattr(outcome, "raw_body")

    def test_the_cli_never_prints_the_body(self, db_session: Session, capsys) -> None:
        """Bodies stay unprinted; short evidence excerpts are printed on purpose.

        The distinction is the whole design: an excerpt is a bounded quotation
        the contract already caps in size and count, and it is what makes a
        verdict checkable. Asserting on body text that is deliberately NOT
        quoted as evidence is what actually tests the boundary.
        """
        message = _store(db_session)
        outcome = dry_run_classify(db_session, FakeClassifier(), [message.id])[0]

        _load_cli().print_outcome(outcome)

        printed = capsys.readouterr().out
        assert "Thank you for applying for the Backend Engineer role" not in printed
        assert "Questions? Write to" not in printed
        assert "After careful consideration" not in printed
        # The evidence excerpt IS printed — that is required output.
        assert "we have decided to move forward with other candidates" in printed

    def test_the_cli_never_prints_redacted_values(
        self, db_session: Session, capsys
    ) -> None:
        message = _store(db_session)
        outcome = dry_run_classify(db_session, FakeClassifier(), [message.id])[0]

        _load_cli().print_outcome(outcome)

        printed = capsys.readouterr().out
        assert "recruiter.person@example.org" not in printed
        assert "972-50-123-4567" not in printed
        assert "aB3xY9zQ7mN2pL5kJ8hG4dF6s" not in printed
        assert "utm_source" not in printed

    def test_the_cli_prints_the_privacy_counts(self, db_session: Session, capsys) -> None:
        message = _store(db_session)
        outcome = dry_run_classify(db_session, FakeClassifier(), [message.id])[0]

        _load_cli().print_outcome(outcome)

        printed = capsys.readouterr().out
        assert "sanitized:" in printed
        assert "emails:" in printed
        assert "urls_cleaned:" in printed

    def test_counts_are_zero_when_nothing_was_removed(
        self, db_session: Session, capsys
    ) -> None:
        message = _store(
            db_session,
            sender="Recruiter",
            subject="Interview invitation",
            body_text="Are you available next week for a short call?",
        )
        outcome = dry_run_classify(
            db_session, FakeClassifier(evidence=["short call"]), [message.id]
        )[0]

        _load_cli().print_outcome(outcome)

        printed = capsys.readouterr().out
        assert "emails: 0" in printed
        assert "phones: 0" in printed


class TestTheCliRefusesOversizedRuns:
    def test_there_is_a_cap_on_ids_per_run(self) -> None:
        # Not a security boundary, but a typo should fail loudly rather than
        # send hundreds of real emails to a third party.
        cli = _load_cli()
        assert cli.MAX_IDS_PER_RUN <= 25


@pytest.mark.parametrize("direction", ["incoming", "unknown"])
def test_every_classifiable_direction_reaches_the_provider(
    db_session: Session, direction: str
) -> None:
    message = _store(db_session, direction=direction)
    fake = FakeClassifier()

    dry_run_classify(db_session, fake, [message.id])

    assert len(fake.calls) == 1


class TestMatchingIsIncludedInTheDryRun:
    """Identity is decided alongside meaning, from the same read-only session."""

    def test_a_match_is_attached_when_an_application_exists(
        self, db_session: Session
    ) -> None:
        from app.enums import ApplicationStatus, MatchStatus
        from app.schemas.application import ApplicationCreate
        from app.services.applications import create_application

        application = create_application(
            db_session,
            ApplicationCreate(
                company_name="Brightpath Systems",
                role_title="Backend Engineer",
                status=ApplicationStatus.SAVED,
            ),
        )
        message = _store(db_session)

        outcome = dry_run_classify(db_session, FakeClassifier(), [message.id])[0]

        assert outcome.match is not None
        assert outcome.match.status is MatchStatus.MATCHED
        assert outcome.match.application_id == application.id

    def test_no_application_yields_no_match_not_an_error(
        self, db_session: Session
    ) -> None:
        from app.enums import MatchStatus

        message = _store(db_session)

        outcome = dry_run_classify(db_session, FakeClassifier(), [message.id])[0]

        assert outcome.match is not None
        assert outcome.match.status is MatchStatus.NO_MATCH
        assert outcome.match.application_id is None
        assert outcome.error is None

    def test_a_skipped_outgoing_message_is_never_matched(
        self, db_session: Session
    ) -> None:
        # Direction gates everything, matching included.
        message = _store(db_session, direction="outgoing")

        outcome = dry_run_classify(db_session, FakeClassifier(), [message.id])[0]

        assert outcome.skipped
        assert outcome.match is None

    def test_a_classification_error_leaves_matching_unattempted(
        self, db_session: Session
    ) -> None:
        # There is nothing to match on without a company and role.
        message = _store(db_session)
        fake = FakeClassifier(raises=EmailClassificationFailed("provider timeout"))

        outcome = dry_run_classify(db_session, fake, [message.id])[0]

        assert outcome.error is not None
        assert outcome.match is None

    def test_matching_writes_nothing(self, db_session: Session) -> None:
        from app.enums import ApplicationStatus
        from app.schemas.application import ApplicationCreate
        from app.services.applications import create_application

        application = create_application(
            db_session,
            ApplicationCreate(
                company_name="Brightpath Systems",
                role_title="Backend Engineer",
                status=ApplicationStatus.SAVED,
            ),
        )
        before = (application.status, application.updated_at)
        message = _store(db_session)

        dry_run_classify(db_session, FakeClassifier(), [message.id])

        db_session.refresh(application)
        assert (application.status, application.updated_at) == before
        assert db_session.execute(select(Suggestion)).scalars().all() == []

    def test_the_cli_prints_the_match_and_its_reason(
        self, db_session: Session, capsys
    ) -> None:
        from app.enums import ApplicationStatus
        from app.schemas.application import ApplicationCreate
        from app.services.applications import create_application

        create_application(
            db_session,
            ApplicationCreate(
                company_name="Brightpath Systems",
                role_title="Backend Engineer",
                status=ApplicationStatus.SAVED,
            ),
        )
        message = _store(db_session)
        outcome = dry_run_classify(db_session, FakeClassifier(), [message.id])[0]

        _load_cli().print_outcome(outcome)

        printed = capsys.readouterr().out
        assert "match" in printed
        assert "matched" in printed
        # The answer to "why did JobOps think this?" is printed, not implied.
        assert "why" in printed
        assert "signals" in printed
