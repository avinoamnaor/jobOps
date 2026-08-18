"""Tests for the destructive-test-setup guard.

The guard exists so that a wrong character in `.env` can never cause the test
suite to drop and truncate a database holding real applications. These tests
prove it actually refuses, rather than merely intending to.
"""

import pytest

from tests.conftest import REQUIRED_TEST_DATABASE_NAME, assert_safe_to_destroy

_TEST_URL = f"postgresql+psycopg://jobops:jobops@localhost:5432/{REQUIRED_TEST_DATABASE_NAME}"


def test_allows_the_real_test_database() -> None:
    assert_safe_to_destroy(_TEST_URL)


@pytest.mark.parametrize(
    "database_name",
    [
        "jobops",  # the development database — the one we must never touch
        "postgres",  # the server's default database
        "jobops_prod",
        "jobops_test_backup",  # close, but not exact
        "JOBOPS_TEST",  # case must match exactly
    ],
)
def test_refuses_any_other_database(database_name: str) -> None:
    url = f"postgresql+psycopg://jobops:jobops@localhost:5432/{database_name}"

    with pytest.raises(RuntimeError, match="REFUSING TO RUN DESTRUCTIVE TEST SETUP"):
        assert_safe_to_destroy(url)


def test_error_names_the_offending_database(the_wrong_database: str = "jobops") -> None:
    """The failure must be actionable, not just loud."""
    url = f"postgresql+psycopg://jobops:jobops@localhost:5432/{the_wrong_database}"

    with pytest.raises(RuntimeError) as error:
        assert_safe_to_destroy(url)

    message = str(error.value)
    assert the_wrong_database in message
    assert REQUIRED_TEST_DATABASE_NAME in message
    assert "TEST_DATABASE_URL" in message


def test_refuses_when_dev_and_test_urls_are_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    """Catches the copy-paste mistake of pointing both variables at one database."""
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", _TEST_URL)

    with pytest.raises(RuntimeError, match="must be separate"):
        assert_safe_to_destroy(_TEST_URL)
