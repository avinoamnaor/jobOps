"""Unit tests for content-addressed storage and path safety.

No database, no HTTP. The path-safety tests are the important ones here: they are
the difference between "we generate safe paths" and "we cannot be made to read an
arbitrary file".
"""

from pathlib import Path

import pytest

from app.core.errors import UnsafeDocumentPath
from app.core.storage import (
    DocumentStorage,
    compute_content_hash,
    safe_extension,
    safe_header_filename,
    sanitize_windows_component,
)


class TestSanitizeWindowsComponent:
    def test_keeps_a_normal_company_role_name(self) -> None:
        assert (
            sanitize_windows_component("Harmonic - Junior SW Development Engineer")
            == "Harmonic - Junior SW Development Engineer"
        )

    @pytest.mark.parametrize("bad", '<>:"/\\|?*')
    def test_removes_windows_invalid_characters(self, bad: str) -> None:
        result = sanitize_windows_component(f"Ac{bad}me - Dev{bad}Role")
        assert bad not in result
        # Never becomes a path separator or escapes its component.
        assert "/" not in result and "\\" not in result

    def test_collapses_whitespace_left_by_removed_characters(self) -> None:
        assert sanitize_windows_component("R&D:  Backend / Infra") == "R&D Backend Infra"

    def test_strips_trailing_dots_and_spaces(self) -> None:
        # Windows silently drops these, which would desync the folder name.
        assert sanitize_windows_component("Acme Corp. ") == "Acme Corp"

    def test_control_characters_are_removed(self) -> None:
        assert sanitize_windows_component("Ac\x00me\t- Role") == "Ac me - Role"

    @pytest.mark.parametrize("reserved", ["CON", "con", "PRN", "nul", "COM1", "LPT9"])
    def test_reserved_device_names_are_escaped(self, reserved: str) -> None:
        result = sanitize_windows_component(reserved)
        assert result.startswith("_")

    def test_empty_after_cleaning_uses_fallback(self) -> None:
        assert sanitize_windows_component("///", fallback="untitled") == "untitled"

    def test_length_is_bounded(self) -> None:
        result = sanitize_windows_component("A" * 500, max_length=120)
        assert len(result) <= 120


class TestContentHash:
    def test_identical_bytes_hash_identically(self) -> None:
        assert compute_content_hash(b"same") == compute_content_hash(b"same")

    def test_different_bytes_hash_differently(self) -> None:
        assert compute_content_hash(b"one") != compute_content_hash(b"two")

    def test_is_a_sha256_hex_digest(self) -> None:
        digest = compute_content_hash(b"")
        assert len(digest) == 64
        # The well-known SHA-256 of the empty string.
        assert digest == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class TestSafeExtension:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("cv.pdf", ".pdf"),
            ("Applicant_CV.PDF", ".pdf"),
            ("resume.docx", ".docx"),
            ("archive.tar.gz", ".gz"),
            ("no_extension", ""),
            (None, ""),
            ("", ""),
        ],
    )
    def test_extracts_conservatively(self, filename: str | None, expected: str) -> None:
        assert safe_extension(filename) == expected

    @pytest.mark.parametrize(
        "filename",
        [
            "../../etc/passwd",
            "..\\..\\windows\\system32\\config\\sam",
            "/etc/shadow",
            "cv.pdf/../../evil",
            "cv.<script>",
            "cv." + "a" * 50,  # implausibly long extension
        ],
    )
    def test_never_returns_anything_dangerous(self, filename: str) -> None:
        result = safe_extension(filename)
        assert "/" not in result
        assert "\\" not in result
        assert ".." not in result


class TestSafeHeaderFilename:
    def test_strips_quotes_and_control_characters(self) -> None:
        dangerous = 'cv".pdf\r\nX-Injected: evil'
        result = safe_header_filename(dangerous, fallback="fallback.pdf")

        assert '"' not in result
        assert "\r" not in result
        assert "\n" not in result

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_falls_back_when_unusable(self, value: str | None) -> None:
        assert safe_header_filename(value, fallback="document-7") == "document-7"


class TestDocumentStoragePathSafety:
    """The security boundary: nothing may resolve outside the root."""

    @pytest.fixture
    def storage(self, tmp_path: Path) -> DocumentStorage:
        # exist_ok because the autouse `temporary_document_storage` fixture has
        # already created this directory for the global singleton.
        root = tmp_path / "documents"
        root.mkdir(exist_ok=True)
        return DocumentStorage(root)

    @pytest.mark.parametrize(
        "malicious_path",
        [
            "../escaped.pdf",
            "../../etc/passwd",
            "subdir/../../escaped.pdf",
            "..\\..\\escaped.pdf",
        ],
    )
    def test_refuses_traversal(self, storage: DocumentStorage, malicious_path: str) -> None:
        with pytest.raises(UnsafeDocumentPath):
            storage.resolve(malicious_path)

    def test_refuses_absolute_paths(self, storage: DocumentStorage) -> None:
        """`root / "/etc/passwd"` returns `/etc/passwd` — joining does not contain.

        This is the subtle pathlib behaviour the containment check exists for.
        """
        absolute = "C:\\Windows\\System32\\drivers\\etc\\hosts" if _on_windows() else "/etc/passwd"

        with pytest.raises(UnsafeDocumentPath):
            storage.resolve(absolute)

    def test_allows_ordinary_relative_paths(self, storage: DocumentStorage) -> None:
        resolved = storage.resolve("abc123.pdf")

        assert resolved.parent == storage.root.resolve()
        assert resolved.name == "abc123.pdf"


class TestDocumentStorageReadWrite:
    @pytest.fixture
    def storage(self, tmp_path: Path) -> DocumentStorage:
        # exist_ok because the autouse `temporary_document_storage` fixture has
        # already created this directory for the global singleton.
        root = tmp_path / "documents"
        root.mkdir(exist_ok=True)
        return DocumentStorage(root)

    def test_round_trips_bytes_exactly(self, storage: DocumentStorage) -> None:
        content = b"%PDF-1.4 fake pdf bytes \x00\xff"
        path = storage.build_relative_path(compute_content_hash(content), "cv.pdf")

        storage.write(path, content)

        assert storage.read(path) == content

    def test_path_is_named_after_the_content_hash(self, storage: DocumentStorage) -> None:
        content = b"hello"
        digest = compute_content_hash(content)

        assert storage.build_relative_path(digest, "cv.pdf") == f"{digest}.pdf"

    def test_uploaded_filename_never_becomes_the_path(self, storage: DocumentStorage) -> None:
        """The whole reason content addressing is safe."""
        content = b"payload"
        path = storage.build_relative_path(compute_content_hash(content), "../../evil.pdf")

        assert ".." not in path
        assert "evil" not in path

    def test_write_is_idempotent(self, storage: DocumentStorage) -> None:
        content = b"same bytes"
        path = storage.build_relative_path(compute_content_hash(content), "a.pdf")

        storage.write(path, content)
        storage.write(path, content)

        assert storage.read(path) == content
        assert len(list(storage.root.iterdir())) == 1

    def test_exists_reports_accurately(self, storage: DocumentStorage) -> None:
        path = storage.build_relative_path(compute_content_hash(b"x"), "x.pdf")

        assert storage.exists(path) is False
        storage.write(path, b"x")
        assert storage.exists(path) is True


def _on_windows() -> bool:
    import sys

    return sys.platform.startswith("win")
