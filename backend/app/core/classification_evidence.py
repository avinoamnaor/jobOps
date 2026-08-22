"""Verifying a classification's evidence against the message it came from.

This is what turns `EmailClassification.evidence` from an explanation into a
check. The schema guarantees the excerpts are present, few and short; this
module answers the question the schema cannot, because the schema never sees the
email: **does each excerpt actually appear in the source?**

A model that paraphrases, embellishes, or invents a supporting quote fails here.
That is the point — an unverifiable citation is worse than no citation, because
it reads as corroboration while providing none.

Pure functions, no I/O, no LLM. Nothing calls these yet: the classifier they
will police does not exist. They are written now so the contract is enforceable
the moment it does, and so the eval dataset can be checked against them today.
"""

import re
import unicodedata

from app.schemas.classification import EmailClassification

# Characters an email client may substitute for their ASCII equivalents when
# rendering or re-encoding a message. A model quoting from rendered text should
# not be failed for a curly apostrophe where the source had a straight one:
# that is a transcription artefact, not a fabrication.
_EQUIVALENT_CHARACTERS = {
    "‘": "'",
    "’": "'",
    "‚": "'",
    "“": '"',
    "”": '"',
    "–": "-",
    "—": "-",
    "−": "-",
    " ": " ",
}


def normalize_for_comparison(text: str) -> str:
    """Reduce text to a form where only the words matter.

    Deliberately forgiving about presentation and unforgiving about content.
    Line wrapping, run of spaces, quote style and letter case are all artefacts
    of how a message was encoded or displayed, and failing a genuine quotation
    over any of them would train the check to be ignored. Everything else —
    every word, number and their order — must match exactly.

    Punctuation is NOT stripped: dropping it would let "we will not proceed"
    match a source containing "we will, not proceed" and, more importantly,
    erodes the guarantee that the excerpt is a real fragment of the message.
    """
    normalized = unicodedata.normalize("NFKC", text)
    for source_char, replacement in _EQUIVALENT_CHARACTERS.items():
        normalized = normalized.replace(source_char, replacement)
    # Collapse every run of whitespace (including newlines) to a single space,
    # so a quote spanning a wrapped line still matches.
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip().casefold()


def searchable_parts(subject: str | None, body_text: str | None) -> list[str]:
    """The message's quotable regions, normalised, each searched independently.

    Subject and body are kept as separate haystacks rather than concatenated
    into one. Concatenating them looks equivalent and is not: whatever joins
    them normalises to a space, so an excerpt could "verify" by straddling the
    boundary — matching a run of text that exists nowhere in the message as a
    contiguous fragment. Searching each part on its own closes that off, and an
    excerpt must therefore lie wholly within the subject or wholly within the
    body.

    The subject is included at all because it is legitimately quotable: a
    subject line really is part of what the message says.
    """
    return [
        normalize_for_comparison(part) for part in (subject, body_text) if part and part.strip()
    ]


def unverifiable_evidence(
    classification: EmailClassification,
    *,
    subject: str | None,
    body_text: str | None,
) -> list[str]:
    """Return the excerpts that do NOT appear in the source message.

    An empty list means every excerpt checked out. Returning the offenders
    rather than a bare bool is what makes a failure actionable: an evaluation
    report can name the invented quote instead of just saying "unverified".
    """
    parts = searchable_parts(subject, body_text)
    if not parts:
        # No source to check against: nothing can be verified, so everything
        # quoted is unverifiable. Silently passing here would make the check
        # vacuous exactly when it is least deserved.
        return list(classification.evidence)

    return [
        excerpt
        for excerpt in classification.evidence
        if not any(normalize_for_comparison(excerpt) in part for part in parts)
    ]


def evidence_is_verifiable(
    classification: EmailClassification,
    *,
    subject: str | None,
    body_text: str | None,
) -> bool:
    """Whether every excerpt appears in the source message."""
    return not unverifiable_evidence(classification, subject=subject, body_text=body_text)
