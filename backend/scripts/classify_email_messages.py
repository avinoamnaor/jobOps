"""Classify specific stored Gmail messages, read-only, for validation.

Run from the backend/ directory:

    .venv\\Scripts\\python.exe scripts\\classify_email_messages.py --ids 37 40 66

Makes real provider calls, for the ids you name and no others. There is no
"classify everything" mode by design: this exists to check the classifier
against a handful of messages a human picked, and an accidental inbox-wide run
would be both expensive and a much larger disclosure than intended.

Privacy: each message is sanitised locally (see `core/email_sanitizer.py`)
before anything is sent, and only the sanitised copy leaves the machine. The
full body is never printed — not the original, and not the sanitised version
either. What is printed is the classification, plus counts of how much was
redacted, so you can confirm sanitisation happened without the output itself
becoming a disclosure.

Writes nothing. The session runs inside a PostgreSQL READ ONLY transaction, so
an accidental write fails at the database rather than depending on this script
behaving.
"""

import argparse
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.config import EmailClassifierProvider, settings  # noqa: E402
from app.services.email_classifier import (  # noqa: E402
    PROVIDER_KEY_VARIABLES,
    StructuredEmailClassifier,
    configured_api_key,
    resolved_model,
)
from app.services.email_dry_run import DryRunOutcome, dry_run_classify  # noqa: E402

MAX_IDS_PER_RUN = 25
"""A blunt cap on one invocation.

Not a security boundary — you could run the script twice — but a typo like
`--ids $(seq 1 500)` should fail loudly rather than quietly send five hundred
real emails to a third party and bill you for it.
"""


@contextmanager
def read_only_session() -> Iterator[Session]:
    """A session that the database itself refuses to let write.

    `SET TRANSACTION READ ONLY` is what makes the read-only claim real: any
    INSERT, UPDATE or DELETE raises at the server. Relying on "this code does
    not call commit" would be a promise; this is an enforcement.
    """
    engine = create_engine(settings.database_url, connect_args={"connect_timeout": 5})
    session = sessionmaker(bind=engine)()
    try:
        session.execute(text("SET TRANSACTION READ ONLY"))
        yield session
    finally:
        # Rollback, never commit — there is nothing to commit, and this makes
        # that explicit at the one place it could go wrong.
        session.rollback()
        session.close()
        engine.dispose()


def _fmt(value: object) -> str:
    return "-" if value in (None, "") else str(value)


def print_outcome(outcome: DryRunOutcome) -> None:
    print(f"  #{outcome.message_id}")

    if not outcome.found:
        print("      not found in email_messages")
        print()
        return

    print(f"      direction   {outcome.direction.value if outcome.direction else '-'}")

    if outcome.skipped:
        # Reached before sanitisation, so there is deliberately nothing else to
        # show: the message was never read into a request.
        print(f"      SKIPPED     {outcome.skipped_reason}")
        print()
        return

    # Sanitised values only. The originals never reach this object.
    print(f"      from        {_fmt(outcome.sender)}")
    print(f"      subject     {_fmt(outcome.subject)}")

    if outcome.counts is not None:
        print("      sanitized:")
        for line in outcome.counts.as_lines():
            print(f"        {line}")

    if outcome.error is not None:
        print(f"      ERROR       {outcome.error}")
        print()
        return

    result = outcome.classification
    print(f"      type        {result.message_type.value}")
    print(f"      company     {_fmt(result.company_name)}")
    print(f"      role        {_fmt(result.role_title)}")
    print(f"      datetime    {_fmt(result.event_datetime)}")
    print(f"      confidence  {result.confidence.value}")
    print(f"      grounded    {'yes' if outcome.grounded else 'NO'}")
    for excerpt in result.evidence:
        print(f"      evidence    {excerpt!r}")
    print()


def print_summary(outcomes: list[DryRunOutcome]) -> None:
    found = [o for o in outcomes if o.found]
    classified = [o for o in found if o.classified]
    skipped = [o for o in found if o.skipped]
    errored = [o for o in found if o.error is not None]
    missing = [o for o in outcomes if not o.found]

    print("=" * 68)
    print("SUMMARY")
    print("=" * 68)
    print(f"  requested        {len(outcomes)}")
    print(f"  classified       {len(classified)}")
    print(f"  skipped          {len(skipped)} (nothing sent)")
    print(f"  errors           {len(errored)}")
    if missing:
        print(f"  not found        {len(missing)}")

    grounded = sum(1 for o in classified if o.grounded)
    if classified:
        print(f"  grounded         {grounded}/{len(classified)}")

    totals = [o.counts for o in found if o.counts is not None]
    if totals:
        print("  redactions       ", end="")
        print(
            f"emails={sum(c.emails for c in totals)} "
            f"phones={sum(c.phones for c in totals)} "
            f"urls_cleaned={sum(c.urls_cleaned for c in totals)} "
            f"opaque_tokens={sum(c.opaque_tokens for c in totals)}"
        )

    input_tokens = sum(o.input_tokens or 0 for o in classified)
    output_tokens = sum(o.output_tokens or 0 for o in classified)
    if input_tokens or output_tokens:
        print(
            f"  usage            {len(classified)} requests, "
            f"{input_tokens} in / {output_tokens} out tokens"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only classification dry run.")
    parser.add_argument(
        "--ids",
        type=int,
        nargs="+",
        required=True,
        metavar="ID",
        help="JobOps email_messages.id values to classify (explicit list only)",
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

    if len(args.ids) > MAX_IDS_PER_RUN:
        print(f"Refusing to classify {len(args.ids)} messages in one run.")
        print(f"The cap is {MAX_IDS_PER_RUN}; name fewer ids, or run again.")
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
    ids = list(dict.fromkeys(args.ids))  # de-duplicated, order preserved

    print("=" * 68)
    print(f"REAL-MESSAGE DRY RUN  —  {provider.value} / {resolved_model(provider)}")
    print(f"{len(ids)} explicitly selected message(s). Read-only; nothing is stored.")
    print("Sanitised locally before sending. Bodies are never printed.")
    print("=" * 68)
    print()

    with read_only_session() as db:
        outcomes = dry_run_classify(db, classifier, ids)

    for outcome in outcomes:
        print_outcome(outcome)

    print_summary(outcomes)


if __name__ == "__main__":
    main()
