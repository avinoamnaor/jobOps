"""Validation of the hand-labelled classifier evaluation set.

Two jobs:

  1. Prove the dataset is usable as ground truth — every expected value is a
     valid `EmailClassification`, the intended categories are covered, and the
     labels are internally consistent with the contract's own rules.
  2. Prove it is safe to commit publicly — no real personal, company, or
     account identifiers from the live inbox it was modelled on.

The second is enforced mechanically rather than by review, because "I anonymised
it" is exactly the kind of claim that decays quietly as cases get added later.

No network, no LLM, no database.
"""

import re
from datetime import timedelta

import pytest

from app.core.classification_evidence import (
    evidence_is_verifiable,
    unverifiable_evidence,
)
from app.enums import (
    CLASSIFIABLE_EMAIL_DIRECTIONS,
    ClassificationConfidence,
    EmailDirection,
    EmailMessageType,
)
from app.schemas.classification import EmailClassification
from tests.fixtures.email_eval_dataset import (
    ALLOWED_EXAMPLE_DOMAINS,
    COVERED_MESSAGE_TYPES,
    EVAL_CASES,
    EVAL_CASES_BY_NAME,
    UNCOVERED_MESSAGE_TYPES,
    EvalCase,
)

# Identifiers from the real inbox this dataset was modelled on. If any of these
# ever appears in a fixture, anonymisation has regressed.
#
# Kept as generic well-known strings plus the user's own account shape rather
# than a list of real employers — listing real companies here to prove they are
# absent would put them in the repository, defeating the point.
_FORBIDDEN_SUBSTRINGS = (
    "avinoam",
    "aniavinoam",
    "gmail.com",
    "checkpoint",
    "check point",
)


@pytest.mark.parametrize("case", EVAL_CASES, ids=lambda case: case.name)
class TestEveryCaseIsWellFormed:
    def test_expected_value_is_a_valid_classification(self, case: EvalCase) -> None:
        # Re-validating rather than trusting construction: the dataclass holds
        # an already-constructed model, so this asserts it round-trips through
        # the contract cleanly and would catch a field going stale.
        revalidated = EmailClassification.model_validate(case.expected.model_dump())
        assert revalidated == case.expected

    def test_message_has_content_to_classify(self, case: EvalCase) -> None:
        assert case.message.sender.strip()
        assert case.message.subject.strip()
        # The body must carry the signal: several cases exist precisely because
        # the subject is misleading, so a one-line body would not test them.
        assert len(case.message.body_text.strip()) > 80

    def test_explains_what_it_guards(self, case: EvalCase) -> None:
        assert case.tests_that.strip(), "each case should say which trap it catches"

    def test_no_real_identifiers(self, case: EvalCase) -> None:
        haystack = " ".join(
            [case.name, case.message.sender, case.message.subject, case.message.body_text]
        ).lower()
        for forbidden in _FORBIDDEN_SUBSTRINGS:
            assert forbidden not in haystack, f"{case.name} leaks {forbidden!r}"

    def test_sender_uses_only_reserved_example_domains(self, case: EvalCase) -> None:
        """RFC 2606 domains can never resolve to a real mail host.

        That is what makes the addresses safe to publish: they are not merely
        unlikely to be real, they are reserved so they cannot be.
        """
        domains = re.findall(r"@([\w.-]+)", case.message.sender)
        assert domains, f"{case.name} has no parseable sender domain"
        for domain in domains:
            assert domain.lower() in ALLOWED_EXAMPLE_DOMAINS, (
                f"{case.name} uses non-reserved domain {domain!r}"
            )

    def test_body_contains_no_email_addresses(self, case: EvalCase) -> None:
        # Bodies describe recruitment situations; none needs a contact address,
        # and one appearing later would most likely be a copy-paste from real
        # mail.
        found = re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", case.message.body_text)
        assert found == [], f"{case.name} contains address(es): {found}"

    def test_body_contains_no_urls(self, case: EvalCase) -> None:
        # Real recruitment mail is full of tracking links; their absence here is
        # a good signal nothing was pasted in wholesale.
        found = re.findall(r"https?://\S+", case.message.body_text)
        assert found == [], f"{case.name} contains URL(s): {found}"


class TestDatasetShape:
    def test_case_names_are_unique(self) -> None:
        assert len(EVAL_CASES_BY_NAME) == len(EVAL_CASES)

    def test_covers_at_least_sixteen_situations(self) -> None:
        assert len(EVAL_CASES) >= 16

    def test_actual_coverage_matches_the_declared_coverage(self) -> None:
        """The declared set and the real set must agree.

        Adding a case for a new type without declaring it (or declaring one
        without adding a case) is a silent gap in an eval set, which is worse
        than a loud one.
        """
        actual = {case.expected.message_type for case in EVAL_CASES}
        assert actual == COVERED_MESSAGE_TYPES

    def test_uncovered_types_are_declared_and_disjoint(self) -> None:
        assert COVERED_MESSAGE_TYPES.isdisjoint(UNCOVERED_MESSAGE_TYPES)
        assert COVERED_MESSAGE_TYPES | UNCOVERED_MESSAGE_TYPES == frozenset(EmailMessageType)

    def test_the_hard_cases_are_present(self) -> None:
        """The specific traps this dataset exists to catch."""
        names = set(EVAL_CASES_BY_NAME)
        assert "rejection_with_thank_you_subject" in names
        assert "job_alert_with_named_companies" in names
        assert "referral_submitted_by_a_colleague" in names
        assert "outgoing_candidate_reply" in names

    def test_confidence_is_not_uniformly_high(self) -> None:
        """A genuinely ambiguous case must be labelled as such.

        Otherwise a model that answers `high` to everything scores perfectly on
        calibration it does not have.
        """
        levels = {case.expected.confidence for case in EVAL_CASES}
        assert ClassificationConfidence.HIGH in levels
        assert levels != {ClassificationConfidence.HIGH}


class TestLabelsAreInternallyConsistent:
    def test_only_scheduled_interviews_carry_an_event_datetime(self) -> None:
        """`event_datetime` is for a time the message actually establishes.

        Pinning this in the ground truth matters: if the dataset itself invented
        datetimes for vague phrases, it would teach the behaviour the contract
        exists to prevent.
        """
        for case in EVAL_CASES:
            if case.expected.event_datetime is not None:
                assert case.expected.message_type == EmailMessageType.INTERVIEW_SCHEDULED, (
                    f"{case.name} sets event_datetime without a settled time"
                )

    def test_the_scheduled_case_actually_has_a_datetime(self) -> None:
        scheduled = [
            case
            for case in EVAL_CASES
            if case.expected.message_type == EmailMessageType.INTERVIEW_SCHEDULED
        ]
        assert scheduled, "the dataset needs a settled-time case"
        assert all(case.expected.event_datetime is not None for case in scheduled)

    def test_irrelevant_cases_extract_no_company_or_role(self) -> None:
        for case in EVAL_CASES:
            if case.expected.message_type == EmailMessageType.IRRELEVANT:
                assert case.expected.company_name is None
                assert case.expected.role_title is None

    def test_the_referral_case_is_not_labelled_as_an_application(self) -> None:
        # The distinction the type exists for: being put forward by someone else
        # is not the candidate having applied.
        case = EVAL_CASES_BY_NAME["referral_submitted_by_a_colleague"]
        assert case.expected.message_type == EmailMessageType.REFERRAL_OR_RECOMMENDATION

    def test_the_misleading_subject_case_is_labelled_from_its_body(self) -> None:
        case = EVAL_CASES_BY_NAME["rejection_with_thank_you_subject"]
        assert case.expected.message_type == EmailMessageType.REJECTION
        # The subject alone reads as positive — that is the whole point.
        assert "thank you" in case.message.subject.lower()
        assert "move forward with other candidates" in case.message.body_text.lower()

    def test_the_availability_request_is_an_invitation_not_a_schedule(self) -> None:
        case = EVAL_CASES_BY_NAME["interview_invitation_asking_for_availability"]
        assert case.expected.message_type == EmailMessageType.INTERVIEW_INVITATION
        assert case.expected.event_datetime is None


class TestDirectionInTheDataset:
    def test_an_outgoing_case_exists(self) -> None:
        directions = {case.message.direction for case in EVAL_CASES}
        assert EmailDirection.OUTGOING in directions

    def test_most_cases_are_incoming(self) -> None:
        incoming = [
            case for case in EVAL_CASES if case.message.direction == EmailDirection.INCOMING
        ]
        assert len(incoming) >= len(EVAL_CASES) - 2

    def test_the_outgoing_case_would_be_excluded_from_classification(self) -> None:
        """Direction gates the pipeline before any model is called.

        The case carries an expected label so the exclusion itself is testable —
        not as a request to classify it.
        """
        case = EVAL_CASES_BY_NAME["outgoing_candidate_reply"]
        assert case.message.direction not in CLASSIFIABLE_EMAIL_DIRECTIONS

    def test_every_incoming_case_is_classifiable(self) -> None:
        for case in EVAL_CASES:
            if case.message.direction == EmailDirection.INCOMING:
                assert case.message.direction in CLASSIFIABLE_EMAIL_DIRECTIONS


class TestEvidenceInTheDataset:
    """The dataset must exercise the verification mechanism, not just declare it.

    Every recorded excerpt is checked against its own fixture text here, so a
    typo in the dataset surfaces now rather than being mistaken later for a
    classifier failure.
    """

    @pytest.mark.parametrize("case", EVAL_CASES, ids=lambda case: case.name)
    def test_every_recorded_excerpt_appears_in_its_source(self, case: EvalCase) -> None:
        offenders = unverifiable_evidence(
            case.expected,
            subject=case.message.subject,
            body_text=case.message.body_text,
        )
        assert offenders == [], f"{case.name} cites text absent from the message: {offenders}"

    @pytest.mark.parametrize("case", EVAL_CASES, ids=lambda case: case.name)
    def test_non_irrelevant_cases_cite_evidence(self, case: EvalCase) -> None:
        if case.expected.message_type is not EmailMessageType.IRRELEVANT:
            assert case.expected.evidence, f"{case.name} must support its verdict"

    def test_the_irrelevant_case_exercises_the_empty_evidence_exemption(self) -> None:
        # Documented decision: the support for "irrelevant" is the absence of
        # anything recruitment-related to quote.
        case = EVAL_CASES_BY_NAME["irrelevant_marketing_newsletter"]
        assert case.expected.message_type == EmailMessageType.IRRELEVANT
        assert case.expected.evidence == []

    @pytest.mark.parametrize("case", EVAL_CASES, ids=lambda case: case.name)
    def test_invented_evidence_would_fail_for_this_case(self, case: EvalCase) -> None:
        """The check has teeth on every fixture, not just on a chosen one.

        Without this, a verification bug that silently passed everything would
        look identical to a perfectly anonymised dataset.
        """
        fabricated = case.expected.model_copy(
            update={"evidence": ["we were deeply impressed by your portfolio submission"]}
        )
        assert not evidence_is_verifiable(
            fabricated,
            subject=case.message.subject,
            body_text=case.message.body_text,
        )


class TestBoundaryCoverage:
    """An eval set of only unambiguous examples measures very little."""

    def test_enough_cases_are_boundary_cases(self) -> None:
        contrasting = [case for case in EVAL_CASES if case.contrasts_with is not None]
        assert len(contrasting) >= 6

    @pytest.mark.parametrize("case", EVAL_CASES, ids=lambda case: case.name)
    def test_a_contrast_is_never_the_expected_answer(self, case: EvalCase) -> None:
        # `contrasts_with` names the WRONG reading, so it must differ from the
        # right one -- otherwise the case documents nothing.
        if case.contrasts_with is not None:
            assert case.contrasts_with != case.expected.message_type

    def test_the_required_distinctions_are_all_represented(self) -> None:
        """Each pair the correction pass called for, as (expected, mistaken)."""
        pairs = {
            (case.expected.message_type, case.contrasts_with)
            for case in EVAL_CASES
            if case.contrasts_with is not None
        }
        required = {
            # recruiter_outreach vs interview_invitation
            (EmailMessageType.RECRUITER_OUTREACH, EmailMessageType.INTERVIEW_INVITATION),
            # interview_invitation vs interview_scheduled
            (EmailMessageType.INTERVIEW_INVITATION, EmailMessageType.INTERVIEW_SCHEDULED),
            # rejection behind a positive subject vs application_received
            (EmailMessageType.REJECTION, EmailMessageType.APPLICATION_RECEIVED),
            # referral vs application_received
            (
                EmailMessageType.REFERRAL_OR_RECOMMENDATION,
                EmailMessageType.APPLICATION_RECEIVED,
            ),
            # job_alert vs recruiter_outreach
            (EmailMessageType.JOB_ALERT, EmailMessageType.RECRUITER_OUTREACH),
            # process_update vs a more specific type
            (EmailMessageType.PROCESS_UPDATE, EmailMessageType.OFFER_RECEIVED),
        }
        assert required <= pairs

    def test_the_slots_case_does_not_leak_a_datetime(self) -> None:
        """Three parseable times in the body, none agreed.

        The sharpest event_datetime trap in the set: a model that extracts any
        of them invents a commitment the message never made.
        """
        case = EVAL_CASES_BY_NAME["interview_invitation_with_proposed_slots"]
        assert case.expected.message_type == EmailMessageType.INTERVIEW_INVITATION
        assert case.expected.event_datetime is None
        assert "15 September" in case.message.body_text

    def test_positive_progress_is_not_labelled_an_offer(self) -> None:
        case = EVAL_CASES_BY_NAME["advancing_to_final_round_is_not_an_offer"]
        assert case.expected.message_type == EmailMessageType.PROCESS_UPDATE
        assert case.contrasts_with == EmailMessageType.OFFER_RECEIVED

    def test_outreach_without_an_ask_is_not_an_invitation(self) -> None:
        case = EVAL_CASES_BY_NAME["recruiter_outreach_without_an_interview_ask"]
        assert case.expected.message_type == EmailMessageType.RECRUITER_OUTREACH
        # No availability request anywhere in the body -- that is the whole
        # distinction from interview_invitation.
        assert "available" not in case.message.body_text.lower()


class TestEventDatetimeInTheDataset:
    def test_the_only_datetime_is_timezone_aware(self) -> None:
        """Ground truth must model the rule it is testing for.

        A naive datetime here would teach exactly the behaviour the contract
        exists to prevent -- and the schema would reject it anyway.
        """
        with_times = [case for case in EVAL_CASES if case.expected.event_datetime is not None]
        assert with_times, "the dataset needs at least one settled-time case"
        for case in with_times:
            assert case.expected.event_datetime.utcoffset() is not None, case.name

    def test_the_scheduled_case_states_its_timezone_in_the_body(self) -> None:
        # What makes recording a value safe: the email establishes the offset,
        # rather than the classifier assuming one.
        case = EVAL_CASES_BY_NAME["interview_scheduled_with_explicit_datetime"]
        assert "UTC+3" in case.message.body_text
        assert case.expected.event_datetime.utcoffset() == timedelta(hours=3)
