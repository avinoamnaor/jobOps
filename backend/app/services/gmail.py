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
from app.core.gmail_parse import (
    MAX_BODY_TEXT_LENGTH,
    direction_from_labels,
    extract_body,
    parse_gmail_message,
)
from app.enums import (
    BODY_SOURCE_QUALITY,
    UPGRADABLE_BODY_SOURCES,
    EmailBodySource,
    EmailDirection,
)
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
    # Already-stored rows improved on this run — a direction filled in, a Gmail
    # preview replaced by the real body, or both. Counted separately from
    # `imported` because no new row was created; without it, a sync that
    # repaired hundreds of rows would report as doing nothing at all.
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
            if _enrich_existing(db, existing, gmail):
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


def _needs_enrichment(existing: EmailMessage) -> bool:
    """Whether re-fetching this stored message could improve it.

    Two independent gaps qualify: a direction that was never determined, and a
    body that is a Gmail preview (or was recorded before sources were tracked).
    Checked before any API call, so a row with nothing to gain costs nothing.
    """
    if existing.direction == EmailDirection.UNKNOWN.value:
        return True
    return EmailBodySource(existing.body_source) in UPGRADABLE_BODY_SOURCES


def _enrich_existing(db: Session, existing: EmailMessage, gmail: GmailMessages) -> bool:
    """Improve a stored message in place, using one re-fetch for both gaps.

    Enrichment exists because plain dedupe leaves early rows permanently worse
    than later ones: the message is already stored, so nothing looks at it
    again. This backfills opportunistically — when a later sync happens to see
    the message, it costs one API call to learn what the original import could
    not.

    Deliberately narrow, in the same three ways as before:

      * Only rows with something to gain are fetched (see `_needs_enrichment`).
      * Only the fields that improved are written.
      * A re-fetch that yields nothing better changes nothing and is not
        counted, so a degenerate response cannot make a row look freshly
        confirmed.

    Returns True only when something was actually written.
    """
    if not _needs_enrichment(existing):
        return False

    raw_message = gmail.get_message(existing.gmail_message_id)
    changed = False

    if existing.direction == EmailDirection.UNKNOWN.value:
        direction = direction_from_labels(raw_message.get("labelIds"))
        if direction != EmailDirection.UNKNOWN:
            existing.direction = direction.value
            changed = True

    if EmailBodySource(existing.body_source) in UPGRADABLE_BODY_SOURCES:
        changed = _upgrade_body(existing, raw_message) or changed

    if changed:
        db.commit()
    return changed


def _upgrade_body(existing: EmailMessage, raw_message: dict) -> bool:
    """Improve what is known about a stored body — content or provenance.

    Two distinct improvements, deliberately kept apart because they mean
    different things:

    **Content upgrade.** A strictly better source replaces the body outright:
    `plain` beats `html` beats `snippet`, and `unknown` ranks lowest so any
    determined source improves on "never recorded". The comparison is on
    *source*, not length — a longer body is not necessarily a better one, and an
    HTML conversion that swept up a footer should not displace the plain part it
    was rendered from.

    **Provenance upgrade.** `unknown` and `none` both rank zero, so the quality
    test alone can never move between them — yet they are not the same claim.
    `unknown` means nobody has looked; `none` means we looked and this message
    genuinely carries no usable body or snippet. Recording that is a real gain
    in knowledge even though not a byte of text changes.

    That second case writes `body_source` and nothing else, and only when the
    row has no text to contradict it. A row that visibly holds content is never
    relabelled "no body" — the label would be false, and a stored body is
    evidence that outranks a fresh parse disagreeing with it.
    """
    body_text, body_source = extract_body(raw_message)
    if body_text and len(body_text) > MAX_BODY_TEXT_LENGTH:
        body_text = body_text[:MAX_BODY_TEXT_LENGTH]

    stored = EmailBodySource(existing.body_source)

    if BODY_SOURCE_QUALITY[body_source] > BODY_SOURCE_QUALITY[stored]:
        existing.body_text = body_text
        existing.body_source = body_source.value
        return True

    if (
        stored is EmailBodySource.UNKNOWN
        and body_source is EmailBodySource.NONE
        and not (existing.body_text or "").strip()
    ):
        # Metadata only. No text is written, invented, or cleared.
        existing.body_source = EmailBodySource.NONE.value
        return True

    return False


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
