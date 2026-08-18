"""HTTP-level tests for the document library."""

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import requires_database

pytestmark = requires_database

PDF_BYTES = b"%PDF-1.4 pretend this is a real CV\n%%EOF"
OTHER_BYTES = b"%PDF-1.4 a completely different CV\n%%EOF"


def upload(
    client: TestClient,
    *,
    content: bytes = PDF_BYTES,
    filename: str = "cv.pdf",
    kind: str = "cv",
    label: str | None = None,
) -> tuple[dict, int]:
    data = {"kind": kind}
    if label is not None:
        data["label"] = label

    response = client.post(
        "/documents",
        files={"file": (filename, content, "application/pdf")},
        data=data,
    )
    return response.json(), response.status_code


class TestUpload:
    def test_successful_upload_returns_201_with_metadata(self, client: TestClient) -> None:
        body, code = upload(client, label="Fullstack CV v3")

        assert code == 201
        assert body["kind"] == "cv"
        assert body["label"] == "Fullstack CV v3"
        assert body["original_filename"] == "cv.pdf"
        assert body["size_bytes"] == len(PDF_BYTES)
        assert len(body["content_hash"]) == 64

    def test_stored_path_is_not_exposed(self, client: TestClient) -> None:
        """Filesystem layout is an internal detail, not part of the API."""
        body, _ = upload(client)

        assert "stored_path" not in body

    def test_file_lands_in_storage_named_by_hash(
        self, client: TestClient, temporary_document_storage: Path
    ) -> None:
        body, _ = upload(client)

        stored = list(temporary_document_storage.iterdir())
        assert len(stored) == 1
        assert stored[0].name.startswith(body["content_hash"])
        assert stored[0].read_bytes() == PDF_BYTES

    def test_every_document_kind_is_accepted(self, client: TestClient) -> None:
        for index, kind in enumerate(["cv", "cover_letter", "take_home", "portfolio", "other"]):
            body, code = upload(client, content=f"unique content {index}".encode(), kind=kind)
            assert code == 201, kind
            assert body["kind"] == kind

    def test_unknown_kind_is_rejected(self, client: TestClient) -> None:
        _, code = upload(client, kind="not_a_kind")
        assert code == 422

    def test_empty_file_is_rejected(self, client: TestClient) -> None:
        _, code = upload(client, content=b"")
        assert code == 422

    def test_oversized_file_is_rejected_with_413(self, client: TestClient) -> None:
        from app.config import settings

        oversized = b"x" * (settings.max_document_bytes + 1)
        _, code = upload(client, content=oversized)

        assert code == 413

    def test_dangerous_filename_does_not_reach_the_filesystem(
        self, client: TestClient, temporary_document_storage: Path
    ) -> None:
        body, code = upload(client, filename="../../../evil.pdf")

        assert code == 201
        stored = list(temporary_document_storage.iterdir())
        assert len(stored) == 1
        # Named by hash; the traversal attempt survives only as display metadata.
        assert stored[0].name.startswith(body["content_hash"])
        assert temporary_document_storage.resolve() in stored[0].resolve().parents


class TestDeduplication:
    def test_identical_bytes_reuse_the_existing_document(self, client: TestClient) -> None:
        first, first_code = upload(client)
        second, second_code = upload(client)

        assert first_code == 201  # created
        assert second_code == 200  # already had it
        assert first["id"] == second["id"]
        assert first["content_hash"] == second["content_hash"]

    def test_identical_bytes_do_not_write_a_second_file(
        self, client: TestClient, temporary_document_storage: Path
    ) -> None:
        upload(client, filename="cv.pdf")
        upload(client, filename="cv_copy.pdf")

        assert len(list(temporary_document_storage.iterdir())) == 1

    def test_deduplication_ignores_the_filename(self, client: TestClient) -> None:
        """Same bytes are the same document, whatever they were called."""
        first, _ = upload(client, filename="cv.pdf")
        second, _ = upload(client, filename="totally_different_name.pdf")

        assert first["id"] == second["id"]

    def test_same_filename_different_contents_creates_two_documents(
        self, client: TestClient, temporary_document_storage: Path
    ) -> None:
        """An edited CV is a NEW document — the point of immutability."""
        first, first_code = upload(client, content=PDF_BYTES, filename="cv.pdf")
        second, second_code = upload(client, content=OTHER_BYTES, filename="cv.pdf")

        assert first_code == 201
        assert second_code == 201
        assert first["id"] != second["id"]
        assert first["content_hash"] != second["content_hash"]
        assert len(list(temporary_document_storage.iterdir())) == 2

    def test_only_one_row_exists_after_repeated_uploads(self, client: TestClient) -> None:
        for _ in range(3):
            upload(client)

        assert len(client.get("/documents").json()) == 1


class TestReadAndList:
    def test_get_metadata(self, client: TestClient) -> None:
        created, _ = upload(client)

        response = client.get(f"/documents/{created['id']}")

        assert response.status_code == 200
        assert response.json()["id"] == created["id"]

    def test_unknown_document_returns_404(self, client: TestClient) -> None:
        assert client.get("/documents/999999").status_code == 404

    def test_list_filters_by_kind(self, client: TestClient) -> None:
        upload(client, content=b"a cv", kind="cv")
        upload(client, content=b"a cover letter", kind="cover_letter")

        cvs = client.get("/documents", params={"kind": "cv"}).json()

        assert len(cvs) == 1
        assert cvs[0]["kind"] == "cv"


class TestDownload:
    def test_returns_the_exact_bytes(self, client: TestClient) -> None:
        created, _ = upload(client)

        response = client.get(f"/documents/{created['id']}/download")

        assert response.status_code == 200
        assert response.content == PDF_BYTES

    def test_served_as_an_attachment(self, client: TestClient) -> None:
        """Never inline: a stored HTML/SVG file served inline is an XSS bug."""
        created, _ = upload(client)

        response = client.get(f"/documents/{created['id']}/download")

        assert "attachment" in response.headers["content-disposition"]
        assert "cv.pdf" in response.headers["content-disposition"]

    def test_download_of_unknown_document_returns_404(self, client: TestClient) -> None:
        assert client.get("/documents/999999/download").status_code == 404

    def test_missing_file_produces_a_controlled_error(
        self, client: TestClient, temporary_document_storage: Path
    ) -> None:
        """Metadata without bytes must not be an unhandled exception."""
        created, _ = upload(client)
        for stored_file in temporary_document_storage.iterdir():
            stored_file.unlink()

        response = client.get(f"/documents/{created['id']}/download")

        assert response.status_code == 500
        assert "missing from storage" in response.json()["detail"]
