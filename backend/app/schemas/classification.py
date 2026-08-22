"""The email-classification contract — what a future classifier must produce.

Phase 6.2A-0 scope: this module defines and validates a shape. Nothing in the
codebase produces an `EmailClassification` yet. No OpenAI client, no prompt, no
model selection, no Structured Outputs wiring exists — those are the next slice,
and this contract is what they will have to satisfy.

The contract is a *semantic interpretation*, never an action policy. It says
what an email means; it never says what JobOps should do about it. That split is
deliberate and load-bearing, and it is why several plausible-sounding fields are
absent:

  * No `recruitment_related`. `message_type` already answers it (everything
    except `irrelevant`), and two fields that can disagree is a bug waiting to
    happen. `enums.RECRUITMENT_MESSAGE_TYPES` derives it instead.
  * No `actionable`. Whether JobOps acts is deterministic product policy
    evaluated from this classification plus context the model never sees (does a
    matching application exist? what status is it in?). That is not the model's
    call to make.
  * No `application_id`. Matching a classified email to an Application is
    Phase 6.2B. Letting the classifier guess an id would fuse two phases that
    have to stay separately testable — and it has no way to know the ids.
  * No `suggested_status` / `suggestion_type`. Status decisions belong to the
    suggestion/policy phase, which routes through the one service function that
    is allowed to assign a status.

Product decisions already made for later phases, recorded here so they are not
re-litigated or lost (NOT implemented in this slice):

  * `application_received` should eventually write an explicit
    "Application confirmed by email" event even when the Application is already
    `applied` — the confirmation is evidence worth keeping, and it must not
    vanish merely because the status happened to be correct already. If the
    Application is still `saved`, policy may additionally suggest `applied`.
  * `referral_or_recommendation` must never be auto-read as "the candidate
    applied". With a matching Application, later phases surface it
    informationally. With none, they may offer "Create application from this
    email", prefilled with any extracted company/role and most likely the
    `referral` channel — always requiring the user to approve and edit.
  * `outgoing` mail (see `EmailDirection`) is excluded from classification
    entirely in the first classifier version, and produces no suggestion by
    itself.
"""

from datetime import datetime

from pydantic import Field, field_validator, model_validator

from app.enums import ClassificationConfidence, EmailMessageType
from app.schemas.base import StrictModel

# Bounds on `evidence`. These exist so the field stays a set of short, checkable
# quotes rather than drifting into free-form model commentary — the difference
# between something a later phase can verify against the source message and
# something a reviewer has to take on trust.
MAX_EVIDENCE_ITEMS = 5
MAX_EVIDENCE_EXCERPT_LENGTH = 300

# Company/role are captured as the message states them. The cap is a sanity
# bound against a model returning a paragraph, not a formatting rule.
MAX_EXTRACTED_NAME_LENGTH = 200


class EmailClassification(StrictModel):
    """One classifier verdict about one email message.

    Inherits `StrictModel`, so an unexpected key is a hard validation error.
    That matters more here than on a normal request body: the likeliest source
    of an unknown field is a model inventing one (`"suggested_status"`,
    `"application_id"`), and those must fail loudly rather than be silently
    dropped and mistaken for a contract this project never agreed to.
    """

    message_type: EmailMessageType
    """What the message means. The single source of truth — there is no separate
    `recruitment_related` flag to contradict it."""

    company_name: str | None = Field(default=None, max_length=MAX_EXTRACTED_NAME_LENGTH)
    """The company as the email itself states it, or null.

    Deliberately NOT canonicalised. If the message says "Example Security
    Technologies", that is the value — silently shortening it to "Example
    Security" destroys the evidence a human would use to check the match, and
    guesses at an equivalence that belongs to the matching phase (6.2B), which
    has `normalize.company_key` and the actual application list to compare
    against. Over-normalising here would make two different employers collide
    before anything could notice."""

    role_title: str | None = Field(default=None, max_length=MAX_EXTRACTED_NAME_LENGTH)
    """The role as the email supports it, or null. Same principle as
    `company_name`: preserve what the message said, normalise later."""

    event_datetime: datetime | None = None
    """An explicitly stated event date/time — in practice, a scheduled
    interview — or null. Must be timezone-aware when present.

    Only for a datetime the message actually establishes. "Sometime next week"
    and "we will find a slot" are not datetimes, and inventing one would put a
    fabricated commitment on a real calendar. Null is the correct answer far
    more often than not; `interview_scheduled` is the one type that routinely
    justifies a value.

    A naive datetime is rejected outright rather than accepted and interpreted
    later. "10:00" with no zone is not a time, it is a time in *some* zone, and
    whichever zone the code guesses later — server local, UTC, the user's — is
    a guess that silently shifts a real interview by hours. Rejecting it forces
    the honest answer: null, which the user fixes in seconds, instead of a
    plausible wrong time nobody re-reads.

    This is stricter than the underlying database column requires and will yield
    null for real emails that say "Monday at 10:00" without a zone. That cost is
    accepted deliberately — a missing time is a two-second correction, a wrong
    one is a missed interview — and it matches `email_messages.received_at`,
    which is likewise `DateTime(timezone=True)`."""

    confidence: ClassificationConfidence
    """Coarse certainty. Three named levels rather than a numeric probability:
    an LLM's self-reported `0.97` is uncalibrated and reads as far more precise
    than it is."""

    evidence: list[str] = Field(default_factory=list, max_length=MAX_EVIDENCE_ITEMS)
    """Short excerpts from the message supporting the classification.

    Bounded on purpose (see the module constants). The intent is that a later
    phase can check each excerpt actually appears in the source message, turning
    evidence into something verifiable rather than unrestricted generated
    explanation. Keeping the items short and few is what makes that check
    meaningful — a model that may quote a whole email can always "support" any
    verdict."""

    @field_validator("company_name", "role_title", mode="after")
    @classmethod
    def _blank_string_is_null(cls, value: str | None) -> str | None:
        """Treat "" and "   " as absent.

        A model asked for `string | null` will sometimes return an empty string
        instead of null. Storing that would mean a field that is falsy but not
        null, so every consumer would need to check both — an easy thing to get
        wrong exactly once, silently.
        """
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("event_datetime", mode="after")
    @classmethod
    def _event_datetime_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        """Reject a naive datetime; see the field's docstring for why.

        `utcoffset()` is checked rather than `tzinfo`, because a tzinfo object
        that returns None for the offset is naive in every way that matters.
        """
        if value is None:
            return None
        if value.utcoffset() is None:
            raise ValueError(
                "event_datetime must be timezone-aware: a time with no zone silently "
                "shifts when interpreted later. Return null unless the message itself "
                "establishes the timezone."
            )
        return value

    @field_validator("evidence", mode="after")
    @classmethod
    def _evidence_items_are_short_non_empty_excerpts(cls, value: list[str]) -> list[str]:
        """Reject blank or over-long excerpts.

        `max_length` on the field bounds the list; this bounds each item. An
        excerpt that is empty supports nothing, and one that is 4 KB long is a
        paraphrase of the whole email rather than a quotation from it — neither
        can serve the verification role `evidence` exists for.
        """
        cleaned: list[str] = []
        for item in value:
            excerpt = item.strip()
            if not excerpt:
                raise ValueError("evidence excerpts must not be blank")
            if len(excerpt) > MAX_EVIDENCE_EXCERPT_LENGTH:
                raise ValueError(
                    f"evidence excerpts must be at most {MAX_EVIDENCE_EXCERPT_LENGTH} "
                    f"characters (got {len(excerpt)})"
                )
            cleaned.append(excerpt)
        return cleaned

    @model_validator(mode="after")
    def _substantive_classifications_must_cite_evidence(self) -> "EmailClassification":
        """Require at least one excerpt for any non-`irrelevant` verdict.

        Evidence is the mechanism that makes a classification checkable: paired
        with `core.classification_evidence.verify_evidence`, every excerpt can be
        confirmed to appear in the source message, so a model that invents
        support fails rather than persuades. That only works if evidence is
        actually present — optional evidence is decorative evidence.

        `irrelevant` is exempt, and the exemption is deliberate. That verdict is
        a statement that nothing in the message is about recruitment, and the
        honest support for it is the absence of anything to quote. Demanding a
        quote would push a model to pluck an arbitrary line and dress it up as
        reasoning. Evidence is still *allowed* there — a marketing footer is a
        fair thing to cite — just not required.
        """
        if self.message_type is not EmailMessageType.IRRELEVANT and not self.evidence:
            raise ValueError(
                f"evidence is required for message_type={self.message_type.value!r}: "
                "a classification that cannot quote the message supporting it "
                "cannot be verified against it"
            )
        return self
