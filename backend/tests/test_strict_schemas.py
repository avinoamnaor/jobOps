"""Unknown fields in request bodies must be rejected, not silently ignored.

Pydantic's default is to drop fields it does not recognise. That turns a
misunderstanding into a success response: the caller sends `status`, gets 200 OK,
and believes the status changed. Every request schema therefore sets
`extra="forbid"` via `StrictModel`.
"""

from fastapi.testclient import TestClient

from tests.conftest import requires_database

pytestmark = requires_database


def _create(client: TestClient, **overrides: object) -> dict:
    payload = {"company_name": "ProgrammaticX", "role_title": "Fullstack Developer"}
    payload.update(overrides)
    response = client.post("/applications", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


class TestPatchRejectsStatus:
    """The specific case this hardening exists for."""

    def test_patch_with_status_returns_422(self, client: TestClient) -> None:
        created = _create(client)

        response = client.patch(
            f"/applications/{created['id']}",
            json={"notes": "updated notes", "status": "offer"},
        )

        assert response.status_code == 422

    def test_error_points_at_the_correct_endpoint(self, client: TestClient) -> None:
        created = _create(client)

        response = client.patch(f"/applications/{created['id']}", json={"status": "offer"})

        assert "/applications/{id}/status" in response.text

    def test_rejected_patch_changes_nothing_at_all(self, client: TestClient) -> None:
        """A refused request must not half-apply the fields it did understand."""
        created = _create(client, notes="original notes")

        client.patch(
            f"/applications/{created['id']}",
            json={"notes": "should not be saved", "status": "offer"},
        )

        after = client.get(f"/applications/{created['id']}").json()
        assert after["status"] == "saved"
        assert after["notes"] == "original notes"
        assert [event["event_type"] for event in after["events"]] == ["created"]

    def test_patch_without_status_still_works(self, client: TestClient) -> None:
        created = _create(client)

        response = client.patch(f"/applications/{created['id']}", json={"notes": "fine"})

        assert response.status_code == 200
        assert response.json()["notes"] == "fine"


class TestUnknownFieldsAreRejected:
    def test_create_rejects_unknown_field(self, client: TestClient) -> None:
        response = client.post(
            "/applications",
            json={
                "company_name": "X",
                "role_title": "Y",
                "salary_expectation": 100_000,  # not a field we support
            },
        )

        assert response.status_code == 422

    def test_create_rejects_a_typo_instead_of_dropping_it(self, client: TestClient) -> None:
        """The everyday value of extra="forbid": typos fail loudly."""
        response = client.post(
            "/applications",
            json={"company_name": "X", "role_title": "Y", "compnay_key": "typo"},
        )

        assert response.status_code == 422

    def test_create_rejects_server_owned_fields(self, client: TestClient) -> None:
        """Derived and server-assigned columns are not client input."""
        for field, value in [
            ("id", 999),
            ("company_key", "forged"),
            ("job_url_canonical", "https://forged.example.com"),
            ("created_at", "2020-01-01T00:00:00Z"),
        ]:
            response = client.post(
                "/applications",
                json={"company_name": "X", "role_title": "Y", field: value},
            )
            assert response.status_code == 422, field

    def test_status_change_rejects_unknown_field(self, client: TestClient) -> None:
        created = _create(client)

        response = client.post(
            f"/applications/{created['id']}/status",
            json={"to": "applied", "reason": "not a field — the field is called 'note'"},
        )

        assert response.status_code == 422

    def test_status_change_still_works_with_valid_fields(self, client: TestClient) -> None:
        created = _create(client)

        # `on_hold` needs no submitted CV, keeping this test focused on schema
        # validation rather than the CV rule.
        response = client.post(
            f"/applications/{created['id']}/status",
            json={"to": "on_hold", "note": "valid"},
        )

        assert response.status_code == 200

    def test_event_creation_rejects_status_fields(self, client: TestClient) -> None:
        """Belt and braces: these were already impossible, now they are explicit."""
        created = _create(client)

        for field in ("new_status", "previous_status", "source", "application_id"):
            response = client.post(
                f"/applications/{created['id']}/events",
                json={"event_type": "note_added", "summary": "x", field: "applied"},
            )
            assert response.status_code == 422, field
