"""Hand-labelled evaluation set for email-to-application matching.

Ground truth for the matcher, written before the matcher was tuned against any
real data. Every company, role and scenario is fictional; nothing from a real
job search appears here, which `tests/test_email_matching.py` enforces
mechanically rather than trusting this paragraph.

Each case declares the applications that exist, the company and role the
classifier extracted, and the decision the matcher must reach. Applications are
referred to by *index* rather than database id, so the dataset never depends on
insertion order or sequence state.

The set is weighted toward cases that must NOT match. That is deliberate: a
false NO_MATCH costs one manual link, while a false MATCHED files an employer's
rejection against the wrong job — and only one of those is discoverable by the
person it happens to.

Inert data. No network, no LLM, no provider.
"""

from dataclasses import dataclass

from app.enums import MatchConfidence, MatchStatus


@dataclass(frozen=True)
class SeedApplication:
    """An application that exists when the case runs."""

    company_name: str
    role_title: str


@dataclass(frozen=True)
class MatchingCase:
    name: str
    applications: tuple[SeedApplication, ...]
    # What the classifier extracted from the email.
    company_name: str | None
    role_title: str | None
    expected_status: MatchStatus
    # Index into `applications`; only meaningful when the expectation is MATCHED.
    expected_application_index: int | None = None
    expected_confidence: MatchConfidence | None = None
    # How many candidates an ambiguous answer should offer.
    expected_candidate_count: int | None = None
    tests_that: str = ""


_ACME = SeedApplication("Acme Security", "Security Analyst")
_ACME_BACKEND = SeedApplication("Acme Security", "Backend Engineer")


MATCHING_CASES: tuple[MatchingCase, ...] = (
    # --- 1. the easy case, which must still work ---------------------------
    MatchingCase(
        name="exact_company_and_role",
        tests_that="identical company and role match at high confidence",
        applications=(_ACME,),
        company_name="Acme Security",
        role_title="Security Analyst",
        expected_status=MatchStatus.MATCHED,
        expected_application_index=0,
        expected_confidence=MatchConfidence.HIGH,
    ),
    # --- 2. formatting only -------------------------------------------------
    MatchingCase(
        name="case_and_punctuation_differences",
        tests_that=(
            "case and punctuation are formatting, not identity — but the match "
            "drops to medium because normalisation was needed to reach it"
        ),
        applications=(_ACME,),
        company_name="ACME  SECURITY",
        role_title="security analyst",
        expected_status=MatchStatus.MATCHED,
        expected_application_index=0,
        expected_confidence=MatchConfidence.MEDIUM,
    ),
    # --- 3. legal suffix ----------------------------------------------------
    MatchingCase(
        name="company_legal_suffix_variation",
        tests_that=(
            "a trailing legal suffix is safe to fold, because the role also "
            "agrees — the suffix alone would not be enough"
        ),
        applications=(_ACME,),
        company_name="Acme Security Ltd.",
        role_title="Security Analyst",
        expected_status=MatchStatus.MATCHED,
        expected_application_index=0,
        expected_confidence=MatchConfidence.MEDIUM,
    ),
    # --- 4. role formatting -------------------------------------------------
    MatchingCase(
        name="role_formatting_variation",
        tests_that="a parenthesised note is formatting and does not change the job",
        applications=(SeedApplication("Meridian Freight", "Backend Engineer"),),
        company_name="Meridian Freight",
        role_title="Backend Engineer (m/f/d)",
        expected_status=MatchStatus.MATCHED,
        expected_application_index=0,
        expected_confidence=MatchConfidence.MEDIUM,
    ),
    # --- 5. same company, clearly different roles ---------------------------
    MatchingCase(
        name="same_company_two_different_roles_picks_the_right_one",
        tests_that=(
            "the role is what discriminates between siblings at one employer; "
            "the other application must not be touched"
        ),
        applications=(_ACME, _ACME_BACKEND),
        company_name="Acme Security",
        role_title="Backend Engineer",
        expected_status=MatchStatus.MATCHED,
        expected_application_index=1,
        expected_confidence=MatchConfidence.HIGH,
    ),
    # --- 6. same company, similar roles -------------------------------------
    MatchingCase(
        name="same_company_similar_roles_still_discriminates",
        tests_that=(
            "seniority is preserved by role normalisation, so Senior and "
            "Junior are different jobs rather than one fuzzy cluster"
        ),
        applications=(
            SeedApplication("Verdant Cloud", "Senior Platform Engineer"),
            SeedApplication("Verdant Cloud", "Junior Platform Engineer"),
        ),
        company_name="Verdant Cloud",
        role_title="Junior Platform Engineer",
        expected_status=MatchStatus.MATCHED,
        expected_application_index=1,
        expected_confidence=MatchConfidence.HIGH,
    ),
    # --- 7. company matches, role missing, several applications -------------
    MatchingCase(
        name="company_matches_but_role_missing_with_several_applications",
        tests_that=(
            "the headline refusal: matching on company alone when the employer "
            "has more than one open application would be a coin flip"
        ),
        applications=(_ACME, _ACME_BACKEND),
        company_name="Acme Security",
        role_title=None,
        expected_status=MatchStatus.AMBIGUOUS,
        expected_candidate_count=2,
    ),
    # --- 8. role matches, company does not ----------------------------------
    MatchingCase(
        name="role_matches_but_company_is_different",
        tests_that=(
            "role similarity never crosses employers — this is the error that "
            "would file one company's rejection against another's job"
        ),
        applications=(SeedApplication("Brightpath Systems", "Security Analyst"),),
        company_name="Ironleaf Software",
        role_title="Security Analyst",
        expected_status=MatchStatus.NO_MATCH,
    ),
    # --- 9. no company ------------------------------------------------------
    MatchingCase(
        name="missing_company_never_matches",
        tests_that="without an employer there is nothing to identify, however exact the role",
        applications=(_ACME,),
        company_name=None,
        role_title="Security Analyst",
        expected_status=MatchStatus.NO_MATCH,
    ),
    # --- 10. unique company, role missing -----------------------------------
    MatchingCase(
        name="unique_company_with_missing_role_is_low_confidence",
        tests_that=(
            "a unique candidate is still only company-level evidence; the "
            "status may be matched but the confidence must not be inflated"
        ),
        applications=(_ACME,),
        company_name="Acme Security",
        role_title=None,
        expected_status=MatchStatus.MATCHED,
        expected_application_index=0,
        expected_confidence=MatchConfidence.LOW,
    ),
    # --- 11. nothing tracked ------------------------------------------------
    MatchingCase(
        name="no_applications_exist_at_all",
        tests_that="an empty tracker yields no match rather than an error",
        applications=(),
        company_name="Acme Security",
        role_title="Security Analyst",
        expected_status=MatchStatus.NO_MATCH,
    ),
    # --- 12. referral with nothing to match ---------------------------------
    MatchingCase(
        name="referral_for_an_untracked_company",
        tests_that=(
            "a referral to a company with no application is no_match — offering "
            "to create one is a later product decision, not the matcher's"
        ),
        applications=(_ACME,),
        company_name="Halcyon Robotics",
        role_title="Controls Engineer",
        expected_status=MatchStatus.NO_MATCH,
    ),
    # --- 13. rejection among siblings ---------------------------------------
    MatchingCase(
        name="rejection_matched_to_the_correct_role_among_several",
        tests_that=(
            "a rejection is routed by company and role like anything else; "
            "being a rejection tells the matcher nothing about which job"
        ),
        applications=(
            SeedApplication("Silverquay Group", "Data Engineer"),
            SeedApplication("Silverquay Group", "Site Reliability Engineer"),
            SeedApplication("Silverquay Group", "Product Analyst"),
        ),
        company_name="Silverquay Group",
        role_title="Site Reliability Engineer",
        expected_status=MatchStatus.MATCHED,
        expected_application_index=1,
        expected_confidence=MatchConfidence.HIGH,
    ),
    # --- 14. confirmation among siblings ------------------------------------
    MatchingCase(
        name="application_confirmation_matched_to_the_correct_role",
        tests_that="the same routing applies to a confirmation, by the same signals",
        applications=(
            SeedApplication("Northwind Analytics", "Data Platform Engineer"),
            SeedApplication("Northwind Analytics", "Analytics Engineer"),
        ),
        company_name="Northwind Analytics",
        role_title="Data Platform Engineer",
        expected_status=MatchStatus.MATCHED,
        expected_application_index=0,
        expected_confidence=MatchConfidence.HIGH,
    ),
    # --- 15. duplicate role at one employer ---------------------------------
    MatchingCase(
        name="duplicate_role_at_one_company_must_not_auto_match",
        tests_that=(
            "two applications for the same role at the same company are "
            "genuinely indistinguishable from an email; picking the lower id "
            "would be arbitrary dressed up as a decision"
        ),
        applications=(_ACME, SeedApplication("Acme Security", "Security Analyst")),
        company_name="Acme Security",
        role_title="Security Analyst",
        expected_status=MatchStatus.AMBIGUOUS,
        expected_candidate_count=2,
    ),
    # --- 16. names that only look alike -------------------------------------
    MatchingCase(
        name="similar_looking_but_different_companies",
        tests_that=(
            "shared words are not shared identity — normalisation folds "
            "formatting, never distinct names"
        ),
        applications=(SeedApplication("Acme Security", "Security Analyst"),),
        company_name="Acme Security Solutions",
        role_title="Security Analyst",
        expected_status=MatchStatus.NO_MATCH,
    ),
    # --- 17. role named differently on each side ----------------------------
    MatchingCase(
        name="company_matches_but_role_is_named_differently",
        tests_that=(
            "where deterministic matching stops, and declines rather than "
            "hedges: 'SecOps' and 'Security Operations Analyst' may well be the "
            "same job, but nothing here can establish that, and a named role "
            "that does not agree is evidence against a match"
        ),
        applications=(SeedApplication("Aidoc Systems", "Security Operations Analyst"),),
        company_name="Aidoc Systems",
        role_title="SecOps",
        expected_status=MatchStatus.NO_MATCH,
        expected_candidate_count=0,
    ),
    # --- 19. company matches, role plainly different ------------------------
    MatchingCase(
        name="company_matches_but_role_conflicts",
        tests_that=(
            "an explicit conflicting role is negative evidence: the tracked "
            "application is a different job, so surfacing it as a candidate "
            "would invite a confirmation the email argues against"
        ),
        applications=(SeedApplication("ProgrammaticX", "Fullstack Developer"),),
        company_name="ProgrammaticX",
        role_title="Backend Engineer",
        expected_status=MatchStatus.NO_MATCH,
        expected_candidate_count=0,
    ),
    # --- 20. conflicting role, several applications -------------------------
    MatchingCase(
        name="company_matches_several_but_the_role_conflicts_with_all",
        tests_that=(
            "the same rule holds with more candidates — a longer shortlist of "
            "jobs the email did not name is not better evidence"
        ),
        applications=(
            SeedApplication("Verdant Cloud", "Senior Platform Engineer"),
            SeedApplication("Verdant Cloud", "Data Engineer"),
            SeedApplication("Verdant Cloud", "Product Analyst"),
        ),
        company_name="Verdant Cloud",
        role_title="Marketing Manager",
        expected_status=MatchStatus.NO_MATCH,
        expected_candidate_count=0,
    ),
    # --- 18. soft-deleted applications are invisible ------------------------
    MatchingCase(
        name="only_live_applications_are_considered",
        tests_that="a deleted application is not a candidate (asserted separately)",
        applications=(_ACME,),
        company_name="Acme Security",
        role_title="Security Analyst",
        expected_status=MatchStatus.MATCHED,
        expected_application_index=0,
        expected_confidence=MatchConfidence.HIGH,
    ),
)

MATCHING_CASES_BY_NAME: dict[str, MatchingCase] = {
    case.name: case for case in MATCHING_CASES
}

__all__ = ["MATCHING_CASES", "MATCHING_CASES_BY_NAME", "MatchingCase", "SeedApplication"]
