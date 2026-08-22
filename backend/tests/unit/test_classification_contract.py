"""Tests for the email-classification contract (Phase 6.2A-0).

Pure schema validation — no database, no network, and emphatically no LLM. This
contract exists before any classifier does, so these tests are the specification
a future implementation has to satisfy rather than a description of one.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.enums import (
    CLASSIFIABLE_EMAIL_DIRECTIONS,
    RECRUITMENT_MESSAGE_TYPES,
    ClassificationConfidence,
    EmailDirection,
    EmailMessageType,
)
from app.schemas.classification import (
    MAX_EVIDENCE_EXCERPT_LENGTH,
    MAX_EVIDENCE_ITEMS,
    EmailClassification,
)


def _minimal(**overrides: object) -> dict:
    """A valid classification, for tests that vary exactly one thing.

    Carries evidence by default because the schema now requires it for any
    non-irrelevant verdict -- see TestEvidenceIsRequired.
    """
    base = {
        "message_type": EmailMessageType.REJECTION,
        "confidence": ClassificationConfidence.HIGH,
        "evidence": ["we have decided to move forward with other candidates"],
    }
    base.update(overrides)
    return base


class TestMessageTypeEnum:
    def test_every_specified_category_exists(self) -> None:
        """The agreed vocabulary, pinned by value.

        Written out literally rather than derived from the enum: a test that
        reads the enum to check the enum would pass no matter what anyone
        renamed.
        """
        assert {member.value for member in EmailMessageType} == {
            "irrelevant",
            "job_alert",
            "application_received",
            "referral_or_recommendation",
            "recruiter_outreach",
            "interview_invitation",
            "interview_scheduled",
            "assessment_requested",
            "process_update",
            "rejection",
            "offer_received",
            "privacy_or_retention_notice",
            "post_interview_survey",
            "other_recruitment",
        }

    @pytest.mark.parametrize("member", list(EmailMessageType))
    def test_every_enum_value_validates(self, member: EmailMessageType) -> None:
        parsed = EmailClassification(**_minimal(message_type=member))
        assert parsed.message_type is member

    def test_a_value_outside_the_enum_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EmailClassification(**_minimal(message_type="ghosted"))

    def test_message_type_is_required(self) -> None:
        with pytest.raises(ValidationError):
            EmailClassification(confidence=ClassificationConfidence.HIGH)

    def test_recruitment_grouping_excludes_only_irrelevant(self) -> None:
        # Derived from message_type rather than stored as its own field, so the
        # two can never disagree.
        assert EmailMessageType.IRRELEVANT not in RECRUITMENT_MESSAGE_TYPES
        assert EmailMessageType.JOB_ALERT in RECRUITMENT_MESSAGE_TYPES
        assert len(RECRUITMENT_MESSAGE_TYPES) == len(EmailMessageType) - 1


class TestConfidence:
    @pytest.mark.parametrize("level", ["high", "medium", "low"])
    def test_the_three_levels_validate(self, level: str) -> None:
        assert EmailClassification(**_minimal(confidence=level)).confidence == level

    def test_exactly_three_levels_exist(self) -> None:
        assert {member.value for member in ClassificationConfidence} == {
            "high",
            "medium",
            "low",
        }

    def test_a_numeric_probability_is_rejected(self) -> None:
        # The whole reason this is an enum: an uncalibrated 0.97 reads as far
        # more precise than the underlying signal supports.
        for value in (0.97, "0.97", 1, "very high"):
            with pytest.raises(ValidationError):
                EmailClassification(**_minimal(confidence=value))

    def test_confidence_is_required(self) -> None:
        with pytest.raises(ValidationError):
            EmailClassification(message_type=EmailMessageType.REJECTION)


class TestNullableExtractedFields:
    def test_company_role_and_datetime_default_to_none(self) -> None:
        parsed = EmailClassification(**_minimal())
        assert parsed.company_name is None
        assert parsed.role_title is None
        assert parsed.event_datetime is None

    def test_explicit_nulls_are_accepted(self) -> None:
        parsed = EmailClassification(
            **_minimal(company_name=None, role_title=None, event_datetime=None)
        )
        assert (parsed.company_name, parsed.role_title, parsed.event_datetime) == (
            None,
            None,
            None,
        )

    def test_values_are_preserved_exactly_not_canonicalized(self) -> None:
        """The contract promises what the email said, not a tidied version.

        Canonicalisation belongs to the matching phase, which has the
        application list to compare against; guessing an equivalence here would
        collide two employers before anything could notice.
        """
        parsed = EmailClassification(
            **_minimal(
                company_name="Example Security Technologies",
                role_title="Senior Security Analyst (Threat Intel)",
            )
        )
        assert parsed.company_name == "Example Security Technologies"
        assert parsed.role_title == "Senior Security Analyst (Threat Intel)"

    def test_blank_strings_become_none(self) -> None:
        # A model asked for `string | null` sometimes answers "". Storing that
        # would give consumers a value that is falsy but not null.
        parsed = EmailClassification(**_minimal(company_name="", role_title="   "))
        assert parsed.company_name is None
        assert parsed.role_title is None

    def test_event_datetime_accepts_an_iso_timestamp(self) -> None:
        parsed = EmailClassification(**_minimal(event_datetime="2026-09-14T10:00:00Z"))
        assert parsed.event_datetime == datetime(2026, 9, 14, 10, 0, tzinfo=UTC)

    def test_an_unparseable_datetime_is_rejected(self) -> None:
        for value in ("next Tuesday", "soon", "14/09"):
            with pytest.raises(ValidationError):
                EmailClassification(**_minimal(event_datetime=value))

    def test_an_over_long_name_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EmailClassification(**_minimal(company_name="x" * 201))


class TestEvidence:
    def test_defaults_to_an_empty_list_where_that_is_allowed(self) -> None:
        # `irrelevant` is the one verdict exempt from the evidence requirement,
        # so it is the only place the default is reachable.
        parsed = EmailClassification(
            message_type=EmailMessageType.IRRELEVANT,
            confidence=ClassificationConfidence.HIGH,
        )
        assert parsed.evidence == []

    def test_accepts_short_excerpts(self) -> None:
        excerpts = ["we have decided to move forward with other candidates"]
        assert EmailClassification(**_minimal(evidence=excerpts)).evidence == excerpts

    def test_excerpts_are_stripped(self) -> None:
        parsed = EmailClassification(**_minimal(evidence=["  quoted line  "]))
        assert parsed.evidence == ["quoted line"]

    def test_the_list_is_bounded(self) -> None:
        ok = ["excerpt"] * MAX_EVIDENCE_ITEMS
        assert len(EmailClassification(**_minimal(evidence=ok)).evidence) == MAX_EVIDENCE_ITEMS

        with pytest.raises(ValidationError):
            EmailClassification(**_minimal(evidence=["excerpt"] * (MAX_EVIDENCE_ITEMS + 1)))

    def test_each_excerpt_is_bounded(self) -> None:
        """An excerpt long enough to be the whole email cannot serve as evidence.

        Bounding the item, not just the list, is what keeps `evidence` a set of
        checkable quotations rather than a paraphrase that could "support" any
        verdict.
        """
        at_limit = "x" * MAX_EVIDENCE_EXCERPT_LENGTH
        assert EmailClassification(**_minimal(evidence=[at_limit])).evidence == [at_limit]

        with pytest.raises(ValidationError):
            EmailClassification(**_minimal(evidence=["x" * (MAX_EVIDENCE_EXCERPT_LENGTH + 1)]))

    def test_blank_excerpts_are_rejected(self) -> None:
        for bad in ([""], ["   "], ["real excerpt", ""]):
            with pytest.raises(ValidationError):
                EmailClassification(**_minimal(evidence=bad))

    def test_a_bare_string_is_not_accepted_as_evidence(self) -> None:
        with pytest.raises(ValidationError):
            EmailClassification(**_minimal(evidence="a single excerpt"))


class TestForbiddenPolicyFields:
    """Action/policy decisions are not the classifier's to make.

    `StrictModel` makes each of these a hard validation error rather than a
    silently-dropped key — the likeliest source of one is a model inventing a
    field, and that must fail loudly instead of being mistaken for a contract
    this project agreed to.
    """

    @pytest.mark.parametrize(
        "field_name",
        [
            "recruitment_related",
            "actionable",
            "application_id",
            "suggested_status",
            "suggestion_type",
        ],
    )
    def test_forbidden_field_is_rejected(self, field_name: str) -> None:
        with pytest.raises(ValidationError):
            EmailClassification(**_minimal(**{field_name: "anything"}))

    def test_none_of_them_are_declared_on_the_schema(self) -> None:
        declared = set(EmailClassification.model_fields)
        assert declared.isdisjoint(
            {
                "recruitment_related",
                "actionable",
                "application_id",
                "suggested_status",
                "suggestion_type",
            }
        )

    def test_the_schema_declares_exactly_the_agreed_fields(self) -> None:
        assert set(EmailClassification.model_fields) == {
            "message_type",
            "company_name",
            "role_title",
            "event_datetime",
            "confidence",
            "evidence",
        }


class TestOutgoingMailIsNotClassifiable:
    """Direction gates classification before a model is ever involved."""

    def test_outgoing_is_excluded(self) -> None:
        assert EmailDirection.OUTGOING not in CLASSIFIABLE_EMAIL_DIRECTIONS

    def test_incoming_and_unknown_are_included(self) -> None:
        # `unknown` is included deliberately: a message with no direction
        # evidence is far more likely to be received mail with unusual labels
        # than something the user sent.
        assert EmailDirection.INCOMING in CLASSIFIABLE_EMAIL_DIRECTIONS
        assert EmailDirection.UNKNOWN in CLASSIFIABLE_EMAIL_DIRECTIONS


class TestNoLlmIntegrationExistsYet:
    def test_no_openai_client_is_wired_into_the_backend(self) -> None:
        """This slice specifies a contract; it must not have connected a model.

        Guards the scope boundary mechanically, so "we only added a tiny call"
        cannot slip in unnoticed alongside contract work.
        """
        import ast
        from pathlib import Path

        import app

        app_root = Path(app.__file__).parent
        llm_packages = {"openai", "anthropic"}
        offenders: list[str] = []

        # Parsed, not grepped: prose in a docstring explaining that no client is
        # wired up yet must not count as wiring one up. Only real import
        # statements do.
        for path in app_root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = {alias.name.split(".")[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom):
                    names = {(node.module or "").split(".")[0]}
                else:
                    continue
                if names & llm_packages:
                    offenders.append(path.relative_to(app_root).as_posix())
                    break

        assert offenders == [], f"unexpected LLM client import in: {offenders}"


class TestEvidenceIsRequiredForSubstantiveVerdicts:
    """Evidence is the mechanism that makes a classification checkable.

    Optional evidence is decorative evidence: paired with
    `verify_evidence`, a required excerpt is what lets a model that invents
    support fail rather than persuade.
    """

    @pytest.mark.parametrize(
        "member", [m for m in EmailMessageType if m is not EmailMessageType.IRRELEVANT]
    )
    def test_every_non_irrelevant_type_requires_evidence(
        self, member: EmailMessageType
    ) -> None:
        with pytest.raises(ValidationError, match="evidence is required"):
            EmailClassification(
                message_type=member,
                confidence=ClassificationConfidence.HIGH,
                evidence=[],
            )

    def test_irrelevant_may_omit_evidence(self) -> None:
        """Documented exemption: the support for 'irrelevant' is the absence of
        anything recruitment-related to quote.

        Requiring a quote there would push a model to pluck an arbitrary line
        and dress it up as reasoning.
        """
        parsed = EmailClassification(
            message_type=EmailMessageType.IRRELEVANT,
            confidence=ClassificationConfidence.HIGH,
        )
        assert parsed.evidence == []

    def test_irrelevant_may_still_supply_evidence(self) -> None:
        # Allowed, just not required -- a marketing footer is a fair citation.
        parsed = EmailClassification(
            message_type=EmailMessageType.IRRELEVANT,
            confidence=ClassificationConfidence.HIGH,
            evidence=["Unsubscribe from marketing emails"],
        )
        assert parsed.evidence == ["Unsubscribe from marketing emails"]

    def test_whitespace_only_evidence_does_not_satisfy_the_requirement(self) -> None:
        # Rejected by the per-item validator before the requirement is reached;
        # either way, a blank cannot stand in for a citation.
        with pytest.raises(ValidationError):
            EmailClassification(**_minimal(evidence=["   "]))


class TestEventDatetimeTimezoneSafety:
    """A naive datetime is rejected rather than interpreted later.

    '10:00' with no zone is not a time, it is a time in *some* zone, and
    whichever zone downstream code picks is a guess that silently shifts a real
    interview by hours.
    """

    def test_an_offset_aware_datetime_is_accepted(self) -> None:
        parsed = EmailClassification(**_minimal(event_datetime="2026-08-13T10:00:00+03:00"))
        assert parsed.event_datetime is not None
        assert parsed.event_datetime.utcoffset() == timedelta(hours=3)

    def test_a_utc_datetime_is_accepted(self) -> None:
        parsed = EmailClassification(**_minimal(event_datetime="2026-08-13T10:00:00Z"))
        assert parsed.event_datetime == datetime(2026, 8, 13, 10, 0, tzinfo=UTC)

    def test_a_naive_datetime_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            EmailClassification(**_minimal(event_datetime="2026-08-13T10:00:00"))

    def test_a_naive_datetime_object_is_rejected(self) -> None:
        # Not only the string form: a constructed naive datetime fails too.
        with pytest.raises(ValidationError, match="timezone-aware"):
            EmailClassification(**_minimal(event_datetime=datetime(2026, 8, 13, 10, 0)))

    def test_a_date_only_value_is_rejected_as_naive(self) -> None:
        # A bare date has no time and no zone; accepting it would invent both.
        with pytest.raises(ValidationError, match="timezone-aware"):
            EmailClassification(**_minimal(event_datetime="2026-08-13"))

    def test_null_remains_the_safe_default(self) -> None:
        """The intended answer when the email does not establish a timezone.

        This is the behaviour the strictness exists to produce: a missing time
        is a two-second correction, a wrong one is a missed interview.
        """
        assert EmailClassification(**_minimal(event_datetime=None)).event_datetime is None
        assert EmailClassification(**_minimal()).event_datetime is None

    def test_the_rule_matches_the_projects_datetime_convention(self) -> None:
        # `email_messages.received_at` is DateTime(timezone=True); the contract
        # is deliberately consistent with it rather than looser.
        from app.models.email_message import EmailMessage

        assert EmailMessage.__table__.c.received_at.type.timezone is True
