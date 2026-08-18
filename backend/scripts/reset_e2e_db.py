"""Reset the end-to-end test database.

The browser tests need a predictable, empty database. That database must never
be the development one — it holds real applications — so this script refuses to
run against anything not named exactly `jobops_e2e`, the same guard the pytest
suite uses for `jobops_test`.

Usage:
    python scripts/reset_e2e_db.py          # create if missing, migrate, empty
    python scripts/reset_e2e_db.py --fast   # just empty it (between tests)
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

REQUIRED_DATABASE_NAME = "jobops_e2e"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent

# Children before parents, so foreign keys never block the truncate.
TABLES_IN_DELETION_ORDER = ("application_events", "applications", "documents")


def _database_url() -> str:
    url = os.environ.get("E2E_DATABASE_URL")
    if not url:
        raise SystemExit("E2E_DATABASE_URL is not set")

    name = make_url(url).database
    if name != REQUIRED_DATABASE_NAME:
        raise SystemExit(
            "REFUSING TO RESET DATABASE.\n"
            f"  E2E_DATABASE_URL points at {name!r}, but only "
            f"{REQUIRED_DATABASE_NAME!r} may be reset by the browser tests."
        )
    return url


def _create_database_if_missing(url_string: str) -> None:
    """CREATE DATABASE cannot run inside a transaction, hence AUTOCOMMIT."""
    url = make_url(url_string)
    admin_engine = create_engine(
        url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 5},
    )
    with admin_engine.connect() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": REQUIRED_DATABASE_NAME},
        ).scalar()
        if not exists:
            connection.execute(text(f'CREATE DATABASE "{REQUIRED_DATABASE_NAME}"'))
            print(f"created database {REQUIRED_DATABASE_NAME}")
    admin_engine.dispose()


def _run_migrations(url_string: str) -> None:
    environment = {**os.environ, "DATABASE_URL": url_string}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env=environment,
        check=True,
        capture_output=True,
    )


def _empty_tables(url_string: str) -> None:
    engine = create_engine(url_string, connect_args={"connect_timeout": 5})
    with engine.begin() as connection:
        for table in TABLES_IN_DELETION_ORDER:
            connection.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
    engine.dispose()


def _empty_document_storage() -> None:
    """Clear the E2E document directory only — never the development one."""
    documents_root = PROJECT_ROOT / "e2e-data" / "documents"
    if documents_root.exists():
        shutil.rmtree(documents_root)
    documents_root.mkdir(parents=True, exist_ok=True)


def main() -> None:
    url_string = _database_url()
    fast = "--fast" in sys.argv

    if not fast:
        _create_database_if_missing(url_string)
        _run_migrations(url_string)

    _empty_tables(url_string)
    _empty_document_storage()


if __name__ == "__main__":
    main()
