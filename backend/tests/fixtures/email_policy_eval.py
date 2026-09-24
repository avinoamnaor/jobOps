"""Hand-labelled evaluation set for the email → suggestion policy.

Ground truth for what JobOps should *propose*. Every case is a triple —
classification type, match result, current application state — paired with the
plan the policy must reach.

No company names, no real applications, no private data: the policy sees only a
message type, a match status, a status and a boolean, so there is nothing
identifying to anonymise in the first place.

Weighted toward refusal. A wrong proposal is not a harmless suggestion: it is a
prompt asking the user to confirm something false about their own job search,
and confirming it is one click.

Inert data. No network, no LLM, no database.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from app.enums import (
    ApplicationStatus,
    EmailMessageType,
    MatchConfidence,
    MatchStatus,
    ProposedActionType,
    SuggestionPlanOutcome,
)

SCHEDULED_AT = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)


@dataclass(frozen=True)
class PolicyCase:
    name: str
    message_type: EmailMessageType
    match_status: MatchStatus
    expected_outcome: SuggestionPlanOutcome
    expected_action_types: tuple[ProposedActionType, ...] = ()
    match_confidence: MatchConfidence | None = MatchConfidence.HIGH
    current_status: ApplicationStatus | None = None
    has_submitted_cv: bool = True
    company_name: str | None = None
    role_title: str | None = None
    event_datetime: datetime | None = None
    candidate_application_ids: tuple[int, ...] = ()
    expected_status_change: ApplicationStatus | None = None
    tests_that: str = ""


_RECORD = (ProposedActionType.RECORD_EVENT,)
_RECORD_AND_MOVE = (ProposedActionType.RECORD_EVENT, ProposedActionType.CHANGE_STATUS)


POLICY_CASES: tuple[PolicyCase, ...] = (
    # --- application_received, by current status ---------------------------
    PolicyCase(
        name="confirmation_matched_while_saved",
        tests_that="a confirmation on a saved application records it and moves it to applied",
        message_type=EmailMessageType.APPLICATION_RECEIVED,
        match_status=MatchStatus.MATCHED,
        current_status=ApplicationStatus.SAVED,
        expected_outcome=SuggestionPlanOutcome.PROPOSE_ACTIONS,
        expected_action_types=_RECORD_AND_MOVE,
        expected_status_change=ApplicationStatus.APPLIED,
    ),
    PolicyCase(
        name="confirmation_matched_while_saved_without_a_cv",
        tests_that=(
            "the submitted-CV rule is a real precondition: proposing applied "
            "here would be proposing an action the service refuses"
        ),
        message_type=EmailMessageType.APPLICATION_RECEIVED,
        match_status=MatchStatus.MATCHED,
        current_status=ApplicationStatus.SAVED,
        has_submitted_cv=False,
        expected_outcome=SuggestionPlanOutcome.PROPOSE_ACTIONS,
        expected_action_types=_RECORD,
    ),
    PolicyCase(
        name="confirmation_matched_while_already_applied",
        tests_that=(
            "the confirmation event is still proposed when the status is already "
            "correct — the evidence is worth keeping even when nothing changes"
        ),
        message_type=EmailMessageType.APPLICATION_RECEIVED,
        match_status=MatchStatus.MATCHED,
        current_status=ApplicationStatus.APPLIED,
        expected_outcome=SuggestionPlanOutcome.PROPOSE_ACTIONS,
        expected_action_types=_RECORD,
    ),
    PolicyCase(
        name="confirmation_matched_at_a_later_stage_never_regresses",
        tests_that=(
            "a late confirmation must not drag an interviewing application back "
            "to applied; the event records it without rewriting history"
        ),
        message_type=EmailMessageType.APPLICATION_RECEIVED,
        match_status=MatchStatus.MATCHED,
        current_status=ApplicationStatus.HR_INTERVIEW,
        expected_outcome=SuggestionPlanOutcome.PROPOSE_ACTIONS,
        expected_action_types=_RECORD,
    ),
    PolicyCase(
        name="confirmation_matched_while_rejected_does_not_reopen",
        tests_that="an inbound email never reopens a terminal application",
        message_type=EmailMessageType.APPLICATION_RECEIVED,
        match_status=MatchStatus.MATCHED,
        current_status=ApplicationStatus.REJECTED,
        expected_outcome=SuggestionPlanOutcome.PROPOSE_ACTIONS,
        expected_action_types=_RECORD,
    ),
    PolicyCase(
        name="confirmation_with_ambiguous_match",
        tests_that="an unresolved identity never produces a mutation",
        message_type=EmailMessageType.APPLICATION_RECEIVED,
        match_status=MatchStatus.AMBIGUOUS,
        match_confidence=None,
        candidate_application_ids=(11, 12),
        expected_outcome=SuggestionPlanOutcome.REVIEW_REQUIRED,
    ),
    PolicyCase(
        name="confirmation_no_match_proposes_creation_and_the_confirmation",
        tests_that=(
            "the strongest creation evidence there is — not 'someone thinks you "
            "would fit' but 'we have your application' — so the plan creates it "
            "and records the confirmation against what it created"
        ),
        message_type=EmailMessageType.APPLICATION_RECEIVED,
        match_status=MatchStatus.NO_MATCH,
        match_confidence=None,
        company_name="Northwind Analytics",
        role_title="Data Platform Engineer",
        expected_outcome=SuggestionPlanOutcome.PROPOSE_ACTIONS,
        expected_action_types=(
            ProposedActionType.CREATE_APPLICATION,
            ProposedActionType.RECORD_EVENT,
        ),
    ),
    PolicyCase(
        name="confirmation_no_match_without_a_company",
        tests_that=(
            "without a company there is nothing to prefill, so the creation is "
            "withheld rather than padded with invented fields"
        ),
        message_type=EmailMessageType.APPLICATION_RECEIVED,
        match_status=MatchStatus.NO_MATCH,
        match_confidence=None,
        expected_outcome=SuggestionPlanOutcome.REVIEW_REQUIRED,
    ),
    # --- rejection ----------------------------------------------------------
    PolicyCase(
        name="rejection_matched_on_an_active_application",
        tests_that="a rejection records the event and closes the application",
        message_type=EmailMessageType.REJECTION,
        match_status=MatchStatus.MATCHED,
        current_status=ApplicationStatus.HR_INTERVIEW,
        expected_outcome=SuggestionPlanOutcome.PROPOSE_ACTIONS,
        expected_action_types=_RECORD_AND_MOVE,
        expected_status_change=ApplicationStatus.REJECTED,
    ),
    PolicyCase(
        name="rejection_matched_while_saved_needs_no_cv",
        tests_that=(
            "rejected is exempt from the submitted-CV rule, so this proposal is "
            "executable where a move to applied would not be"
        ),
        message_type=EmailMessageType.REJECTION,
        match_status=MatchStatus.MATCHED,
        current_status=ApplicationStatus.SAVED,
        has_submitted_cv=False,
        expected_outcome=SuggestionPlanOutcome.PROPOSE_ACTIONS,
        expected_action_types=_RECORD_AND_MOVE,
        expected_status_change=ApplicationStatus.REJECTED,
    ),
    PolicyCase(
        name="rejection_when_already_rejected",
        tests_that=(
            "the same status change is not proposed twice; the event may still "
            "be, since a second rejection email is still a fact"
        ),
        message_type=EmailMessageType.REJECTION,
        match_status=MatchStatus.MATCHED,
        current_status=ApplicationStatus.REJECTED,
        expected_outcome=SuggestionPlanOutcome.PROPOSE_ACTIONS,
        expected_action_types=_RECORD,
    ),
    PolicyCase(
        name="rejection_with_ambiguous_match",
        tests_that="a rejection is never routed to a guessed application",
        message_type=EmailMessageType.REJECTION,
        match_status=MatchStatus.AMBIGUOUS,
        match_confidence=None,
        candidate_application_ids=(21, 22),
        expected_outcome=SuggestionPlanOutcome.REVIEW_REQUIRED,
    ),
    PolicyCase(
        name="rejection_with_no_match",
        tests_that="a rejection for an untracked role is review, not invention",
        message_type=EmailMessageType.REJECTION,
        match_status=MatchStatus.NO_MATCH,
        match_confidence=None,
        expected_outcome=SuggestionPlanOutcome.REVIEW_REQUIRED,
    ),
    # --- referral -----------------------------------------------------------
    PolicyCase(
        name="referral_matched_is_informational_only",
        tests_that=(
            "being referred is not applying: the referral is recorded but the "
            "application is not advanced"
        ),
        message_type=EmailMessageType.REFERRAL_OR_RECOMMENDATION,
        match_status=MatchStatus.MATCHED,
        current_status=ApplicationStatus.SAVED,
        expected_outcome=SuggestionPlanOutcome.INFORMATIONAL,
        expected_action_types=_RECORD,
    ),
    PolicyCase(
        name="referral_no_match_with_company_and_role",
        tests_that="the one case that proposes creating an application, prefilled",
        message_type=EmailMessageType.REFERRAL_OR_RECOMMENDATION,
        match_status=MatchStatus.NO_MATCH,
        match_confidence=None,
        company_name="Halcyon Robotics",
        role_title="Controls Engineer",
        expected_outcome=SuggestionPlanOutcome.PROPOSE_ACTIONS,
        expected_action_types=(ProposedActionType.CREATE_APPLICATION,),
    ),
    PolicyCase(
        name="referral_no_match_with_missing_role",
        tests_that=(
            "a company alone is enough to prefill a creation proposal; the "
            "missing role is flagged rather than invented"
        ),
        message_type=EmailMessageType.REFERRAL_OR_RECOMMENDATION,
        match_status=MatchStatus.NO_MATCH,
        match_confidence=None,
        company_name="Halcyon Robotics",
        expected_outcome=SuggestionPlanOutcome.PROPOSE_ACTIONS,
        expected_action_types=(ProposedActionType.CREATE_APPLICATION,),
    ),
    PolicyCase(
        name="referral_no_match_with_missing_company",
        tests_that=(
            "without a company there is nothing sensible to create, so the "
            "proposal is withheld rather than padded with invented fields"
        ),
        message_type=EmailMessageType.REFERRAL_OR_RECOMMENDATION,
        match_status=MatchStatus.NO_MATCH,
        match_confidence=None,
        expected_outcome=SuggestionPlanOutcome.REVIEW_REQUIRED,
    ),
    # --- interview and assessment -------------------------------------------
    PolicyCase(
        name="interview_invitation_matched_records_no_status_change",
        tests_that=(
            "an invitation is not an interview, and there is no lifecycle "
            "status for 'invited' to move to"
        ),
        message_type=EmailMessageType.INTERVIEW_INVITATION,
        match_status=MatchStatus.MATCHED,
        current_status=ApplicationStatus.APPLIED,
        expected_outcome=SuggestionPlanOutcome.PROPOSE_ACTIONS,
        expected_action_types=_RECORD,
    ),
    PolicyCase(
        name="interview_scheduled_matched_with_datetime",
        tests_that="a stated time is carried onto the proposed event",
        message_type=EmailMessageType.INTERVIEW_SCHEDULED,
        match_status=MatchStatus.MATCHED,
        current_status=ApplicationStatus.APPLIED,
        event_datetime=SCHEDULED_AT,
        expected_outcome=SuggestionPlanOutcome.PROPOSE_ACTIONS,
        expected_action_types=_RECORD,
    ),
    PolicyCase(
        name="interview_scheduled_matched_without_datetime",
        tests_that="no time is invented when classification supplied none",
        message_type=EmailMessageType.INTERVIEW_SCHEDULED,
        match_status=MatchStatus.MATCHED,
        current_status=ApplicationStatus.APPLIED,
        expected_outcome=SuggestionPlanOutcome.PROPOSE_ACTIONS,
        expected_action_types=_RECORD,
    ),
    PolicyCase(
        name="assessment_requested_matched",
        tests_that=(
            "an assessment is recorded but not mapped to take_home: a screening "
            "form is not a take-home and the email cannot tell them apart"
        ),
        message_type=EmailMessageType.ASSESSMENT_REQUESTED,
        match_status=MatchStatus.MATCHED,
        current_status=ApplicationStatus.APPLIED,
        expected_outcome=SuggestionPlanOutcome.PROPOSE_ACTIONS,
        expected_action_types=_RECORD,
    ),
    # --- offer --------------------------------------------------------------
    PolicyCase(
        name="offer_received_matched",
        tests_that="an offer records the event and proposes the offer status",
        message_type=EmailMessageType.OFFER_RECEIVED,
        match_status=MatchStatus.MATCHED,
        current_status=ApplicationStatus.FINAL_INTERVIEW,
        expected_outcome=SuggestionPlanOutcome.PROPOSE_ACTIONS,
        expected_action_types=_RECORD_AND_MOVE,
        expected_status_change=ApplicationStatus.OFFER,
    ),
    PolicyCase(
        name="offer_received_without_a_submitted_cv",
        tests_that="offer is a submitted-state status, so the CV precondition applies",
        message_type=EmailMessageType.OFFER_RECEIVED,
        match_status=MatchStatus.MATCHED,
        current_status=ApplicationStatus.FINAL_INTERVIEW,
        has_submitted_cv=False,
        expected_outcome=SuggestionPlanOutcome.PROPOSE_ACTIONS,
        expected_action_types=_RECORD,
    ),
    # --- outreach and process ------------------------------------------------
    PolicyCase(
        name="recruiter_outreach_matched_is_informational",
        tests_that="generic recruiter contact is news, never an inferred interview",
        message_type=EmailMessageType.RECRUITER_OUTREACH,
        match_status=MatchStatus.MATCHED,
        current_status=ApplicationStatus.APPLIED,
        expected_outcome=SuggestionPlanOutcome.INFORMATIONAL,
        expected_action_types=_RECORD,
    ),
    PolicyCase(
        name="recruiter_outreach_no_match_offers_optional_creation",
        tests_that=(
            "news first, action second: the candidate has not applied and may "
            "never want to, so tracking it is offered rather than recommended"
        ),
        message_type=EmailMessageType.RECRUITER_OUTREACH,
        match_status=MatchStatus.NO_MATCH,
        match_confidence=None,
        company_name="Larkfield Systems",
        role_title="Senior Data Engineer",
        expected_outcome=SuggestionPlanOutcome.INFORMATIONAL,
        expected_action_types=(ProposedActionType.CREATE_APPLICATION,),
    ),
    PolicyCase(
        name="recruiter_outreach_no_match_without_a_company",
        tests_that="insufficient company information falls back to review",
        message_type=EmailMessageType.RECRUITER_OUTREACH,
        match_status=MatchStatus.NO_MATCH,
        match_confidence=None,
        role_title="Senior Data Engineer",
        expected_outcome=SuggestionPlanOutcome.REVIEW_REQUIRED,
    ),
    PolicyCase(
        name="process_update_matched_is_informational",
        tests_that="'moving forward' is not an interview and not an offer",
        message_type=EmailMessageType.PROCESS_UPDATE,
        match_status=MatchStatus.MATCHED,
        current_status=ApplicationStatus.APPLIED,
        expected_outcome=SuggestionPlanOutcome.INFORMATIONAL,
        expected_action_types=_RECORD,
    ),
    # --- low identity confidence --------------------------------------------
    PolicyCase(
        name="low_confidence_match_never_mutates",
        tests_that=(
            "LOW means the company matched and the role was absent — enough to "
            "show someone, not enough to move their application"
        ),
        message_type=EmailMessageType.REJECTION,
        match_status=MatchStatus.MATCHED,
        match_confidence=MatchConfidence.LOW,
        current_status=ApplicationStatus.APPLIED,
        expected_outcome=SuggestionPlanOutcome.REVIEW_REQUIRED,
    ),
    PolicyCase(
        name="medium_confidence_match_is_treated_like_high",
        tests_that=(
            "MEDIUM only means normalisation was needed to agree, which is a "
            "formatting fact rather than an identity risk"
        ),
        message_type=EmailMessageType.REJECTION,
        match_status=MatchStatus.MATCHED,
        match_confidence=MatchConfidence.MEDIUM,
        current_status=ApplicationStatus.APPLIED,
        expected_outcome=SuggestionPlanOutcome.PROPOSE_ACTIONS,
        expected_action_types=_RECORD_AND_MOVE,
        expected_status_change=ApplicationStatus.REJECTED,
    ),
    # --- types that never act ------------------------------------------------
    PolicyCase(
        name="job_alert_does_nothing_even_when_matched",
        tests_that=(
            "a job alert names real companies and roles but is not evidence of "
            "an application process, so a match must not rescue it"
        ),
        message_type=EmailMessageType.JOB_ALERT,
        match_status=MatchStatus.MATCHED,
        current_status=ApplicationStatus.APPLIED,
        expected_outcome=SuggestionPlanOutcome.NO_ACTION,
    ),
    PolicyCase(
        name="privacy_notice_does_nothing",
        tests_that="data-retention administration is not timeline material",
        message_type=EmailMessageType.PRIVACY_OR_RETENTION_NOTICE,
        match_status=MatchStatus.MATCHED,
        current_status=ApplicationStatus.APPLIED,
        expected_outcome=SuggestionPlanOutcome.NO_ACTION,
    ),
    PolicyCase(
        name="post_interview_survey_does_nothing",
        tests_that=(
            "a survey implies an interview happened, but inferring retrospective "
            "history from it was explicitly deferred"
        ),
        message_type=EmailMessageType.POST_INTERVIEW_SURVEY,
        match_status=MatchStatus.MATCHED,
        current_status=ApplicationStatus.APPLIED,
        expected_outcome=SuggestionPlanOutcome.NO_ACTION,
    ),
    PolicyCase(
        name="irrelevant_does_nothing",
        tests_that="marketing is not recruitment",
        message_type=EmailMessageType.IRRELEVANT,
        match_status=MatchStatus.NO_MATCH,
        match_confidence=None,
        expected_outcome=SuggestionPlanOutcome.NO_ACTION,
    ),
    # --- other recruitment ---------------------------------------------------
    PolicyCase(
        name="other_recruitment_matched_records_only",
        tests_that="the catch-all records the email without inferring a change",
        message_type=EmailMessageType.OTHER_RECRUITMENT,
        match_status=MatchStatus.MATCHED,
        current_status=ApplicationStatus.APPLIED,
        expected_outcome=SuggestionPlanOutcome.PROPOSE_ACTIONS,
        expected_action_types=_RECORD,
    ),
    PolicyCase(
        name="matched_but_no_application_state_supplied",
        tests_that="a caller error becomes review, never a decision made anyway",
        message_type=EmailMessageType.REJECTION,
        match_status=MatchStatus.MATCHED,
        current_status=None,
        expected_outcome=SuggestionPlanOutcome.REVIEW_REQUIRED,
    ),
)

POLICY_CASES_BY_NAME: dict[str, PolicyCase] = {case.name: case for case in POLICY_CASES}

__all__ = ["POLICY_CASES", "POLICY_CASES_BY_NAME", "PolicyCase", "SCHEDULED_AT"]
