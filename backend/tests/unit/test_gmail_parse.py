"""Unit tests for pure Gmail message parsing.

No I/O, no Gmail client, no database — these operate directly on plain dicts
shaped like the Gmail API's `users.messages.get` response.
"""

import base64
from datetime import UTC, datetime

from app.core.gmail_parse import (
    MAX_BODY_TEXT_LENGTH,
    decode_base64url,
    direction_from_labels,
    find_plain_text,
    headers_by_name,
    normalize_body_text,
    parse_gmail_message,
    received_at,
)
from app.enums import EmailDirection


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


class TestNormalizeBodyText:
    def test_decodes_rsquo_nbsp_mdash(self) -> None:
        assert normalize_body_text("We&rsquo;re hiring") == "We’re hiring"
        assert normalize_body_text("Team&nbsp;Lead") == "Team Lead"
        assert normalize_body_text("Full&mdash;time") == "Full—time"

    def test_decodes_numeric_and_hex_entities(self) -> None:
        assert normalize_body_text("&#8217;") == "’"
        assert normalize_body_text("&#x2019;") == "’"

    def test_plain_text_without_entities_is_unchanged(self) -> None:
        text = "Thanks for applying. We'll be in touch soon — best of luck!"
        assert normalize_body_text(text) == text

    def test_unicode_hebrew_survives_untouched(self) -> None:
        text = "תודה על הפנייה, ניצור קשר בקרוב"
        assert normalize_body_text(text) == text

    def test_lone_ampersand_is_left_alone(self) -> None:
        assert normalize_body_text("Q&A session") == "Q&A session"


class TestDirectionFromLabels:
    """Direction comes from Gmail's own system labels, never the sender address.

    The rule answers "did I write this?", not "is it in my inbox": Gmail marks
    owner-authored mail with SENT/DRAFT, and everything else in the mailbox
    arrived from someone else.
    """

    def test_sent_label_means_outgoing(self) -> None:
        assert direction_from_labels(["SENT"]) == EmailDirection.OUTGOING

    def test_draft_label_means_outgoing(self) -> None:
        # A draft is the user's own unsent writing. It must be excluded from
        # classification for the same reason a sent reply is -- and `unknown`
        # is a *classifiable* direction, so mapping drafts there would feed the
        # user's own words to the classifier as if an employer wrote them.
        assert direction_from_labels(["DRAFT"]) == EmailDirection.OUTGOING

    def test_inbox_label_means_incoming(self) -> None:
        assert direction_from_labels(["INBOX", "UNREAD"]) == EmailDirection.INCOMING

    def test_realistic_received_label_set(self) -> None:
        labels = ["UNREAD", "CATEGORY_PERSONAL", "INBOX", "IMPORTANT"]
        assert direction_from_labels(labels) == EmailDirection.INCOMING

    def test_archived_received_mail_is_still_incoming(self) -> None:
        """The regression this rule exists to fix.

        Archiving removes INBOX. Keying `incoming` off INBOX therefore marked
        every archived recruitment email `unknown` -- and archiving after
        reading is completely ordinary, so that mislabelled a large share of
        exactly the messages this integration exists to process.
        """
        assert direction_from_labels(["CATEGORY_PERSONAL"]) == EmailDirection.INCOMING
        assert direction_from_labels(["IMPORTANT"]) == EmailDirection.INCOMING
        assert direction_from_labels(["UNREAD", "CATEGORY_UPDATES"]) == EmailDirection.INCOMING

    def test_spam_and_trash_are_still_incoming(self) -> None:
        # Where a received message currently sits says nothing about who wrote it.
        assert direction_from_labels(["SPAM"]) == EmailDirection.INCOMING
        assert direction_from_labels(["TRASH"]) == EmailDirection.INCOMING

    def test_sent_wins_over_inbox_for_self_addressed_mail(self) -> None:
        # A message sent to yourself carries both. Outgoing is the conservative
        # reading: later phases exclude outgoing mail from classification, so a
        # tie resolves toward not treating your own writing as an employer's.
        assert direction_from_labels(["INBOX", "SENT"]) == EmailDirection.OUTGOING

    def test_draft_wins_over_other_labels(self) -> None:
        assert direction_from_labels(["DRAFT", "CATEGORY_PERSONAL"]) == EmailDirection.OUTGOING

    def test_only_a_total_absence_of_labels_is_unknown(self) -> None:
        # Gmail returns labels for every real message, so an empty set means a
        # degenerate response -- the one genuine no-evidence case.
        assert direction_from_labels(None) == EmailDirection.UNKNOWN
        assert direction_from_labels([]) == EmailDirection.UNKNOWN

    def test_label_matching_is_exact_not_substring(self) -> None:
        # A user-created label containing "SENT" must not be read as the system
        # SENT label -- it is someone else's mail filed under a folder name.
        assert direction_from_labels(["Label_SENT_ARCHIVE"]) == EmailDirection.INCOMING
        assert direction_from_labels(["NOT_SENT"]) == EmailDirection.INCOMING

    def test_a_user_label_alone_is_incoming_not_unknown(self) -> None:
        assert direction_from_labels(["Label_12345"]) == EmailDirection.INCOMING


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
            # A real received message carries INBOX among its labels; direction
            # is read from here, never from the From: header below.
            "labelIds": ["UNREAD", "CATEGORY_PERSONAL", "INBOX"],
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
            "direction": "incoming",
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

    def test_decodes_html_entities_in_the_plain_text_part(self) -> None:
        # A naive HTML-to-text conversion on the sender's side, the real
        # failure mode this fixes: the "plain text" alternative still carries
        # literal entities.
        message = {
            "id": "abc",
            "payload": {
                "headers": [],
                "mimeType": "text/plain",
                "body": {
                    "data": _b64url(
                        "We&rsquo;d love to have you&nbsp;&mdash; welcome aboard!"
                    )
                },
            },
        }
        parsed = parse_gmail_message(message)
        # rsquo decodes to U+2019, nbsp decodes to U+00A0 (a real non-breaking
        # space, not a plain space), mdash decodes to U+2014 -- all decoded,
        # not left as literal entities.
        assert parsed["body_text"] == 'We’d love to have you\xa0— welcome aboard!'

    def test_multipart_alternative_still_picks_plain_text_and_normalizes_it(self) -> None:
        message = {
            "id": "abc",
            "payload": {
                "headers": [],
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/html", "body": {"data": _b64url("<p>Hi&nbsp;there</p>")}},
                    {"mimeType": "text/plain", "body": {"data": _b64url("Hi&nbsp;there")}},
                ],
            },
        }
        parsed = parse_gmail_message(message)
        assert parsed["body_text"] == 'Hi\xa0there'

    def test_hebrew_body_survives_full_pipeline(self) -> None:
        message = {
            "id": "abc",
            "payload": {
                "headers": [],
                "mimeType": "text/plain",
                "body": {"data": _b64url("תודה על פנייתך, ניצור איתך קשר בהקדם")},
            },
        }
        parsed = parse_gmail_message(message)
        assert parsed["body_text"] == "תודה על פנייתך, ניצור איתך קשר בהקדם"

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


class TestParsedDirection:
    def test_direction_comes_from_message_level_label_ids(self) -> None:
        # labelIds sits on the message, not the payload -- it is Gmail's own
        # metadata about the message, not part of its MIME content.
        message = {
            "id": "abc",
            "labelIds": ["SENT"],
            "payload": {"headers": [], "mimeType": "text/plain", "body": {}},
        }
        assert parse_gmail_message(message)["direction"] == "outgoing"

    def test_message_without_label_ids_is_unknown(self) -> None:
        message = {"id": "abc", "payload": {"headers": []}}
        assert parse_gmail_message(message)["direction"] == "unknown"

    def test_direction_is_a_plain_string_for_the_orm(self) -> None:
        # The column is VARCHAR + CHECK, so the parsed value must be the enum's
        # string value rather than the enum member itself.
        message = {"id": "abc", "labelIds": ["INBOX"], "payload": {"headers": []}}
        direction = parse_gmail_message(message)["direction"]
        assert isinstance(direction, str)
        assert direction == EmailDirection.INCOMING.value

    def test_sender_address_never_influences_direction(self) -> None:
        """The same From: address parses both ways depending only on labels.

        This is the regression guard for the approach itself: if anyone ever
        reintroduces address-based inference, one of these two must break.
        """
        sender = "Someone <someone@example.com>"

        def _message(message_id: str, labels: list[str]) -> dict:
            return {
                "id": message_id,
                "labelIds": labels,
                "payload": {
                    "headers": [{"name": "From", "value": sender}],
                    "mimeType": "text/plain",
                    "body": {"data": _b64url("body")},
                },
            }

        assert parse_gmail_message(_message("a", ["SENT"]))["direction"] == "outgoing"
        assert parse_gmail_message(_message("b", ["INBOX"]))["direction"] == "incoming"
        # Same address again, archived (no INBOX): still incoming.
        assert parse_gmail_message(_message("c", ["CATEGORY_PERSONAL"]))["direction"] == "incoming"


class TestNoHardcodedPersonalAddress:
    def test_parsing_module_contains_no_email_address_literal(self) -> None:
        """Direction must never depend on a personal address baked into source.

        Checked mechanically rather than by review: an address added later to
        "just handle my own mail" would work locally and be wrong for everyone,
        including the repository's public readers.
        """
        import re
        from pathlib import Path

        import app.core.gmail_parse as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        # Any local@domain.tld literal. The module legitimately mentions header
        # names and label names, but never an address.
        addresses = re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", source)
        assert addresses == [], f"no email address literal belongs here: {addresses}"

    def test_gmail_settings_expose_no_account_address(self) -> None:
        """There is no configured 'my address' for anything to fall back on."""
        from app.config import settings

        assert not any(
            "address" in name or "email" in name
            for name in type(settings).model_fields
            if name.startswith("gmail")
        )
