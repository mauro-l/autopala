from __future__ import annotations

from datetime import date, datetime

import pytest

from app.models import (
    JobSource,
    NormalizedJob,
    from_ashby_job,
    from_greenhouse_job,
    from_jobdrop_row,
    parse_amount,
    parse_date,
    sanitize_json,
    strip_html,
)
from app.normalize import make_fingerprint, make_loose_key


def linkedin_row(**overrides):
    row = {
        "id": "li-4489030478",
        "site": "linkedin",
        "job_url": "https://www.linkedin.com/jobs/view/4489030478",
        "job_url_direct": None,
        "title": "Desarrollador Senior React + TypeScript  - Trabajo Remoto",
        "company": "BairesDev",
        "location": "",
        "date_posted": "2026-11-11",
        "job_type": None,
        "salary_source": None,
        "interval": None,
        "min_amount": float("nan"),
        "max_amount": None,
        "currency": None,
        "is_remote": "False",
        "job_level": "",
        "description": None,
    }
    row.update(overrides)
    return row


class TestStripHtml:
    def test_unescapes_then_drops_tags(self):
        assert strip_html("&lt;p&gt;Hola &amp; chau&lt;/p&gt;") == "Hola & chau"

    def test_separates_adjacent_blocks(self):
        assert strip_html("<div>uno</div><div>dos</div>") == "uno dos"

    def test_collapses_whitespace(self):
        assert strip_html("<p>a\n\n   b</p>") == "a b"

    def test_missing_value(self):
        assert strip_html(None) == ""


class TestParseDate:
    def test_iso_date(self):
        assert parse_date("2026-11-11") == date(2026, 11, 11)

    def test_iso_datetime_with_offset(self):
        assert parse_date("2026-11-01T12:00:00-05:00") == date(2026, 11, 1)

    def test_datetime_becomes_date(self):
        assert parse_date(datetime(2026, 1, 2, 3, 4)) == date(2026, 1, 2)

    def test_date_passes_through(self):
        assert parse_date(date(2026, 5, 6)) == date(2026, 5, 6)

    @pytest.mark.parametrize("raw", [None, float("nan"), "", "   ", "no es fecha"])
    def test_unusable_values_become_none(self, raw):
        assert parse_date(raw) is None


class TestParseAmount:
    def test_number(self):
        assert parse_amount(1000) == 1000.0

    def test_numeric_string_with_separators(self):
        assert parse_amount("1,200.50") == 1200.5

    def test_currency_prefix(self):
        assert parse_amount("$80000") == 80000.0

    @pytest.mark.parametrize("raw", [None, float("nan"), "", "n/a"])
    def test_unusable_values_become_none(self, raw):
        assert parse_amount(raw) is None


class TestSanitizeJson:
    def test_replaces_non_finite_floats(self):
        assert sanitize_json({"a": float("nan"), "b": [1, float("inf")]}) == {
            "a": None,
            "b": [1, None],
        }

    def test_dates_become_iso_strings(self):
        assert sanitize_json({"d": date(2026, 1, 2)}) == {"d": "2026-01-02"}

    def test_top_level_non_finite(self):
        assert sanitize_json(float("-inf")) is None

    def test_leaves_plain_data_untouched(self):
        payload = {"site": "linkedin", "remote": False, "n": 3}
        assert sanitize_json(payload) == payload


class TestJobSource:
    def test_is_frozen(self):
        source = JobSource(source="linkedin", url="https://x")
        with pytest.raises(Exception):
            source.url = "https://y"


class TestFromJobdropRow:
    def test_maps_the_published_fields_without_rewriting_them(self):
        job = from_jobdrop_row(linkedin_row())
        assert job.company == "BairesDev"
        assert job.title == "Desarrollador Senior React + TypeScript  - Trabajo Remoto"
        assert job.location == ""

    def test_casts_the_stringly_typed_remote_flag(self):
        assert from_jobdrop_row(linkedin_row()).is_remote is False
        assert from_jobdrop_row(linkedin_row(is_remote="True")).is_remote is True

    def test_fills_missing_description_and_salary(self):
        job = from_jobdrop_row(linkedin_row())
        assert job.description == ""
        assert job.salary_min is None
        assert job.salary_max is None
        assert job.salary_currency is None
        assert job.job_type is None

    def test_parses_dates_and_salaries(self):
        job = from_jobdrop_row(
            linkedin_row(
                date_posted="2026-11-05",
                min_amount=1000,
                max_amount="2000",
                currency="USD",
                job_type="fulltime",
            )
        )
        assert job.posted_at == date(2026, 11, 5)
        assert job.salary_min == 1000.0
        assert job.salary_max == 2000.0
        assert job.salary_currency == "USD"
        assert job.job_type == "fulltime"

    def test_keeps_the_portal_url_as_a_child_source(self):
        job = from_jobdrop_row(linkedin_row())
        assert job.sources == [
            JobSource(
                source="linkedin",
                url="https://www.linkedin.com/jobs/view/4489030478",
                source_job_id="li-4489030478",
                url_direct=None,
            )
        ]

    def test_identity_matches_the_fingerprint_helper(self):
        row = linkedin_row()
        job = from_jobdrop_row(row)
        assert job.fingerprint == make_fingerprint("BairesDev", row["title"], "")
        assert job.loose_key == make_loose_key("BairesDev", row["title"])

    def test_retains_a_json_safe_raw_payload(self):
        job = from_jobdrop_row(linkedin_row(company_description=float("nan")))
        assert job.raw["id"] == "li-4489030478"
        assert job.raw["company_description"] is None


class TestGreenhouseAdapter:
    def payload(self, **overrides):
        data = {
            "id": 4001,
            "title": "Frontend Engineer",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/4001",
            "location": {"name": "Remote - Argentina"},
            "content": "&lt;p&gt;Build UI with React&lt;/p&gt;",
            "updated_at": "2026-11-01T12:00:00-05:00",
        }
        data.update(overrides)
        return data

    def test_reads_the_nested_location_and_escaped_html(self):
        job = from_greenhouse_job(self.payload(), company="Acme")
        assert job.company == "Acme"
        assert job.title == "Frontend Engineer"
        assert job.location == "Remote - Argentina"
        assert job.description == "Build UI with React"

    def test_detects_remote_from_the_location_text(self):
        assert from_greenhouse_job(self.payload(), company="Acme").is_remote is True
        on_site = self.payload(location={"name": "Buenos Aires"})
        assert from_greenhouse_job(on_site, company="Acme").is_remote is False

    def test_records_the_ats_url_and_date(self):
        job = from_greenhouse_job(self.payload(), company="Acme")
        assert job.sources[0].source == "greenhouse"
        assert job.sources[0].url == "https://boards.greenhouse.io/acme/jobs/4001"
        assert job.sources[0].source_job_id == "4001"
        assert job.posted_at == date(2026, 11, 1)

    def test_falls_back_to_company_name_in_the_payload(self):
        job = from_greenhouse_job(self.payload(company_name="Acme Inc."), company=None)
        assert job.company == "Acme Inc."


class TestAshbyAdapter:
    def payload(self, **overrides):
        data = {
            "id": "abc123",
            "title": "React Developer",
            "location": "Buenos Aires",
            "isRemote": True,
            "descriptionPlain": "Plain text description",
            "descriptionHtml": "<p>Html description</p>",
            "publishedAt": "2026-11-05T10:00:00.000Z",
            "employmentType": "FullTime",
            "jobUrl": "https://jobs.ashbyhq.com/linear/abc123",
        }
        data.update(overrides)
        return data

    def test_prefers_plain_description(self):
        assert from_ashby_job(self.payload(), company="Linear").description == (
            "Plain text description"
        )

    def test_falls_back_to_html_description(self):
        job = from_ashby_job(self.payload(descriptionPlain=None), company="Linear")
        assert job.description == "Html description"

    def test_maps_remote_flag_and_employment_type(self):
        job = from_ashby_job(self.payload(), company="Linear")
        assert job.is_remote is True
        assert job.job_type == "FullTime"

    def test_records_the_ats_url_and_date(self):
        job = from_ashby_job(self.payload(), company="Linear")
        assert job.sources[0].source == "ashby"
        assert job.sources[0].url == "https://jobs.ashbyhq.com/linear/abc123"
        assert job.sources[0].source_job_id == "abc123"
        assert job.posted_at == date(2026, 11, 5)


class TestCrossSourceDedupe:
    def test_same_offer_from_two_sources_shares_one_fingerprint(self):
        portal = from_jobdrop_row(
            linkedin_row(company="Acme S.A.", title="React Developer", location="CABA")
        )
        board = from_greenhouse_job(
            {
                "id": 1,
                "title": "React Developer (Remote)",
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                "location": {"name": "CABA"},
            },
            company="acme",
        )
        assert portal.fingerprint == board.fingerprint

    def test_merged_row_can_carry_both_sources(self):
        portal = from_jobdrop_row(linkedin_row())
        board = from_greenhouse_job(
            {
                "id": 1,
                "title": linkedin_row()["title"],
                "absolute_url": "https://boards.greenhouse.io/bairesdev/jobs/1",
                "location": {"name": ""},
            },
            company="BairesDev",
        )
        assert portal.fingerprint == board.fingerprint
        merged = portal.merge(board.sources[0])
        assert [s.source for s in merged.sources] == ["linkedin", "greenhouse"]
        assert isinstance(merged, NormalizedJob)
