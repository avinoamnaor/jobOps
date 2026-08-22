"""Pure parsing of a Gmail API message resource into `email_messages` columns.

No I/O, no Gmail client, no database — these are the cheapest and most valuable
functions in this integration to unit-test, the same reasoning as
`app/core/normalize.py`. Everything here operates on a plain dict shaped like
the JSON Gmail's `users.messages.get` endpoint returns.
"""

import base64
import html
from collections.abc import Iterable
from datetime import UTC, datetime

from app.enums import EmailDirection

# A plain-text email is comfortably a few KB. This is a defensive cap against a
# pathological message (a giant newsletter, a quoted thread), not a realistic
# limit — and it is exactly why we store decoded plain text, never raw MIME.
MAX_BODY_TEXT_LENGTH = 20_000


def decode_base64url(data: str) -> str:
    """Gmail encodes body data as URL-safe base64 without padding."""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def headers_by_name(headers: list[dict]) -> dict[str, str]:
    """Gmail returns headers as a list of {name, value}; index by lowercase name."""
    return {item.get("name", "").lower(): item.get("value", "") for item in headers}


def find_plain_text(payload: dict) -> str | None:
    """Depth-first search for the first `text/plain` part's decoded body.

    A simple message has its body directly on the top-level payload; a
    multipart one (the common case — HTML + plain-text alternatives, or with
    attachments) nests it inside `payload["parts"]`, arbitrarily deep for
    multipart/mixed wrapping multipart/alternative. Recursing handles both.
    """
    if payload.get("mimeType") == "text/plain":
        data = (payload.get("body") or {}).get("data")
        if data:
            return decode_base64url(data)

    for part in payload.get("parts") or []:
        found = find_plain_text(part)
        if found is not None:
            return found

    return None


def normalize_body_text(text: str) -> str:
    """Decode HTML character/entity references (&rsquo; &nbsp; &mdash; …).

    Some senders' "plain text" MIME part is itself a naive HTML-to-text
    conversion that leaves entities undecoded. `html.unescape` covers the full
    HTML5 named/decimal/hex entity set, handles Unicode correctly, and is a
    no-op on text with no entities — so it never touches an already-plain body.
    """
    return html.unescape(text)


# The only Gmail system labels that mean "the account owner wrote this".
#
# Gmail applies both itself, so they are metadata rather than a heuristic. The
# alternative — comparing the `From:` header against the account's own address —
# would mean hardcoding a personal address, and would still be wrong for
# aliases, plus-addressing and delegated sending.
#
# DRAFT is here rather than treated as "no evidence" deliberately. A draft is
# the user's own unsent writing, so it must be excluded from classification for
# exactly the same reason a sent reply is; `unknown` is a *classifiable*
# direction (see CLASSIFIABLE_EMAIL_DIRECTIONS), so mapping drafts there would
# quietly feed the user's own words to the classifier as if an employer had
# written them.
AUTHORED_BY_ACCOUNT_OWNER_LABELS = frozenset({"SENT", "DRAFT"})


def direction_from_labels(label_ids: Iterable[str] | None) -> EmailDirection:
    """Decide who authored a message, from Gmail's own system labels.

    The question this answers is "did I write this, or did someone send it to
    me?" — not "is it in my inbox right now". Those are different questions, and
    conflating them was a real bug: `INBOX` is removed the moment a message is
    archived, so keying `incoming` off it made every archived recruitment email
    read as `unknown`. Archiving after reading is completely ordinary, so that
    mislabelled a large share of exactly the messages this integration exists to
    process.

    The reliable distinction is the other way round. Gmail marks the messages the
    account owner authored — `SENT` for sent mail, `DRAFT` for unsent drafts —
    and everything else in the mailbox arrived from someone else. That holds
    regardless of archiving, starring, spam, trash, or category labels, none of
    which say anything about authorship.

    So:
      * any authored-by-owner label -> `outgoing`
      * any other labels at all     -> `incoming`
      * no labels whatsoever        -> `unknown`

    The last case is the genuine no-evidence one. Gmail returns labels for every
    real message, so an empty set means a degenerate or unexpected response
    shape, and asserting a direction from nothing would be a guess.

    An authored label wins over everything else, including a message the user
    sent to themselves (which carries both `SENT` and `INBOX`). That tie
    resolves toward not treating the user's own writing as an employer's
    statement — the conservative reading, since outgoing mail is excluded from
    classification.
    """
    labels = {str(label) for label in label_ids or ()}
    if labels & AUTHORED_BY_ACCOUNT_OWNER_LABELS:
        return EmailDirection.OUTGOING
    if labels:
        return EmailDirection.INCOMING
    return EmailDirection.UNKNOWN


def received_at(message: dict) -> datetime:
    """Gmail's own `internalDate` (epoch milliseconds, UTC).

    Preferred over parsing the message's own `Date` header: that header is
    client-supplied and occasionally malformed or missing entirely, whereas
    `internalDate` is assigned by Gmail itself on receipt.
    """
    internal_date = message.get("internalDate")
    if internal_date:
        return datetime.fromtimestamp(int(internal_date) / 1000, tz=UTC)
    return datetime.now(UTC)


def parse_gmail_message(message: dict) -> dict:
    """Extract exactly the fields `EmailMessage` stores from a raw API message.

    Returns a plain dict of constructor kwargs (not an `EmailMessage` itself),
    so this module stays free of any ORM/session dependency.
    """
    payload = message.get("payload") or {}
    headers = headers_by_name(payload.get("headers") or [])

    # Plain text first; Gmail's own snippet (a short preview it always
    # computes) is a reasonable fallback when no text/plain part exists at all
    # (e.g. an HTML-only marketing email).
    body_text = find_plain_text(payload) or message.get("snippet") or None
    if body_text:
        # Decode entities before capping length, so a long body is never
        # truncated mid-entity (e.g. left dangling on "&rsq").
        body_text = normalize_body_text(body_text)
        if len(body_text) > MAX_BODY_TEXT_LENGTH:
            body_text = body_text[:MAX_BODY_TEXT_LENGTH]

    return {
        "gmail_message_id": message["id"],
        "thread_id": message.get("threadId") or message["id"],
        "sender": headers.get("from", ""),
        "subject": headers.get("subject") or None,
        "received_at": received_at(message),
        "body_text": body_text,
        # `labelIds` lives on the message, not the payload — it is Gmail's own
        # metadata about the message, not part of its MIME content.
        "direction": direction_from_labels(message.get("labelIds")).value,
    }
