"""Tests for deterministic email-to-application matching (Phase 6.2B).

Network-free and LLM-free. The eval dataset in
`tests/fixtures/email_matching_eval.py` is the ground truth, and most of these
tests exist to hold the matcher to its safety principle: a false NO_MATCH costs
one manual link, a false MATCHED files an employer's decision against the wrong
job.
"""

import re

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import ApplicationStatus, CompanySignal, MatchConfidence, MatchStatus, RoleSignal
from app.models.application import Application
from app.models.application_event import ApplicationEvent
from app.models.suggestion import Suggestion
from app.schemas.application import ApplicationCreate
from app.services.applications import create_application
from app.services.email_matching import (
    MAX_CANDIDATES,
    MatchInput,
    match_email_to_application,
)
from tests.conftest import requires_database
from tests.fixtures.email_matching_eval import (
    MATCHING_CASES,
    MATCHING_CASES_BY_NAME,
    MatchingCase,
    SeedApplication,
)

pytestmark = requires_database


def _seed(db: Session, *applications: SeedApplication) -> list[Application]:
    created = []
    for seed in applications:
        created.append(
            create_application(
                db,
                ApplicationCreate(
                    company_name=seed.company_name,
                    role_title=seed.role_title,
                    # `saved` avoids the submitted-CV rule; this suite is about
                    # identity, not status policy.
                    status=ApplicationStatus.SAVED,
                ),
            )
        )
    return created


def _match(db: Session, company: str | None, role: str | None):
    return match_email_to_application(
        db, MatchInput(company_name=company, role_title=role)
    )


# ---------------------------------------------------------------------------
# The eval dataset, run end to end
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", MATCHING_CASES, ids=lambda case: case.name)
def test_eval_case(db_session: Session, case: MatchingCase) -> None:
    created = _seed(db_session, *case.applications)

    result = _match(db_session, case.company_name, case.role_title)

    assert result.status is case.expected_status, (
        f"{case.name}: {case.tests_that}\n  reason given: {result.reason}"
    )

    if case.expected_application_index is not None:
        expected = created[case.expected_application_index]
        assert result.application_id == expected.id
    else:
        # Nothing but MATCHED may carry an application id, so a caller that
        # ignores `status` still cannot act on a non-decision.
        assert result.application_id is None

    if case.expected_confidence is not None:
        assert result.confidence is case.expected_confidence

    if case.expected_candidate_count is not None:
        assert len(result.candidate_ids) == case.expected_candidate_count


@pytest.mark.parametrize("case", MATCHING_CASES, ids=lambda case: case.name)
def test_every_case_explains_itself(case: MatchingCase) -> None:
    assert case.tests_that.strip()


class TestDatasetIsAnonymised:
    """Committed fixtures must not carry a real job search."""

    FORBIDDEN = ("avinoam", "gmail.com", "checkpoint", "check point")

    def test_no_real_identifiers(self) -> None:
        haystack = " ".join(
            [
                part
                for case in MATCHING_CASES
                for part in (
                    case.name,
                    case.company_name or "",
                    case.role_title or "",
                    *[a.company_name for a in case.applications],
                    *[a.role_title for a in case.applications],
                )
            ]
        ).lower()
        for forbidden in self.FORBIDDEN:
            assert forbidden not in haystack

    def test_the_required_situations_are_covered(self) -> None:
        names = set(MATCHING_CASES_BY_NAME)
        for required in (
            "exact_company_and_role",
            "case_and_punctuation_differences",
            "company_legal_suffix_variation",
            "role_formatting_variation",
            "same_company_two_different_roles_picks_the_right_one",
            "company_matches_but_role_missing_with_several_applications",
            "role_matches_but_company_is_different",
            "missing_company_never_matches",
            "unique_company_with_missing_role_is_low_confidence",
            "no_applications_exist_at_all",
            "referral_for_an_untracked_company",
            "rejection_matched_to_the_correct_role_among_several",
            "duplicate_role_at_one_company_must_not_auto_match",
            "similar_looking_but_different_companies",
        ):
            assert required in names, required

    def test_refusals_outnumber_easy_matches(self) -> None:
        """The set is weighted toward what must not happen.

        Only one of the two failure modes is discoverable by the person it
        happens to, so the dataset should spend most of its cases there.
        """
        refusals = sum(
            1
            for case in MATCHING_CASES
            if case.expected_status in (MatchStatus.NO_MATCH, MatchStatus.AMBIGUOUS)
        )
        assert refusals >= 6


# ---------------------------------------------------------------------------
# Targeted behaviour
# ---------------------------------------------------------------------------


class TestWrongCompanyProtection:
    def test_an_identical_role_at_another_company_never_matches(
        self, db_session: Session
    ) -> None:
        _seed(db_session, SeedApplication("Brightpath Systems", "Backend Engineer"))

        result = _match(db_session, "Ironleaf Software", "Backend Engineer")

        assert result.status is MatchStatus.NO_MATCH
        assert result.signals.company is CompanySignal.NONE

    def test_a_shared_word_is_not_a_shared_company(self, db_session: Session) -> None:
        _seed(db_session, SeedApplication("Acme Security", "Analyst"))

        assert _match(db_session, "Acme Logistics", "Analyst").status is MatchStatus.NO_MATCH
        assert _match(db_session, "Acme", "Analyst").status is MatchStatus.NO_MATCH

    def test_normalisation_folds_formatting_not_names(self, db_session: Session) -> None:
        # "Ltd." is a legal suffix and safe to fold; "Solutions" is part of a
        # different name and must not be.
        _seed(db_session, SeedApplication("Acme Security", "Analyst"))

        assert _match(db_session, "Acme Security Ltd.", "Analyst").matched
        assert not _match(db_session, "Acme Security Solutions", "Analyst").matched


class TestMessageTypeCannotForceIdentity:
    def test_the_matcher_cannot_see_a_message_type(self) -> None:
        """Structural, not disciplinary.

        `MatchInput` has no field for it, so "a rejection must not be routed by
        being a rejection" cannot be violated by a function that never receives
        the type.
        """
        assert set(MatchInput.__dataclass_fields__) == {"company_name", "role_title"}

    def test_a_lone_application_is_not_matched_just_because_it_looks_plausible(
        self, db_session: Session
    ) -> None:
        # One application exists, and an email arrives about a different
        # employer. Nothing about "there is only one candidate" may rescue it.
        _seed(db_session, SeedApplication("Brightpath Systems", "Backend Engineer"))

        result = _match(db_session, "Ironleaf Software", "Backend Engineer")

        assert result.status is MatchStatus.NO_MATCH


class TestAmbiguity:
    def test_candidates_are_listed_for_a_human_to_choose_between(
        self, db_session: Session
    ) -> None:
        created = _seed(
            db_session,
            SeedApplication("Acme Security", "Security Analyst"),
            SeedApplication("Acme Security", "Backend Engineer"),
        )

        result = _match(db_session, "Acme Security", None)

        assert result.status is MatchStatus.AMBIGUOUS
        assert result.candidate_ids == [created[0].id, created[1].id]
        assert result.application_id is None

    def test_the_candidate_shortlist_is_capped(self, db_session: Session) -> None:
        _seed(
            db_session,
            *[
                SeedApplication("Acme Security", f"Role Number {index}")
                for index in range(MAX_CANDIDATES + 3)
            ],
        )

        result = _match(db_session, "Acme Security", None)

        assert result.status is MatchStatus.AMBIGUOUS
        assert len(result.candidate_ids) == MAX_CANDIDATES
        # The count in the reason reflects reality, not the truncated list.
        assert str(MAX_CANDIDATES + 3) in result.reason

    def test_an_unresolvable_role_name_is_declined_not_hedged(
        self, db_session: Session
    ) -> None:
        """Where deterministic matching stops, it declines rather than hedges.

        These two may well be the same job, but nothing available here can
        establish it — and a named role that does not agree is evidence
        *against* a match, not uncertainty about one.
        """
        _seed(db_session, SeedApplication("Aidoc Systems", "Security Operations Analyst"))

        result = _match(db_session, "Aidoc Systems", "SecOps")

        assert result.status is MatchStatus.NO_MATCH
        assert result.application_id is None
        assert result.candidate_ids == []
        assert result.signals.role is RoleSignal.NONE


class TestAConflictingRoleIsNegativeEvidence:
    """A named role that does not agree argues against a match.

    The distinction from ambiguity is the whole point: ambiguous means "one of
    these, probably", while this is "none of these, on the evidence given".
    Offering candidates here would invite the confirmation the email contradicts.
    """

    def test_a_conflicting_role_yields_no_match_with_no_candidates(
        self, db_session: Session
    ) -> None:
        _seed(db_session, SeedApplication("ProgrammaticX", "Fullstack Developer"))

        result = _match(db_session, "ProgrammaticX", "Backend Engineer")

        assert result.status is MatchStatus.NO_MATCH
        assert result.application_id is None
        assert result.candidate_ids == []

    def test_no_plausible_application_is_exposed_as_a_decision(
        self, db_session: Session
    ) -> None:
        # The tracked application is a different job. It must not appear as
        # something a caller could accept.
        created = _seed(db_session, SeedApplication("ProgrammaticX", "Fullstack Developer"))

        result = _match(db_session, "ProgrammaticX", "Backend Engineer")

        assert created[0].id not in result.candidate_ids
        assert result.application_id != created[0].id

    def test_the_rule_holds_with_several_applications(self, db_session: Session) -> None:
        _seed(
            db_session,
            SeedApplication("Verdant Cloud", "Senior Platform Engineer"),
            SeedApplication("Verdant Cloud", "Data Engineer"),
            SeedApplication("Verdant Cloud", "Product Analyst"),
        )

        result = _match(db_session, "Verdant Cloud", "Marketing Manager")

        assert result.status is MatchStatus.NO_MATCH
        assert result.candidate_ids == []

    def test_the_reason_distinguishes_this_from_an_untracked_company(
        self, db_session: Session
    ) -> None:
        """Two different no-matches deserve two different explanations."""
        _seed(db_session, SeedApplication("ProgrammaticX", "Fullstack Developer"))

        conflicting = _match(db_session, "ProgrammaticX", "Backend Engineer")
        untracked = _match(db_session, "Nowhere Systems", "Backend Engineer")

        assert conflicting.reason != untracked.reason
        assert "company matches" in conflicting.reason.lower()
        assert conflicting.signals.company is not CompanySignal.NONE
        assert untracked.signals.company is CompanySignal.NONE

    def test_a_missing_role_is_still_treated_as_uncertainty(
        self, db_session: Session
    ) -> None:
        """The rule turns on the role being *given* and disagreeing.

        Absent evidence and contrary evidence are different things, and only the
        second one rules a match out.
        """
        _seed(
            db_session,
            SeedApplication("Acme Security", "Security Analyst"),
            SeedApplication("Acme Security", "Backend Engineer"),
        )

        assert _match(db_session, "Acme Security", None).status is MatchStatus.AMBIGUOUS
        assert _match(db_session, "Acme Security", "Data Engineer").status is (
            MatchStatus.NO_MATCH
        )


class TestSignalsAreInterpretable:
    def test_an_exact_match_reports_exact_signals(self, db_session: Session) -> None:
        _seed(db_session, SeedApplication("Acme Security", "Security Analyst"))

        result = _match(db_session, "Acme Security", "Security Analyst")

        assert result.signals.company is CompanySignal.EXACT
        assert result.signals.role is RoleSignal.EXACT
        assert result.confidence is MatchConfidence.HIGH

    def test_a_normalised_match_says_so(self, db_session: Session) -> None:
        _seed(db_session, SeedApplication("Acme Security", "Security Analyst"))

        result = _match(db_session, "Acme Security Ltd.", "Security Analyst")

        assert result.signals.company is CompanySignal.NORMALIZED
        assert result.confidence is MatchConfidence.MEDIUM
        assert "normalisation" in result.reason

    def test_the_description_is_human_readable(self, db_session: Session) -> None:
        _seed(
            db_session,
            SeedApplication("Acme Security", "Security Analyst"),
            SeedApplication("Acme Security", "Backend Engineer"),
        )

        described = _match(db_session, "Acme Security", "Backend Engineer").signals.describe()

        assert "company" in described
        assert "role" in described
        # Named signals, not a score to argue with.
        assert not re.search(r"\d\.\d{2,}", described)

    def test_every_outcome_carries_a_reason(self, db_session: Session) -> None:
        _seed(
            db_session,
            SeedApplication("Acme Security", "Security Analyst"),
            SeedApplication("Acme Security", "Backend Engineer"),
        )

        for company, role in (
            ("Acme Security", "Security Analyst"),
            ("Acme Security", None),
            ("Unknown Co", "Security Analyst"),
            (None, "Security Analyst"),
            ("Acme Security", "Something Else Entirely"),
        ):
            result = _match(db_session, company, role)
            assert result.reason.strip(), (company, role)

    def test_reasons_are_deterministic(self, db_session: Session) -> None:
        _seed(db_session, SeedApplication("Acme Security", "Security Analyst"))

        first = _match(db_session, "Acme Security", "Security Analyst")
        second = _match(db_session, "Acme Security", "Security Analyst")

        assert (first.status, first.reason, first.application_id) == (
            second.status,
            second.reason,
            second.application_id,
        )


class TestSoftDeletedApplications:
    def test_a_deleted_application_is_not_a_candidate(self, db_session: Session) -> None:
        from datetime import UTC, datetime

        created = _seed(db_session, SeedApplication("Acme Security", "Security Analyst"))
        created[0].deleted_at = datetime.now(UTC)
        db_session.commit()

        result = _match(db_session, "Acme Security", "Security Analyst")

        assert result.status is MatchStatus.NO_MATCH

    def test_deleting_one_sibling_resolves_the_ambiguity(self, db_session: Session) -> None:
        from datetime import UTC, datetime

        created = _seed(
            db_session,
            SeedApplication("Acme Security", "Security Analyst"),
            SeedApplication("Acme Security", "Backend Engineer"),
        )
        created[1].deleted_at = datetime.now(UTC)
        db_session.commit()

        result = _match(db_session, "Acme Security", None)

        assert result.status is MatchStatus.MATCHED
        assert result.confidence is MatchConfidence.LOW


class TestMatchingWritesNothing:
    def test_no_rows_are_created_or_changed(self, db_session: Session) -> None:
        created = _seed(
            db_session,
            SeedApplication("Acme Security", "Security Analyst"),
            SeedApplication("Acme Security", "Backend Engineer"),
        )
        before_status = created[0].status
        before_updated = created[0].updated_at
        events_before = len(
            db_session.execute(select(ApplicationEvent)).scalars().all()
        )

        for company, role in (
            ("Acme Security", "Security Analyst"),
            ("Acme Security", None),
            ("Nowhere Inc", "Anything"),
        ):
            _match(db_session, company, role)

        db_session.refresh(created[0])
        assert created[0].status == before_status
        assert created[0].updated_at == before_updated
        # No suggestion, no event, no status change — this phase decides
        # identity and does nothing about it.
        assert db_session.execute(select(Suggestion)).scalars().all() == []
        assert (
            len(db_session.execute(select(ApplicationEvent)).scalars().all())
            == events_before
        )

    def test_no_applications_are_created_for_an_unmatched_email(
        self, db_session: Session
    ) -> None:
        # A referral to an untracked company must not conjure an application.
        _match(db_session, "Halcyon Robotics", "Controls Engineer")

        assert db_session.execute(select(Application)).scalars().all() == []
