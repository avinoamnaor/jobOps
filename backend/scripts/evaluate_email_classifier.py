"""Run the OpenAI email classifier against the hand-labelled evaluation set.

Run from the backend/ directory:

    .venv\\Scripts\\python.exe scripts\\evaluate_email_classifier.py

This is the ONE place in the project that deliberately makes real OpenAI API
calls. It requires OPENAI_API_KEY in your local .env and fails clearly without
one. Every automated test stays network-free.

It never touches the database — it opens no session, imports no model, and
writes nothing. The eval set is fixture data, and the results are printed, not
persisted. Whether classifications are worth storing is the next decision, and
this script exists to inform it.

Ground truth is `tests/fixtures/email_eval_dataset.py`, hand-labelled before any
classifier existed. No model judges another model here: the dataset is the
specification, and a disagreement between it and the classifier is a classifier
failure until a human decides otherwise.
"""

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# The eval set lives under tests/, which is not an installed package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import EmailClassifierProvider, settings  # noqa: E402
from app.core.classification_evidence import unverifiable_evidence  # noqa: E402
from app.core.errors import JobOpsError, OutgoingMessageNotClassifiable  # noqa: E402
from app.schemas.classification import EmailClassification  # noqa: E402
from app.services.email_classifier import (  # noqa: E402
    PROVIDER_KEY_VARIABLES,
    ClassificationInput,
    StructuredEmailClassifier,
    configured_api_key,
    resolved_model,
)
from tests.fixtures.email_eval_dataset import EVAL_CASES, EvalCase  # noqa: E402


@dataclass
class CaseOutcome:
    case: EvalCase
    predicted: EmailClassification | None
    error: str | None
    grounded: bool | None
    input_tokens: int | None = None
    output_tokens: int | None = None
    skipped: bool = False
    """Excluded from classification by policy before any request was made.

    Today that means one thing: an outgoing message, which the classifier
    refuses by design. Counting that refusal as a failure was actively
    misleading — it made a correct, deliberate exclusion look like a defect and
    put a ceiling of 18/19 on a classifier that got everything it was asked to
    classify right. A skip is neither a success nor a failure; it is a case that
    was never in scope, so it leaves the accuracy denominator entirely.
    """

    @property
    def type_matches(self) -> bool:
        if self.skipped:
            return False
        return (
            self.predicted is not None
            and self.predicted.message_type == self.case.expected.message_type
        )

    @property
    def failed(self) -> bool:
        """A classification that was attempted and produced no verdict.

        Kept separate from a wrong verdict throughout: not knowing and being
        mistaken are different problems with different fixes. A skip is neither
        — nothing was attempted.
        """
        return self.predicted is None and not self.skipped


def _fmt(value: object, width: int = 28) -> str:
    text = "-" if value is None else str(value)
    return text if len(text) <= width else text[: width - 1] + "…"


def _same_datetime(expected: datetime | None, predicted: datetime | None) -> bool:
    if expected is None or predicted is None:
        return expected is None and predicted is None
    # Compared as instants: +03:00 and the equivalent UTC value are the same
    # moment, and marking that a mismatch would penalise a correct answer.
    return expected == predicted


def run_case(classifier: StructuredEmailClassifier, case: EvalCase) -> CaseOutcome:
    message = ClassificationInput(
        sender=case.message.sender,
        subject=case.message.subject,
        body_text=case.message.body_text,
        direction=case.message.direction,
    )
    try:
        predicted = classifier.classify(message)
    except OutgoingMessageNotClassifiable as exc:
        # The classifier working correctly, not failing. Recorded as a skip so
        # it stays visible in the per-case output without distorting the score.
        return CaseOutcome(
            case=case, predicted=None, error=str(exc), grounded=None, skipped=True
        )
    except JobOpsError as exc:
        # Domain errors only. An unexpected exception is a bug in this script
        # and should surface as a traceback rather than a tidy row.
        return CaseOutcome(case=case, predicted=None, error=str(exc), grounded=None)

    grounded = not unverifiable_evidence(
        predicted,
        subject=case.message.subject,
        body_text=case.message.body_text,
    )
    usage = classifier.last_usage
    return CaseOutcome(
        case=case,
        predicted=predicted,
        error=None,
        grounded=grounded,
        input_tokens=usage.input_tokens if usage else None,
        output_tokens=usage.output_tokens if usage else None,
    )


def print_case_row(outcome: CaseOutcome) -> None:
    case = outcome.case
    expected = case.expected

    if outcome.skipped:
        verdict = "SKIP"
        predicted_type = "(not classified)"
    elif outcome.failed:
        verdict = "ERROR"
        predicted_type = "-"
    else:
        verdict = "PASS" if outcome.type_matches else "FAIL"
        predicted_type = outcome.predicted.message_type.value

    print(f"  {verdict:<5} {case.name}")
    print(f"        type      expected={expected.message_type.value:<28} got={predicted_type}")

    if outcome.skipped:
        print("        reason    excluded by policy before any request (outgoing)")
        print()
        return

    if outcome.failed:
        print(f"        error     {outcome.error}")
        print()
        return

    predicted = outcome.predicted
    if expected.company_name or predicted.company_name:
        mark = "ok" if expected.company_name == predicted.company_name else "XX"
        print(
            f"        company   [{mark}] expected={_fmt(expected.company_name):<28} "
            f"got={_fmt(predicted.company_name)}"
        )
    if expected.role_title or predicted.role_title:
        mark = "ok" if expected.role_title == predicted.role_title else "XX"
        print(
            f"        role      [{mark}] expected={_fmt(expected.role_title):<28} "
            f"got={_fmt(predicted.role_title)}"
        )
    if expected.event_datetime or predicted.event_datetime:
        mark = "ok" if _same_datetime(expected.event_datetime, predicted.event_datetime) else "XX"
        print(
            f"        datetime  [{mark}] expected={_fmt(expected.event_datetime):<28} "
            f"got={_fmt(predicted.event_datetime)}"
        )

    print(
        f"        conf      expected={expected.confidence.value:<28} "
        f"got={predicted.confidence.value}"
    )
    print(f"        grounded  {'yes' if outcome.grounded else 'NO'}")
    print()


def print_summary(outcomes: list[CaseOutcome]) -> None:
    skipped = [o for o in outcomes if o.skipped]
    # The denominator is what the classifier was actually asked to do. A case
    # excluded by policy before any request was never in scope, so counting it
    # would measure the exclusion rather than the classifier.
    classifiable = [o for o in outcomes if not o.skipped]
    total = len(classifiable)
    answered = [o for o in classifiable if not o.failed]

    type_correct = sum(1 for o in classifiable if o.type_matches)
    failures = sum(1 for o in classifiable if o.failed)
    grounded = sum(1 for o in answered if o.grounded)

    company_correct = sum(
        1 for o in answered if o.case.expected.company_name == o.predicted.company_name
    )
    role_correct = sum(1 for o in answered if o.case.expected.role_title == o.predicted.role_title)
    datetime_correct = sum(
        1
        for o in answered
        if _same_datetime(o.case.expected.event_datetime, o.predicted.event_datetime)
    )

    def pct(count: int, out_of: int) -> str:
        return f"{count}/{out_of}" + (f" ({100 * count / out_of:.0f}%)" if out_of else "")

    print("=" * 72)
    print("AGGREGATE")
    print("=" * 72)
    print(f"  classifiable cases         {total} of {len(outcomes)}")
    if skipped:
        print(f"  skipped by policy          {len(skipped)} (outgoing — never sent)")
    print(f"  message-type accuracy      {pct(type_correct, total)}")
    print(f"  classification failures    {failures}")
    print(f"  evidence-grounding rate    {pct(grounded, len(answered))}")
    print(f"  company exact/null         {pct(company_correct, len(answered))}")
    print(f"  role exact/null            {pct(role_correct, len(answered))}")
    print(f"  datetime exact/null        {pct(datetime_correct, len(answered))}")

    input_tokens = sum(o.input_tokens or 0 for o in answered)
    output_tokens = sum(o.output_tokens or 0 for o in answered)
    if input_tokens or output_tokens:
        print(
            f"  usage                      {len(answered)} requests, "
            f"{input_tokens} in / {output_tokens} out tokens"
        )

    mismatched = [o for o in classifiable if not o.type_matches]
    if not mismatched:
        print()
        print("  No mismatches.")
        return

    print()
    print("=" * 72)
    print(f"MISMATCHES ({len(mismatched)})")
    print("=" * 72)
    for outcome in mismatched:
        got = "ERROR" if outcome.failed else outcome.predicted.message_type.value
        print(f"  {outcome.case.name}")
        print(f"      expected {outcome.case.expected.message_type.value}, got {got}")
        if outcome.failed:
            print(f"      error: {outcome.error}")
        elif outcome.case.contrasts_with is not None:
            # Naming the intended trap turns a bare mismatch into a diagnosis.
            print(f"      designed to be confused with: {outcome.case.contrasts_with.value}")
        print(f"      tests that: {outcome.case.tests_that}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        metavar="SUBSTRING",
        help="run only cases whose name contains this substring",
    )
    parser.add_argument(
        "--provider",
        choices=[member.value for member in EmailClassifierProvider],
        help="override EMAIL_CLASSIFIER_PROVIDER for this run",
    )
    parser.add_argument(
        "--model",
        help="override EMAIL_CLASSIFIER_MODEL for this run (must match the provider)",
    )
    args = parser.parse_args()

    provider = (
        EmailClassifierProvider(args.provider)
        if args.provider
        else settings.email_classifier_provider
    )
    if args.model:
        # Mutating the settings object rather than threading the model through
        # every layer: this is a developer script, the override lasts one
        # process, and it keeps the production path free of eval-only plumbing.
        settings.email_classifier_model = args.model

    env_var = PROVIDER_KEY_VARIABLES[provider]
    if not configured_api_key(provider):
        # The key itself is never printed, here or anywhere else.
        print(f"{env_var} is not configured (provider: {provider.value}).")
        print()
        print("Add this line to your local .env at the repository root (it is")
        print("gitignored), then re-run this script:")
        print()
        print(f"    {env_var}=...your key...")
        print()
        print("To use the other provider instead, pass --provider, or set")
        print("EMAIL_CLASSIFIER_PROVIDER in .env.")
        sys.exit(1)

    cases = list(EVAL_CASES)
    if args.only:
        cases = [case for case in cases if args.only in case.name]
        if not cases:
            print(f"No eval case name contains {args.only!r}.")
            sys.exit(1)

    classifier = StructuredEmailClassifier.from_settings(provider)

    print("=" * 72)
    print(
        f"EMAIL CLASSIFIER EVAL  —  {provider.value} / {resolved_model(provider)}"
    )
    print(f"{len(cases)} hand-labelled cases. No database access, no LLM judge.")
    print("=" * 72)
    print()

    outcomes = [run_case(classifier, case) for case in cases]
    for outcome in outcomes:
        print_case_row(outcome)

    print_summary(outcomes)


if __name__ == "__main__":
    main()
