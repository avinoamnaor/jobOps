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


class ExportRequiresSubmittedCv(JobOpsError):
    """A folder export was requested for an application with no submitted CV."""

    def __init__(self, application_id: int) -> None:
        super().__init__(
            f"Application {application_id} has no submitted CV to export. "
            "Attach the CV you sent first."
        )
        self.application_id = application_id


class ApplicationExportFailed(JobOpsError):
    """The local folder/CV export could not be written.

    The application itself is already saved — this concerns only the convenience
    copy on disk, and is safe to retry.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(f"Could not prepare the application folder: {detail}")
        self.detail = detail


class FolderOpenFailed(JobOpsError):
    """The local folder could not be opened in the OS file manager."""

    def __init__(self, path: str, detail: str) -> None:
        super().__init__(f"Could not open the folder '{path}': {detail}")
        self.path = path
        self.detail = detail


class SubmittedCvRequired(JobOpsError):
    """A submitted-state status was requested without a submitted CV attached.

    A `saved` application may have no CV, but once it counts as submitted
    (applied and beyond) it must record which CV was actually sent.
    """

    def __init__(self, status: str, *, on_create: bool = False) -> None:
        if on_create:
            message = (
                f"An application cannot be created with status '{status}' without a "
                "submitted CV. Create it as 'saved', attach the CV you sent, then "
                "update the status."
            )
        else:
            message = (
                f"Status '{status}' means the application was submitted, so a submitted "
                "CV is required. Attach the CV you sent, then change the status."
            )
        super().__init__(message)
        self.status = status
        self.on_create = on_create


class GmailNotConnected(JobOpsError):
    """No authorized Gmail token is available.

    The running API server never opens a browser or prompts for login itself —
    that happens once, manually, via `scripts/gmail_authorize.py`. This error is
    what a sync attempt gets before that has ever been run (or if the token was
    revoked).
    """

    def __init__(self) -> None:
        super().__init__(
            "Gmail is not connected. Run `python scripts/gmail_authorize.py` from "
            "the backend directory once to authorize read-only access, then try "
            "the sync again."
        )


class GmailSyncFailed(JobOpsError):
    """A call to the Gmail API itself failed (network, quota, revoked access, …).

    Distinct from GmailNotConnected: this means we HAD a token and the API call
    still failed, so the message should point at the underlying cause rather
    than at re-running authorization.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(f"Gmail sync failed: {detail}")
        self.detail = detail


class EmailMessageNotFound(JobOpsError):
    def __init__(self, message_id: int) -> None:
        super().__init__(f"Email message {message_id} does not exist")
        self.message_id = message_id


class EmailClassifierNotConfigured(JobOpsError):
    """No API key is configured for the selected email-classifier provider.

    Deliberately parallel to `GmailNotConnected`: a setup problem, not a
    provider failure, so the message points at the fix rather than at a cause.

    Names the provider and the variable to set, because with more than one
    provider available "the classifier is not configured" is no longer enough
    to act on — the likeliest cause is switching provider and forgetting the
    matching key. Never includes the key, the configured value, or any part of
    one.
    """

    def __init__(self, provider: str, env_var: str) -> None:
        super().__init__(
            f"The email classifier is not configured for provider '{provider}'. "
            f"Set {env_var} in your local .env (it is gitignored) and restart "
            "the process, then try again."
        )
        self.provider = provider
        self.env_var = env_var


class EmailClassificationFailed(JobOpsError):
    """The classification request itself failed.

    Covers everything that can go wrong on the provider side — network error,
    timeout, rate limit, refusal, truncated response, a reply that does not
    satisfy the schema. One domain error type for all of it, so callers never
    see a raw SDK exception and never need the SDK installed to catch it.

    Distinct from a semantic `irrelevant` verdict: this means we do not know
    what the message is, not that we decided it is nothing.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(f"Email classification failed: {detail}")
        self.detail = detail


class EmailClassificationNotGrounded(JobOpsError):
    """The model returned evidence that does not appear in the source message.

    A schema-valid response is not automatically a trustworthy one. Fabricated
    support is worse than none, because it reads as corroboration — so the
    classification is rejected outright rather than quietly stripped of its bad
    excerpts and returned as if it had been verified.
    """

    def __init__(self, excerpts: list[str]) -> None:
        joined = "; ".join(repr(excerpt) for excerpt in excerpts)
        super().__init__(
            f"Email classification rejected: {len(excerpts)} evidence excerpt(s) "
            f"do not appear in the source message: {joined}"
        )
        self.excerpts = excerpts


class OutgoingMessageNotClassifiable(JobOpsError):
    """Classification was attempted on a message the account owner wrote.

    Outgoing mail is excluded by policy (see `EmailDirection`): the user's own
    words are evidence about the user, not about an employer's decision. Raised
    before any API call, so a mistake here costs nothing.
    """

    def __init__(self) -> None:
        super().__init__(
            "Outgoing messages are not classified: the text is the account "
            "owner's own writing, not an employer's statement."
        )


class SuggestionNotFound(JobOpsError):
    def __init__(self, suggestion_id: int) -> None:
        super().__init__(f"Suggestion {suggestion_id} does not exist")
        self.suggestion_id = suggestion_id


class SuggestionAlreadyResolved(JobOpsError):
    """Accept/reject was attempted on a suggestion that is no longer pending.

    A resolved suggestion (accepted or rejected) is final — it cannot be
    processed again, which is what makes "resolved" a meaningful guarantee
    rather than just a label.
    """

    def __init__(self, suggestion_id: int, state: str) -> None:
        super().__init__(
            f"Suggestion {suggestion_id} is already '{state}' and cannot be processed again"
        )
        self.suggestion_id = suggestion_id
        self.state = state
