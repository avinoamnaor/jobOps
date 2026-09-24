"""Tests for the deterministic email → suggestion policy (Phase 6.2C-0).

Pure unit tests: the policy takes no session and performs no I/O, so these need
no database, no provider and no network. That is itself part of the contract —
a policy that could reach a database could also change one.

Most of these assert refusals. A wrong proposal is not a harmless suggestion; it
is a prompt asking the user to confirm something false about their own job
search, and confirming it takes one click.
"""

import pytest

from app.enums import (
    NO_ACTION_MESSAGE_TYPES,
    ApplicationChannel,
    ApplicationStatus,
    EmailMessageType,
    MatchConfidence,
    MatchStatus,
    ProposedActionType,
    SuggestionPlanOutcome,
)
from app.services.email_policy import (
    STATUS_FOR_MESSAGE,
    ApplicationContext,
    PolicyInput,
    decide,
    idempotency_key,
)
from tests.fixtures.email_policy_eval import (
    POLICY_CASES,
    POLICY_CASES_BY_NAME,
    SCHEDULED_AT,
    PolicyCase,
)

# The three types that may propose creating an application when nothing matches,
# ordered by how strongly the email evidences that an application exists.
CREATION_CAPABLE_TYPES = frozenset(
    {
        EmailMessageType.APPLICATION_RECEIVED,
        EmailMessageType.REFERRAL_OR_RECOMMENDATION,
        EmailMessageType.RECRUITER_OUTREACH,
    }
)


def _input_for(case: PolicyCase) -> PolicyInput:
    application = None
    if case.current_status is not None:
        application = ApplicationContext(
            application_id=99,
            status=case.current_status,
            has_submitted_cv=case.has_submitted_cv,
        )
    return PolicyInput(
        message_type=case.message_type,
        match_status=case.match_status,
        match_confidence=case.match_confidence,
        application=application,
        company_name=case.company_name,
        role_title=case.role_title,
        event_datetime=case.event_datetime,
        candidate_application_ids=case.candidate_application_ids,
    )


def _plan(
    message_type: EmailMessageType,
    *,
    match_status: MatchStatus = MatchStatus.MATCHED,
    confidence: MatchConfidence | None = MatchConfidence.HIGH,
    status: ApplicationStatus | None = ApplicationStatus.APPLIED,
    has_cv: bool = True,
    **kwargs,
):
    application = (
        ApplicationContext(application_id=99, status=status, has_submitted_cv=has_cv)
        if status is not None
        else None
    )
    return decide(
        PolicyInput(
            message_type=message_type,
            match_status=match_status,
            match_confidence=confidence,
            application=application,
            **kwargs,
        )
    )


# ---------------------------------------------------------------------------
# The eval dataset
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", POLICY_CASES, ids=lambda case: case.name)
def test_eval_case(case: PolicyCase) -> None:
    plan = decide(_input_for(case))

    assert plan.outcome is case.expected_outcome, (
        f"{case.name}: {case.tests_that}\n  reason given: {plan.reason}"
    )
    assert tuple(action.action_type for action in plan.actions) == case.expected_action_types

    status_changes = [
        action.to_status
        for action in plan.actions
        if action.action_type is ProposedActionType.CHANGE_STATUS
    ]
    if case.expected_status_change is not None:
        assert status_changes == [case.expected_status_change]
    else:
        assert status_changes == []


@pytest.mark.parametrize("case", POLICY_CASES, ids=lambda case: case.name)
def test_every_case_explains_itself(case: PolicyCase) -> None:
    assert case.tests_that.strip()


@pytest.mark.parametrize("case", POLICY_CASES, ids=lambda case: case.name)
def test_every_plan_carries_a_reason(case: PolicyCase) -> None:
    assert decide(_input_for(case)).reason.strip()


class TestDatasetShape:
    def test_it_covers_every_message_type(self) -> None:
        covered = {case.message_type for case in POLICY_CASES}
        assert covered == set(EmailMessageType)

    def test_status_changes_are_rare(self) -> None:
        """Moving someone's application is the consequential proposal.

        Recording an event is cheap and reversible; changing a status rewrites
        what the tracker claims about a real job search. The dataset should show
        that the policy reaches for it only occasionally — measured on status
        changes specifically, because counting "cases with any action" would
        score an event-only plan as though it were a mutation.
        """
        with_status_change = sum(
            1 for case in POLICY_CASES if case.expected_status_change is not None
        )
        assert with_status_change <= len(POLICY_CASES) / 4

    def test_most_cases_either_refuse_or_only_record(self) -> None:
        restrained = sum(
            1
            for case in POLICY_CASES
            if case.expected_status_change is None
            and ProposedActionType.CREATE_APPLICATION not in case.expected_action_types
        )
        assert restrained >= len(POLICY_CASES) * 0.7


# ---------------------------------------------------------------------------
# Safety properties
# ---------------------------------------------------------------------------


class TestUncertainIdentityNeverMutates:
    @pytest.mark.parametrize(
        "message_type",
        [m for m in EmailMessageType if m not in NO_ACTION_MESSAGE_TYPES],
    )
    def test_an_ambiguous_match_never_proposes_an_action(
        self, message_type: EmailMessageType
    ) -> None:
        plan = _plan(message_type, match_status=MatchStatus.AMBIGUOUS, confidence=None)

        assert plan.outcome is SuggestionPlanOutcome.REVIEW_REQUIRED
        assert plan.actions == ()
        assert plan.application_id is None

    @pytest.mark.parametrize(
        "message_type",
        [
            m
            for m in EmailMessageType
            if m not in NO_ACTION_MESSAGE_TYPES and m not in CREATION_CAPABLE_TYPES
        ],
    )
    def test_no_match_never_targets_an_application(
        self, message_type: EmailMessageType
    ) -> None:
        plan = _plan(
            message_type, match_status=MatchStatus.NO_MATCH, confidence=None, status=None
        )

        assert plan.outcome is SuggestionPlanOutcome.REVIEW_REQUIRED
        assert plan.application_id is None
        assert all(action.application_id is None for action in plan.actions)

    def test_candidate_ids_are_offered_as_context_not_a_choice(self) -> None:
        plan = _plan(
            EmailMessageType.REJECTION,
            match_status=MatchStatus.AMBIGUOUS,
            confidence=None,
            candidate_application_ids=(7, 8),
        )

        assert plan.candidate_application_ids == (7, 8)
        # Context, not a decision: nothing is targeted and nothing is proposed.
        assert plan.application_id is None
        assert plan.actions == ()

    def test_low_confidence_is_conservative(self) -> None:
        """LOW means the company matched and the role was absent.

        Enough to show someone; not enough to move their application.
        """
        plan = _plan(EmailMessageType.REJECTION, confidence=MatchConfidence.LOW)

        assert plan.outcome is SuggestionPlanOutcome.REVIEW_REQUIRED
        assert plan.actions == ()

    def test_medium_and_high_are_treated_alike(self) -> None:
        # MEDIUM only records that normalisation was needed to agree, which is a
        # formatting fact rather than an identity risk. Distinguishing them would
        # be an arbitrary threshold.
        high = _plan(EmailMessageType.REJECTION, confidence=MatchConfidence.HIGH)
        medium = _plan(EmailMessageType.REJECTION, confidence=MatchConfidence.MEDIUM)

        assert high.outcome is medium.outcome
        assert [a.action_type for a in high.actions] == [a.action_type for a in medium.actions]


class TestStatusSafety:
    def test_a_confirmation_never_regresses_a_later_stage(self) -> None:
        for status in (
            ApplicationStatus.HR_INTERVIEW,
            ApplicationStatus.TECHNICAL_INTERVIEW,
            ApplicationStatus.FINAL_INTERVIEW,
            ApplicationStatus.OFFER,
        ):
            plan = _plan(EmailMessageType.APPLICATION_RECEIVED, status=status)
            assert [a.action_type for a in plan.actions] == [ProposedActionType.RECORD_EVENT]

    def test_a_confirmation_never_reopens_a_terminal_application(self) -> None:
        for status in (
            ApplicationStatus.REJECTED,
            ApplicationStatus.WITHDRAWN,
            ApplicationStatus.ACCEPTED,
            ApplicationStatus.ON_HOLD,
        ):
            plan = _plan(EmailMessageType.APPLICATION_RECEIVED, status=status)
            assert all(
                a.action_type is not ProposedActionType.CHANGE_STATUS for a in plan.actions
            )

    def test_the_confirmation_event_survives_when_the_status_does_not_change(self) -> None:
        # The evidence is worth keeping even when nothing moves.
        plan = _plan(EmailMessageType.APPLICATION_RECEIVED, status=ApplicationStatus.APPLIED)

        assert len(plan.actions) == 1
        assert plan.actions[0].action_type is ProposedActionType.RECORD_EVENT
        assert plan.actions[0].summary == "Application confirmed by email"

    def test_an_unexecutable_status_change_is_never_proposed(self) -> None:
        """The submitted-CV rule is a real precondition, not a formality.

        Proposing `applied` here would put a button in front of the user that
        raises when pressed.
        """
        plan = _plan(
            EmailMessageType.APPLICATION_RECEIVED,
            status=ApplicationStatus.SAVED,
            has_cv=False,
        )

        assert all(
            a.action_type is not ProposedActionType.CHANGE_STATUS for a in plan.actions
        )
        assert any("submitted_cv_required" in note for note in plan.field_notes)

    def test_the_same_status_is_never_proposed_twice(self) -> None:
        plan = _plan(EmailMessageType.REJECTION, status=ApplicationStatus.REJECTED)

        assert all(
            a.action_type is not ProposedActionType.CHANGE_STATUS for a in plan.actions
        )

    def test_only_three_message_types_ever_propose_a_status(self) -> None:
        # Documented restraint: everything else records an event, because the
        # existing lifecycle has no member that clearly corresponds.
        assert set(STATUS_FOR_MESSAGE) == {
            EmailMessageType.APPLICATION_RECEIVED,
            EmailMessageType.REJECTION,
            EmailMessageType.OFFER_RECEIVED,
        }

    def test_the_policy_uses_the_canonical_blocker(self) -> None:
        """Not a second copy of the lifecycle rules.

        `change_status` and the policy call the same function, so a change to
        one cannot silently diverge from the other.
        """
        import inspect

        import app.services.email_policy as policy

        source = inspect.getsource(policy)
        assert "status_change_blocker" in source
        assert "STATUSES_REQUIRING_SUBMITTED_CV" not in source


class TestReferral:
    def test_a_referral_with_no_match_proposes_creation_not_applied(self) -> None:
        plan = _plan(
            EmailMessageType.REFERRAL_OR_RECOMMENDATION,
            match_status=MatchStatus.NO_MATCH,
            confidence=None,
            status=None,
            company_name="Halcyon Robotics",
            role_title="Controls Engineer",
        )

        assert [a.action_type for a in plan.actions] == [
            ProposedActionType.CREATE_APPLICATION
        ]
        action = plan.actions[0]
        assert action.company_name == "Halcyon Robotics"
        assert action.role_title == "Controls Engineer"
        assert action.application_channel is ApplicationChannel.REFERRAL
        # Being referred is not having applied.
        assert action.to_status is None

    def test_a_referral_without_a_company_proposes_nothing(self) -> None:
        plan = _plan(
            EmailMessageType.REFERRAL_OR_RECOMMENDATION,
            match_status=MatchStatus.NO_MATCH,
            confidence=None,
            status=None,
        )

        assert plan.outcome is SuggestionPlanOutcome.REVIEW_REQUIRED
        assert plan.actions == ()

    def test_a_missing_role_is_flagged_rather_than_invented(self) -> None:
        plan = _plan(
            EmailMessageType.REFERRAL_OR_RECOMMENDATION,
            match_status=MatchStatus.NO_MATCH,
            confidence=None,
            status=None,
            company_name="Halcyon Robotics",
        )

        assert plan.actions[0].role_title is None
        assert any("role" in note.lower() for note in plan.field_notes)

    def test_a_matched_referral_does_not_advance_the_application(self) -> None:
        plan = _plan(
            EmailMessageType.REFERRAL_OR_RECOMMENDATION, status=ApplicationStatus.SAVED
        )

        assert plan.outcome is SuggestionPlanOutcome.INFORMATIONAL
        assert all(
            a.action_type is not ProposedActionType.CHANGE_STATUS for a in plan.actions
        )


class TestDatetimeHandling:
    def test_a_stated_time_is_carried_onto_the_event(self) -> None:
        plan = _plan(EmailMessageType.INTERVIEW_SCHEDULED, event_datetime=SCHEDULED_AT)

        assert plan.actions[0].occurred_at == SCHEDULED_AT

    def test_no_time_is_invented_when_classification_supplied_none(self) -> None:
        plan = _plan(EmailMessageType.INTERVIEW_SCHEDULED)

        assert plan.actions[0].occurred_at is None
        assert any("timezone-aware" in note for note in plan.field_notes)

    def test_other_types_never_carry_a_time(self) -> None:
        # Only a scheduled interview has a time that means anything.
        plan = _plan(EmailMessageType.REJECTION, event_datetime=SCHEDULED_AT)

        assert all(action.occurred_at is None for action in plan.actions)


class TestNoActionTypes:
    @pytest.mark.parametrize("message_type", sorted(NO_ACTION_MESSAGE_TYPES))
    def test_they_do_nothing_even_when_confidently_matched(
        self, message_type: EmailMessageType
    ) -> None:
        plan = _plan(message_type)

        assert plan.outcome is SuggestionPlanOutcome.NO_ACTION
        assert plan.actions == ()
        assert plan.application_id is None


class TestPurity:
    def test_the_policy_takes_no_session_and_imports_no_provider(self) -> None:
        import ast
        import inspect
        from pathlib import Path

        import app.services.email_policy as policy

        signature = inspect.signature(decide)
        assert list(signature.parameters) == ["policy_input"]

        tree = ast.parse(Path(policy.__file__).read_text(encoding="utf-8"))
        imported = {
            (node.module or "").split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        } | {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        # No provider, and no database access of any kind.
        assert not imported & {"openai", "anthropic", "sqlalchemy"}

    def test_it_is_deterministic(self) -> None:
        first = _plan(EmailMessageType.REJECTION, status=ApplicationStatus.HR_INTERVIEW)
        second = _plan(EmailMessageType.REJECTION, status=ApplicationStatus.HR_INTERVIEW)

        assert first == second

    def test_the_application_context_carries_nothing_sensitive(self) -> None:
        # Three fields, none of them content. A struct that cannot hold a CV or
        # a description cannot leak one into a decision or a log line.
        assert set(ApplicationContext.__dataclass_fields__) == {
            "application_id",
            "status",
            "has_submitted_cv",
        }


class TestIdempotencyKey:
    def test_it_is_stable_for_the_same_email_and_action(self) -> None:
        plan = _plan(EmailMessageType.APPLICATION_RECEIVED, status=ApplicationStatus.SAVED)

        keys = [idempotency_key(42, action) for action in plan.actions]
        assert keys == [idempotency_key(42, action) for action in plan.actions]

    def test_the_two_actions_from_one_email_dedupe_independently(self) -> None:
        # A confirmation proposes both an event and a status change; collapsing
        # them into one key would lose one of the two.
        plan = _plan(EmailMessageType.APPLICATION_RECEIVED, status=ApplicationStatus.SAVED)

        assert len(plan.actions) == 2
        assert len({idempotency_key(42, action) for action in plan.actions}) == 2

    def test_it_uses_the_internal_email_id_not_a_provider_id(self) -> None:
        plan = _plan(EmailMessageType.REJECTION)
        key = idempotency_key(42, plan.actions[0])

        assert key.startswith("email:42:")
        assert "gmail" not in key.lower()

    def test_different_emails_produce_different_keys(self) -> None:
        plan = _plan(EmailMessageType.REJECTION)
        assert idempotency_key(1, plan.actions[0]) != idempotency_key(2, plan.actions[0])


class TestNamedCasesAreReachable:
    def test_the_landmark_cases_are_present_by_name(self) -> None:
        """The decisions most likely to be argued about later."""
        for required in (
            "confirmation_matched_while_saved",
            "confirmation_matched_at_a_later_stage_never_regresses",
            "rejection_when_already_rejected",
            "referral_no_match_with_company_and_role",
            "confirmation_no_match_proposes_creation_and_the_confirmation",
            "recruiter_outreach_no_match_offers_optional_creation",
            "referral_no_match_with_missing_company",
            "low_confidence_match_never_mutates",
            "job_alert_does_nothing_even_when_matched",
        ):
            assert required in POLICY_CASES_BY_NAME, required



class TestCreationFromUnmatchedEmails:
    """Three types may propose creation, and they are not equivalent."""

    def _no_match(self, message_type: EmailMessageType, **kwargs):
        return _plan(
            message_type,
            match_status=MatchStatus.NO_MATCH,
            confidence=None,
            status=None,
            **kwargs,
        )

    def test_a_confirmation_creates_and_records_the_confirmation(self) -> None:
        plan = self._no_match(
            EmailMessageType.APPLICATION_RECEIVED,
            company_name="Northwind Analytics",
            role_title="Data Platform Engineer",
        )

        assert plan.outcome is SuggestionPlanOutcome.PROPOSE_ACTIONS
        create, record = plan.actions
        assert create.action_type is ProposedActionType.CREATE_APPLICATION
        assert create.company_name == "Northwind Analytics"
        assert create.role_title == "Data Platform Engineer"
        assert record.summary == "Application confirmed by email"

    def test_the_follow_up_event_targets_the_application_being_created(self) -> None:
        """The contract can express create-then-record without executing either.

        `application_id=None` alone would be ambiguous: it means both "no
        target" and "the one we are about to make".
        """
        plan = self._no_match(
            EmailMessageType.APPLICATION_RECEIVED, company_name="Northwind Analytics"
        )

        create, record = plan.actions
        assert create.targets_new_application is False
        assert record.targets_new_application is True
        assert record.application_id is None
        # Ordered: creation first, so an executor has a row to attach to.
        assert plan.actions.index(create) < plan.actions.index(record)

    def test_a_confirmation_leaves_the_channel_unset(self) -> None:
        # The email proves an application exists; it says nothing about whether
        # it went through a careers site, LinkedIn or a job board.
        plan = self._no_match(
            EmailMessageType.APPLICATION_RECEIVED, company_name="Northwind Analytics"
        )

        assert plan.actions[0].application_channel is None
        assert any("channel is unknown" in note for note in plan.field_notes)

    def test_a_confirmation_never_proposes_a_status(self) -> None:
        # Creating it is enough; nothing is marked applied automatically.
        plan = self._no_match(
            EmailMessageType.APPLICATION_RECEIVED, company_name="Northwind Analytics"
        )

        assert all(action.to_status is None for action in plan.actions)

    def test_outreach_is_informational_with_an_optional_creation(self) -> None:
        plan = self._no_match(
            EmailMessageType.RECRUITER_OUTREACH,
            company_name="Larkfield Systems",
            role_title="Senior Data Engineer",
        )

        assert plan.outcome is SuggestionPlanOutcome.INFORMATIONAL
        assert len(plan.actions) == 1
        action = plan.actions[0]
        assert action.action_type is ProposedActionType.CREATE_APPLICATION
        # Offered, not recommended.
        assert action.optional is True
        # A recruiter really is how this arrived, unlike a confirmation.
        assert action.application_channel is ApplicationChannel.RECRUITER
        assert action.to_status is None

    def test_a_referral_creation_stays_non_optional_and_records_nothing(self) -> None:
        plan = self._no_match(
            EmailMessageType.REFERRAL_OR_RECOMMENDATION, company_name="Halcyon Robotics"
        )

        action = plan.actions[0]
        assert action.optional is False
        assert action.application_channel is ApplicationChannel.REFERRAL
        # Being referred is not applying, so no confirmation event accompanies it.
        assert len(plan.actions) == 1

    @pytest.mark.parametrize("message_type", sorted(CREATION_CAPABLE_TYPES))
    def test_a_missing_company_always_falls_back_to_review(
        self, message_type: EmailMessageType
    ) -> None:
        plan = self._no_match(message_type, role_title="Some Role")

        assert plan.outcome is SuggestionPlanOutcome.REVIEW_REQUIRED
        assert plan.actions == ()

    @pytest.mark.parametrize("message_type", sorted(CREATION_CAPABLE_TYPES))
    def test_a_missing_role_is_flagged_never_invented(
        self, message_type: EmailMessageType
    ) -> None:
        plan = self._no_match(message_type, company_name="Halcyon Robotics")

        create = next(
            a
            for a in plan.actions
            if a.action_type is ProposedActionType.CREATE_APPLICATION
        )
        assert create.role_title is None
        assert any("role" in note.lower() for note in plan.field_notes)

    def test_a_rejection_with_no_match_still_creates_nothing(self) -> None:
        """An application whose only content is that it already ended.

        Unchanged by this correction, and deliberately so.
        """
        plan = self._no_match(
            EmailMessageType.REJECTION,
            company_name="Brightpath Systems",
            role_title="Backend Engineer",
        )

        assert plan.outcome is SuggestionPlanOutcome.REVIEW_REQUIRED
        assert plan.actions == ()

    @pytest.mark.parametrize(
        "message_type",
        [
            EmailMessageType.INTERVIEW_INVITATION,
            EmailMessageType.INTERVIEW_SCHEDULED,
            EmailMessageType.ASSESSMENT_REQUESTED,
            EmailMessageType.OFFER_RECEIVED,
            EmailMessageType.PROCESS_UPDATE,
            EmailMessageType.OTHER_RECRUITMENT,
        ],
    )
    def test_other_types_never_create_from_a_no_match(
        self, message_type: EmailMessageType
    ) -> None:
        plan = self._no_match(
            message_type,
            company_name="Brightpath Systems",
            role_title="Backend Engineer",
        )

        assert plan.outcome is SuggestionPlanOutcome.REVIEW_REQUIRED
        assert plan.actions == ()


class TestProvenanceOnProposals:
    def test_every_proposed_action_is_attributed_to_gmail(self) -> None:
        """Executed later, these must not look like something the user typed.

        `EventSource.GMAIL` has existed since Phase 1 and was reserved for this.
        """
        from app.enums import EventSource

        for plan in (
            _plan(EmailMessageType.REJECTION, status=ApplicationStatus.APPLIED),
            _plan(
                EmailMessageType.APPLICATION_RECEIVED,
                match_status=MatchStatus.NO_MATCH,
                confidence=None,
                status=None,
                company_name="Northwind Analytics",
            ),
        ):
            assert plan.actions
            assert all(a.event_source is EventSource.GMAIL for a in plan.actions)
