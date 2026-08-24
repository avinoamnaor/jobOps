"""Local redaction of avoidable personal data before an email leaves the machine.

Deliberately small. This is not a PII-detection framework and does not pretend
to be one: a recruitment email is *about* a person by nature, and no regex is
going to change that. What it does is remove the categories that are both easy
to identify and useless for classification — contact details, tracking
parameters, opaque identifiers — so that the text sent to a third party carries
the meaning without the incidental exposure.

The guiding trade-off runs the other way from most redaction tools. Over-
sanitising is a real cost here, not a safe default: strip the company name and
the classifier cannot tell a rejection from a job alert, which defeats the point
of sending anything at all. So company names, role titles, recruiter names as
they appear in prose, dates, times and recruitment wording are all kept
deliberately. See `SANITIZER_KEEPS` for the full list and the reasoning.

Pure and deterministic: same input, same output, no network, no clock, no
provider awareness. It never mutates what it is given — callers get a new
`SanitizedEmail`, and the stored `EmailMessage` row is untouched.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

EMAIL_PLACEHOLDER = "[EMAIL]"
PHONE_PLACEHOLDER = "[PHONE]"
TOKEN_PLACEHOLDER = "[TOKEN]"

# What is deliberately NOT removed, and why. Documented as data so the decision
# is reviewable rather than implied by the absence of a regex.
SANITIZER_KEEPS: dict[str, str] = {
    "company names": "the single most useful field the classifier extracts",
    "role titles": "likewise extracted, and meaningless once redacted",
    "recruiter names in prose": (
        "a name in a signature is what makes referral_or_recommendation "
        "distinguishable from an automated alert"
    ),
    "URL host and path": (
        "careers.example.com/apply says who is hiring; the query string is "
        "what carries the tracking"
    ),
    "dates and times": "event_datetime depends on them entirely",
    "job and requisition ids": (
        "short, human-visible references the message itself displays; they are "
        "identifiers of a posting, not of a person"
    ),
}

# An address anywhere in the text. Broad on purpose: an address is never needed
# to classify a message, so there is no cost to matching aggressively.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

# A candidate phone number: an optional +, then digits and separators. The
# regex is deliberately loose and the decision is made in code, because the
# thing that distinguishes a phone number from a date is how many digits it
# has, which a regex expresses badly.
_PHONE_CANDIDATE_RE = re.compile(r"(?<![\w])\+?\d[\d\s().\-]{6,}\d(?![\w])")

# Real phone numbers, national or international, sit inside this range. The
# lower bound is what keeps ISO dates (2026-09-14, eight digits) and times out
# of it; the upper bound rejects long digit runs that are really identifiers.
_MIN_PHONE_DIGITS = 9
_MAX_PHONE_DIGITS = 15

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)

# A long opaque identifier: mixed letters and digits, long enough that it
# cannot be a word. Requiring BOTH a letter and a digit is what keeps ordinary
# prose — including long compound words and hyphenated role titles — intact.
_MIXED_TOKEN_RE = re.compile(
    r"(?<![\w-])(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{24,}(?![\w-])"
)

# A long pure-hex run: a digest or session id. Handled separately because it
# has no letters-and-digits mix to key off.
_HEX_TOKEN_RE = re.compile(r"(?<![\w-])[0-9a-fA-F]{32,}(?![\w-])")


@dataclass(frozen=True)
class SanitizationCounts:
    """How much was removed, never what.

    Reported so a dry run can show that sanitisation actually happened. The
    values themselves are never recorded — a count of redactions is safe to
    print, a list of them would recreate the exposure being prevented.
    """

    emails: int = 0
    phones: int = 0
    urls_cleaned: int = 0
    opaque_tokens: int = 0

    @property
    def total(self) -> int:
        return self.emails + self.phones + self.urls_cleaned + self.opaque_tokens

    def as_lines(self) -> list[str]:
        return [
            f"emails: {self.emails}",
            f"phones: {self.phones}",
            f"urls_cleaned: {self.urls_cleaned}",
            f"opaque_tokens: {self.opaque_tokens}",
        ]


@dataclass(frozen=True)
class SanitizedEmail:
    """A copy of an email with avoidable personal data removed."""

    sender: str
    subject: str | None
    body_text: str | None
    counts: SanitizationCounts


class _Redactor:
    """Applies the passes in order, tallying as it goes.

    Order matters. URLs are cleaned first so that an address or token living in
    a query string is removed as part of the query, rather than being
    individually redacted and counted twice.
    """

    def __init__(self) -> None:
        self.emails = 0
        self.phones = 0
        self.urls_cleaned = 0
        self.opaque_tokens = 0

    def run(self, text: str | None) -> str | None:
        if text is None:
            return None
        cleaned = _URL_RE.sub(self._clean_url, text)
        cleaned = _EMAIL_RE.sub(self._redact_email, cleaned)
        cleaned = _PHONE_CANDIDATE_RE.sub(self._maybe_redact_phone, cleaned)
        cleaned = _MIXED_TOKEN_RE.sub(self._redact_token, cleaned)
        cleaned = _HEX_TOKEN_RE.sub(self._redact_token, cleaned)
        return cleaned

    def _clean_url(self, match: re.Match[str]) -> str:
        url = match.group(0)
        # Trailing punctuation belongs to the sentence, not the URL, and
        # putting it back keeps the prose readable.
        trailing = ""
        while url and url[-1] in ".,;:!?":
            trailing = url[-1] + trailing
            url = url[:-1]

        try:
            parts = urlsplit(url)
        except ValueError:
            # An unparseable URL is exactly the kind of thing not to forward.
            self.urls_cleaned += 1
            return "[URL]" + trailing

        if not parts.query and not parts.fragment:
            return url + trailing

        # Host and path survive: "careers.example.com/apply" identifies the
        # employer, which is useful. The query and fragment are where the
        # tracking, session tokens and personal parameters live, and none of
        # them help decide what the message means.
        self.urls_cleaned += 1
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")) + trailing

    def _redact_email(self, _match: re.Match[str]) -> str:
        self.emails += 1
        return EMAIL_PLACEHOLDER

    def _maybe_redact_phone(self, match: re.Match[str]) -> str:
        candidate = match.group(0)
        digits = sum(character.isdigit() for character in candidate)
        if _MIN_PHONE_DIGITS <= digits <= _MAX_PHONE_DIGITS:
            self.phones += 1
            return PHONE_PLACEHOLDER
        # Not a phone number — a date, a time, a year range, a short reference.
        # Returned untouched, because guessing wrong here silently destroys the
        # information `event_datetime` depends on.
        return candidate

    def _redact_token(self, _match: re.Match[str]) -> str:
        self.opaque_tokens += 1
        return TOKEN_PLACEHOLDER

    def counts(self) -> SanitizationCounts:
        return SanitizationCounts(
            emails=self.emails,
            phones=self.phones,
            urls_cleaned=self.urls_cleaned,
            opaque_tokens=self.opaque_tokens,
        )


def sanitize_email(
    *, sender: str, subject: str | None, body_text: str | None
) -> SanitizedEmail:
    """Redact avoidable personal data from one email.

    Returns a new object; nothing passed in is modified. The sender is
    sanitised too — a `From:` header is mostly an address, and the display name
    that survives is what actually carries signal about who is writing.
    """
    redactor = _Redactor()
    return SanitizedEmail(
        sender=redactor.run(sender) or "",
        subject=redactor.run(subject),
        body_text=redactor.run(body_text),
        counts=redactor.counts(),
    )
