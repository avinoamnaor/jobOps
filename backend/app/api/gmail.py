"""HTTP layer for the Gmail integration.

Phase 6.1: sync, plus a small read-only inspection layer over what has already
been imported. Authenticate, read, parse, deduplicate, store — nothing about an
application or a suggestion happens here; see `services/gmail.py`.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.gmail_auth import describe_local_status
from app.db import get_db
from app.schemas.gmail import (
    EmailMessageDetail,
    EmailMessagePage,
    EmailMessageSummary,
    GmailStatus,
    GmailSyncResponse,
)
from app.services.gmail import (
    GmailClient,
    get_stored_message,
    list_stored_messages,
    sync_recent_messages,
)

router = APIRouter(prefix="/integrations/gmail", tags=["gmail"])


@router.post("/sync", response_model=GmailSyncResponse)
def sync_gmail(
    window_days: int | None = Query(default=None, ge=1, le=365),
    max_messages: int | None = Query(default=None, ge=1, le=500),
    db: Session = Depends(get_db),
) -> object:
    """Read-only sync: fetch recent messages, parse, deduplicate, store.

    Never matches an application, creates a suggestion, changes a status, runs a
    keyword rule, or calls an LLM — this endpoint only populates
    `email_messages`. Raises a controlled error (mapped to a clear HTTP status
    in `main.py`) if Gmail has never been connected, or if the API call itself
    fails.
    """
    client = GmailClient.from_stored_credentials()
    result = sync_recent_messages(db, client, window_days=window_days, max_messages=max_messages)
    return GmailSyncResponse(
        fetched=result.fetched,
        imported=result.imported,
        already_existing=result.already_existing,
    )


@router.get("/status", response_model=GmailStatus)
def gmail_status() -> object:
    """Whether the integration looks ready for authorization/sync.

    Purely local: reads only whether the configured files exist and, if a token
    does, whether it looks usable — never a Gmail API call. Never returns token
    contents, OAuth secrets, credential JSON, or a filesystem path.
    """
    return GmailStatus(**describe_local_status())


@router.get("/messages", response_model=EmailMessagePage)
def list_gmail_messages(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> object:
    """List stored messages, newest first. Reads only the local database.

    The body is deliberately not included per row — the same reasoning as
    `documents.stored_path` being left out of `DocumentRead`: a list is for
    scanning, and the full text belongs on the detail view.
    """
    items, total = list_stored_messages(db, limit=limit, offset=offset)
    return EmailMessagePage(
        items=[EmailMessageSummary.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/messages/{message_id}", response_model=EmailMessageDetail)
def get_gmail_message(message_id: int, db: Session = Depends(get_db)) -> object:
    """Retrieve one stored message, including its body.

    `message_id` is JobOps's own id (the `id` field from the list response),
    not Gmail's `gmail_message_id`. Reads only the local database — never a
    Gmail API call — and returns the project's normal 404 if it does not exist.
    """
    return get_stored_message(db, message_id)
