"""HTTP-level tests for the applications API."""

from fastapi.testclient import TestClient

from tests.conftest import requires_database

pytestmark = requires_database


def _create(client: TestClient, **overrides: object) -> dict:
    payload = {"company_name": "ProgrammaticX", "role_title": "Fullstack Developer"}
    payload.update(overrides)
    response = client.post("/applications", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


class TestCreateAndRead:
    def test_create_returns_201_with_derived_fields(self, client: TestClient) -> None:
        body = _create(client, company_name="ProgrammaticX Ltd.")

        assert body["status"] == "saved"
        assert body["company_key"] == "programmaticx"
        assert body["id"] > 0

    def test_detail_includes_the_timeline(self, client: TestClient) -> None:
        created = _create(client)

        response = client.get(f"/applications/{created['id']}")

        assert response.status_code == 200
        events = response.json()["events"]
        assert len(events) == 1
        assert events[0]["event_type"] == "created"

    def test_unknown_application_returns_404(self, client: TestClient) -> None:
        assert client.get("/applications/999999").status_code == 404

    def test_invalid_status_is_rejected_with_422(self, client: TestClient) -> None:
        response = client.post(
            "/applications",
            json={"company_name": "X", "role_title": "Y", "status": "definitely_not_a_status"},
        )
        assert response.status_code == 422

    def test_missing_required_fields_return_422(self, client: TestClient) -> None:
        assert client.post("/applications", json={"company_name": "X"}).status_code == 422


class TestListing:
    def test_pagination_reports_total_separately_from_page(self, client: TestClient) -> None:
        for index in range(5):
            _create(client, company_name=f"Company {index}")

        response = client.get("/applications", params={"page": 1, "page_size": 2})

        body = response.json()
        assert body["total"] == 5
        assert len(body["items"]) == 2
        assert body["page"] == 1

    def test_filter_by_status(self, client: TestClient) -> None:
        _create(client, company_name="Saved Co")
        # `on_hold` is a non-submitted status, so it can be created directly
        # without a CV — enough to exercise the status filter.
        on_hold = _create(client, company_name="On Hold Co", status="on_hold")

        response = client.get("/applications", params={"status": "on_hold"})

        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == on_hold["id"]

    def test_search_matches_company_or_role(self, client: TestClient) -> None:
        _create(client, company_name="ProgrammaticX", role_title="Fullstack Developer")
        _create(client, company_name="Acme", role_title="Data Engineer")

        by_company = client.get("/applications", params={"q": "programmatic"}).json()
        by_role = client.get("/applications", params={"q": "data"}).json()

        assert by_company["total"] == 1
        assert by_role["total"] == 1

    def test_soft_deleted_applications_are_hidden(self, client: TestClient) -> None:
        created = _create(client)

        assert client.delete(f"/applications/{created['id']}").status_code == 204
        assert client.get("/applications").json()["total"] == 0
        assert client.get(f"/applications/{created['id']}").status_code == 404


class TestStatusEndpoint:
    def _attach_cv(self, client: TestClient, application_id: int) -> None:
        document = client.post(
            "/documents",
            files={"file": ("cv.pdf", b"%PDF-1.4 demo cv\n%%EOF", "application/pdf")},
            data={"kind": "cv"},
        ).json()
        assert (
            client.put(
                f"/applications/{application_id}/submitted-cv",
                json={"document_id": document["id"]},
            ).status_code
            == 200
        )

    def test_status_change_to_submitted_status_without_cv_returns_422(
        self, client: TestClient
    ) -> None:
        created = _create(client)

        response = client.post(f"/applications/{created['id']}/status", json={"to": "applied"})

        assert response.status_code == 422
        assert "submitted CV" in response.json()["detail"]

    def test_status_change_returns_updated_application_with_event(
        self, client: TestClient
    ) -> None:
        created = _create(client)
        self._attach_cv(client, created["id"])

        response = client.post(
            f"/applications/{created['id']}/status",
            json={"to": "applied", "note": "Submitted via careers page"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "applied"
        assert body["applied_at"] is not None

        status_events = [e for e in body["events"] if e["event_type"] == "status_changed"]
        assert len(status_events) == 1
        assert status_events[0]["previous_status"] == "saved"
        assert status_events[0]["new_status"] == "applied"
        assert status_events[0]["body"] == "Submitted via careers page"

    def test_repeating_the_same_status_returns_409(self, client: TestClient) -> None:
        created = _create(client)

        response = client.post(f"/applications/{created['id']}/status", json={"to": "saved"})

        assert response.status_code == 409

    def test_unknown_status_returns_422(self, client: TestClient) -> None:
        created = _create(client)

        response = client.post(f"/applications/{created['id']}/status", json={"to": "nonsense"})

        assert response.status_code == 422


class TestPatchCannotChangeStatus:
    def test_patch_rejects_a_status_field(self, client: TestClient) -> None:
        """The central guarantee: no generic update path may bypass the history.

        The request is refused outright rather than accepted-and-ignored — see
        tests/test_strict_schemas.py for the full behaviour.
        """
        created = _create(client)

        response = client.patch(
            f"/applications/{created['id']}",
            json={"notes": "updated notes", "status": "offer"},
        )

        assert response.status_code == 422

        # Status untouched and no event fabricated.
        detail = client.get(f"/applications/{created['id']}").json()
        assert detail["status"] == "saved"
        assert [e["event_type"] for e in detail["events"]] == ["created"]

    def test_patch_recomputes_derived_keys(self, client: TestClient) -> None:
        created = _create(client)

        response = client.patch(
            f"/applications/{created['id']}", json={"company_name": "Acme GmbH"}
        )

        assert response.json()["company_key"] == "acme"


class TestManualEvents:
    def test_add_a_note(self, client: TestClient) -> None:
        created = _create(client)

        response = client.post(
            f"/applications/{created['id']}/events",
            json={"event_type": "note_added", "summary": "Referred by a friend"},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["source"] == "manual"
        # Manual events never carry status.
        assert body["new_status"] is None

    def test_scheduled_interview_keeps_its_future_date(self, client: TestClient) -> None:
        created = _create(client)

        response = client.post(
            f"/applications/{created['id']}/events",
            json={
                "event_type": "interview_scheduled",
                "summary": "Technical interview",
                "scheduled_for": "2026-09-01T14:00:00Z",
            },
        )

        assert response.status_code == 201
        assert response.json()["scheduled_for"] is not None

    def test_status_bearing_event_types_are_refused(self, client: TestClient) -> None:
        """A hand-written event must not be able to rewrite status."""
        created = _create(client)

        for event_type in ("status_changed", "created"):
            response = client.post(
                f"/applications/{created['id']}/events",
                json={"event_type": event_type, "summary": "sneaky"},
            )
            assert response.status_code == 422, event_type

    def test_history_has_no_edit_or_delete_endpoints(self, client: TestClient) -> None:
        """Phase 1 exposes no way to rewrite history at all."""
        created = _create(client)
        events = client.get(f"/applications/{created['id']}/events").json()
        event_id = events[0]["id"]

        assert client.patch(f"/events/{event_id}", json={"summary": "x"}).status_code == 404
        assert client.delete(f"/events/{event_id}").status_code == 404


class TestMetaEndpoint:
    def test_enums_are_exposed_for_the_frontend(self, plain_client: TestClient) -> None:
        body = plain_client.get("/meta/enums").json()

        statuses = {entry["value"]: entry for entry in body["statuses"]}
        assert statuses["rejected"]["is_terminal"] is True
        assert statuses["applied"]["is_terminal"] is False
        assert statuses["applied"]["stage_order"] == 1
        # Terminal statuses are endings, not stages.
        assert statuses["rejected"]["stage_order"] is None

        event_types = {entry["value"]: entry for entry in body["event_types"]}
        assert event_types["note_added"]["manually_addable"] is True
        assert event_types["status_changed"]["manually_addable"] is False


class TestDuplicateCheckEndpoint:
    def test_returns_a_possible_match_for_same_company_and_role(self, client: TestClient) -> None:
        created = _create(client, company_name="Harmonic", role_title="Junior SW Engineer")

        response = client.post(
            "/applications/duplicate-check",
            json={"company_name": "Harmonic", "role_title": "Junior SW Engineer"},
        )

        assert response.status_code == 200, response.text
        matches = response.json()
        assert len(matches) == 1
        assert matches[0]["application_id"] == created["id"]
        assert matches[0]["confidence"] == "possible"
        assert matches[0]["status"] == "saved"

    def test_returns_empty_list_when_nothing_matches(self, client: TestClient) -> None:
        response = client.post(
            "/applications/duplicate-check",
            json={"company_name": "No Such Company", "role_title": "No Such Role"},
        )

        assert response.status_code == 200
        assert response.json() == []

    def test_does_not_create_or_modify_anything(self, client: TestClient) -> None:
        client.post(
            "/applications/duplicate-check",
            json={"company_name": "Harmonic", "role_title": "Junior SW Engineer"},
        )

        assert client.get("/applications").json()["total"] == 0

    def test_rejects_missing_required_fields(self, client: TestClient) -> None:
        response = client.post("/applications/duplicate-check", json={"company_name": "Harmonic"})

        assert response.status_code == 422
