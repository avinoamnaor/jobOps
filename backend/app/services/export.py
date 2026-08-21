"""Local per-application folder export.

A convenience layer on top of the immutable document store: given a company,
role and CV document, it prepares a folder named after the company and role and
drops a copy of the exact CV bytes inside, named with the configured submission
filename. It writes nothing to the database and never touches the stored document.

Design notes:
  * The folder name is deterministic, so the same company/role reuses the same
    folder (`exist_ok=True`) — never `(1)` suffixes. Only the generated CV file is
    replaced; other files in the folder are left alone.
  * It writes nothing to the database and runs independently of any DB write, so
    an export failure can never leave data in a half-saved state. It is safe to
    retry, and safe to run again on an existing folder (idempotent) — which is why
    the "draft" flow (before an application exists) and the per-application export
    share the SAME filesystem helper and can both write the same folder.
"""

import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import (
    ApplicationExportFailed,
    DocumentKindNotAllowed,
    ExportRequiresSubmittedCv,
)
from app.core.os_reveal import open_folder
from app.core.storage import sanitize_windows_component
from app.enums import DocumentKind
from app.models.document import Document
from app.services.applications import get_application
from app.services.documents import get_document, resolve_document_file, submission_filename


def application_folder_name(company_name: str, role_title: str) -> str:
    """The safe folder component for an application: '<company> - <role>'."""
    return sanitize_windows_component(f"{company_name} - {role_title}")


def _export_cv_to_folder(company_name: str, role_title: str, document: Document) -> Path:
    """Copy a CV document's exact bytes into `<root>/<company> - <role>/`.

    The single low-level filesystem operation shared by the per-application export
    and the pre-application draft export, so both use identical naming,
    sanitization and collision behaviour. Returns the exported CV file's path.
    """
    # Resolves inside the document root and confirms the bytes exist on disk
    # (raises DocumentFileMissing otherwise).
    source = resolve_document_file(document)

    folder = settings.application_export_path / application_folder_name(company_name, role_title)
    export_name = sanitize_windows_component(submission_filename(document), fallback="CV.pdf")

    try:
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / export_name
        # copyfile writes the exact source bytes and overwrites only this file.
        shutil.copyfile(source, destination)
    except OSError as exc:
        raise ApplicationExportFailed(str(exc)) from exc

    return destination


def export_application_folder(db: Session, application_id: int) -> Path:
    """Prepare the application's local folder and copy the submitted CV into it."""
    application = get_application(db, application_id)
    if application.submitted_cv_document_id is None:
        raise ExportRequiresSubmittedCv(application_id)

    document = get_document(db, application.submitted_cv_document_id)
    return _export_cv_to_folder(application.company_name, application.role_title, document)


def open_application_folder(db: Session, application_id: int) -> Path:
    """Prepare (or refresh) the application's folder, then open it in Explorer.

    Idempotent: `export_application_folder` creates the folder if missing and
    always refreshes the CV copy, so a single "Open application folder" action both
    prepares-when-needed and opens. Returns the exported CV path (its parent is the
    folder that was opened).
    """
    destination = export_application_folder(db, application_id)
    open_folder(destination.parent)
    return destination


def export_draft_folder(
    db: Session, *, company_name: str, role_title: str, document_id: int
) -> Path:
    """Prepare a submission folder BEFORE an application row exists.

    Used from the New Application form so a tailored CV can be exported and handed
    to the employer before recording the application. No application row is created
    or required; the selected document remains the source of truth. Because it uses
    the same helper, the later automatic per-application export writes the same
    folder idempotently.
    """
    document = get_document(db, document_id)
    if document.kind != DocumentKind.CV:
        raise DocumentKindNotAllowed(document.id, document.kind, DocumentKind.CV.value)
    return _export_cv_to_folder(company_name, role_title, document)


def open_draft_folder(
    db: Session, *, company_name: str, role_title: str, document_id: int
) -> Path:
    """Prepare the draft submission folder, then open it in Explorer."""
    destination = export_draft_folder(
        db, company_name=company_name, role_title=role_title, document_id=document_id
    )
    open_folder(destination.parent)
    return destination
