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

from app.core.classification_evidence import unverifiable_evidence
from app.core.email_sanitizer import SanitizationCounts, sanitize_email
from app.core.errors import JobOpsError, OutgoingMessageNotClassifiable
from app.enums import EmailDirection
from app.models.email_message import EmailMessage
from app.schemas.classification import EmailClassification
from app.services.email_classifier import ClassificationInput, EmailClassifier
from app.services.email_matching import (
    MatchInput,
    MatchResult,
    match_email_to_application,
)


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

        if direction == EmailDirection.OUTGOING:
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

        sanitized = sanitize_email(
            sender=message.sender,
            subject=message.subject,
            body_text=message.body_text,
        )

        classifier_input = ClassificationInput(
            sender=sanitized.sender,
            subject=sanitized.subject,
            body_text=sanitized.body_text,
            received_at=message.received_at,
            direction=direction,
        )

        try:
            classification = classifier.classify(classifier_input)
        except OutgoingMessageNotClassifiable as exc:
            # Unreachable via the guard above; kept so a future caller that
            # skips the pre-check still cannot spend a request.
            outcomes.append(
                DryRunOutcome(
                    message_id=message_id,
                    direction=direction,
                    sender=sanitized.sender,
                    subject=sanitized.subject,
                    counts=sanitized.counts,
                    skipped_reason=str(exc),
                )
            )
            continue
        except JobOpsError as exc:
            outcomes.append(
                DryRunOutcome(
                    message_id=message_id,
                    direction=direction,
                    sender=sanitized.sender,
                    subject=sanitized.subject,
                    counts=sanitized.counts,
                    error=str(exc),
                )
            )
            continue

        # Re-checked here against the sanitised text rather than inferred from
        # `classify` having succeeded. The classifier already refuses ungrounded
        # answers, so this is belt and braces — but a validation tool that
        # reports "grounded: yes" should have looked, not assumed.
        grounded = not unverifiable_evidence(
            classification,
            subject=sanitized.subject,
            body_text=sanitized.body_text,
        )

        # Identity, decided separately from meaning and from the same session.
        # Deterministic and read-only: no provider call, nothing written.
        match = match_email_to_application(
            db,
            MatchInput(
                company_name=classification.company_name,
                role_title=classification.role_title,
            ),
        )

        usage = getattr(classifier, "last_usage", None)
        outcomes.append(
            DryRunOutcome(
                message_id=message_id,
                direction=direction,
                sender=sanitized.sender,
                subject=sanitized.subject,
                counts=sanitized.counts,
                classification=classification,
                grounded=grounded,
                input_tokens=getattr(usage, "input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None),
                match=match,
            )
        )

    return outcomes
