"""Tests that need PostgreSQL to be running."""

from fastapi.testclient import TestClient

from tests.conftest import requires_database


@requires_database
def test_readiness_reports_a_healthy_database(plain_client: TestClient) -> None:
    response = plain_client.get("/health/db")

    assert response.status_code == 200
    assert response.json()["database"] == "ok"
