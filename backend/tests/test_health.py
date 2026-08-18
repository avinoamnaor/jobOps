"""Tests that must pass without any database running."""

from fastapi.testclient import TestClient


def test_liveness_does_not_depend_on_the_database(plain_client: TestClient) -> None:
    response = plain_client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_openapi_schema_is_generated(plain_client: TestClient) -> None:
    """Guards against a broken router or an un-serialisable type annotation.

    Cheap, and it catches a surprising number of mistakes as the API grows.
    """
    response = plain_client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/health" in paths
    assert "/applications" in paths
