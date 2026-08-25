"""Deciding which existing Application a classified email belongs to.

Phase 6.2B, deliberately narrow: identity only. This module reads applications
and returns a decision. It never writes, never creates a Suggestion, never
changes a status, never adds an event, and is never called during Gmail sync.

Separate from classification on purpose. The classifier answers *what an email
means*; this answers *which job it is about*. Those are different questions with
different failure modes, and keeping them apart is what lets each be wrong
without corrupting the other.

`MatchInput` carries a company and a role and nothing else — in particular, no
`message_type`. That is structural rather than disciplinary: "a rejection must
still match the right application, and its being a rejection must not influence
which one" cannot be violated by a function that never receives the type.

Relationship to `services/matching.py`: both compare applications, and both use
`core/normalize.py` so they agree with how applications were stored. They are
not merged because their contracts genuinely differ — duplicate detection needs
a company *and* a role (it runs at creation, when both exist) and returns an
advisory list, whereas an email routinely arrives with no role at all and needs
a single decision. Bending one function around both would weaken each.

No LLM, no network, no fuzzy-distance library. Deterministic and interpretable
by design: the question "why did JobOps think this email belongs to that
application?" has to have an answer a person can read.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.normalize import normalize_company, normalize_role
from app.enums import CompanySignal, MatchConfidence, MatchStatus, RoleSignal
from app.models.application import Application

# How many candidate applications an ambiguous result will list. A shortlist a
# person can actually read; beyond this the useful message is "too many to
# choose from", not a wall of ids.
MAX_CANDIDATES = 5


@dataclass(frozen=True)
class MatchInput:
    """What the matcher is allowed to consider.

    Both fields come from `EmailClassification`, copied across explicitly. There
    is no `message_type`, no evidence, no confidence and no email id here, so
    none of them can leak into an identity decision.
    """

    company_name: str | None
    role_title: str | None


@dataclass(frozen=True)
class MatchSignals:
    """The observations behind a decision, in readable form.

    Named signals rather than a numeric score. A score of 0.847 cannot be
    argued with or corrected; "company exact, role missing, 3 applications at
    this company" can.
    """

    company: CompanySignal
    role: RoleSignal
    # Applications whose normalised company matched. The denominator for every
    # decision below, and on its own the reason most ambiguity happens.
    company_candidates: int = 0
    # Of those, how many also matched on role.
    role_candidates: int = 0

    def describe(self) -> str:
        parts = [f"company {self.company.value}", f"role {self.role.value}"]
        if self.company_candidates:
            parts.append(
                f"{self.role_candidates} of {self.company_candidates} "
                f"application(s) at this company matched the role"
                if self.role is not RoleSignal.MISSING
                else f"{self.company_candidates} application(s) at this company"
            )
        return "; ".join(parts)


@dataclass(frozen=True)
class MatchResult:
    """One identity decision."""

    status: MatchStatus
    signals: MatchSignals
    reason: str
    # Set only when status is MATCHED. Null for ambiguous and no-match, so a
    # caller that ignores `status` still cannot act on a non-decision.
    application_id: int | None = None
    confidence: MatchConfidence | None = None
    # Populated for AMBIGUOUS: the shortlist a person would choose between.
    candidate_ids: list[int] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return self.status is MatchStatus.MATCHED


def _company_signal(email_company: str, application: Application) -> CompanySignal:
    if email_company.strip().casefold() == application.company_name.strip().casefold():
        return CompanySignal.EXACT
    return CompanySignal.NORMALIZED


def _role_signal(email_role: str, application: Application) -> RoleSignal:
    if email_role.strip().casefold() == application.role_title.strip().casefold():
        return RoleSignal.EXACT
    return RoleSignal.NORMALIZED


def match_email_to_application(db: Session, email: MatchInput) -> MatchResult:
    """Decide which application a classified email refers to.

    The decision table, in order:

    1. **No company** -> NO_MATCH. A role alone can never identify an employer,
       and matching "Security Analyst" across companies is precisely the error
       that would file one company's rejection against another's job.
    2. **Company matches nothing** -> NO_MATCH, whatever the role says.
    3. **Company matches, role given, exactly one role agrees** -> MATCHED.
       HIGH normally; MEDIUM when normalisation was needed to make either
       signal agree, because "we transformed it to fit" deserves to be visible.
    4. **Company matches, role given, several roles agree** -> AMBIGUOUS. Two
       applications for the same role at the same company are genuinely
       indistinguishable from here.
    5. **Company matches, role given, no role agrees** -> NO_MATCH.
       An explicitly different role is *negative* evidence, not missing
       evidence. The email named a job and none of the tracked ones is it, so
       the honest reading is "not one of these" rather than "one of these,
       probably". No candidates are offered, because offering them would invite
       exactly the confirmation the evidence argues against.

       This does cost recall: "SecOps" and "Security Operations Analyst" may
       well be the same job, and this rule declines both. That is the intended
       trade — a false NO_MATCH costs one manual link, while a false MATCHED
       files an employer's decision against the wrong job, and only one of those
       is visible to the person it happens to. Cases like that are the evidence
       for deciding later whether an LLM fallback earns its place.
    6. **Company matches, role missing, exactly one application** -> MATCHED at
       LOW confidence. There is a unique candidate, but company agreement alone
       is thin evidence, and the confidence says so rather than the status
       overstating it.
    7. **Company matches, role missing, several applications** -> AMBIGUOUS.
       Picking one because the company matched is the exact shortcut this
       matcher exists to refuse.
    """
    company_key = normalize_company(email.company_name or "")
    role_key = normalize_role(email.role_title or "")

    if not company_key:
        return MatchResult(
            status=MatchStatus.NO_MATCH,
            signals=MatchSignals(company=CompanySignal.MISSING, role=_missing_or_present(role_key)),
            reason="No company could be extracted, and a role alone cannot identify an employer",
        )

    candidates = (
        db.execute(
            select(Application)
            .where(
                Application.company_key == company_key,
                Application.deleted_at.is_(None),
            )
            .order_by(Application.id)
        )
        .scalars()
        .all()
    )

    if not candidates:
        return MatchResult(
            status=MatchStatus.NO_MATCH,
            signals=MatchSignals(company=CompanySignal.NONE, role=_missing_or_present(role_key)),
            reason="No application exists for this company",
        )

    if not role_key:
        return _decide_without_role(email, candidates)

    role_matches = [
        application for application in candidates if application.role_key == role_key
    ]

    if len(role_matches) == 1:
        return _matched(email, role_matches[0], len(candidates), role_candidates=1)

    if len(role_matches) > 1:
        return MatchResult(
            status=MatchStatus.AMBIGUOUS,
            signals=MatchSignals(
                company=_company_signal(email.company_name or "", role_matches[0]),
                role=_role_signal(email.role_title or "", role_matches[0]),
                company_candidates=len(candidates),
                role_candidates=len(role_matches),
            ),
            reason=(
                f"{len(role_matches)} applications at this company share this role; "
                "they cannot be told apart from the email alone"
            ),
            candidate_ids=[application.id for application in role_matches[:MAX_CANDIDATES]],
        )

    return MatchResult(
        status=MatchStatus.NO_MATCH,
        signals=MatchSignals(
            company=_company_signal(email.company_name or "", candidates[0]),
            role=RoleSignal.NONE,
            company_candidates=len(candidates),
            role_candidates=0,
        ),
        reason=(
            f"The company matches {len(candidates)} application(s) but none has this "
            "role — an explicitly different role is evidence against a match, not "
            "uncertainty about one"
        ),
        # Deliberately no candidate_ids. Listing the company's other
        # applications here would present a decision the evidence contradicts:
        # the email named a role, and these are not it. Ambiguity means "one of
        # these, probably"; this is "none of these, on the evidence given".
    )


def _decide_without_role(email: MatchInput, candidates: list[Application]) -> MatchResult:
    """Company agreement only. Never enough for a confident answer."""
    if len(candidates) == 1:
        application = candidates[0]
        return MatchResult(
            status=MatchStatus.MATCHED,
            application_id=application.id,
            # Low on purpose, and not promoted just because the candidate is
            # unique. Whether low confidence is actionable is a product policy
            # decision for a later phase, not something to settle by inflating
            # the number here.
            confidence=MatchConfidence.LOW,
            signals=MatchSignals(
                company=_company_signal(email.company_name or "", application),
                role=RoleSignal.MISSING,
                company_candidates=1,
            ),
            reason=(
                "Only one application exists for this company, but the email names "
                "no role, so the company is the only evidence"
            ),
        )

    return MatchResult(
        status=MatchStatus.AMBIGUOUS,
        signals=MatchSignals(
            company=_company_signal(email.company_name or "", candidates[0]),
            role=RoleSignal.MISSING,
            company_candidates=len(candidates),
        ),
        reason=(
            f"{len(candidates)} applications exist for this company and the email names "
            "no role, so there is nothing to choose between them"
        ),
        candidate_ids=[application.id for application in candidates[:MAX_CANDIDATES]],
    )


def _matched(
    email: MatchInput,
    application: Application,
    company_candidates: int,
    *,
    role_candidates: int,
) -> MatchResult:
    company = _company_signal(email.company_name or "", application)
    role = _role_signal(email.role_title or "", application)

    # HIGH is reserved for agreement that needed no transformation. When either
    # side had to be normalised the match is still good, but the reviewer should
    # be able to see that we met the data halfway.
    exact_throughout = company is CompanySignal.EXACT and role is RoleSignal.EXACT
    confidence = MatchConfidence.HIGH if exact_throughout else MatchConfidence.MEDIUM

    if exact_throughout:
        reason = "Company and role both match this application exactly"
    else:
        reason = "Company and role both match this application after normalisation"

    return MatchResult(
        status=MatchStatus.MATCHED,
        application_id=application.id,
        confidence=confidence,
        signals=MatchSignals(
            company=company,
            role=role,
            company_candidates=company_candidates,
            role_candidates=role_candidates,
        ),
        reason=reason,
    )


def _missing_or_present(role_key: str) -> RoleSignal:
    return RoleSignal.MISSING if not role_key else RoleSignal.NONE
