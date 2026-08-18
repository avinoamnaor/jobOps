"""Document library business logic."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import DocumentFileMissing, DocumentNotFound, DocumentTooLarge, EmptyDocument
from app.core.storage import DocumentStorage, compute_content_hash, document_storage
from app.enums import DocumentKind
from app.models.document import Document


def store_document(
    db: Session,
    *,
    kind: DocumentKind,
    content: bytes,
    original_filename: str | None = None,
    content_type: str | None = None,
    label: str | None = None,
    notes: str | None = None,
    storage: DocumentStorage = document_storage,
) -> tuple[Document, bool]:
    """Store bytes and return (document, was_created).

    `was_created` is False when the exact same bytes were already in the library,
    in which case the existing row is returned untouched and nothing is written.
    The router turns that into 200 instead of 201, so the caller can tell the
    difference between "stored" and "already had this".

    Ordering note — the file is written BEFORE the database row is committed.
    That ordering is deliberate, and it is the safer of the two failure modes:

      * file written, commit fails  -> an unreferenced file sits in storage.
        Harmless: it is named by its own hash, so the next upload of the same
        content simply reuses it.
      * row committed, write fails  -> a document that the UI offers for download
        but which does not exist. A real defect.

    We take the harmless failure.
    """
    if not content:
        raise EmptyDocument
    if len(content) > settings.max_document_bytes:
        raise DocumentTooLarge(len(content), settings.max_document_bytes)

    content_hash = compute_content_hash(content)

    # Deduplication. The UNIQUE constraint on content_hash is the real guarantee;
    # this lookup is what turns it into a friendly response instead of an error.
    existing = db.execute(
        select(Document).where(Document.content_hash == content_hash)
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    relative_path = storage.build_relative_path(content_hash, original_filename)
    storage.write(relative_path, content)

    document = Document(
        kind=kind.value,
        label=label,
        # Stored for display only. Truncated because the column is bounded and a
        # 4000-character filename is not worth rejecting an upload over.
        original_filename=(original_filename or None) and original_filename[:255],
        content_hash=content_hash,
        stored_path=relative_path,
        content_type=content_type,
        size_bytes=len(content),
        notes=notes,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document, True


def get_document(db: Session, document_id: int) -> Document:
    document = db.execute(
        select(Document).where(Document.id == document_id)
    ).scalar_one_or_none()
    if document is None:
        raise DocumentNotFound(document_id)
    return document


def list_documents(
    db: Session,
    *,
    kind: DocumentKind | None = None,
    include_archived: bool = False,
) -> Sequence[Document]:
    filters = []
    if kind is not None:
        filters.append(Document.kind == kind.value)
    if not include_archived:
        filters.append(Document.archived_at.is_(None))

    stmt = select(Document).where(*filters).order_by(Document.created_at.desc(), Document.id.desc())
    return db.execute(stmt).scalars().all()


def resolve_document_file(
    document: Document,
    *,
    storage: DocumentStorage = document_storage,
) -> object:
    """Absolute path to a document's bytes, or a controlled error.

    Raises `DocumentFileMissing` rather than letting a FileNotFoundError escape,
    so a missing file produces an actionable message instead of a stack trace.
    `storage.resolve` independently guarantees the path stays inside
    DOCUMENTS_ROOT.
    """
    path = storage.resolve(document.stored_path)
    if not path.is_file():
        raise DocumentFileMissing(document.id, document.stored_path)
    return path
