"""Scoring rules: deterministic, explainable, and stable without an LLM.

The LLM pass is optional and strictly additive, so every expectation here is a
pure function of the job row plus a frozen "today".
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.models import JobSource, NormalizedJob, from_jobdrop_row
from app.scoring import (
    MAX_SCORES,
    NOTIFY_THRESHOLD,
    SILENT_THRESHOLD,
    score_job,
    should_notify,
)

TODAY = date(2026, 11, 11)
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def make_job(**overrides) -> NormalizedJob:
    """A mid-quality offer: every field can be overridden per test."""
    fields = {
        "fingerprint": "fp",
        "loose_key": "lk",
        "company": "Acme",
        "title": "Frontend Developer",
        "location": "",
        "is_remote": False,
        "description": "",
        "posted_at": None,
        "salary_min": None,
        "salary_max": None,
        "salary_currency": None,
        "job_type": None,
        "sources": [JobSource(source="linkedin", url="https://example.test/1")],
        "raw": {},
    }
    fields.update(overrides)
    return NormalizedJob(**fields)


class TestStack:
    def test_full_target_stack_scores_the_maximum(self):
        job = make_job(
            description=(
                "React, TypeScript, JavaScript, Node.js, Express and MongoDB. "
                "We build with React on the frontend and Node.js on the backend."
            )
        )
        assert score_job(job, today=TODAY).stack == MAX_SCORES["stack"]

    def test_no_stack_signal_scores_zero(self):
        job = make_job(description="We need a passionate person with good vibes.")
        assert score_job(job, today=TODAY).stack == 0

    def test_react_alone_is_reported_as_the_only_match(self):
        job = make_job(description="You will write React components.")
        result = score_job(job, today=TODAY)
        assert result.matched_skills == ("react",)
        assert result.stack == 12

    def test_spelling_variants_count_as_the_same_skill(self):
        for text in ("React.js", "ReactJS", "react js"):
            result = score_job(make_job(description=text), today=TODAY)
            assert result.matched_skills == ("react",), text

    def test_node_variants_do_not_double_count_as_javascript(self):
        result = score_job(make_job(description="Node.js services in production."), today=TODAY)
        assert result.matched_skills == ("node",)
        assert result.stack == 8

    def test_bare_js_counts_as_javascript(self):
        result = score_job(make_job(description="Strong JS fundamentals required."), today=TODAY)
        assert result.matched_skills == ("javascript",)

    def test_skill_weights_are_capped_at_the_stack_budget(self):
        job = make_job(
            description="React TypeScript JavaScript Node Express MongoDB ReactJS TypeScript"
        )
        assert score_job(job, today=TODAY).stack <= MAX_SCORES["stack"]

    def test_matching_is_case_and_accent_insensitive(self):
        result = score_job(make_job(description="REACT y TYPESCRIPT"), today=TODAY)
        assert result.matched_skills == ("react", "typescript")


class TestSeniority:
    def test_junior_title_scores_the_maximum(self):
        for title in ("Junior Frontend Developer", "Desarrollador Jr.", "Frontend Trainee"):
            assert score_job(make_job(title=title), today=TODAY).seniority == 25, title

    def test_spanish_junior_markers_are_recognized(self):
        for title in ("Pasantía Frontend", "Estudiante Avanzado - React", "Sin experiencia - Dev"):
            assert score_job(make_job(title=title), today=TODAY).seniority == 25, title

    def test_two_to_three_years_is_flexible_not_discarded(self):
        result = score_job(make_job(title="Semi Senior Frontend Developer"), today=TODAY)
        assert result.seniority == 18
        assert result.seniority_level == "mid"

    def test_senior_title_scores_zero(self):
        for title in ("Senior Frontend Developer", "Tech Lead", "Staff Engineer"):
            assert score_job(make_job(title=title), today=TODAY).seniority == 0, title

    def test_unmarked_title_scores_the_neutral_middle(self):
        result = score_job(make_job(title="Frontend Developer"), today=TODAY)
        assert result.seniority == 12
        assert result.seniority_level == "unknown"

    def test_title_wins_over_description(self):
        job = make_job(
            title="Junior Developer",
            description="You will work alongside our senior staff and report to the lead.",
        )
        assert score_job(job, today=TODAY).seniority_level == "junior"

    def test_description_is_used_when_the_title_is_silent(self):
        job = make_job(title="Developer", description="Buscamos un perfil junior con ganas.")
        assert score_job(job, today=TODAY).seniority_level == "junior"


class TestExperience:
    def test_no_year_requirement_scores_the_maximum(self):
        assert score_job(make_job(), today=TODAY).experience == 15

    def test_one_year_scores_the_maximum(self):
        result = score_job(make_job(description="1+ years of experience with React."), today=TODAY)
        assert result.required_years == 1
        assert result.experience == 15

    def test_three_years_keeps_a_strong_score(self):
        result = score_job(make_job(description="3 years of experience required."), today=TODAY)
        assert result.required_years == 3
        assert result.experience == 12

    def test_five_years_halves_the_budget(self):
        result = score_job(make_job(description="5+ years building web apps."), today=TODAY)
        assert result.experience == 6

    def test_more_than_five_years_scores_zero(self):
        result = score_job(make_job(description="8 years of experience."), today=TODAY)
        assert result.experience == 0

    def test_spanish_years_are_parsed(self):
        result = score_job(make_job(description="2 años de experiencia en frontend."), today=TODAY)
        assert result.required_years == 2

    def test_year_ranges_use_the_lower_bound(self):
        result = score_job(make_job(description="2 a 4 años de experiencia."), today=TODAY)
        assert result.required_years == 2

    def test_the_strictest_reasonable_requirement_wins(self):
        result = score_job(
            make_job(description="1 year with React and 3 years of experience overall."),
            today=TODAY,
        )
        assert result.required_years == 3

    def test_implausible_numbers_are_treated_as_noise(self):
        result = score_job(
            make_job(description="Our company has 25 years of history in the market."),
            today=TODAY,
        )
        assert result.required_years is None
        assert result.experience == 15


class TestLocation:
    def test_remote_scores_the_maximum(self):
        assert score_job(make_job(is_remote=True), today=TODAY).location == 10

    def test_argentina_scores_high(self):
        for place in ("Buenos Aires, Argentina", "CABA", "Rosario, Santa Fe, Argentina"):
            assert score_job(make_job(location=place), today=TODAY).location == 8, place

    def test_unknown_location_is_neutral(self):
        assert score_job(make_job(location=""), today=TODAY).location == 5

    def test_a_foreign_location_scores_zero(self):
        for place in ("Madrid, Spain", "Córdoba, España", "Berlin, Germany"):
            assert score_job(make_job(location=place), today=TODAY).location == 0, place

    def test_remote_beats_an_awkward_location(self):
        job = make_job(location="Berlin, Germany", is_remote=True)
        assert score_job(job, today=TODAY).location == 10


class TestRecency:
    @pytest.mark.parametrize(
        ("days_ago", "expected"),
        [(0, 10), (2, 10), (3, 8), (7, 8), (8, 5), (14, 5), (15, 2), (30, 2), (31, 0), (90, 0)],
    )
    def test_fresh_postings_score_more(self, days_ago, expected):
        job = make_job(posted_at=TODAY - timedelta(days=days_ago))
        assert score_job(job, today=TODAY).recency == expected

    def test_unknown_date_is_neutral(self):
        assert score_job(make_job(posted_at=None), today=TODAY).recency == 4


class TestTotal:
    def test_a_dream_match_clears_the_notify_threshold(self):
        job = make_job(
            title="Junior Frontend Developer",
            location="Buenos Aires, Argentina",
            description="React, TypeScript, JavaScript, Node.js, Express, MongoDB. 1 year.",
            posted_at=TODAY,
        )
        result = score_job(job, today=TODAY)
        # 98 is the ceiling in Argentina: only a remote offer reaches the last 2.
        assert result.total == 98
        assert should_notify(result.total) is True

    def test_a_remote_dream_match_scores_the_maximum(self):
        job = make_job(
            title="Junior Frontend Developer",
            location="Buenos Aires, Argentina",
            is_remote=True,
            description="React, TypeScript, JavaScript, Node.js, Express, MongoDB. 1 year.",
            posted_at=TODAY,
        )
        assert score_job(job, today=TODAY).total == 100

    def test_a_poor_match_scores_low_and_stays_silent(self):
        job = make_job(
            title="Senior Backend Engineer",
            location="Berlin, Germany",
            description="Java, Spring. 8 years of experience.",
            posted_at=TODAY - timedelta(days=60),
        )
        result = score_job(job, today=TODAY)
        assert result.total < SILENT_THRESHOLD
        assert should_notify(result.total) is False

    def test_components_always_sum_to_the_total(self):
        job = make_job(title="Semi Senior React Developer", description="React 2 a 3 años")
        result = score_job(job, today=TODAY)
        assert result.total == sum(result.parts.values())

    def test_no_component_exceeds_its_budget(self):
        job = make_job(
            title="Junior React Trainee",
            location="Argentina",
            description="React TypeScript JavaScript Node Express MongoDB 1 año",
            posted_at=TODAY,
        )
        result = score_job(job, today=TODAY)
        for name, value in result.parts.items():
            assert 0 <= value <= MAX_SCORES[name], name

    def test_total_is_always_within_zero_and_one_hundred(self):
        assert 0 <= score_job(make_job(), today=TODAY).total <= 100

    def test_breakdown_is_json_serializable_for_the_jsonb_column(self):
        payload = score_job(make_job(description="React"), today=TODAY).to_payload()
        assert json.loads(json.dumps(payload))["parts"]["stack"] == 12

    def test_scoring_is_deterministic(self):
        job = make_job(description="React 2 años")
        assert score_job(job, today=TODAY) == score_job(job, today=TODAY)


class TestThresholds:
    def test_notify_threshold_is_eighty_five(self):
        assert NOTIFY_THRESHOLD == 85
        assert should_notify(85) is True
        assert should_notify(84) is False

    def test_silent_threshold_is_seventy(self):
        assert SILENT_THRESHOLD == 70


class TestRealRows:
    def test_every_spike_row_scores_within_bounds(self):
        files = sorted(FIXTURES.glob("spike_*.json"))
        assert files, "spike fixtures are missing"
        rows = []
        for path in files:
            capture = json.loads(path.read_text(encoding="utf-8"))
            for payload in capture["sources"].values():
                rows.extend(payload["records"])
        assert rows, "spike fixtures are empty"
        for row in rows:
            result = score_job(from_jobdrop_row(row), today=TODAY)
            assert 0 <= result.total <= 100
            assert result.total == sum(result.parts.values())
