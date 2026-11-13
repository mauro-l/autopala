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
        assert result.stack == 11

    def test_spelling_variants_count_as_the_same_skill(self):
        for text in ("React.js", "ReactJS", "react js"):
            result = score_job(make_job(description=text), today=TODAY)
            assert result.matched_skills == ("react",), text

    def test_node_variants_do_not_double_count_as_javascript(self):
        result = score_job(make_job(description="Node.js services in production."), today=TODAY)
        assert result.matched_skills == ("node",)
        assert result.stack == 3

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

    def test_next_js_counts_only_as_the_framework(self):
        with_framework = score_job(make_job(description="We use Next.js."), today=TODAY)
        plain_english = score_job(make_job(description="Next steps are defined together."), today=TODAY)
        assert "next" in with_framework.matched_skills
        assert "next" not in plain_english.matched_skills

    def test_react_native_also_counts_as_react(self):
        result = score_job(make_job(description="React Native with Expo."), today=TODAY)
        assert "react native" in result.matched_skills
        assert "react" in result.matched_skills

    def test_the_frontend_core_outweighs_a_backend_only_stack(self):
        frontend = score_job(make_job(description="React, TypeScript, JavaScript."), today=TODAY)
        backend = score_job(
            make_job(description="Node.js, Express, MongoDB, Prisma, Supabase."), today=TODAY
        )
        assert frontend.stack > backend.stack


class TestRole:
    def test_frontend_title_scores_the_full_role_budget(self):
        for title in (
            "Frontend Developer",
            "Desarrollador Frontend (UX/UI)",
            "Front-end Engineer",
            "Maquetador Web",
        ):
            result = score_job(make_job(title=title), today=TODAY)
            assert result.role_focus == "frontend", title
            assert result.role == MAX_SCORES["role"], title

    def test_backend_title_scores_zero(self):
        for title in ("Backend Developer", "Back End Engineer", "Node Developer"):
            result = score_job(make_job(title=title), today=TODAY)
            assert result.role_focus == "backend", title
            assert result.role == 0, title

    def test_mobile_is_the_secondary_target(self):
        result = score_job(make_job(title="React Native Developer Junior"), today=TODAY)
        assert result.role_focus == "mobile"
        assert 0 < result.role < MAX_SCORES["role"]

    def test_fullstack_is_allowed_but_worth_less_than_frontend(self):
        frontend = score_job(make_job(title="Frontend Developer"), today=TODAY)
        fullstack = score_job(make_job(title="Full Stack Developer"), today=TODAY)
        assert fullstack.role_focus == "fullstack"
        assert fullstack.role < frontend.role

    def test_a_silent_title_reads_the_body(self):
        result = score_job(
            make_job(title="Software Engineer", description="You will own our React frontend."),
            today=TODAY,
        )
        assert result.role_focus == "frontend"

    def test_a_body_naming_both_sides_is_fullstack(self):
        result = score_job(
            make_job(
                title="Software Engineer",
                description="React frontend plus backend APIs and microservices.",
            ),
            today=TODAY,
        )
        assert result.role_focus == "fullstack"

    def test_a_backend_role_cannot_notify_even_with_node_and_typescript(self):
        job = make_job(
            title="Backend Developer",
            is_remote=True,
            location="Argentina",
            description="Node.js, TypeScript, Express, MongoDB. 1 year.",
            posted_at=TODAY,
        )
        result = score_job(job, today=TODAY)
        assert result.total < NOTIFY_THRESHOLD
        assert should_notify(result.total) is False


class TestSeniority:
    def test_junior_title_scores_the_maximum(self):
        for title in ("Junior Frontend Developer", "Desarrollador Jr.", "Frontend Trainee"):
            assert score_job(make_job(title=title), today=TODAY).seniority == 25, title

    def test_spanish_junior_markers_are_recognized(self):
        for title in ("Pasantía Frontend", "Estudiante Avanzado - React", "Sin experiencia - Dev"):
            assert score_job(make_job(title=title), today=TODAY).seniority == 25, title

    def test_semi_senior_is_mid_not_senior(self):
        for title in ("Semi Senior Frontend Developer", "SSR React Developer"):
            result = score_job(make_job(title=title), today=TODAY)
            assert result.seniority_level == "mid", title
            assert result.seniority == 4, title

    def test_a_title_naming_senior_and_ssr_is_senior(self):
        result = score_job(
            make_job(title="FullStack Developer SENIOR/SSR  Angular/NodeJS"), today=TODAY
        )
        assert result.seniority_level == "senior"
        assert result.seniority == 0

    def test_senior_title_scores_zero(self):
        for title in ("Senior Frontend Developer", "Sr. Software Engineer", "Tech Lead", "Staff Engineer"):
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

    def test_prose_in_the_body_does_not_make_a_role_senior(self):
        """Measured false positive: "lead" as a verb zeroed a real frontend row."""
        for text in (
            "You will lead the activities of a team for software design.",
            "Principal responsibilities include building interfaces.",
            "You will work with our staff and report progress weekly.",
            "Ability to architect small solutions with guidance.",
        ):
            result = score_job(make_job(title="React Developer", description=text), today=TODAY)
            assert result.seniority_level == "unknown", text

    def test_a_body_that_states_the_senior_requirement_is_read(self):
        for text in (
            "Senior level experience required.",
            "Buscamos un perfil senior con experiencia.",
            "You will act as tech lead for the squad.",
        ):
            result = score_job(make_job(title="React Developer", description=text), today=TODAY)
            assert result.seniority_level == "senior", text

    def test_all_levels_openings_accept_a_junior(self):
        result = score_job(make_job(title="Mobile Engineer (Argentina, All Levels)"), today=TODAY)
        assert result.seniority_level == "junior"
        assert result.seniority == 25

    def test_a_mid_level_role_cannot_notify(self):
        job = make_job(
            title="Semi Senior Frontend Developer",
            is_remote=True,
            location="Argentina",
            description="React, TypeScript, JavaScript, Tailwind, HTML, CSS. 2 años.",
            posted_at=TODAY,
        )
        result = score_job(job, today=TODAY)
        assert result.parts["role"] == MAX_SCORES["role"]
        assert result.parts["stack"] == MAX_SCORES["stack"]
        assert result.total < NOTIFY_THRESHOLD


class TestExperience:
    def test_no_year_requirement_scores_the_maximum(self):
        assert score_job(make_job(), today=TODAY).experience == 10

    def test_one_year_scores_the_maximum(self):
        result = score_job(make_job(description="1+ years of experience with React."), today=TODAY)
        assert result.required_years == 1
        assert result.experience == 10

    def test_two_years_is_still_the_target_range(self):
        result = score_job(make_job(description="2 years of experience required."), today=TODAY)
        assert result.required_years == 2
        assert result.experience == 8

    def test_three_years_falls_to_the_floor_of_the_curve(self):
        result = score_job(make_job(description="3 years of experience required."), today=TODAY)
        assert result.required_years == 3
        assert result.experience == 2

    def test_five_years_scores_zero(self):
        result = score_job(make_job(description="5+ years building web apps."), today=TODAY)
        assert result.experience == 0

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
        assert result.experience == 10


class TestLocation:
    def test_remote_scores_the_maximum(self):
        assert score_job(make_job(is_remote=True), today=TODAY).location == 10

    def test_amba_on_site_scores_just_below_remote(self):
        for place in (
            "Buenos Aires, Argentina",
            "CABA",
            "Quilmes, Buenos Aires",
            "Avellaneda, Buenos Aires Province, Argentina",
        ):
            assert score_job(make_job(location=place), today=TODAY).location == 9, place

    def test_another_province_scores_lower_than_amba(self):
        for place in ("Córdoba, Argentina", "Rosario, Santa Fe, Argentina", "Mendoza, Argentina"):
            assert score_job(make_job(location=place), today=TODAY).location == 7, place

    def test_unknown_location_is_neutral_but_low(self):
        assert score_job(make_job(location=""), today=TODAY).location == 4

    def test_a_foreign_location_scores_zero(self):
        for place in ("Madrid, Spain", "Córdoba, España", "Berlin, Germany"):
            assert score_job(make_job(location=place), today=TODAY).location == 0, place

    def test_remote_abroad_ranks_below_remote_argentina(self):
        local = score_job(make_job(location="Argentina", is_remote=True), today=TODAY)
        abroad = score_job(make_job(location="Berlin, Germany", is_remote=True), today=TODAY)
        assert abroad.location < local.location
        assert abroad.location > 0

    def test_foreign_markers_win_over_argentine_ones(self):
        assert score_job(make_job(location="Córdoba, España"), today=TODAY).location == 0


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

    @pytest.mark.parametrize(
        ("days_ago", "expected"),
        [(0, 1.0), (30, 1.0), (31, 0.7), (60, 0.7), (61, 0.4), (365, 0.4)],
    )
    def test_the_whole_score_is_scaled_past_the_first_month(self, days_ago, expected):
        job = make_job(posted_at=TODAY - timedelta(days=days_ago))
        assert score_job(job, today=TODAY).recency_multiplier == expected

    def test_an_unknown_date_is_not_scaled(self):
        assert score_job(make_job(posted_at=None), today=TODAY).recency_multiplier == 1.0


class TestFlags:
    def test_advanced_english_is_reported_as_required(self):
        for text in ("Inglés avanzado excluyente.", "Fluent English is a must.", "English level: C1."):
            result = score_job(make_job(description=text), today=TODAY)
            assert result.english_level == "advanced", text
            assert result.english_required is True, text

    def test_intermediate_english_is_reported_without_being_required(self):
        result = score_job(make_job(description="Inglés intermedio deseable."), today=TODAY)
        assert result.english_level == "intermediate"
        assert result.english_required is False

    def test_no_english_mention_stays_unknown(self):
        result = score_job(make_job(description="React y ganas de aprender."), today=TODAY)
        assert result.english_level is None
        assert result.english_required is False

    def test_unpaid_internships_are_flagged(self):
        for text in ("Pasantía no remunerada.", "This is an unpaid internship.", "Ad honorem."):
            assert score_job(make_job(description=text), today=TODAY).unpaid is True, text

    def test_a_paid_role_is_not_flagged(self):
        assert score_job(make_job(description="Pasantía remunerada."), today=TODAY).unpaid is False

    def test_a_degree_requirement_is_flagged(self):
        result = score_job(make_job(description="Título universitario excluyente."), today=TODAY)
        assert result.degree_required is True

    def test_flags_do_not_change_the_score(self):
        plain = score_job(make_job(description="React."), today=TODAY)
        flagged = score_job(
            make_job(description="React. Sin remuneración. Inglés avanzado."), today=TODAY
        )
        assert plain.total == flagged.total


class TestTotal:
    def test_a_dream_match_clears_the_notify_threshold(self):
        job = make_job(
            title="Junior Frontend Developer",
            location="Buenos Aires, Argentina",
            description="React, TypeScript, JavaScript, Node.js, Express, MongoDB. 1 year.",
            posted_at=TODAY,
        )
        result = score_job(job, today=TODAY)
        # 99 is the on-site ceiling: only a remote offer reaches the last point.
        assert result.total == 99
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

    def test_a_title_only_linkedin_row_can_still_notify(self):
        job = make_job(
            title="Desarrollador Frontend React Junior",
            is_remote=True,
            posted_at=TODAY,
        )
        result = score_job(job, today=TODAY)
        assert result.parts["stack"] == 11
        assert result.total == 81
        assert should_notify(result.total) is True

    def test_the_total_is_the_raw_total_times_the_multiplier(self):
        job = make_job(
            title="Senior Frontend React",
            description="React. 5 años de experiencia.",
            posted_at=TODAY - timedelta(days=45),
        )
        result = score_job(job, today=TODAY)
        assert result.raw_total == sum(result.parts.values())
        assert result.recency_multiplier == 0.7
        assert result.total == round(result.raw_total * 0.7)

    def test_a_stale_dream_match_stops_notifying(self):
        fields = {
            "title": "Junior Frontend Developer",
            "location": "Buenos Aires, Argentina",
            "is_remote": True,
            "description": "React, TypeScript, JavaScript, Node.js, Express, MongoDB. 1 year.",
        }
        fresh = score_job(make_job(posted_at=TODAY, **fields), today=TODAY)
        stale = score_job(
            make_job(posted_at=TODAY - timedelta(days=180), **fields), today=TODAY
        )
        assert should_notify(fresh.total) is True
        assert should_notify(stale.total) is False

    def test_the_payload_explains_the_age_scaling(self):
        payload = score_job(
            make_job(posted_at=TODAY - timedelta(days=90)), today=TODAY
        ).to_payload()
        assert payload["recency_multiplier"] == 0.4
        assert payload["total"] == round(payload["raw_total"] * 0.4)

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

    def test_a_frontend_role_outranks_a_senior_fullstack_one(self):
        """The inversion measured on the real corpus: the old ranking was wrong."""
        senior_fullstack = make_job(
            title="FullStack Developer SENIOR/SSR  Angular/NodeJS",
            description="Angular, Node.js, Express, MongoDB. 3 años de experiencia.",
            posted_at=TODAY,
        )
        frontend = make_job(
            title="Desarrollador Frontend (UX/UI)",
            location="Buenos Aires, Buenos Aires Province, Argentina",
            posted_at=TODAY - timedelta(days=6),
        )
        assert score_job(frontend, today=TODAY).total > score_job(senior_fullstack, today=TODAY).total

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

    def test_the_budgets_sum_to_one_hundred(self):
        assert sum(MAX_SCORES.values()) == 100

    def test_total_is_always_within_zero_and_one_hundred(self):
        assert 0 <= score_job(make_job(), today=TODAY).total <= 100

    def test_breakdown_is_json_serializable_for_the_jsonb_column(self):
        payload = score_job(make_job(description="React"), today=TODAY).to_payload()
        assert json.loads(json.dumps(payload))["parts"]["stack"] == 11

    def test_the_payload_carries_the_new_flags(self):
        payload = score_job(
            make_job(description="React. Inglés avanzado. Pasantía no remunerada."), today=TODAY
        ).to_payload()
        assert payload["role_focus"] == "frontend"
        assert payload["english_required"] is True
        assert payload["unpaid"] is True

    def test_scoring_is_deterministic(self):
        job = make_job(description="React 2 años")
        assert score_job(job, today=TODAY) == score_job(job, today=TODAY)


class TestThresholds:
    def test_notify_threshold_is_eighty(self):
        assert NOTIFY_THRESHOLD == 80
        assert should_notify(80) is True
        assert should_notify(79) is False

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
            assert result.raw_total == sum(result.parts.values())
            assert result.total == round(result.raw_total * result.recency_multiplier)
