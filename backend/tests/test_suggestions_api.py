"""HTTP-level tests for the suggestions API."""

from fastapi.testclient import TestClient

from tests.conftest import requires_database

pytestmark = requires_database


def _create_application(client: TestClient, **overrides: object) -> dict:
    payload = {"company_name": "Harmonic", "role_title": "Junior SW Engineer"}
    payload.update(overrides)
    response = client.post("/applications", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _create_suggestion(client: TestClient, application_id: int, **overrides: object) -> dict:
    payload = {
        "application_id": application_id,
        "proposed_status": "technical_interview",
        "source": "manual",
        "confidence": "high",
        "rationale": "A future integration detected a technical-interview invitation",
    }
    payload.update(overrides)
    response = client.post("/suggestions", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


class TestCreateSuggestion:
    def test_create_returns_the_suggestion_with_application_context(
        self, client: TestClient
    ) -> None:
        application = _create_application(client)

        body = _create_suggestion(client, application["id"])

        assert body["state"] == "pending"
        assert body["company_name"] == "Harmonic"
        assert body["role_title"] == "Junior SW Engineer"
        assert body["current_status"] == "saved"
        assert body["proposed_status"] == "technical_interview"

    def test_creating_does_not_change_the_application(self, client: TestClient) -> None:
        application = _create_application(client)

        _create_suggestion(client, application["id"])

        refetched = client.get(f"/applications/{application['id']}").json()
        assert refetched["status"] == "saved"
        assert [e["event_type"] for e in refetched["events"]] == ["created"]

    def test_unknown_application_returns_404(self, client: TestClient) -> None:
        response = client.post(
            "/suggestions",
            json={
                "application_id": 999999,
                "proposed_status": "applied",
                "confidence": "high",
                "rationale": "test",
            },
        )
        assert response.status_code == 404


class TestListSuggestionsEndpoint:
    def test_pending_filter(self, client: TestClient) -> None:
        application = _create_application(client)
        created = _create_suggestion(client, application["id"])

        response = client.get("/suggestions", params={"state": "pending"})

        assert response.status_code == 200
        ids = [item["id"] for item in response.json()]
        assert ids == [created["id"]]

    def test_pending_count_via_list_length(self, client: TestClient) -> None:
        application = _create_application(client)
        _create_suggestion(client, application["id"], proposed_status="hr_interview")
        _create_suggestion(client, application["id"], proposed_status="offer")

        response = client.get("/suggestions", params={"state": "pending"})

        assert len(response.json()) == 2


class TestAcceptEndpoint:
    def test_accept_changes_the_application_status(self, client: TestClient) -> None:
        # A submitted-state status requires a CV even at creation time, so the
        # document must exist before the application does.
        document = client.post(
            "/documents",
            files={"file": ("cv.pdf", b"%PDF-1.4 cv\n%%EOF", "application/pdf")},
            data={"kind": "cv"},
        ).json()
        application = _create_application(
            client, status="applied", submitted_cv_document_id=document["id"]
        )
        suggestion = _create_suggestion(
            client, application["id"], proposed_status="technical_interview"
        )

        response = client.post(f"/suggestions/{suggestion['id']}/accept", json={})

        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "accepted"
        assert body["current_status"] == "technical_interview"

        refetched = client.get(f"/applications/{application['id']}").json()
        assert refetched["status"] == "technical_interview"
        assert "status_changed" in [e["event_type"] for e in refetched["events"]]

    def test_accepting_a_resolved_suggestion_returns_409(self, client: TestClient) -> None:
        application = _create_application(client)
        suggestion = _create_suggestion(
            client, application["id"], proposed_status="on_hold"
        )
        client.post(f"/suggestions/{suggestion['id']}/accept", json={})

        response = client.post(f"/suggestions/{suggestion['id']}/accept", json={})

        assert response.status_code == 409


class TestRejectEndpoint:
    def test_reject_leaves_the_application_unchanged(self, client: TestClient) -> None:
        application = _create_application(client)
        suggestion = _create_suggestion(client, application["id"])

        response = client.post(f"/suggestions/{suggestion['id']}/reject")

        assert response.status_code == 200
        assert response.json()["state"] == "rejected"

        refetched = client.get(f"/applications/{application['id']}").json()
        assert refetched["status"] == "saved"

    def test_rejecting_a_resolved_suggestion_returns_409(self, client: TestClient) -> None:
        application = _create_application(client)
        suggestion = _create_suggestion(client, application["id"])
        client.post(f"/suggestions/{suggestion['id']}/reject")

        response = client.post(f"/suggestions/{suggestion['id']}/reject")

        assert response.status_code == 409

    def test_unknown_suggestion_returns_404(self, client: TestClient) -> None:
        assert client.post("/suggestions/999999/reject").status_code == 404
