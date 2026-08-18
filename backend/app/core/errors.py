"""Domain errors.

The service layer raises these; `app/main.py` translates them into HTTP status
codes. Services deliberately know nothing about HTTP — that way the same
functions can later be called by the Gmail job or a CLI script without dragging
FastAPI along, and the rules live in one place regardless of who calls them.
"""


class JobOpsError(Exception):
    """Base class for every error this application raises on purpose."""


class ApplicationNotFound(JobOpsError):
    def __init__(self, application_id: int) -> None:
        super().__init__(f"Application {application_id} does not exist")
        self.application_id = application_id


class StatusUnchanged(JobOpsError):
    """Raised when a status change would be a no-op.

    Writing a `status_changed` event from X to X would add a line to the history
    that records nothing happening, so we refuse instead.
    """

    def __init__(self, status: str) -> None:
        super().__init__(f"Application is already in status '{status}'")
        self.status = status


class DocumentNotFound(JobOpsError):
    def __init__(self, document_id: int) -> None:
        super().__init__(f"Document {document_id} does not exist")
        self.document_id = document_id


class DocumentTooLarge(JobOpsError):
    def __init__(self, size_bytes: int, limit_bytes: int) -> None:
        super().__init__(
            f"Document is {size_bytes} bytes, which exceeds the {limit_bytes} byte limit"
        )
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes


class EmptyDocument(JobOpsError):
    def __init__(self) -> None:
        super().__init__("Uploaded file is empty")


class DocumentFileMissing(JobOpsError):
    """The database row exists but the bytes are gone from disk.

    A genuine server-side integrity problem — surfaced as a controlled error with
    an actionable message rather than an unhandled exception and a stack trace.
    """

    def __init__(self, document_id: int, stored_path: str) -> None:
        super().__init__(
            f"Document {document_id} is recorded at '{stored_path}' "
            "but that file is missing from storage"
        )
        self.document_id = document_id
        self.stored_path = stored_path


class UnsafeDocumentPath(JobOpsError):
    """A stored path would resolve outside DOCUMENTS_ROOT.

    We generate every stored path ourselves from a content hash, so this should
    be unreachable. It exists because "should be unreachable" is not a security
    control: if a row is ever edited by hand or corrupted, this refuses to read
    an arbitrary file off the disk.
    """

    def __init__(self, stored_path: str) -> None:
        super().__init__(f"Refusing to access '{stored_path}': resolves outside the document root")
        self.stored_path = stored_path


class DocumentKindNotAllowed(JobOpsError):
    """A document was used somewhere its kind does not belong."""

    def __init__(self, document_id: int, actual_kind: str, expected_kind: str) -> None:
        super().__init__(
            f"Document {document_id} has kind '{actual_kind}', but '{expected_kind}' is required"
        )
        self.document_id = document_id
        self.actual_kind = actual_kind
        self.expected_kind = expected_kind


class SubmittedCvUnchanged(JobOpsError):
    def __init__(self, document_id: int) -> None:
        super().__init__(f"Document {document_id} is already the submitted CV")
        self.document_id = document_id
