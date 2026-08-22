"""Targeted tests for the Gmail read-only sync service (Phase 6.1).

`FakeGmailClient` implements the same small `GmailMessages` interface the real
`GmailClient` does, so these tests exercise the real `sync_recent_messages`
logic — deduplication, parsing, counts — without a real Gmail account, OAuth
token, or network access of any kind.
"""

import base64
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.email_message import EmailMessage
from app.services.gmail import sync_recent_messages
from tests.conftest import requires_database

pytestmark = requires_database


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _raw_message(
    message_id: str,
    *,
    subject: str,
    sender: str,
    body: str,
    label_ids: list[str] | None = None,
) -> dict:
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "internalDate": "1767225600000",  # 2026-01-01T00:00:00Z
        "snippet": body[:50],
        # Defaults to a realistic received-message label set; direction is read
        # from here, never from `sender`.
        "labelIds": ["INBOX", "UNREAD"] if label_ids is None else label_ids,
        "payload": {
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
            ],
            "mimeType": "text/plain",
            "body": {"data": _b64url(body)},
        },
    }


class FakeGmailClient:
    """A fake `GmailMessages` — the whole point being it is never the real one."""

    def __init__(self, messages: dict[str, dict]) -> None:
        self._messages = messages
        self.list_calls: list[dict] = []
        self.get_calls: list[str] = []

    def list_message_ids(self, *, query: str, max_results: int) -> list[str]:
        self.list_calls.append({"query": query, "max_results": max_results})
        return list(self._messages.keys())[:max_results]

    def get_message(self, message_id: str) -> dict:
        self.get_calls.append(message_id)
        return self._messages[message_id]


class TestSyncRecentMessages:
    def test_imports_new_messages_and_reports_counts(self, db_session: Session) -> None:
        fake = FakeGmailClient(
            {
                "m1": _raw_message(
                    "m1", subject="Interview invitation", sender="a@example.com", body="Hi 1"
                ),
                "m2": _raw_message(
                    "m2", subject="Application received", sender="b@example.com", body="Hi 2"
                ),
            }
        )

        result = sync_recent_messages(db_session, fake)

        assert result.fetched == 2
        assert result.imported == 2
        assert result.already_existing == 0

        stored = db_session.execute(select(EmailMessage)).scalars().all()
        assert {row.gmail_message_id for row in stored} == {"m1", "m2"}

    def test_second_sync_skips_already_imported_messages(self, db_session: Session) -> None:
        fake = FakeGmailClient(
            {"m1": _raw_message("m1", subject="Hi", sender="a@example.com", body="body")}
        )

        first = sync_recent_messages(db_session, fake)
        second = sync_recent_messages(db_session, fake)

        assert first.imported == 1
        assert second.imported == 0
        assert second.already_existing == 1
        assert second.fetched == 1

        # Still exactly one row — dedupe by gmail_message_id, not a duplicate.
        count = db_session.execute(select(EmailMessage)).scalars().all()
        assert len(count) == 1

    def test_mixed_new_and_already_existing_in_one_call(self, db_session: Session) -> None:
        fake = FakeGmailClient(
            {"m1": _raw_message("m1", subject="A", sender="a@example.com", body="1")}
        )
        sync_recent_messages(db_session, fake)  # m1 now imported

        fake_second = FakeGmailClient(
            {
                "m1": _raw_message("m1", subject="A", sender="a@example.com", body="1"),
                "m2": _raw_message("m2", subject="B", sender="b@example.com", body="2"),
            }
        )
        result = sync_recent_messages(db_session, fake_second)

        assert result.fetched == 2
        assert result.imported == 1
        assert result.already_existing == 1

    def test_parsed_fields_are_stored_correctly(self, db_session: Session) -> None:
        fake = FakeGmailClient(
            {
                "m1": _raw_message(
                    "m1",
                    subject="Technical interview",
                    sender="Recruiter <r@example.com>",
                    body="We would like to schedule a technical interview.",
                )
            }
        )

        sync_recent_messages(db_session, fake)

        row = db_session.execute(select(EmailMessage)).scalar_one()
        assert row.gmail_message_id == "m1"
        assert row.thread_id == "thread-m1"
        assert row.sender == "Recruiter <r@example.com>"
        assert row.subject == "Technical interview"
        assert row.body_text == "We would like to schedule a technical interview."
        assert row.received_at == datetime(2026, 1, 1, tzinfo=UTC)
        assert row.direction == "incoming"

    def test_window_days_and_max_messages_are_passed_through(self, db_session: Session) -> None:
        fake = FakeGmailClient({})

        sync_recent_messages(db_session, fake, window_days=7, max_messages=5)

        assert fake.list_calls == [{"query": "newer_than:7d", "max_results": 5}]

    def test_defaults_come_from_settings_when_not_specified(self, db_session: Session) -> None:
        from app.config import settings

        fake = FakeGmailClient({})

        sync_recent_messages(db_session, fake)

        assert fake.list_calls[0]["query"] == f"newer_than:{settings.gmail_sync_window_days}d"
        assert fake.list_calls[0]["max_results"] == settings.gmail_max_messages_per_sync

    def test_max_messages_caps_how_many_are_fetched(self, db_session: Session) -> None:
        fake = FakeGmailClient(
            {
                "m1": _raw_message("m1", subject="A", sender="a@example.com", body="1"),
                "m2": _raw_message("m2", subject="B", sender="b@example.com", body="2"),
                "m3": _raw_message("m3", subject="C", sender="c@example.com", body="3"),
            }
        )

        result = sync_recent_messages(db_session, fake, max_messages=2)

        assert result.fetched == 2
        assert result.imported == 2


class TestSyncDoesNotTouchApplicationsOrSuggestions:
    """The hard scope boundary for this phase: read -> parse -> dedupe -> store,
    and nothing else — no matching, no Suggestion, no status/event change."""

    def test_sync_creates_no_suggestions_even_for_interview_like_content(
        self, db_session: Session
    ) -> None:
        from app.models.suggestion import Suggestion

        fake = FakeGmailClient(
            {
                "m1": _raw_message(
                    "m1",
                    subject="Technical Interview Invitation",
                    sender="recruiter@example.com",
                    body="We would like to invite you to a technical interview next week.",
                )
            }
        )

        sync_recent_messages(db_session, fake)

        assert db_session.execute(select(Suggestion)).scalars().all() == []

    def test_sync_does_not_touch_existing_applications(self, db_session: Session) -> None:
        from app.enums import ApplicationStatus
        from app.models.application_event import ApplicationEvent
        from app.schemas.application import ApplicationCreate
        from app.services.applications import create_application

        application = create_application(
            db_session,
            ApplicationCreate(
                company_name="Harmonic",
                role_title="Junior SW Engineer",
                status=ApplicationStatus.SAVED,
            ),
        )
        original_status = application.status
        original_updated_at = application.updated_at

        fake = FakeGmailClient(
            {
                "m1": _raw_message(
                    "m1",
                    subject="Harmonic - Technical Interview",
                    sender="recruiter@harmonic.example",
                    body="Your Harmonic technical interview is scheduled.",
                )
            }
        )
        sync_recent_messages(db_session, fake)

        db_session.refresh(application)
        assert application.status == original_status
        assert application.updated_at == original_updated_at

        events = db_session.execute(
            select(ApplicationEvent).where(ApplicationEvent.application_id == application.id)
        ).scalars().all()
        assert [event.event_type for event in events] == ["created"]


class TestDirectionIsPersisted:
    """Direction is decided at import time from Gmail's labels and stored.

    Stored rather than re-derived because the raw `labelIds` are not kept: this
    is the one bit of that metadata later phases need, and going back to Gmail
    just to learn a message's direction would make an offline question depend on
    the network.
    """

    def test_sent_label_is_stored_as_outgoing(self, db_session: Session) -> None:
        fake = FakeGmailClient(
            {
                "m1": _raw_message(
                    "m1",
                    subject="Re: Next step",
                    sender="Me <me@example.com>",
                    body="I am available Tuesday afternoon.",
                    label_ids=["SENT"],
                )
            }
        )

        sync_recent_messages(db_session, fake)

        row = db_session.execute(select(EmailMessage)).scalar_one()
        assert row.direction == "outgoing"

    def test_inbox_label_is_stored_as_incoming(self, db_session: Session) -> None:
        fake = FakeGmailClient(
            {
                "m1": _raw_message(
                    "m1",
                    subject="Interview invitation",
                    sender="Recruiter <r@example.com>",
                    body="We would like to speak with you.",
                    label_ids=["INBOX", "IMPORTANT"],
                )
            }
        )

        sync_recent_messages(db_session, fake)

        assert db_session.execute(select(EmailMessage)).scalar_one().direction == "incoming"

    def test_archived_mail_without_inbox_is_still_incoming(self, db_session: Session) -> None:
        """The regression the direction rule was corrected for.

        An archived recruitment email has no INBOX label. Reading that as
        `unknown` mislabelled a large share of exactly the messages this
        integration exists to process.
        """
        fake = FakeGmailClient(
            {
                "m1": _raw_message(
                    "m1",
                    subject="Archived thread",
                    sender="someone@example.com",
                    body="Read and archived, so no INBOX label remains.",
                    label_ids=["CATEGORY_PERSONAL"],
                )
            }
        )

        sync_recent_messages(db_session, fake)

        assert db_session.execute(select(EmailMessage)).scalar_one().direction == "incoming"

    def test_a_draft_is_stored_as_outgoing(self, db_session: Session) -> None:
        # The user's own unsent writing, excluded from classification for the
        # same reason a sent reply is.
        fake = FakeGmailClient(
            {
                "m1": _raw_message(
                    "m1",
                    subject="Re: Next step",
                    sender="Me <me@example.com>",
                    body="Draft reply I have not sent yet.",
                    label_ids=["DRAFT"],
                )
            }
        )

        sync_recent_messages(db_session, fake)

        assert db_session.execute(select(EmailMessage)).scalar_one().direction == "outgoing"

    def test_a_message_with_no_labels_at_all_is_unknown(self, db_session: Session) -> None:
        fake = FakeGmailClient(
            {
                "m1": _raw_message(
                    "m1",
                    subject="No labels",
                    sender="someone@example.com",
                    body="Gmail returned no labelIds for this one.",
                    label_ids=[],
                )
            }
        )

        sync_recent_messages(db_session, fake)

        assert db_session.execute(select(EmailMessage)).scalar_one().direction == "unknown"

    def test_the_same_sender_yields_different_directions_by_label(
        self, db_session: Session
    ) -> None:
        """The regression guard for the approach: labels decide, addresses do not.

        Both messages share a From: address. If anyone ever reintroduces
        address-based inference, these two cannot both hold.
        """
        sender = "Same Person <same@example.com>"
        fake = FakeGmailClient(
            {
                "sent": _raw_message(
                    "sent", subject="A", sender=sender, body="one", label_ids=["SENT"]
                ),
                "recv": _raw_message(
                    "recv", subject="B", sender=sender, body="two", label_ids=["INBOX"]
                ),
            }
        )

        sync_recent_messages(db_session, fake)

        rows = {
            row.gmail_message_id: row.direction
            for row in db_session.execute(select(EmailMessage)).scalars().all()
        }
        assert rows == {"sent": "outgoing", "recv": "incoming"}

    def test_direction_is_stored_for_every_imported_message(self, db_session: Session) -> None:
        # NOT NULL with a server default: no row can end up without a direction.
        fake = FakeGmailClient(
            {
                "m1": _raw_message("m1", subject="A", sender="a@example.com", body="1"),
                "m2": _raw_message(
                    "m2", subject="B", sender="b@example.com", body="2", label_ids=["SENT"]
                ),
                "m3": _raw_message(
                    "m3", subject="C", sender="c@example.com", body="3", label_ids=[]
                ),
            }
        )

        sync_recent_messages(db_session, fake)

        rows = db_session.execute(select(EmailMessage)).scalars().all()
        assert len(rows) == 3
        assert all(row.direction in {"incoming", "outgoing", "unknown"} for row in rows)


class TestSyncStillCreatesNoClassification:
    """Phase 6.2A-0 adds a contract, not a classifier.

    Direction is now determined during sync; nothing else about the message's
    meaning is. No classification is produced, stored, or requested.
    """

    def test_sync_does_not_classify_an_obvious_rejection(self, db_session: Session) -> None:
        fake = FakeGmailClient(
            {
                "m1": _raw_message(
                    "m1",
                    subject="Thank you for your interest",
                    sender="careers@example.com",
                    body="We have decided to move forward with other candidates.",
                )
            }
        )

        sync_recent_messages(db_session, fake)

        row = db_session.execute(select(EmailMessage)).scalar_one()
        # Direction yes; meaning no. The stored row has no notion of what the
        # message says beyond its text.
        assert row.direction == "incoming"
        assert not hasattr(row, "message_type")
        assert not hasattr(row, "classification")


class TestDirectionEnrichmentOnResync:
    """Rows stored before `direction` existed are repaired opportunistically.

    Plain dedupe would leave all already-imported messages `unknown` forever:
    the row exists, so nothing would ever look at it again. Enrichment fills the
    gap the migration could not, without a second import path and without
    touching message content.
    """

    def _stored_unknown(self, db: Session, gmail_message_id: str = "m1") -> EmailMessage:
        """A row exactly as the pre-direction migration left it."""
        message = EmailMessage(
            gmail_message_id=gmail_message_id,
            thread_id=f"thread-{gmail_message_id}",
            sender="Recruiter <r@example.com>",
            subject="Interview invitation",
            received_at=datetime(2026, 1, 1, tzinfo=UTC),
            body_text="Original body text.",
            direction="unknown",
        )
        db.add(message)
        db.commit()
        db.refresh(message)
        return message

    def test_unknown_direction_is_enriched_without_creating_a_duplicate(
        self, db_session: Session
    ) -> None:
        stored = self._stored_unknown(db_session)
        fake = FakeGmailClient(
            {
                "m1": _raw_message(
                    "m1",
                    subject="Interview invitation",
                    sender="Recruiter <r@example.com>",
                    body="Original body text.",
                    label_ids=["INBOX"],
                )
            }
        )

        result = sync_recent_messages(db_session, fake)

        db_session.refresh(stored)
        assert stored.direction == "incoming"
        # Enrichment is not an import: still exactly one row, counted as
        # already-existing.
        assert result.imported == 0
        assert result.already_existing == 1
        assert result.enriched == 1
        assert len(db_session.execute(select(EmailMessage)).scalars().all()) == 1

    def test_enrichment_works_for_archived_mail(self, db_session: Session) -> None:
        # The common case among existing rows: read, archived, no INBOX label.
        stored = self._stored_unknown(db_session)
        fake = FakeGmailClient(
            {
                "m1": _raw_message(
                    "m1",
                    subject="Interview invitation",
                    sender="Recruiter <r@example.com>",
                    body="Original body text.",
                    label_ids=["CATEGORY_PERSONAL"],
                )
            }
        )

        sync_recent_messages(db_session, fake)

        db_session.refresh(stored)
        assert stored.direction == "incoming"

    def test_enrichment_only_writes_the_direction_column(self, db_session: Session) -> None:
        """Repairing metadata must not re-import content."""
        stored = self._stored_unknown(db_session)
        original = (stored.subject, stored.body_text, stored.sender, stored.received_at)

        fake = FakeGmailClient(
            {
                "m1": _raw_message(
                    "m1",
                    # Deliberately different from what is stored: if enrichment
                    # rewrote content, these would overwrite the row.
                    subject="A DIFFERENT SUBJECT",
                    sender="someone-else@example.com",
                    body="COMPLETELY DIFFERENT BODY",
                    label_ids=["SENT"],
                )
            }
        )

        sync_recent_messages(db_session, fake)

        db_session.refresh(stored)
        assert stored.direction == "outgoing"
        assert (stored.subject, stored.body_text, stored.sender, stored.received_at) == original

    def test_a_known_direction_is_never_degraded_to_unknown(self, db_session: Session) -> None:
        """A row that already has a direction is not a candidate at all."""
        stored = self._stored_unknown(db_session)
        stored.direction = "incoming"
        db_session.commit()

        fake = FakeGmailClient(
            {
                "m1": _raw_message(
                    "m1",
                    subject="Interview invitation",
                    sender="Recruiter <r@example.com>",
                    body="Original body text.",
                    # No labels at all, which would parse as `unknown`.
                    label_ids=[],
                )
            }
        )

        result = sync_recent_messages(db_session, fake)

        db_session.refresh(stored)
        assert stored.direction == "incoming"
        assert result.enriched == 0

    def test_a_known_direction_is_never_flipped(self, db_session: Session) -> None:
        stored = self._stored_unknown(db_session)
        stored.direction = "outgoing"
        db_session.commit()

        fake = FakeGmailClient(
            {
                "m1": _raw_message(
                    "m1",
                    subject="Interview invitation",
                    sender="Recruiter <r@example.com>",
                    body="Original body text.",
                    label_ids=["INBOX"],
                )
            }
        )

        result = sync_recent_messages(db_session, fake)

        db_session.refresh(stored)
        assert stored.direction == "outgoing"
        assert result.enriched == 0

    def test_a_re_fetch_with_no_evidence_leaves_the_row_unknown(
        self, db_session: Session
    ) -> None:
        # unknown -> unknown is not a write, and must not be counted as one.
        stored = self._stored_unknown(db_session)
        fake = FakeGmailClient(
            {
                "m1": _raw_message(
                    "m1",
                    subject="Interview invitation",
                    sender="Recruiter <r@example.com>",
                    body="Original body text.",
                    label_ids=[],
                )
            }
        )

        result = sync_recent_messages(db_session, fake)

        db_session.refresh(stored)
        assert stored.direction == "unknown"
        assert result.enriched == 0

    def test_an_already_enriched_row_is_not_re_fetched(self, db_session: Session) -> None:
        """Enrichment is a one-off cost per row, not a permanent tax.

        The extra API call happens only while a row is still unknown; once it
        has a direction the row is skipped without any fetch, so a steady-state
        sync costs exactly what it did before this feature existed.
        """
        self._stored_unknown(db_session)
        fake = FakeGmailClient(
            {
                "m1": _raw_message(
                    "m1",
                    subject="Interview invitation",
                    sender="Recruiter <r@example.com>",
                    body="Original body text.",
                    label_ids=["INBOX"],
                )
            }
        )

        first = sync_recent_messages(db_session, fake)
        fetches_after_first = len(fake.get_calls)
        second = sync_recent_messages(db_session, fake)

        assert first.enriched == 1
        assert second.enriched == 0
        # No additional get_message call on the second run.
        assert len(fake.get_calls) == fetches_after_first

    def test_enrichment_is_idempotent_across_many_runs(self, db_session: Session) -> None:
        stored = self._stored_unknown(db_session)
        fake = FakeGmailClient(
            {
                "m1": _raw_message(
                    "m1",
                    subject="Interview invitation",
                    sender="Recruiter <r@example.com>",
                    body="Original body text.",
                    label_ids=["INBOX"],
                )
            }
        )

        for _ in range(3):
            sync_recent_messages(db_session, fake)

        db_session.refresh(stored)
        assert stored.direction == "incoming"
        assert len(db_session.execute(select(EmailMessage)).scalars().all()) == 1

    def test_a_mixed_batch_imports_enriches_and_skips(self, db_session: Session) -> None:
        self._stored_unknown(db_session, "stale")
        known = self._stored_unknown(db_session, "known")
        known.direction = "incoming"
        db_session.commit()

        fake = FakeGmailClient(
            {
                "stale": _raw_message(
                    "stale", subject="A", sender="a@example.com", body="1", label_ids=["INBOX"]
                ),
                "known": _raw_message(
                    "known", subject="B", sender="b@example.com", body="2", label_ids=["INBOX"]
                ),
                "fresh": _raw_message(
                    "fresh", subject="C", sender="c@example.com", body="3", label_ids=["SENT"]
                ),
            }
        )

        result = sync_recent_messages(db_session, fake)

        assert result.fetched == 3
        assert result.imported == 1
        assert result.already_existing == 2
        assert result.enriched == 1

        rows = {
            row.gmail_message_id: row.direction
            for row in db_session.execute(select(EmailMessage)).scalars().all()
        }
        assert rows == {"stale": "incoming", "known": "incoming", "fresh": "outgoing"}
