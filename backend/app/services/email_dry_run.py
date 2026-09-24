"""Read-only classification of specific stored emails, for validation.

Phase 6.2A-2. This is a measuring instrument, not a feature. It classifies
explicitly named `email_messages` rows and reports what came back; it stores
nothing, matches nothing, suggests nothing, and is never called during Gmail
sync.

Three boundaries hold here, in this order, and the order is the point:

  1. Only the ids the caller named are read. There is no "classify everything
     recruitment-shaped" path, because the whole question this phase answers is
     whether the classifier is trustworthy — and the way to find out is on a
     handful of messages a human chose, not on an inbox.
  2. Outgoing messages are dropped before anything else happens: before
     sanitisation, before rendering, before any request.
  3. What survives is sanitised locally, and only the sanitised copy is passed
     onward. The stored row is never modified.

Because the model only ever sees the sanitised text, evidence grounding is
checked against that same sanitised text. Verifying quotes against the original
would be checking against something the model never read, which would quietly
turn "source-grounded" into a claim about the wrong source.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.email_sanitizer import SanitizationCounts
from app.enums import EmailDirection
from app.models.email_message import EmailMessage
from app.schemas.classification import EmailClassification
from app.services.email_classifier import EmailClassifier
from app.services.email_matching import MatchResult
from app.services.email_policy import SuggestionPlan
from app.services.email_processing import analyze_email, is_classifiable


@dataclass(frozen=True)
class DryRunOutcome:
    """What happened to one requested message id."""

    message_id: int
    found: bool = True
    direction: EmailDirection | None = None
    # Sanitised, and safe to display. The originals are never carried on this
    # object at all, so a printing mistake cannot leak them.
    sender: str | None = None
    subject: str | None = None
    counts: SanitizationCounts | None = None
    skipped_reason: str | None = None
    classification: EmailClassification | None = None
    error: str | None = None
    grounded: bool | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    # Which application this email refers to, decided deterministically from the
    # classification's company and role. Present only when classification
    # succeeded — there is nothing to match on otherwise.
    match: MatchResult | None = None
    # What JobOps would propose. A plan, never an action — nothing in this
    # module executes any part of it.
    plan: SuggestionPlan | None = None
    # The matched application's current status, carried so the dry run can show
    # why a status change was or was not proposed.
    application_status: str | None = None

    @property
    def skipped(self) -> bool:
        return self.skipped_reason is not None

    @property
    def classified(self) -> bool:
        return self.classification is not None


def dry_run_classify(
    db: Session,
    classifier: EmailClassifier,
    message_ids: list[int],
) -> list[DryRunOutcome]:
    """Classify exactly the named messages. Reads only; writes nothing.

    The session is used for `SELECT` and nothing else — no add, no flush, no
    commit. `scripts/classify_email_messages.py` additionally opens it in a
    PostgreSQL `READ ONLY` transaction, so an accidental write fails at the
    database rather than relying on this function's good behaviour.
    """
    outcomes: list[DryRunOutcome] = []

    for message_id in message_ids:
        message = db.execute(
            select(EmailMessage).where(EmailMessage.id == message_id)
        ).scalar_one_or_none()

        if message is None:
            outcomes.append(DryRunOutcome(message_id=message_id, found=False))
            continue

        direction = EmailDirection(message.direction)

        if not is_classifiable(message):
            # Before sanitisation and before any request: the cheapest possible
            # place to enforce "we do not classify the user's own writing".
            outcomes.append(
                DryRunOutcome(
                    message_id=message_id,
                    direction=direction,
                    skipped_reason="outgoing — excluded by policy, nothing sent",
                )
            )
            continue

        # The same sanitise -> classify -> match -> decide step the production
        # pipeline runs (`services.email_processing`), so what this tool measures
        # is what processing would do. It writes nothing.
        analysis = analyze_email(db, classifier, message)
        sanitized = analysis.sanitized

        if analysis.error is not None:
            outcomes.append(
                DryRunOutcome(
                    message_id=message_id,
                    direction=direction,
                    sender=sanitized.sender,
                    subject=sanitized.subject,
                    counts=sanitized.counts,
                    error=analysis.error,
                )
            )
            continue

        outcomes.append(
            DryRunOutcome(
                message_id=message_id,
                direction=direction,
                sender=sanitized.sender,
                subject=sanitized.subject,
                counts=sanitized.counts,
                classification=analysis.classification,
                grounded=analysis.grounded,
                input_tokens=analysis.input_tokens,
                output_tokens=analysis.output_tokens,
                match=analysis.match,
                plan=analysis.plan,
                application_status=analysis.application_status,
            )
        )

    return outcomes
