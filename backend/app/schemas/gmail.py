"""API shapes for the Gmail integration."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.enums import EmailDirection


class GmailSyncResponse(BaseModel):
    """Result of one sync call: what was looked at, and how it was handled."""

    fetched: int
    imported: int
    already_existing: int
    # A subset of `already_existing`: rows that were already stored but had no
    # direction recorded, and gained one on this run. Reported separately
    # because no new message was created — otherwise a sync that repaired
    # hundreds of rows would look identical to one that did nothing.
    enriched: int


class EmailMessageSummary(BaseModel):
    """One row in the message list — no body, to keep the list light."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    gmail_message_id: str
    thread_id: str
    sender: str
    subject: str | None
    received_at: datetime
    created_at: datetime
    # Read from Gmail's own SENT/INBOX labels at import time, never guessed from
    # the sender address. Included in the summary rather than only the detail:
    # it is one short word, and "which of these did I send?" is exactly the kind
    # of question a list view should answer without opening every row.
    direction: EmailDirection


class EmailMessageDetail(EmailMessageSummary):
    """A single stored message, including its body.

    Always read from `email_messages` — retrieving this never calls the Gmail
    API.
    """

    body_text: str | None


class EmailMessagePage(BaseModel):
    """One page of stored messages, newest (`received_at`) first."""

    items: list[EmailMessageSummary]
    total: int
    limit: int
    offset: int


class GmailStatus(BaseModel):
    """Locally-determinable readiness. Never a token, secret, or file path."""

    credentials_configured: bool
    token_present: bool
    connected: bool
