"""Targeted tests for per-application local folder export."""

import pytest
from sqlalchemy.orm import Session

import app.services.export as export_module
from app.core.errors import DocumentKindNotAllowed, ExportRequiresSubmittedCv, FolderOpenFailed
from app.enums import ApplicationStatus, DocumentKind
from app.schemas.application import ApplicationCreate
from app.services.applications import create_application
from app.services.documents import store_document
from app.services.export import (
    application_folder_name,
    export_application_folder,
    export_draft_folder,
    open_application_folder,
    open_draft_folder,
)
from tests.conftest import requires_database

pytestmark = requires_database

CV_BYTES = b"%PDF-1.4 tailored cv bytes\n%%EOF"


@pytest.fixture
def export_root(tmp_path, monkeypatch):
    """Point the export root at a throwaway dir and fix the submission name."""
    from app.config import settings

    root = tmp_path / "Jobs"
    monkeypatch.setattr(settings, "application_export_root", root)
    monkeypatch.setattr(settings, "submission_cv_filename", "CV - Test User.pdf")
    return root


def _app_with_cv(
    db: Session,
    *,
    company: str = "Harmonic",
    role: str = "Junior SW Development Engineer",
) -> object:
    cv, _ = store_document(
        db, kind=DocumentKind.CV, content=CV_BYTES, original_filename="Canva Export.pdf"
    )
    return create_application(
        db,
        ApplicationCreate(
            company_name=company,
            role_title=role,
            status=ApplicationStatus.APPLIED,
            submitted_cv_document_id=cv.id,
        ),
    )


def test_application_folder_name_combines_company_and_role() -> None:
    assert (
        application_folder_name("Harmonic", "Junior SW Development Engineer")
        == "Harmonic - Junior SW Development Engineer"
    )


class TestExportApplicationFolder:
    def test_creates_folder_and_copies_exact_bytes(self, db_session, export_root) -> None:
        application = _app_with_cv(db_session)

        path = export_application_folder(db_session, application.id)

        assert path.parent == export_root / "Harmonic - Junior SW Development Engineer"
        assert path.name == "CV - Test User.pdf"
        assert path.read_bytes() == CV_BYTES  # exact bytes, nothing rewritten

    def test_folder_name_is_sanitized(self, db_session, export_root) -> None:
        application = _app_with_cv(db_session, company="Ac/me", role="Dev: Role")

        folder = export_application_folder(db_session, application.id).parent.name

        assert "/" not in folder
        assert ":" not in folder

    def test_reusing_the_folder_replaces_only_the_cv_file(self, db_session, export_root) -> None:
        application = _app_with_cv(db_session)
        first = export_application_folder(db_session, application.id)

        # An unrelated file in the same folder must survive a re-export.
        (first.parent / "notes.txt").write_text("keep me")

        second = export_application_folder(db_session, application.id)

        assert second == first
        assert (first.parent / "notes.txt").read_text() == "keep me"
        # No "(1)" duplicate folder was created.
        folders = sorted(p.name for p in export_root.iterdir() if p.is_dir())
        assert folders == ["Harmonic - Junior SW Development Engineer"]

    def test_same_cv_two_applications_get_separate_folders(self, db_session, export_root) -> None:
        cv, _ = store_document(
            db_session, kind=DocumentKind.CV, content=CV_BYTES, original_filename="Canva.pdf"
        )
        first = create_application(
            db_session,
            ApplicationCreate(
                company_name="Harmonic",
                role_title="Backend Engineer",
                status=ApplicationStatus.APPLIED,
                submitted_cv_document_id=cv.id,
            ),
        )
        second = create_application(
            db_session,
            ApplicationCreate(
                company_name="Acme",
                role_title="Backend Engineer",
                status=ApplicationStatus.APPLIED,
                submitted_cv_document_id=cv.id,
            ),
        )

        path_first = export_application_folder(db_session, first.id)
        path_second = export_application_folder(db_session, second.id)

        assert path_first.parent != path_second.parent
        assert path_first.read_bytes() == path_second.read_bytes() == CV_BYTES

    def test_export_without_a_submitted_cv_is_refused(self, db_session, export_root) -> None:
        application = create_application(
            db_session,
            ApplicationCreate(company_name="Harmonic", role_title="Engineer"),
        )  # saved, no CV

        with pytest.raises(ExportRequiresSubmittedCv):
            export_application_folder(db_session, application.id)


class TestOpenApplicationFolder:
    """Prepares the folder if needed, then opens it — Explorer launch is mocked."""

    def test_prepares_the_folder_then_opens_it(self, db_session, export_root, monkeypatch) -> None:
        opened: dict[str, object] = {}
        monkeypatch.setattr(export_module, "open_folder", lambda p: opened.setdefault("path", p))
        application = _app_with_cv(db_session)

        cv_path = open_application_folder(db_session, application.id)

        assert cv_path.read_bytes() == CV_BYTES
        # The folder (parent of the CV file) is what gets opened.
        assert opened["path"] == cv_path.parent

    def test_open_without_a_submitted_cv_is_refused(
        self, db_session, export_root, monkeypatch
    ) -> None:
        monkeypatch.setattr(export_module, "open_folder", lambda p: None)
        application = create_application(
            db_session, ApplicationCreate(company_name="Harmonic", role_title="Engineer")
        )

        with pytest.raises(ExportRequiresSubmittedCv):
            open_application_folder(db_session, application.id)

    def test_open_failure_is_a_controlled_error(
        self, db_session, export_root, monkeypatch
    ) -> None:
        def boom(path):
            raise FolderOpenFailed(str(path), "explorer missing")

        monkeypatch.setattr(export_module, "open_folder", boom)
        application = _app_with_cv(db_session)

        with pytest.raises(FolderOpenFailed):
            open_application_folder(db_session, application.id)


class TestDraftFolderExport:
    """Prepare a submission folder from company/role + CV, with NO application row."""

    def _cv(self, db):
        document, _ = store_document(
            db, kind=DocumentKind.CV, content=CV_BYTES, original_filename="Canva.pdf"
        )
        return document

    def test_draft_export_creates_folder_and_copies_bytes(self, db_session, export_root) -> None:
        cv = self._cv(db_session)

        path = export_draft_folder(
            db_session,
            company_name="Harmonic",
            role_title="Junior SW Development Engineer",
            document_id=cv.id,
        )

        assert path.parent == export_root / "Harmonic - Junior SW Development Engineer"
        assert path.read_bytes() == CV_BYTES

    def test_draft_export_rejects_a_non_cv_document(self, db_session, export_root) -> None:
        letter, _ = store_document(
            db_session, kind=DocumentKind.COVER_LETTER, content=b"%PDF letter"
        )
        with pytest.raises(DocumentKindNotAllowed):
            export_draft_folder(
                db_session, company_name="Harmonic", role_title="Engineer", document_id=letter.id
            )

    def test_draft_and_application_export_share_the_same_folder(
        self, db_session, export_root
    ) -> None:
        """The later automatic per-application export writes the same folder."""
        cv = self._cv(db_session)
        draft_path = export_draft_folder(
            db_session, company_name="Harmonic", role_title="Engineer", document_id=cv.id
        )

        application = create_application(
            db_session,
            ApplicationCreate(
                company_name="Harmonic",
                role_title="Engineer",
                status=ApplicationStatus.APPLIED,
                submitted_cv_document_id=cv.id,
            ),
        )
        application_path = export_application_folder(db_session, application.id)

        assert draft_path == application_path  # idempotent, same folder + file

    def test_open_draft_folder_opens_the_prepared_folder(
        self, db_session, export_root, monkeypatch
    ) -> None:
        opened: dict[str, object] = {}
        monkeypatch.setattr(export_module, "open_folder", lambda p: opened.setdefault("path", p))
        cv = self._cv(db_session)

        path = open_draft_folder(
            db_session, company_name="Harmonic", role_title="Engineer", document_id=cv.id
        )

        assert opened["path"] == path.parent
