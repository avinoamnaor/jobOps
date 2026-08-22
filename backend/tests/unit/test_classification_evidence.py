"""Tests for evidence verification against a source message.

This is the mechanism that makes `evidence` a check rather than an explanation:
the schema guarantees the excerpts are present, few and short, and these
functions answer the question the schema cannot — does each excerpt actually
appear in the email?

Pure functions. No LLM, no network, no database.
"""

import pytest

from app.core.classification_evidence import (
    evidence_is_verifiable,
    normalize_for_comparison,
    searchable_parts,
    unverifiable_evidence,
)
from app.enums import ClassificationConfidence, EmailMessageType
from app.schemas.classification import EmailClassification

SUBJECT = "Thank you for your interest in Brightpath Systems"
BODY = (
    "Dear candidate,\n\n"
    "Thank you for taking the time to apply for the Backend Engineer role.\n\n"
    "After careful consideration, we have decided to move forward with other\n"
    "candidates whose experience more closely matches the requirements of this\n"
    "position.\n\n"
    "We wish you every success in your search."
)


def _classification(evidence: list[str]) -> EmailClassification:
    return EmailClassification(
        message_type=EmailMessageType.REJECTION,
        confidence=ClassificationConfidence.HIGH,
        evidence=evidence,
    )


class TestNormalizeForComparison:
    def test_collapses_all_whitespace(self) -> None:
        assert normalize_for_comparison("a  b\n\tc") == "a b c"

    def test_is_case_insensitive(self) -> None:
        assert normalize_for_comparison("Hello THERE") == normalize_for_comparison("hello there")

    def test_treats_curly_and_straight_quotes_as_equivalent(self) -> None:
        # A transcription artefact of how the message was rendered, not a
        # fabrication -- failing a genuine quote over it would train the check
        # to be ignored.
        assert normalize_for_comparison("we’re hiring") == normalize_for_comparison(
            "we're hiring"
        )

    def test_treats_dash_variants_as_equivalent(self) -> None:
        assert normalize_for_comparison("full—time") == normalize_for_comparison("full-time")

    def test_keeps_punctuation(self) -> None:
        """Stripping punctuation would erode the guarantee.

        The excerpt must be a real fragment of the message; dropping commas
        would let a differently-punctuated near-miss pass as a quotation.
        """
        assert "," in normalize_for_comparison("we will, not proceed")


class TestSearchableParts:
    def test_keeps_subject_and_body_separate(self) -> None:
        # Separate haystacks, not one concatenated string -- see the docstring
        # on searchable_parts for why that distinction matters.
        assert searchable_parts("A Subject", "A Body") == ["a subject", "a body"]

    def test_tolerates_missing_parts(self) -> None:
        assert searchable_parts(None, "body") == ["body"]
        assert searchable_parts("subject", None) == ["subject"]
        assert searchable_parts(None, None) == []

    def test_blank_parts_are_dropped(self) -> None:
        assert searchable_parts("   ", "body") == ["body"]


class TestUnverifiableEvidence:
    def test_a_verbatim_excerpt_verifies(self) -> None:
        classification = _classification(["we have decided to move forward with other"])
        assert unverifiable_evidence(classification, subject=SUBJECT, body_text=BODY) == []

    def test_an_excerpt_spanning_a_wrapped_line_verifies(self) -> None:
        """The realistic transcription case.

        The body wraps between "other" and "candidates"; a model quoting from
        rendered text sees one continuous sentence. Whitespace normalisation is
        what keeps that a pass.
        """
        classification = _classification(
            ["we have decided to move forward with other candidates"]
        )
        assert unverifiable_evidence(classification, subject=SUBJECT, body_text=BODY) == []

    def test_an_excerpt_from_the_subject_verifies(self) -> None:
        # A subject line really is part of what the message says.
        classification = _classification(["Thank you for your interest"])
        assert unverifiable_evidence(classification, subject=SUBJECT, body_text=BODY) == []

    def test_case_and_spacing_differences_still_verify(self) -> None:
        classification = _classification(["WE HAVE DECIDED   to move forward"])
        assert unverifiable_evidence(classification, subject=SUBJECT, body_text=BODY) == []

    def test_an_invented_quote_fails(self) -> None:
        """The whole point: fabricated support must not pass as corroboration."""
        classification = _classification(["we were impressed by your portfolio"])
        assert unverifiable_evidence(classification, subject=SUBJECT, body_text=BODY) == [
            "we were impressed by your portfolio"
        ]

    def test_a_paraphrase_fails(self) -> None:
        # Close in meaning, absent from the text. An unverifiable citation is
        # worse than none, because it reads as corroboration.
        classification = _classification(["they chose someone else for the role"])
        assert unverifiable_evidence(classification, subject=SUBJECT, body_text=BODY) != []

    def test_reports_only_the_offending_excerpts(self) -> None:
        # Naming the invented quote is what makes a failure actionable.
        classification = _classification(
            ["we have decided to move forward", "and we would like to offer you the job"]
        )
        assert unverifiable_evidence(classification, subject=SUBJECT, body_text=BODY) == [
            "and we would like to offer you the job"
        ]

    def test_empty_evidence_is_vacuously_verifiable(self) -> None:
        classification = EmailClassification(
            message_type=EmailMessageType.IRRELEVANT,
            confidence=ClassificationConfidence.HIGH,
        )
        assert unverifiable_evidence(classification, subject=SUBJECT, body_text=BODY) == []

    def test_an_absent_source_makes_everything_unverifiable(self) -> None:
        """Silently passing here would make the check vacuous exactly when it is
        least deserved."""
        classification = _classification(["anything at all"])
        assert unverifiable_evidence(classification, subject=None, body_text=None) == [
            "anything at all"
        ]

    def test_an_excerpt_cannot_match_by_straddling_subject_and_body(self) -> None:
        # There is no real text spanning the join, so nothing should correspond
        # to a quote that crosses it.
        classification = _classification(["Brightpath Systems Dear candidate"])
        assert unverifiable_evidence(classification, subject=SUBJECT, body_text=BODY) != []


class TestEvidenceIsVerifiable:
    def test_true_when_everything_checks_out(self) -> None:
        classification = _classification(["we have decided to move forward"])
        assert evidence_is_verifiable(classification, subject=SUBJECT, body_text=BODY)

    def test_false_when_any_excerpt_is_invented(self) -> None:
        classification = _classification(
            ["we have decided to move forward", "starting salary is competitive"]
        )
        assert not evidence_is_verifiable(classification, subject=SUBJECT, body_text=BODY)

    @pytest.mark.parametrize(
        "excerpt",
        [
            "we have decided to move forward with other candidates",
            "Backend Engineer role",
            "We wish you every success in your search.",
        ],
    )
    def test_multiple_different_valid_excerpts_all_verify(self, excerpt: str) -> None:
        """Why the eval dataset does not pin one exact quote.

        Several excerpts can each correctly support the same verdict, so
        requiring a specific one would fail correct classifiers. The property —
        whatever is quoted appears in the source — is what must hold.
        """
        assert evidence_is_verifiable(_classification([excerpt]), subject=SUBJECT, body_text=BODY)
