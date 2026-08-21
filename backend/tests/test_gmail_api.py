"""HTTP-level tests for the Gmail integration: sync, plus read-only inspection.

`app.api.gmail.GmailClient` is always monkeypatched here — never the real class
— so these tests can never reach a real Gmail account or the network, and never
depend on whether this machine happens to have a real token file. The
list/detail/status tests go one step further: they never even construct a fake
GmailClient, since those endpoints must not depend on Gmail being reachable at
all — proven with a `_ForbiddenClient` that fails the test if it is ever touched.
"""

import base64
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.api.gmail as gmail_api
from app.core.errors import GmailNotConnected
from app.models.email_message import EmailMessage
from tests.conftest import requires_database

pytestmark = requires_database


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _raw_message(message_id: str) -> dict:
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "internalDate": "1767225600000",
        "snippet": "preview",
        "payload": {
            "headers": [
                {"name": "From", "value": "a@example.com"},
                {"name": "Subject", "value": "Subject"},
            ],
            "mimeType": "text/plain",
            "body": {"data": _b64url("body text")},
        },
    }


class _FakeConnectedClient:
    """Stands in for GmailClient: 'from_stored_credentials' returns a working fake."""

    _messages = {"m1": _raw_message("m1")}

    @classmethod
    def from_stored_credentials(cls) -> "_FakeConnectedClient":
        return cls()

    def list_message_ids(self, *, query: str, max_results: int) -> list[str]:
        return list(self._messages.keys())[:max_results]

    def get_message(self, message_id: str) -> dict:
        return self._messages[message_id]


class _FakeNotConnectedClient:
    """Stands in for GmailClient when authorization has never been completed."""

    @classmethod
    def from_stored_credentials(cls) -> "_FakeNotConnectedClient":
        raise GmailNotConnected()


class _ForbiddenClient:
    """Fails the test the instant anything tries to reach 'Gmail' through it.

    Used by the read-only inspection tests to prove those endpoints never call
    GmailClient at all — not even to construct one.
    """

    @classmethod
    def from_stored_credentials(cls) -> "_ForbiddenClient":
        raise AssertionError("read-only inspection endpoints must not touch GmailClient")


def _store_message(
    db: Session,
    *,
    gmail_message_id: str,
    received_at: datetime,
    subject: str = "Subject",
    sender: str = "a@example.com",
    body_text: str | None = "body",
) -> EmailMessage:
    """Insert a row directly — these endpoints only ever read the database, so
    tests seed it the same way, with full control over `received_at` for
    deterministic ordering/pagination assertions."""
    message = EmailMessage(
        gmail_message_id=gmail_message_id,
        thread_id=f"thread-{gmail_message_id}",
        sender=sender,
        subject=subject,
        received_at=received_at,
        body_text=body_text,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


class TestSyncEndpoint:
    def test_sync_imports_and_reports_counts(self, client: TestClient, monkeypatch) -> None:
        monkeypatch.setattr(gmail_api, "GmailClient", _FakeConnectedClient)

        response = client.post("/integrations/gmail/sync")

        assert response.status_code == 200, response.text
        assert response.json() == {"fetched": 1, "imported": 1, "already_existing": 0}

    def test_second_sync_reports_already_existing(self, client: TestClient, monkeypatch) -> None:
        monkeypatch.setattr(gmail_api, "GmailClient", _FakeConnectedClient)

        client.post("/integrations/gmail/sync")
        response = client.post("/integrations/gmail/sync")

        assert response.json() == {"fetched": 1, "imported": 0, "already_existing": 1}

    def test_window_and_max_messages_query_params_are_accepted(
        self, client: TestClient, monkeypatch
    ) -> None:
        monkeypatch.setattr(gmail_api, "GmailClient", _FakeConnectedClient)

        response = client.post(
            "/integrations/gmail/sync", params={"window_days": 7, "max_messages": 1}
        )

        assert response.status_code == 200

    def test_not_connected_returns_503(self, client: TestClient, monkeypatch) -> None:
        monkeypatch.setattr(gmail_api, "GmailClient", _FakeNotConnectedClient)

        response = client.post("/integrations/gmail/sync")

        assert response.status_code == 503
        assert "gmail_authorize.py" in response.json()["detail"]

    def test_sync_creates_no_application_or_suggestion(
        self, client: TestClient, monkeypatch
    ) -> None:
        monkeypatch.setattr(gmail_api, "GmailClient", _FakeConnectedClient)

        client.post("/integrations/gmail/sync")

        assert client.get("/applications").json()["total"] == 0
        assert client.get("/suggestions").json() == []


class TestListMessagesEndpoint:
    def test_never_touches_gmail_client(
        self, client: TestClient, db_session: Session, monkeypatch
    ) -> None:
        monkeypatch.setattr(gmail_api, "GmailClient", _ForbiddenClient)
        _store_message(db_session, gmail_message_id="m1", received_at=datetime.now(UTC))

        response = client.get("/integrations/gmail/messages")

        assert response.status_code == 200

    def test_ordered_newest_first(self, client: TestClient, db_session: Session) -> None:
        now = datetime.now(UTC)
        oldest = _store_message(
            db_session, gmail_message_id="old", received_at=now - timedelta(days=2)
        )
        middle = _store_message(
            db_session, gmail_message_id="mid", received_at=now - timedelta(days=1)
        )
        newest = _store_message(db_session, gmail_message_id="new", received_at=now)

        response = client.get("/integrations/gmail/messages")

        ids = [item["id"] for item in response.json()["items"]]
        assert ids == [newest.id, middle.id, oldest.id]

    def test_pagination_limit_and_offset(self, client: TestClient, db_session: Session) -> None:
        now = datetime.now(UTC)
        for index in range(5):
            _store_message(
                db_session,
                gmail_message_id=f"m{index}",
                received_at=now - timedelta(minutes=index),
            )

        first_page = client.get("/integrations/gmail/messages", params={"limit": 2, "offset": 0})
        second_page = client.get("/integrations/gmail/messages", params={"limit": 2, "offset": 2})

        assert first_page.json()["total"] == 5
        assert len(first_page.json()["items"]) == 2
        assert len(second_page.json()["items"]) == 2
        # No overlap between consecutive pages.
        first_ids = {item["id"] for item in first_page.json()["items"]}
        second_ids = {item["id"] for item in second_page.json()["items"]}
        assert first_ids.isdisjoint(second_ids)
        assert first_page.json()["limit"] == 2
        assert second_page.json()["offset"] == 2

    def test_default_pagination_values(self, client: TestClient, db_session: Session) -> None:
        _store_message(db_session, gmail_message_id="m1", received_at=datetime.now(UTC))

        response = client.get("/integrations/gmail/messages")

        assert response.json()["limit"] == 50
        assert response.json()["offset"] == 0

    def test_invalid_limit_returns_422(self, client: TestClient) -> None:
        assert client.get("/integrations/gmail/messages", params={"limit": 0}).status_code == 422
        assert client.get("/integrations/gmail/messages", params={"limit": 500}).status_code == 422

    def test_negative_offset_returns_422(self, client: TestClient) -> None:
        response = client.get("/integrations/gmail/messages", params={"offset": -1})
        assert response.status_code == 422

    def test_list_response_does_not_include_the_full_body(
        self, client: TestClient, db_session: Session
    ) -> None:
        _store_message(
            db_session,
            gmail_message_id="m1",
            received_at=datetime.now(UTC),
            body_text="a very specific body that must not leak into the list",
        )

        response = client.get("/integrations/gmail/messages")

        item = response.json()["items"][0]
        assert "body_text" not in item
        assert "body" not in item
        # Confirms the summary shape has exactly the documented list fields.
        assert set(item.keys()) == {
            "id",
            "gmail_message_id",
            "thread_id",
            "sender",
            "subject",
            "received_at",
            "created_at",
        }

    def test_empty_list_when_nothing_imported(self, client: TestClient) -> None:
        response = client.get("/integrations/gmail/messages")

        assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


class TestGetMessageEndpoint:
    def test_never_touches_gmail_client(
        self, client: TestClient, db_session: Session, monkeypatch
    ) -> None:
        monkeypatch.setattr(gmail_api, "GmailClient", _ForbiddenClient)
        message = _store_message(db_session, gmail_message_id="m1", received_at=datetime.now(UTC))

        response = client.get(f"/integrations/gmail/messages/{message.id}")

        assert response.status_code == 200

    def test_returns_the_correct_message_including_body(
        self, client: TestClient, db_session: Session
    ) -> None:
        _store_message(db_session, gmail_message_id="other", received_at=datetime.now(UTC))
        target = _store_message(
            db_session,
            gmail_message_id="target",
            received_at=datetime.now(UTC),
            subject="The one we want",
            body_text="the full body text",
        )

        response = client.get(f"/integrations/gmail/messages/{target.id}")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == target.id
        assert body["gmail_message_id"] == "target"
        assert body["subject"] == "The one we want"
        assert body["body_text"] == "the full body text"

    def test_unknown_message_returns_404(self, client: TestClient) -> None:
        response = client.get("/integrations/gmail/messages/999999")

        assert response.status_code == 404
        assert "999999" in response.json()["detail"]


class TestStatusEndpoint:
    def test_never_touches_gmail_client(self, client: TestClient, monkeypatch) -> None:
        monkeypatch.setattr(gmail_api, "GmailClient", _ForbiddenClient)

        response = client.get("/integrations/gmail/status")

        assert response.status_code == 200

    def test_response_shape_is_booleans_only(self, client: TestClient) -> None:
        response = client.get("/integrations/gmail/status")

        assert response.status_code == 200
        body = response.json()
        assert set(body.keys()) == {"credentials_configured", "token_present", "connected"}
        assert all(isinstance(value, bool) for value in body.values())

    def test_response_never_leaks_paths_or_secrets(self, client: TestClient) -> None:
        from app.config import settings

        response = client.get("/integrations/gmail/status")

        text = response.text
        # Neither configured filesystem path appears anywhere in the body.
        assert str(settings.gmail_credentials_path_resolved) not in text
        assert str(settings.gmail_token_path_resolved) not in text
        # No filename markers that would hint at where secrets live on disk.
        assert ".json" not in text
        # Three short booleans is the whole payload — nowhere near enough room
        # to be hiding an actual token or client secret.
        assert len(response.content) < 120
