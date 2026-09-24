"""Process specific stored Gmail messages into pending suggestions.

Run from the backend/ directory:

    .venv\\Scripts\\python.exe scripts\\process_email_messages.py --ids 37 40 66

For each named `email_messages` id: skip it if it is the user's own mail,
sanitise it locally, classify it with the configured provider (a real, paid
request), match it to an application, decide what to propose, and store that
proposal as a pending Suggestion. See `services/email_processing.py`.

What it does NOT do:
  * choose emails for you — there is no "process everything" mode. Sync imports
    all recent mail, not only recruitment mail, so only ids you name are sent;
  * execute anything — no application is created, no event added, no status
    changed. Every result waits for your approval;
  * process an email twice — one that already has a plan is reported and not
    sent to the provider again;
  * print a body, original or sanitised.

Unlike `classify_email_messages.py` (the read-only dry run), this writes: one
Suggestion per processed email, to the database your `.env` points at.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import EmailClassifierProvider, settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.services.email_classifier import (  # noqa: E402
    PROVIDER_KEY_VARIABLES,
    StructuredEmailClassifier,
    configured_api_key,
    resolved_model,
)
from app.services.email_processing import (  # noqa: E402
    MAX_MESSAGES_PER_BATCH,
    ProcessingOutcome,
    ProcessingStatus,
    process_email_messages,
)


def print_outcome(outcome: ProcessingOutcome) -> None:
    line = f"  #{outcome.message_id:<8} {outcome.status.value:<18}"
    if outcome.suggestion_id is not None:
        line += (
            f" suggestion #{outcome.suggestion_id}  {outcome.plan_outcome}"
            f"  ({outcome.action_count} action(s) proposed)"
        )
    print(line)
    if outcome.detail:
        print(f"            {outcome.detail}")


def print_summary(outcomes: list[ProcessingOutcome]) -> None:
    counts: dict[str, int] = {}
    for outcome in outcomes:
        counts[outcome.status.value] = counts.get(outcome.status.value, 0) + 1

    print()
    print("=" * 68)
    print("SUMMARY")
    print("=" * 68)
    for status in ProcessingStatus:
        if counts.get(status.value):
            print(f"  {status.value:<18} {counts[status.value]}")

    called = [o for o in outcomes if o.classifier_called]
    input_tokens = sum(o.input_tokens or 0 for o in called)
    output_tokens = sum(o.output_tokens or 0 for o in called)
    print(f"  provider requests  {len(called)}", end="")
    if input_tokens or output_tokens:
        print(f"  ({input_tokens} in / {output_tokens} out tokens)", end="")
    print()
    print("  actions executed   0 (every suggestion awaits your approval)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Process named emails into suggestions.")
    parser.add_argument(
        "--ids",
        type=int,
        nargs="+",
        required=True,
        metavar="ID",
        help="JobOps email_messages.id values to process (explicit list only)",
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

    ids = list(dict.fromkeys(args.ids))  # de-duplicated, order preserved
    if len(ids) > MAX_MESSAGES_PER_BATCH:
        print(f"Refusing to process {len(ids)} messages in one run.")
        print(f"The cap is {MAX_MESSAGES_PER_BATCH}; name fewer ids, or run again.")
        sys.exit(1)

    provider = (
        EmailClassifierProvider(args.provider)
        if args.provider
        else settings.email_classifier_provider
    )
    if args.model:
        settings.email_classifier_model = args.model

    env_var = PROVIDER_KEY_VARIABLES[provider]
    if not configured_api_key(provider):
        print(f"{env_var} is not configured (provider: {provider.value}).")
        print(f"Set {env_var} in your local .env (gitignored) and re-run.")
        sys.exit(1)

    classifier = StructuredEmailClassifier.from_settings(provider)

    print("=" * 68)
    print(f"EMAIL PROCESSING  —  {provider.value} / {resolved_model(provider)}")
    print(f"{len(ids)} explicitly selected message(s). Sanitised locally before sending.")
    print("Stores pending suggestions only; executes nothing. Bodies are never printed.")
    print("=" * 68)
    print()

    db = SessionLocal()
    try:
        outcomes = process_email_messages(db, classifier, ids)
    finally:
        db.close()

    for outcome in outcomes:
        print_outcome(outcome)
    print_summary(outcomes)


if __name__ == "__main__":
    main()
