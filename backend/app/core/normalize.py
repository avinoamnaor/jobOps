"""Normalisation helpers.

These turn messy human text into stable comparison keys. "ProgrammaticX Ltd.",
"programmaticx ltd" and "PROGRAMMATIC-X" should all reduce to the same
`company_key`, so that later phases can ask "do I already have an application
here?" without fuzzy matching everything from scratch.

Phase 1 only *stores* these keys. Phase 4 uses them for duplicate detection.
Computing them now costs nothing and means the data is already usable then.

These are pure functions — no database, no I/O — which makes them the cheapest
and most valuable things in the project to unit-test.
"""

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Company suffixes that carry no identifying information.
_LEGAL_SUFFIXES = {
    "ab", "ag", "as", "bv", "co", "corp", "corporation", "gmbh", "inc",
    "incorporated", "limited", "llc", "ltd", "nv", "oy", "plc", "pty",
    "sa", "sarl", "spa", "srl",
}

# Query parameters that identify *how you arrived*, not *which job it is*.
#
# This list is deliberately conservative, and should stay that way. Stripping a
# parameter that actually identifies the posting would make two different jobs
# canonicalise to the same URL — and since Phase 4 uses the canonical URL as its
# strongest duplicate signal, that would silently merge two real applications.
# Wrongly keeping a tracking parameter only costs a missed duplicate hint, which
# is the far cheaper mistake. When in doubt, keep it.
#
# Anything starting with `utm_` is also dropped (handled below), since the whole
# utm_* namespace is campaign tracking by definition.
_TRACKING_PARAMS = {
    "fbclid", "gclid", "li_fat_id", "msclkid", "originalsubdomain",
    "refid", "src", "trackingid", "trk", "trkinfo",
}

_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")
_PARENTHESISED = re.compile(r"\([^)]*\)")


def _strip_accents(value: str) -> str:
    """Fold accented characters to their base letters (é -> e)."""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def normalize_company(name: str) -> str:
    """Reduce a company name to a stable comparison key.

    >>> normalize_company("ProgrammaticX Ltd.")
    'programmaticx'
    """
    text = _NON_ALPHANUMERIC.sub(" ", _strip_accents(name).lower())
    tokens = text.split()

    # Drop legal suffixes from the end only. A leading/middle "co" can be part of
    # the real name ("Co-op Group"), so we never remove those.
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()

    return " ".join(tokens)


def normalize_role(title: str) -> str:
    """Reduce a job title to a stable comparison key.

    Seniority is preserved on purpose: "Senior Backend Engineer" and "Junior
    Backend Engineer" are different jobs, and collapsing them would create false
    duplicates. Only genuine noise is removed — parenthesised notes such as
    "(m/f/d)" or "(Remote)".

    >>> normalize_role("Fullstack Developer (m/f/d)")
    'fullstack developer'
    """
    text = _PARENTHESISED.sub(" ", _strip_accents(title).lower())
    text = _NON_ALPHANUMERIC.sub(" ", text)
    return " ".join(text.split())


def canonicalize_url(url: str | None) -> str | None:
    """Strip a job URL down to the part that identifies the posting.

    Two people can send you the same job posting with different tracking
    parameters. Comparing raw URLs would treat those as different jobs; comparing
    canonical ones does not.

    >>> canonicalize_url("https://WWW.Example.com/jobs/42/?utm_source=x&ref=y#top")
    'https://example.com/jobs/42'
    """
    if url is None or not url.strip():
        return None

    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return None

    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host.removeprefix("www.")

    kept_params = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=False)
        if key.lower() not in _TRACKING_PARAMS and not key.lower().startswith("utm_")
    ]

    # Sort so that the same posting shared with its parameters in a different
    # order canonicalises identically. Query parameter order carries no meaning,
    # but string comparison does not know that.
    kept_params.sort()

    path = parsed.path.rstrip("/") or "/"

    # Fragments (#section) are browser-side only and never identify the posting.
    return urlunparse((parsed.scheme.lower(), host, path, "", urlencode(kept_params), ""))


# Query-parameter names (normalised to alphanumerics) that carry a job/requisition
# id. Generic — no site-specific handling.
_JOB_ID_KEYS = {
    "jobid", "id", "ghjid", "requisitionid", "requisition", "currentjobid",
    "position", "posting", "vacancy", "reqid", "req", "jid", "leverid", "gh",
}
_NUMERIC_SEGMENT = re.compile(r"^\d{2,}$")


def extract_job_id(url: str | None) -> str | None:
    """Best-effort meaningful job/requisition id from a URL.

    Looks first at id-like query parameters (e.g. `job_id`, `gh_jid`,
    `requisitionId`), then at a numeric path segment (e.g. `/jobs/4821`). Returns
    None when nothing id-like is present — a role slug like
    `/careers/senior-backend-engineer` is deliberately not treated as an id.
    """
    canonical = canonicalize_url(url)
    if canonical is None:
        return None

    parsed = urlparse(canonical)
    for key, value in parse_qsl(parsed.query, keep_blank_values=False):
        normalized_key = re.sub(r"[^a-z0-9]", "", key.lower())
        if normalized_key in _JOB_ID_KEYS and value.strip():
            return value.strip()

    numeric_segments = [
        segment for segment in parsed.path.split("/") if _NUMERIC_SEGMENT.match(segment)
    ]
    if numeric_segments:
        # The longest numeric segment is the most id-like.
        return max(numeric_segments, key=len)

    return None


def normalize_description(text: str | None) -> str:
    """Collapse a job description to a stable comparison string.

    Lowercased with runs of whitespace flattened to single spaces, so trivial
    formatting differences (extra blank lines, trailing spaces) do not defeat a
    "same description" comparison. Returns "" for empty input.
    """
    if not text:
        return ""
    return " ".join(text.split()).lower()
