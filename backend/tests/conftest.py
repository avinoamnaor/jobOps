"""Shared pytest fixtures.

`conftest.py` is discovered automatically by pytest — fixtures defined here are
available to every test file without importing anything.

Test-database strategy:

  * Tests run against a REAL PostgreSQL database (`jobops_test`), not SQLite.
    Testing on a different engine than you develop on is how dialect surprises
    reach production.
  * The schema is built by running the actual Alembic migrations. That means
    every test run also verifies that the migrations produce the schema the
    models expect — a broken migration fails the suite instead of being
    discovered later.
  * Tables are truncated between tests. Simple, obvious, and fast enough at this
    size. (The fancier alternative — wrapping each test in a rolled-back
    transaction — buys speed we do not need and costs clarity we do.)
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.config import PROJECT_ROOT, settings
from app.core.storage import document_storage
from app.db import get_db
from app.main import app

BACKEND_ROOT = PROJECT_ROOT / "backend"

# Order matters: children before parents, so foreign keys never block us.
_TABLES_IN_DELETION_ORDER = ("application_events", "applications", "documents")

# The ONLY database this suite is ever allowed to destroy.
REQUIRED_TEST_DATABASE_NAME = "jobops_test"


def assert_safe_to_destroy(url: str) -> None:
    """Refuse to run destructive setup against anything but the test database.

    This suite drops and recreates the entire schema and truncates tables between
    tests. All of that is fine against `jobops_test` and catastrophic against a
    development database that has a year of real applications in it.

    A single wrong character in `.env` is all it would take, so the check is not
    left to discipline. It raises rather than skips: a misconfigured test
    database is a bug to fix, not a condition to work around.
    """
    parsed = make_url(url)
    database_name = parsed.database

    if database_name != REQUIRED_TEST_DATABASE_NAME:
        raise RuntimeError(
            "REFUSING TO RUN DESTRUCTIVE TEST SETUP.\n"
            f"  TEST_DATABASE_URL points at database {database_name!r}, "
            f"but only {REQUIRED_TEST_DATABASE_NAME!r} may be reset by the test suite.\n"
            "  Fix TEST_DATABASE_URL in .env before running tests."
        )

    # Catches the copy-paste mistake where both URLs ended up identical.
    if settings.database_url and make_url(settings.database_url).database == database_name:
        raise RuntimeError(
            "REFUSING TO RUN DESTRUCTIVE TEST SETUP.\n"
            f"  DATABASE_URL and TEST_DATABASE_URL both point at {database_name!r}.\n"
            "  The test database must be separate from the development database."
        )


def _test_database_url() -> str:
    if not settings.test_database_url:
        pytest.fail("TEST_DATABASE_URL is not set in .env")
    # Guard at the single funnel: nothing in this suite reaches a database
    # without passing through here first.
    assert_safe_to_destroy(settings.test_database_url)
    return settings.test_database_url


def _database_is_reachable(url: str) -> bool:
    try:
        probe = create_engine(url, connect_args={"connect_timeout": 5})
        with probe.connect() as connection:
            connection.execute(text("SELECT 1"))
        probe.dispose()
    except SQLAlchemyError:
        return False
    return True


# Evaluated once at import time so the whole suite can be skipped cleanly when
# PostgreSQL is not running.
requires_database = pytest.mark.skipif(
    not _database_is_reachable(_test_database_url()),
    reason="PostgreSQL is not reachable — run `docker compose up -d` first",
)


@pytest.fixture(scope="session")
def test_engine() -> Iterator[Engine]:
    """Session-scoped engine for the test database, with migrations applied."""
    url = _test_database_url()
    engine = create_engine(url, connect_args={"connect_timeout": 5})

    alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    # Explicitly point Alembic at the TEST database. alembic/env.py only falls
    # back to settings.database_url when no URL has been set, so this keeps the
    # development database untouched.
    alembic_config.set_main_option("sqlalchemy.url", url)

    # Re-checked immediately before the destructive call itself, not only at the
    # funnel above. Defence in depth costs one line here and means a future edit
    # that bypasses `_test_database_url()` still cannot wipe the wrong database.
    assert_safe_to_destroy(url)

    # Rebuild from scratch so a leftover schema from an older run cannot mask a
    # broken migration.
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")

    yield engine
    engine.dispose()


@pytest.fixture
def db_session(test_engine: Engine) -> Iterator[Session]:
    """A database session for the test, with tables emptied afterwards."""
    factory = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        # RESTART IDENTITY resets the id sequences so ids are predictable per
        # test; CASCADE handles the foreign key between the two tables.
        for table in _TABLES_IN_DELETION_ORDER:
            session.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
        session.commit()
        session.close()


@pytest.fixture(autouse=True)
def temporary_document_storage(tmp_path: Path) -> Iterator[Path]:
    """Point document storage at a throwaway directory for every test.

    `autouse=True` means no test can accidentally write into the real
    `data/documents/` directory — the protection is on by default rather than
    something each test has to remember to opt into.

    `document_storage` is a module-level singleton whose `root` is a plain
    attribute, precisely so it can be redirected like this without any
    dependency-injection machinery.
    """
    original_root = document_storage.root
    storage_root = tmp_path / "documents"
    storage_root.mkdir(parents=True, exist_ok=True)
    document_storage.root = storage_root
    try:
        yield storage_root
    finally:
        document_storage.root = original_root


@pytest.fixture
def plain_client() -> Iterator[TestClient]:
    """A client with NO database wiring.

    Used by the liveness tests, which must keep passing when PostgreSQL is down —
    that is the entire point of a liveness endpoint.
    """
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    """An in-process HTTP client wired to the test database.

    `dependency_overrides` replaces `get_db` for the duration of the test, so
    endpoints use the test session instead of the real one. This is FastAPI's
    built-in seam for testing — no monkeypatching required.
    """

    def override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
