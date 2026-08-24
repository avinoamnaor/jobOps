"""Unit tests for local email sanitisation.

Two failure modes matter here, and they pull in opposite directions: leaving
contact details or tracking in the text that gets sent, and stripping so much
that the classifier can no longer tell what the message means. Both are tested.

Pure functions — no network, no database, no provider.
"""

from app.core.email_sanitizer import (
    EMAIL_PLACEHOLDER,
    PHONE_PLACEHOLDER,
    TOKEN_PLACEHOLDER,
    SanitizationCounts,
    sanitize_email,
)


def _body(text: str) -> str:
    return sanitize_email(sender="x@example.com", subject=None, body_text=text).body_text


class TestEmailAddresses:
    def test_an_address_is_redacted(self) -> None:
        assert _body("Reply to john.doe@example.com today") == (
            f"Reply to {EMAIL_PLACEHOLDER} today"
        )

    def test_several_addresses_are_all_redacted_and_counted(self) -> None:
        result = sanitize_email(
            sender="Recruiter <r@example.org>",
            subject=None,
            body_text="Contact a@example.com or b@example.net",
        )
        assert result.body_text.count(EMAIL_PLACEHOLDER) == 2
        # Three: two in the body, one in the sender.
        assert result.counts.emails == 3

    def test_the_sender_address_is_redacted_but_the_display_name_survives(self) -> None:
        """The name is signal; the address is not.

        "Dana Fielding" tells the classifier a person wrote this rather than an
        automated system, which is part of what separates recruiter outreach
        from a job alert.
        """
        result = sanitize_email(
            sender="Dana Fielding <d.fielding@example.net>", subject=None, body_text=None
        )
        assert "Dana Fielding" in result.sender
        assert "d.fielding@example.net" not in result.sender
        assert EMAIL_PLACEHOLDER in result.sender

    def test_plus_addressing_and_subdomains_are_caught(self) -> None:
        assert _body("me+jobs@mail.example.co.uk") == EMAIL_PLACEHOLDER


class TestPhoneNumbers:
    def test_an_international_number_is_redacted(self) -> None:
        assert _body("Call +972-50-123-4567 now") == f"Call {PHONE_PLACEHOLDER} now"

    def test_a_national_number_is_redacted(self) -> None:
        assert _body("Ring 050-123-4567") == f"Ring {PHONE_PLACEHOLDER}"

    def test_a_spaced_number_is_redacted(self) -> None:
        assert _body("Tel +1 555 123 4567") == f"Tel {PHONE_PLACEHOLDER}"

    def test_an_iso_date_is_not_mistaken_for_a_phone_number(self) -> None:
        """The failure that would quietly break event_datetime.

        A date has eight digits and plenty of separators; only the digit-count
        floor keeps it out of the phone pattern.
        """
        text = "Your interview is on 2026-09-14 at 10:00."
        assert _body(text) == text

    def test_a_timezone_offset_survives(self) -> None:
        text = "Confirmed for 2026-09-14T10:00:00+03:00 sharp."
        assert _body(text) == text

    def test_short_reference_numbers_survive(self) -> None:
        text = "Job ID: REF209P, posting 4821."
        assert _body(text) == text

    def test_a_year_range_survives(self) -> None:
        text = "We have been hiring since 2019-2024 continuously."
        assert _body(text) == text


class TestUrls:
    def test_the_query_string_is_removed_and_the_path_kept(self) -> None:
        assert _body("Apply at https://careers.example.com/apply?id=123&token=ABC") == (
            "Apply at https://careers.example.com/apply"
        )

    def test_tracking_parameters_never_reach_the_provider(self) -> None:
        cleaned = _body(
            "See https://jobs.example.org/role/9?utm_source=email&utm_campaign=blast"
        )
        assert "utm_source" not in cleaned
        assert "utm_campaign" not in cleaned
        assert "jobs.example.org/role/9" in cleaned

    def test_auth_and_session_values_in_a_url_are_removed(self) -> None:
        cleaned = _body("Confirm: https://ats.example.com/c?session=abc123&auth=Bearer9xy")
        assert "session" not in cleaned
        assert "auth" not in cleaned
        assert "Bearer9xy" not in cleaned

    def test_a_fragment_is_removed(self) -> None:
        assert _body("https://example.com/careers#tracking-id-99") == (
            "https://example.com/careers"
        )

    def test_a_clean_url_is_left_alone_and_not_counted(self) -> None:
        result = sanitize_email(
            sender="a@example.com",
            subject=None,
            body_text="Read https://careers.example.com/about first",
        )
        assert "https://careers.example.com/about" in result.body_text
        assert result.counts.urls_cleaned == 0

    def test_trailing_sentence_punctuation_is_preserved(self) -> None:
        # The full stop belongs to the sentence, not the URL.
        assert _body("Apply at https://example.com/a?x=1.") == "Apply at https://example.com/a."

    def test_the_host_survives_because_it_says_who_is_hiring(self) -> None:
        cleaned = _body("https://careers.acmesecurity.example/apply?ref=xyz")
        assert "careers.acmesecurity.example" in cleaned


class TestOpaqueTokens:
    def test_a_long_mixed_token_is_replaced(self) -> None:
        assert _body("code aB3xY9zQ7mN2pL5kJ8hG4dF6s") == f"code {TOKEN_PLACEHOLDER}"

    def test_a_long_hex_digest_is_replaced(self) -> None:
        digest = "a" * 8 + "0123456789abcdef" * 2
        assert TOKEN_PLACEHOLDER in _body(f"ref {digest}")

    def test_ordinary_long_words_survive(self) -> None:
        # No digits, so not a token however long.
        text = "Internationalization and telecommunications responsibilities"
        assert _body(text) == text

    def test_short_identifiers_survive(self) -> None:
        text = "Requisition REQ-1234 and posting REF209P"
        assert _body(text) == text

    def test_a_hyphenated_role_title_survives(self) -> None:
        text = "Senior Back-End Engineer (Cloud-Native Platforms)"
        assert _body(text) == text


class TestSemanticContentSurvives:
    """Over-sanitising is a real cost, not a safe default."""

    def test_a_full_rejection_still_reads_as_a_rejection(self) -> None:
        body = (
            "Thank you for applying for the Backend Engineer role at "
            "Brightpath Systems.\n\n"
            "After careful consideration, we have decided to move forward with "
            "other candidates.\n\n"
            "Questions? Write to careers@example.org or call +1 555 123 4567.\n"
            "https://brightpath.example.com/careers?utm_source=ats"
        )
        cleaned = _body(body)

        # The meaning is intact.
        assert "Backend Engineer" in cleaned
        assert "Brightpath Systems" in cleaned
        assert "we have decided to move forward with other candidates" in cleaned
        assert "brightpath.example.com/careers" in cleaned
        # The incidental exposure is gone.
        assert "careers@example.org" not in cleaned
        assert "555 123 4567" not in cleaned
        assert "utm_source" not in cleaned

    def test_interview_scheduling_wording_and_times_survive(self) -> None:
        body = (
            "Your technical interview is confirmed for Monday, 14 September 2026 "
            "at 10:00 (UTC+3). It will run 90 minutes."
        )
        assert _body(body) == body

    def test_recruitment_vocabulary_survives(self) -> None:
        body = (
            "Key Responsibilities, Qualifications, take-home exercise, "
            "availability, offer, referral, data-retention policy"
        )
        assert _body(body) == body


class TestPurityAndCounting:
    def test_the_input_is_never_mutated(self) -> None:
        """The stored row must be untouched; strings are immutable, but the
        caller's values must also come back unchanged by reference."""
        sender = "Recruiter <r@example.com>"
        subject = "Interview"
        body = "Call 050-123-4567"

        result = sanitize_email(sender=sender, subject=subject, body_text=body)

        assert sender == "Recruiter <r@example.com>"
        assert body == "Call 050-123-4567"
        assert result.body_text != body

    def test_it_is_deterministic(self) -> None:
        body = "a@example.com https://x.example.com/p?q=1 +972-50-123-4567"
        first = sanitize_email(sender="s@example.com", subject="s", body_text=body)
        second = sanitize_email(sender="s@example.com", subject="s", body_text=body)
        assert first == second

    def test_none_subject_and_body_are_handled(self) -> None:
        result = sanitize_email(sender="a@example.com", subject=None, body_text=None)
        assert result.subject is None
        assert result.body_text is None

    def test_the_subject_is_sanitised_too(self) -> None:
        result = sanitize_email(
            sender="a@example.com", subject="Re: reach me at b@example.org", body_text=None
        )
        assert EMAIL_PLACEHOLDER in result.subject
        assert "b@example.org" not in result.subject

    def test_counts_are_zero_when_nothing_is_removed(self) -> None:
        result = sanitize_email(
            sender="Recruiter", subject="Interview invitation", body_text="Are you free?"
        )
        assert result.counts == SanitizationCounts()
        assert result.counts.total == 0

    def test_counts_reflect_each_category(self) -> None:
        result = sanitize_email(
            sender="Recruiter",
            subject=None,
            body_text=(
                "Mail a@example.com, call +972-50-123-4567, see "
                "https://x.example.com/p?utm_source=y, ref aB3xY9zQ7mN2pL5kJ8hG4dF6s"
            ),
        )
        assert result.counts.emails == 1
        assert result.counts.phones == 1
        assert result.counts.urls_cleaned == 1
        assert result.counts.opaque_tokens == 1
        assert result.counts.total == 4

    def test_the_counts_summary_never_contains_the_removed_values(self) -> None:
        """A count is safe to print; a list of removals would recreate the
        exposure the sanitiser exists to prevent."""
        result = sanitize_email(
            sender="a@example.com",
            subject=None,
            body_text="secret.person@example.org and +972-50-123-4567",
        )
        rendered = " ".join(result.counts.as_lines())
        assert "secret.person" not in rendered
        assert "972" not in rendered
