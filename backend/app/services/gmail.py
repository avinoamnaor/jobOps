"""Gmail read-only sync.

Phase 6.1 scope, deliberately narrow: authenticate -> read -> parse ->
deduplicate -> store. This module never matches an application, creates a
Suggestion, changes a status, runs a keyword rule, or calls Claude/AI — those
are later phases, built on top of the rows this one stores.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import EmailMessageNotFound, GmailSyncFailed
from app.core.gmail_auth import load_credentials
from app.core.gmail_parse import direction_from_labels, parse_gmail_message
from app.enums import EmailDirection
from app.models.email_message import EmailMessage


class GmailMessages(Protocol):
    """The two Gmail calls a sync needs.

    Isolated as a small structural interface so `sync_recent_messages` can be
    tested with a fake implementation — never touching googleapiclient's dynamic
    resource objects, a real Gmail account, or the network in automated tests.
    """

    def list_message_ids(self, *, query: str, max_results: int) -> list[str]: ...

    def get_message(self, message_id: str) -> dict[str, Any]: ...


class GmailClient:
    """Thin wrapper around the real Gmail API resource, implementing `GmailMessages`."""

    def __init__(self, resource: Any) -> None:
        self._resource = resource

    @classmethod
    def from_stored_credentials(cls) -> "GmailClient":
        """Build a client from the token `scripts/gmail_authorize.py` produced.

        Raises `GmailNotConnected` (via `load_credentials`) if that script has
        never been run. Imports the Google client lazily so nothing outside this
        method needs the library installed just to import this module.
        """
        from googleapiclient.discovery import build

        credentials = load_credentials()
        resource = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        return cls(resource)

    def list_message_ids(self, *, query: str, max_results: int) -> list[str]:
        ids: list[str] = []
        try:
            request = (
                self._resource.users()
                .messages()
                .list(userId="me", q=query, maxResults=min(max_results, 500))
            )
            while request is not None and len(ids) < max_results:
                response = request.execute()
                ids.extend(item["id"] for item in response.get("messages", []))
                request = self._resource.users().messages().list_next(request, response)
        except Exception as exc:
            # Any client/network failure becomes this one error type, so the
            # router has a single thing to translate into an HTTP response.
            raise GmailSyncFailed(str(exc)) from exc
        return ids[:max_results]

    def get_message(self, message_id: str) -> dict[str, Any]:
        try:
            return (
                self._resource.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
        except Exception as exc:
            raise GmailSyncFailed(str(exc)) from exc


@dataclass
class GmailSyncResult:
    fetched: int
    imported: int
    already_existing: int
    # Already-stored rows whose `direction` was filled in on this run. Counted
    # separately from `imported` because no new row was created — without it,
    # a sync that repaired 200 rows would report as doing nothing at all.
    enriched: int = 0


def sync_recent_messages(
    db: Session,
    gmail: GmailMessages,
    *,
    window_days: int | None = None,
    max_messages: int | None = None,
) -> GmailSyncResult:
    """Fetch, parse, deduplicate and store recent Gmail messages.

    Scope, deliberately: this function does not look at any `Application`, does
    not write a `Suggestion`, does not run a keyword rule, and does not call an
    LLM. It only asks "have I already imported this Gmail message id?" and, if
    not, stores the parsed row — the entry point every later matching/AI phase
    will read from, not touch.

    Each new row is committed as it is written (rather than one large
    transaction at the end), so an interrupted sync keeps whatever it already
    imported instead of losing all progress.
    """
    window = window_days if window_days is not None else settings.gmail_sync_window_days
    limit = max_messages if max_messages is not None else settings.gmail_max_messages_per_sync

    message_ids = gmail.list_message_ids(query=f"newer_than:{window}d", max_results=limit)

    imported = 0
    already_existing = 0
    enriched = 0

    for message_id in message_ids:
        existing = db.execute(
            select(EmailMessage).where(EmailMessage.gmail_message_id == message_id)
        ).scalar_one_or_none()

        if existing is not None:
            already_existing += 1
            if _enrich_direction(db, existing, gmail):
                enriched += 1
            continue

        raw_message = gmail.get_message(message_id)
        parsed = parse_gmail_message(raw_message)

        db.add(EmailMessage(**parsed))
        db.commit()
        imported += 1

    return GmailSyncResult(
        fetched=len(message_ids),
        imported=imported,
        already_existing=already_existing,
        enriched=enriched,
    )


def _enrich_direction(db: Session, existing: EmailMessage, gmail: GmailMessages) -> bool:
    """Fill in a stored message's `direction` if it is still unknown.

    Rows imported before `direction` existed all read `unknown`, and plain
    dedupe would leave them that way forever — the message is already stored, so
    nothing would ever look at it again. This backfills them opportunistically:
    whenever a later sync happens to see one of those messages again, it costs
    one extra API call to learn what the migration could not.

    Deliberately narrow, in three ways:

      * Only `unknown` rows are touched. A row that already has a direction is
        never re-fetched and never reassigned, so a known value cannot be
        degraded — the row is not even a candidate.
      * Only the `direction` column is written. Subject, body and timestamps are
        left exactly as imported; this repairs metadata, it does not re-import
        content.
      * A re-fetch that yields no evidence changes nothing. `unknown` -> `unknown`
        is not a write and is not counted, so a degenerate response cannot make
        the row look freshly confirmed.

    Returns True only when a value was actually written.
    """
    if existing.direction != EmailDirection.UNKNOWN.value:
        return False

    raw_message = gmail.get_message(existing.gmail_message_id)
    direction = direction_from_labels(raw_message.get("labelIds"))
    if direction == EmailDirection.UNKNOWN:
        return False

    existing.direction = direction.value
    db.commit()
    return True


# --- Read-only inspection ---------------------------------------------------
# Everything below reads ONLY from `email_messages`. Neither function ever
# constructs a GmailClient or makes a Gmail API call — inspecting what has
# already been imported must not depend on Gmail being reachable at all.


def list_stored_messages(
    db: Session, *, limit: int = 50, offset: int = 0
) -> tuple[Sequence[EmailMessage], int]:
    """List stored messages, newest first, with the total match count.

    Mirrors `services.applications.list_applications`'s pagination shape
    (items + total), but with `limit`/`offset` rather than `page`/`page_size` —
    the natural pair for a message inbox that may be scrolled rather than paged.
    """
    total = db.execute(select(func.count()).select_from(EmailMessage)).scalar_one()

    stmt = (
        select(EmailMessage)
        # `id` is a tiebreaker: two messages can share a `received_at` second,
        # and without one, offset pagination could repeat or skip a row.
        .order_by(EmailMessage.received_at.desc(), EmailMessage.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return db.execute(stmt).scalars().all(), total


def get_stored_message(db: Session, message_id: int) -> EmailMessage:
    """Retrieve one stored message by its JobOps id (not the Gmail message id)."""
    message = db.execute(
        select(EmailMessage).where(EmailMessage.id == message_id)
    ).scalar_one_or_none()
    if message is None:
        raise EmailMessageNotFound(message_id)
    return message
