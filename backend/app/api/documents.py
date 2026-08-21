"""HTTP layer for the document library."""

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.storage import safe_header_filename
from app.db import get_db
from app.enums import DocumentKind
from app.schemas.document import DocumentRead
from app.services import documents as document_service

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", response_model=DocumentRead)
async def upload_document(
    response: Response,
    file: UploadFile = File(...),
    kind: DocumentKind = Form(...),
    label: str | None = Form(default=None),
    notes: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> object:
    """Upload a file into the library.

    Returns 201 when the bytes are new, 200 when an identical file was already
    stored — so the caller can tell "saved" from "you already had this" rather
    than guessing.

    This handler is `async` because reading an upload is async in Starlette; the
    service call underneath is ordinary synchronous code.
    """
    content = await file.read()

    document, was_created = document_service.store_document(
        db,
        kind=kind,
        content=content,
        original_filename=file.filename,
        content_type=file.content_type,
        label=label,
        notes=notes,
    )

    response.status_code = status.HTTP_201_CREATED if was_created else status.HTTP_200_OK
    return document


@router.get("", response_model=list[DocumentRead])
def list_documents(
    db: Session = Depends(get_db),
    kind: DocumentKind | None = Query(default=None),
    include_archived: bool = Query(default=False),
) -> object:
    return document_service.list_documents(db, kind=kind, include_archived=include_archived)


@router.get("/{document_id}", response_model=DocumentRead)
def get_document(document_id: int, db: Session = Depends(get_db)) -> object:
    return document_service.get_document(db, document_id)


@router.get("/{document_id}/download")
def download_document(
    document_id: int,
    submission: bool = Query(
        default=False,
        description="Serve with a clean, employer-facing submission filename.",
    ),
    db: Session = Depends(get_db),
) -> FileResponse:
    """Send the file's bytes.

    With `?submission=true` the exact same bytes are served under a clean,
    employer-facing filename (see SUBMISSION_CV_FILENAME) — a download-only
    convenience. The stored document is never renamed or modified.

    Always served as an attachment. Serving user-supplied files inline from the
    same origin as the API is how a stored HTML or SVG file turns into a
    cross-site scripting bug; a download has no such problem. Inline preview can
    be added deliberately, for a narrow allowlist of types, when the UI needs it.
    """
    document = document_service.get_document(db, document_id)
    path = document_service.resolve_document_file(document)

    if submission:
        download_name = document_service.submission_filename(document)
    else:
        # Sanitised so a filename containing quotes or newlines cannot inject
        # extra HTTP headers.
        download_name = safe_header_filename(
            document.original_filename, fallback=f"document-{document.id}"
        )

    return FileResponse(
        path=path,
        media_type=document.content_type or "application/octet-stream",
        filename=download_name,
    )
