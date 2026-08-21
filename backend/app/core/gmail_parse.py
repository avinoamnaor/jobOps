"""Pure parsing of a Gmail API message resource into `email_messages` columns.

No I/O, no Gmail client, no database — these are the cheapest and most valuable
functions in this integration to unit-test, the same reasoning as
`app/core/normalize.py`. Everything here operates on a plain dict shaped like
the JSON Gmail's `users.messages.get` endpoint returns.
"""

import base64
from datetime import UTC, datetime

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
    if body_text and len(body_text) > MAX_BODY_TEXT_LENGTH:
        body_text = body_text[:MAX_BODY_TEXT_LENGTH]

    return {
        "gmail_message_id": message["id"],
        "thread_id": message.get("threadId") or message["id"],
        "sender": headers.get("from", ""),
        "subject": headers.get("subject") or None,
        "received_at": received_at(message),
        "body_text": body_text,
    }
