"""Unit tests for the normalisation helpers.

No database, no HTTP — these run in milliseconds. They are also the highest-value
tests in the project right now, because Phase 4's duplicate detection will be
built entirely on top of these functions.
"""

import pytest

from app.core.normalize import canonicalize_url, normalize_company, normalize_role


class TestNormalizeCompany:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("ProgrammaticX", "programmaticx"),
            ("ProgrammaticX Ltd.", "programmaticx"),
            ("PROGRAMMATIC-X", "programmatic x"),
            ("  Programmatic   X  ", "programmatic x"),
            ("Zürich Insurance", "zurich insurance"),
            ("Acme Inc", "acme"),
            ("Acme GmbH", "acme"),
        ],
    )
    def test_reduces_to_stable_key(self, raw: str, expected: str) -> None:
        assert normalize_company(raw) == expected

    def test_variants_of_the_same_company_collide(self) -> None:
        assert normalize_company("ProgrammaticX Ltd.") == normalize_company("programmaticx")

    def test_legal_suffix_is_only_stripped_from_the_end(self) -> None:
        """'co' is noise at the end but part of the name at the start."""
        assert normalize_company("Co Operative Group") == "co operative group"
        assert normalize_company("Operative Group Co") == "operative group"


class TestNormalizeRole:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Fullstack Developer", "fullstack developer"),
            ("Fullstack Developer (m/f/d)", "fullstack developer"),
            ("Backend Engineer (Remote)", "backend engineer"),
            ("Senior  Backend   Engineer", "senior backend engineer"),
        ],
    )
    def test_reduces_to_stable_key(self, raw: str, expected: str) -> None:
        assert normalize_role(raw) == expected

    def test_seniority_is_preserved(self) -> None:
        """Collapsing these would create false duplicates for different jobs."""
        senior = normalize_role("Senior Backend Engineer")
        junior = normalize_role("Junior Backend Engineer")
        assert senior != junior


class TestCanonicalizeUrl:
    def test_strips_tracking_parameters_and_fragment(self) -> None:
        url = "https://WWW.Example.com/jobs/42/?utm_source=x&refId=abc&trackingId=z#apply"
        assert canonicalize_url(url) == "https://example.com/jobs/42"

    def test_same_posting_shared_two_ways_collides(self) -> None:
        first = canonicalize_url("https://example.com/jobs/42?utm_source=linkedin")
        second = canonicalize_url("https://www.example.com/jobs/42/")
        assert first == second

    def test_parameter_order_does_not_matter(self) -> None:
        """Query parameter order carries no meaning, so it must not change the key."""
        first = canonicalize_url("https://example.com/careers?job_id=123&team=backend")
        second = canonicalize_url("https://example.com/careers?team=backend&job_id=123")
        assert first == second

    @pytest.mark.parametrize("value", [None, "", "   ", "not-a-url"])
    def test_returns_none_for_unusable_input(self, value: str | None) -> None:
        assert canonicalize_url(value) is None


class TestCanonicalizeUrlPreservesMeaningfulParameters:
    """The critical property: canonicalisation must never merge two real jobs.

    Phase 4 will use `job_url_canonical` as its strongest duplicate signal, so a
    parameter that identifies the posting must survive. Two different jobs
    collapsing into one canonical URL would silently merge two applications.
    """

    def test_different_job_ids_do_not_collide(self) -> None:
        first = canonicalize_url("https://example.com/careers?job_id=123")
        second = canonicalize_url("https://example.com/careers?job_id=456")

        assert first == "https://example.com/careers?job_id=123"
        assert second == "https://example.com/careers?job_id=456"
        assert first != second

    def test_different_job_ids_do_not_collide_even_with_tracking_noise(self) -> None:
        """Tracking params are removed; the identifying param still separates them."""
        first = canonicalize_url(
            "https://example.com/careers?job_id=123&utm_source=linkedin&gclid=aaa"
        )
        second = canonicalize_url(
            "https://example.com/careers?job_id=456&utm_source=newsletter&gclid=bbb"
        )

        assert first == "https://example.com/careers?job_id=123"
        assert second == "https://example.com/careers?job_id=456"
        assert first != second

    @pytest.mark.parametrize(
        "param",
        [
            "job_id",  # generic
            "jobId",  # camelCase variant
            "currentJobId",  # LinkedIn collection pages
            "gh_jid",  # Greenhouse
            "lever_id",
            "requisitionId",  # Workday-style
            "id",
            "position",
        ],
    )
    def test_identifying_parameters_are_preserved(self, param: str) -> None:
        result = canonicalize_url(f"https://example.com/careers?{param}=987&utm_campaign=spring")

        assert result == f"https://example.com/careers?{param}=987"
        assert "987" in result

    @pytest.mark.parametrize(
        "param",
        [
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
            "fbclid",
            "gclid",
            "msclkid",
            "refId",
            "trackingId",
            "trk",
            "trkinfo",
            "li_fat_id",
            "src",
            "originalSubdomain",
        ],
    )
    def test_known_tracking_parameters_are_removed(self, param: str) -> None:
        result = canonicalize_url(f"https://example.com/jobs/42?{param}=noise")

        assert result == "https://example.com/jobs/42"
        assert "noise" not in result

    def test_tracking_removal_is_case_insensitive(self) -> None:
        assert canonicalize_url("https://example.com/jobs/42?REFID=x&UTM_SOURCE=y") == (
            "https://example.com/jobs/42"
        )

    def test_mixed_meaningful_and_tracking_parameters(self) -> None:
        result = canonicalize_url(
            "https://boards.example.com/apply?gh_jid=555&utm_source=twitter&team=platform"
        )
        # Both meaningful params kept (sorted), tracking dropped.
        assert result == "https://boards.example.com/apply?gh_jid=555&team=platform"
