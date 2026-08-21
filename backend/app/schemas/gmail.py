"""API shapes for the Gmail integration."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class GmailSyncResponse(BaseModel):
    """Result of one sync call: what was looked at, and how it was handled."""

    fetched: int
    imported: int
    already_existing: int


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
