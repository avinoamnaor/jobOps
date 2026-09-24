"""The email processing pipeline: stored email in, persisted suggestion out.

Phase 6.2C-2. One path, reusing every existing piece rather than restating it:

    EmailMessage
      -> eligibility (direction)          before anything is read into a request
      -> sanitisation                     core.email_sanitizer
      -> structured classification        services.email_classifier (any provider)
      -> application matching             services.email_matching
      -> policy                           services.email_policy
      -> persistence                      services.suggestions.persist_email_plan

Three layers, deliberately separate:

  * `analyze_email` — sanitise, classify, match, decide. Writes nothing. Shared
    with the read-only dry run (`services.email_dry_run`), so the validation
    tool and the production path cannot drift apart.
  * `process_email_message` — one email, end to end, including persistence.
  * `process_email_messages` — a bounded batch of explicitly named emails.

What this module never does: execute a proposed action. No application is
created, no event appended, no status changed — the output is a pending
Suggestion awaiting human approval. It also never *chooses* which emails to
send to a provider: callers name them. Sync imports all recent mail, not only
recruitment mail, so picking automatically would send unrelated personal email
to a third party. That stays off until a local pre-filter exists.

Nothing about a failed attempt is stored: no raw prompt, no raw reply, no
failure row. A failed email simply has no plan, and a later run may retry it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.classification_evidence import unverifiable_evidence
from app.core.email_sanitizer import SanitizedEmail, sanitize_email
from app.core.errors import EmailBatchTooLarge, JobOpsError, OutgoingMessageNotClassifiable
from app.enums import CLASSIFIABLE_EMAIL_DIRECTIONS, ApplicationStatus, EmailDirection
from app.models.application import Application
from app.models.email_message import EmailMessage
from app.models.suggestion import Suggestion
from app.schemas.classification import EmailClassification
from app.services.email_classifier import ClassificationInput, EmailClassifier
from app.services.email_matching import MatchInput, MatchResult, match_email_to_application
from app.services.email_policy import ApplicationContext, PolicyInput, SuggestionPlan, decide
from app.services.suggestions import get_email_plan, persist_email_plan

MAX_MESSAGES_PER_BATCH = 25
"""A blunt cap on one batch — the same one the dry run uses.

Every processed email is a paid request to a third party carrying (sanitised)
personal text. A typo in a list of ids should fail loudly, not quietly send
five hundred emails.
"""


# --- analysis (shared with the dry run) -----------------------------------


@dataclass(frozen=True)
class EmailAnalysis:
    """What the pipeline concluded about one email, before anything is stored.

    `sanitized` is always present: sanitisation happens before the classifier is
    called, so even a failed classification has something safe to display. The
    original body is never carried here.
    """

    sanitized: SanitizedEmail
    classification: EmailClassification | None = None
    # Set when classification failed; everything after it is then None.
    error: str | None = None
    grounded: bool | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    match: MatchResult | None = None
    plan: SuggestionPlan | None = None
    # The matched application's status, so a caller can show why a status
    # change was or was not proposed.
    application_status: str | None = None


def is_classifiable(message: EmailMessage) -> bool:
    """Whether this email may be sent to a classifier at all."""
    return EmailDirection(message.direction) in CLASSIFIABLE_EMAIL_DIRECTIONS


def analyze_email(
    db: Session, classifier: EmailClassifier, message: EmailMessage
) -> EmailAnalysis:
    """Sanitise, classify, match and decide for one email. Writes nothing.

    Raises `OutgoingMessageNotClassifiable` for the user's own mail, before the
    message is even sanitised. Callers are expected to have checked
    `is_classifiable` first and reported a skip; this guard is what makes
    forgetting to do so harmless rather than a disclosure.

    Only the sanitised copy is placed in the `ClassificationInput`, and evidence
    grounding is checked against that same sanitised text — the text the model
    actually saw.
    """
    if not is_classifiable(message):
        raise OutgoingMessageNotClassifiable()

    sanitized = sanitize_email(
        sender=message.sender,
        subject=message.subject,
        body_text=message.body_text,
    )

    try:
        classification = classifier.classify(
            ClassificationInput(
                sender=sanitized.sender,
                subject=sanitized.subject,
                body_text=sanitized.body_text,
                received_at=message.received_at,
                direction=EmailDirection(message.direction),
            )
        )
    except OutgoingMessageNotClassifiable:
        raise
    except JobOpsError as exc:
        # Provider failure, contract violation, ungrounded evidence: all one
        # outcome — we do not know what this email means.
        return EmailAnalysis(sanitized=sanitized, error=str(exc))

    usage = getattr(classifier, "last_usage", None)

    # Re-checked rather than inferred from `classify` having succeeded. The
    # real classifier already refuses ungrounded answers; this is what holds if
    # a classifier that does not is ever plugged in.
    grounded = not unverifiable_evidence(
        classification,
        subject=sanitized.subject,
        body_text=sanitized.body_text,
    )

    # Identity, decided deterministically from what the classifier extracted.
    # Nothing is inferred here that the email did not say.
    match = match_email_to_application(
        db,
        MatchInput(
            company_name=classification.company_name,
            role_title=classification.role_title,
        ),
    )

    # Minimal application state, loaded only when there is one to load. The
    # policy never receives a CV, a description or a URL.
    context = None
    application_status = None
    if match.application_id is not None:
        application = db.execute(
            select(Application).where(Application.id == match.application_id)
        ).scalar_one_or_none()
        if application is not None:
            application_status = application.status
            context = ApplicationContext(
                application_id=application.id,
                status=ApplicationStatus(application.status),
                has_submitted_cv=application.submitted_cv_document_id is not None,
            )

    plan = decide(
        PolicyInput(
            message_type=classification.message_type,
            match_status=match.status,
            match_confidence=match.confidence,
            application=context,
            company_name=classification.company_name,
            role_title=classification.role_title,
            event_datetime=classification.event_datetime,
            candidate_application_ids=tuple(match.candidate_ids),
        )
    )

    return EmailAnalysis(
        sanitized=sanitized,
        classification=classification,
        grounded=grounded,
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
        match=match,
        plan=plan,
        application_status=application_status,
    )


# --- processing -----------------------------------------------------------


class ProcessingStatus(StrEnum):
    """What happened to one email. Reported, never stored."""

    # A new plan was persisted.
    CREATED = "created"
    # A plan already existed; nothing was sent and nothing written.
    ALREADY_PROCESSED = "already_processed"
    # Not eligible (the user's own mail); nothing was sent.
    SKIPPED = "skipped"
    # Classification or persistence failed; nothing was written.
    FAILED = "failed"
    # No such email.
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class ProcessingOutcome:
    message_id: int
    status: ProcessingStatus
    suggestion_id: int | None = None
    # The persisted plan's outcome and action count (new or pre-existing).
    plan_outcome: str | None = None
    action_count: int = 0
    # Skip reason or error message. Never contains the email body.
    detail: str | None = None
    # Whether a classifier request was made for this email.
    classifier_called: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None


def process_email_message(
    db: Session, classifier: EmailClassifier, message_id: int
) -> ProcessingOutcome:
    """Run the full pipeline for one stored email and persist its plan.

    Ordered so that the cheap refusals come before any spend:

      1. unknown id                  -> NOT_FOUND
      2. plan already stored         -> ALREADY_PROCESSED (no classifier call —
                                        re-running a batch costs nothing)
      3. outgoing                    -> SKIPPED (before sanitisation)
      4. classification fails        -> FAILED, nothing written
      5. evidence not grounded       -> FAILED, nothing written
      6. `persist_email_plan`        -> CREATED (or ALREADY_PROCESSED, if a
                                        concurrent run stored it first)

    Persistence is a single commit of the suggestion and all its actions, so a
    failure at any step leaves no partial Suggestion behind. No proposed action
    is executed.
    """
    message = db.execute(
        select(EmailMessage).where(EmailMessage.id == message_id)
    ).scalar_one_or_none()
    if message is None:
        return ProcessingOutcome(message_id=message_id, status=ProcessingStatus.NOT_FOUND)

    existing = get_email_plan(db, message_id)
    if existing is not None:
        return _from_suggestion(message_id, ProcessingStatus.ALREADY_PROCESSED, existing)

    if not is_classifiable(message):
        return ProcessingOutcome(
            message_id=message_id,
            status=ProcessingStatus.SKIPPED,
            detail=f"{message.direction} — excluded by policy, nothing sent",
        )

    analysis = analyze_email(db, classifier, message)
    tokens = {"input_tokens": analysis.input_tokens, "output_tokens": analysis.output_tokens}

    if analysis.error is not None:
        return ProcessingOutcome(
            message_id=message_id,
            status=ProcessingStatus.FAILED,
            detail=analysis.error,
            classifier_called=True,
            **tokens,
        )
    if not analysis.grounded:
        return ProcessingOutcome(
            message_id=message_id,
            status=ProcessingStatus.FAILED,
            detail="classification evidence does not appear in the sanitised email",
            classifier_called=True,
            **tokens,
        )

    assert analysis.plan is not None  # present whenever classification succeeded
    try:
        suggestion, created = persist_email_plan(
            db, email_message_id=message_id, plan=analysis.plan
        )
    except JobOpsError as exc:
        # e.g. the matched application was soft-deleted between matching and
        # persisting. Nothing was committed; discard the session's state so the
        # next email in a batch starts clean.
        db.rollback()
        return ProcessingOutcome(
            message_id=message_id,
            status=ProcessingStatus.FAILED,
            detail=str(exc),
            classifier_called=True,
            **tokens,
        )

    status = ProcessingStatus.CREATED if created else ProcessingStatus.ALREADY_PROCESSED
    return _from_suggestion(message_id, status, suggestion, classifier_called=True, **tokens)


def process_email_messages(
    db: Session, classifier: EmailClassifier, message_ids: Sequence[int]
) -> list[ProcessingOutcome]:
    """Process an explicit, bounded list of emails, one at a time.

    There is no "process everything" mode: only the named ids are read.
    Duplicates are dropped (order preserved), and more than
    `MAX_MESSAGES_PER_BATCH` distinct ids is refused before anything runs.

    Each email is independent — its plan commits on its own — so one failure is
    reported and the batch carries on. An unexpected (non-domain) error is not
    swallowed: the session is rolled back and it propagates, because a bug
    should stop a batch rather than be repeated across it.
    """
    ids = list(dict.fromkeys(message_ids))
    if len(ids) > MAX_MESSAGES_PER_BATCH:
        raise EmailBatchTooLarge(len(ids), MAX_MESSAGES_PER_BATCH)

    outcomes: list[ProcessingOutcome] = []
    for message_id in ids:
        try:
            outcomes.append(process_email_message(db, classifier, message_id))
        except Exception:
            db.rollback()
            raise
    return outcomes


def _from_suggestion(
    message_id: int,
    status: ProcessingStatus,
    suggestion: Suggestion,
    *,
    classifier_called: bool = False,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> ProcessingOutcome:
    return ProcessingOutcome(
        message_id=message_id,
        status=status,
        suggestion_id=suggestion.id,
        plan_outcome=suggestion.outcome,
        action_count=len(suggestion.actions),
        classifier_called=classifier_called,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
