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


def _raw_message(message_id: str, *, subject: str, sender: str, body: str) -> dict:
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "internalDate": "1767225600000",  # 2026-01-01T00:00:00Z
        "snippet": body[:50],
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
