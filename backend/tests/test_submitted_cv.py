"""Tests for attaching a submitted CV to an application."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.errors import DocumentKindNotAllowed
from app.enums import DocumentKind
from app.schemas.application import ApplicationCreate
from app.services.applications import attach_submitted_cv, create_application
from app.services.documents import store_document
from tests.conftest import requires_database

pytestmark = requires_database

CV_BYTES = b"%PDF-1.4 CV\n%%EOF"
LETTER_BYTES = b"%PDF-1.4 cover letter\n%%EOF"


def _create_application(client: TestClient) -> dict:
    response = client.post(
        "/applications",
        json={"company_name": "ProgrammaticX", "role_title": "Fullstack Developer"},
    )
    assert response.status_code == 201
    return response.json()


def _upload(client: TestClient, *, content: bytes, kind: str, label: str | None = None) -> dict:
    data = {"kind": kind}
    if label:
        data["label"] = label
    response = client.post(
        "/documents",
        files={"file": ("file.pdf", content, "application/pdf")},
        data=data,
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


class TestAttachSubmittedCv:
    def test_attaching_sets_the_reference(self, client: TestClient) -> None:
        application = _create_application(client)
        cv = _upload(client, content=CV_BYTES, kind="cv", label="Fullstack CV v3")

        response = client.put(
            f"/applications/{application['id']}/submitted-cv",
            json={"document_id": cv["id"]},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["submitted_cv_document_id"] == cv["id"]
        assert body["submitted_cv"]["label"] == "Fullstack CV v3"

    def test_attaching_writes_a_document_attached_event(self, client: TestClient) -> None:
        application = _create_application(client)
        cv = _upload(client, content=CV_BYTES, kind="cv", label="Fullstack CV v3")

        client.put(
            f"/applications/{application['id']}/submitted-cv",
            json={"document_id": cv["id"], "note": "sent with the online form"},
        )

        events = client.get(f"/applications/{application['id']}/events").json()
        attached = [e for e in events if e["event_type"] == "document_attached"]

        assert len(attached) == 1
        assert attached[0]["document_id"] == cv["id"]
        assert "Fullstack CV v3" in attached[0]["summary"]
        assert attached[0]["body"] == "sent with the online form"
        # A document event is not a status event.
        assert attached[0]["new_status"] is None

    def test_changing_the_cv_records_a_second_event(self, client: TestClient) -> None:
        application = _create_application(client)
        first_cv = _upload(client, content=CV_BYTES, kind="cv", label="CV v3")
        second_cv = _upload(client, content=b"%PDF different cv", kind="cv", label="CV v4")

        client.put(
            f"/applications/{application['id']}/submitted-cv", json={"document_id": first_cv["id"]}
        )
        client.put(
            f"/applications/{application['id']}/submitted-cv", json={"document_id": second_cv["id"]}
        )

        detail = client.get(f"/applications/{application['id']}").json()
        attached = [e for e in detail["events"] if e["event_type"] == "document_attached"]

        assert len(attached) == 2
        assert detail["submitted_cv_document_id"] == second_cv["id"]
        # The history still shows the first CV was the one originally sent.
        assert {e["document_id"] for e in attached} == {first_cv["id"], second_cv["id"]}

    def test_reattaching_the_same_cv_returns_409(self, client: TestClient) -> None:
        application = _create_application(client)
        cv = _upload(client, content=CV_BYTES, kind="cv")

        client.put(
            f"/applications/{application['id']}/submitted-cv", json={"document_id": cv["id"]}
        )
        response = client.put(
            f"/applications/{application['id']}/submitted-cv", json={"document_id": cv["id"]}
        )

        assert response.status_code == 409

    def test_unknown_application_returns_404(self, client: TestClient) -> None:
        cv = _upload(client, content=CV_BYTES, kind="cv")

        response = client.put("/applications/999999/submitted-cv", json={"document_id": cv["id"]})

        assert response.status_code == 404

    def test_unknown_document_returns_404(self, client: TestClient) -> None:
        application = _create_application(client)

        response = client.put(
            f"/applications/{application['id']}/submitted-cv", json={"document_id": 999999}
        )

        assert response.status_code == 404

    def test_unknown_body_field_is_rejected(self, client: TestClient) -> None:
        application = _create_application(client)
        cv = _upload(client, content=CV_BYTES, kind="cv")

        response = client.put(
            f"/applications/{application['id']}/submitted-cv",
            json={"document_id": cv["id"], "kind": "cv"},
        )

        assert response.status_code == 422


class TestOnlyCvDocumentsAreAccepted:
    @pytest.mark.parametrize("kind", ["cover_letter", "take_home", "portfolio", "other"])
    def test_non_cv_document_is_rejected_over_http(self, client: TestClient, kind: str) -> None:
        application = _create_application(client)
        document = _upload(client, content=f"a {kind}".encode(), kind=kind)

        response = client.put(
            f"/applications/{application['id']}/submitted-cv",
            json={"document_id": document["id"]},
        )

        assert response.status_code == 422
        assert kind in response.json()["detail"]

    def test_rule_is_enforced_in_the_service_not_just_the_router(
        self, db_session: Session
    ) -> None:
        """Calling the service directly must fail too.

        The router is only one caller. The Chrome extension endpoint and the
        Gmail suggestion queue will call this same function later, and a rule
        enforced only at the HTTP edge is a rule that quietly stops holding.
        """
        application = create_application(
            db_session,
            ApplicationCreate(company_name="ProgrammaticX", role_title="Fullstack Developer"),
        )
        letter, _ = store_document(
            db_session, kind=DocumentKind.COVER_LETTER, content=LETTER_BYTES
        )

        with pytest.raises(DocumentKindNotAllowed):
            attach_submitted_cv(db_session, application.id, letter.id)

    def test_rejected_attachment_changes_nothing(self, client: TestClient) -> None:
        application = _create_application(client)
        letter = _upload(client, content=LETTER_BYTES, kind="cover_letter")

        client.put(
            f"/applications/{application['id']}/submitted-cv", json={"document_id": letter["id"]}
        )

        detail = client.get(f"/applications/{application['id']}").json()
        assert detail["submitted_cv_document_id"] is None
        assert detail["submitted_cv"] is None
        assert [e["event_type"] for e in detail["events"]] == ["created"]


class TestReferentialIntegrity:
    def test_database_refuses_to_delete_a_submitted_cv(
        self, client: TestClient, db_session: Session
    ) -> None:
        """ON DELETE RESTRICT protects the record of what was actually sent."""
        application = _create_application(client)
        cv = _upload(client, content=CV_BYTES, kind="cv")
        client.put(
            f"/applications/{application['id']}/submitted-cv", json={"document_id": cv["id"]}
        )

        with pytest.raises(Exception, match="(?i)violates foreign key constraint"):
            db_session.execute(text("DELETE FROM documents WHERE id = :id"), {"id": cv["id"]})
            db_session.flush()

        db_session.rollback()


class TestPhase2Migration:
    """The Phase 2 migration really produced the schema the models expect.

    The suite builds `jobops_test` by running the migrations, so these assertions
    are checking migration output, not ORM metadata.
    """

    def test_documents_table_exists_with_expected_columns(self, test_engine: Engine) -> None:
        inspector = inspect(test_engine)

        assert "documents" in inspector.get_table_names()
        columns = {c["name"] for c in inspector.get_columns("documents")}
        assert {
            "id",
            "kind",
            "label",
            "original_filename",
            "content_hash",
            "stored_path",
            "content_type",
            "size_bytes",
            "notes",
            "created_at",
            "archived_at",
        } <= columns

    def test_content_hash_is_unique(self, test_engine: Engine) -> None:
        inspector = inspect(test_engine)
        unique_columns = [
            tuple(c["column_names"]) for c in inspector.get_unique_constraints("documents")
        ]

        assert ("content_hash",) in unique_columns

    def test_new_foreign_key_columns_were_added(self, test_engine: Engine) -> None:
        inspector = inspect(test_engine)

        application_columns = {c["name"] for c in inspector.get_columns("applications")}
        event_columns = {c["name"] for c in inspector.get_columns("application_events")}

        assert "submitted_cv_document_id" in application_columns
        assert "document_id" in event_columns

    def test_foreign_keys_point_at_documents_with_restrict(self, test_engine: Engine) -> None:
        inspector = inspect(test_engine)

        application_fks = [
            fk
            for fk in inspector.get_foreign_keys("applications")
            if fk["referred_table"] == "documents"
        ]
        assert len(application_fks) == 1
        assert application_fks[0]["options"].get("ondelete") == "RESTRICT"

    def test_submitted_cv_column_is_nullable(self, test_engine: Engine) -> None:
        """Nullable is what made this migration safe to apply to existing rows."""
        inspector = inspect(test_engine)
        column = next(
            c
            for c in inspector.get_columns("applications")
            if c["name"] == "submitted_cv_document_id"
        )

        assert column["nullable"] is True
