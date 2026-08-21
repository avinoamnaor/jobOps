"""Unit tests for pure Gmail message parsing.

No I/O, no Gmail client, no database — these operate directly on plain dicts
shaped like the Gmail API's `users.messages.get` response.
"""

import base64
from datetime import UTC, datetime

from app.core.gmail_parse import (
    MAX_BODY_TEXT_LENGTH,
    decode_base64url,
    find_plain_text,
    headers_by_name,
    parse_gmail_message,
    received_at,
)


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


class TestDecodeBase64Url:
    def test_round_trips_text_without_padding(self) -> None:
        assert decode_base64url(_b64url("hello world")) == "hello world"

    def test_handles_values_that_need_padding(self) -> None:
        # Deliberately odd-length source text so the base64 needs '=' padding
        # that Gmail's encoding omits.
        assert decode_base64url(_b64url("a")) == "a"
        assert decode_base64url(_b64url("ab")) == "ab"
        assert decode_base64url(_b64url("abc")) == "abc"


class TestHeadersByName:
    def test_indexes_case_insensitively(self) -> None:
        headers = [
            {"name": "From", "value": "jane@example.com"},
            {"name": "Subject", "value": "Hi"},
        ]
        result = headers_by_name(headers)
        assert result["from"] == "jane@example.com"
        assert result["subject"] == "Hi"


class TestFindPlainText:
    def test_simple_message_with_body_on_the_payload(self) -> None:
        payload = {"mimeType": "text/plain", "body": {"data": _b64url("Hello there")}}
        assert find_plain_text(payload) == "Hello there"

    def test_multipart_alternative_picks_the_plain_text_part(self) -> None:
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/html", "body": {"data": _b64url("<p>Hi</p>")}},
                {"mimeType": "text/plain", "body": {"data": _b64url("Plain hello")}},
            ],
        }
        assert find_plain_text(payload) == "Plain hello"

    def test_nested_multipart_mixed_wrapping_alternative(self) -> None:
        """multipart/mixed (attachments) wrapping multipart/alternative (body)."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": _b64url("Nested body")}},
                    ],
                },
                {"mimeType": "application/pdf", "body": {"attachmentId": "abc"}},
            ],
        }
        assert find_plain_text(payload) == "Nested body"

    def test_html_only_message_returns_none(self) -> None:
        payload = {"mimeType": "text/html", "body": {"data": _b64url("<p>Hi</p>")}}
        assert find_plain_text(payload) is None

    def test_missing_parts_and_body_do_not_raise(self) -> None:
        assert find_plain_text({}) is None
        assert find_plain_text({"mimeType": "text/plain", "body": {}}) is None


class TestReceivedAt:
    def test_uses_internal_date_when_present(self) -> None:
        # 2026-01-01T00:00:00Z in epoch milliseconds.
        message = {"internalDate": "1767225600000"}
        assert received_at(message) == datetime(2026, 1, 1, tzinfo=UTC)

    def test_falls_back_to_now_when_missing(self) -> None:
        result = received_at({})
        assert (datetime.now(UTC) - result).total_seconds() < 5


class TestParseGmailMessage:
    def test_extracts_all_fields_from_a_typical_message(self) -> None:
        message = {
            "id": "18abc123",
            "threadId": "18abc000",
            "internalDate": "1767225600000",
            "snippet": "A short preview",
            "payload": {
                "headers": [
                    {"name": "From", "value": "Recruiter <recruiter@example.com>"},
                    {"name": "Subject", "value": "Interview invitation"},
                ],
                "mimeType": "text/plain",
                "body": {"data": _b64url("We would like to invite you to interview.")},
            },
        }

        parsed = parse_gmail_message(message)

        assert parsed == {
            "gmail_message_id": "18abc123",
            "thread_id": "18abc000",
            "sender": "Recruiter <recruiter@example.com>",
            "subject": "Interview invitation",
            "received_at": datetime(2026, 1, 1, tzinfo=UTC),
            "body_text": "We would like to invite you to interview.",
        }

    def test_falls_back_to_snippet_when_no_plain_text_part_exists(self) -> None:
        message = {
            "id": "1",
            "threadId": "1",
            "internalDate": "1767225600000",
            "snippet": "HTML-only newsletter preview",
            "payload": {
                "headers": [],
                "mimeType": "text/html",
                "body": {"data": _b64url("<p>fancy html</p>")},
            },
        }

        parsed = parse_gmail_message(message)

        assert parsed["body_text"] == "HTML-only newsletter preview"

    def test_missing_thread_id_falls_back_to_message_id(self) -> None:
        message = {"id": "abc", "payload": {"headers": []}}
        assert parse_gmail_message(message)["thread_id"] == "abc"

    def test_missing_subject_is_none_not_empty_string(self) -> None:
        message = {"id": "abc", "payload": {"headers": []}}
        assert parse_gmail_message(message)["subject"] is None

    def test_body_text_is_truncated_defensively(self) -> None:
        huge = "x" * (MAX_BODY_TEXT_LENGTH + 500)
        message = {
            "id": "abc",
            "payload": {
                "headers": [],
                "mimeType": "text/plain",
                "body": {"data": _b64url(huge)},
            },
        }

        parsed = parse_gmail_message(message)

        assert len(parsed["body_text"]) == MAX_BODY_TEXT_LENGTH
