"""Targeted tests for advisory duplicate detection (Phase 4C, part 1)."""

from sqlalchemy.orm import Session

from app.schemas.application import ApplicationCreate
from app.services.applications import create_application, soft_delete_application
from app.services.matching import find_duplicate_candidates
from tests.conftest import requires_database

pytestmark = requires_database

DEFAULTS = {"company_name": "Harmonic", "role_title": "Junior SW Development Engineer"}


def _make(db: Session, **overrides: object) -> object:
    return create_application(db, ApplicationCreate(**{**DEFAULTS, **overrides}))


def _check(db: Session, **overrides: object):
    return find_duplicate_candidates(db, **{**DEFAULTS, **overrides})


class TestDuplicateRules:
    def test_exact_canonical_url_is_strong(self, db_session: Session) -> None:
        app = _make(db_session, job_url="https://www.example.com/jobs/4821/?utm_source=linkedin")

        matches = _check(db_session, job_url="https://example.com/jobs/4821?utm_source=x")

        assert [m.application.id for m in matches] == [app.id]
        assert matches[0].confidence == "strong"
        assert matches[0].reason == "Same job URL"

    def test_url_match_is_not_gated_by_company(self, db_session: Session) -> None:
        app = _make(db_session, company_name="Acme", job_url="https://example.com/jobs/77")

        matches = find_duplicate_candidates(
            db_session,
            company_name="Totally Different Co",
            role_title="Something Else",
            job_url="https://example.com/jobs/77",
        )

        assert [m.application.id for m in matches] == [app.id]
        assert matches[0].confidence == "strong"

    def test_same_company_role_and_job_id_is_strong(self, db_session: Session) -> None:
        _make(db_session, job_url="https://boards.greenhouse.io/acme/jobs/999?gh_jid=999")

        matches = _check(db_session, job_url="https://careers.example.com/apply?job_id=999")

        assert len(matches) == 1
        assert matches[0].confidence == "strong"
        assert "job ID" in matches[0].reason

    def test_same_company_role_and_description_is_strong(self, db_session: Session) -> None:
        _make(db_session, job_description="Build features end to end across the stack.")

        # Only whitespace differs — normalisation should still match.
        matches = _check(
            db_session, job_description="Build features   end to end\nacross the stack."
        )

        assert len(matches) == 1
        assert matches[0].confidence == "strong"
        assert "description" in matches[0].reason

    def test_same_company_and_role_only_is_possible(self, db_session: Session) -> None:
        app = _make(db_session)

        matches = _check(db_session)  # no url, no description

        assert [m.application.id for m in matches] == [app.id]
        assert matches[0].confidence == "possible"
        assert matches[0].reason == "Same company and role"

    def test_different_company_is_no_match(self, db_session: Session) -> None:
        _make(db_session)

        matches = find_duplicate_candidates(
            db_session, company_name="Nonexistent Co", role_title="Junior SW Development Engineer"
        )

        assert matches == []

    def test_different_role_same_company_is_no_match(self, db_session: Session) -> None:
        _make(db_session)

        matches = _check(db_session, role_title="Principal Product Manager")

        assert matches == []

    def test_soft_deleted_applications_are_ignored(self, db_session: Session) -> None:
        app = _make(db_session)
        soft_delete_application(db_session, app.id)

        assert _check(db_session) == []

    def test_strong_matches_are_ordered_before_possible(self, db_session: Session) -> None:
        strong = _make(db_session, job_url="https://example.com/jobs/4821")
        possible = _make(db_session)  # same company + role, but no URL

        matches = _check(db_session, job_url="https://example.com/jobs/4821")

        assert {m.application.id for m in matches} == {strong.id, possible.id}
        assert matches[0].confidence == "strong"
        assert matches[0].application.id == strong.id
        assert matches[-1].confidence == "possible"
