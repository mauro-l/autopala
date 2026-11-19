"""Tests for the collection worker: schedule, dedupe, payload, persistence, run loop.

The worker is the only module allowed to touch jobdrop and Supabase, so every
collaborator here is injected: a fake scraper, a fake writer and a fake clock.
Nothing in this file opens a socket.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from app.models import JobSource, NormalizedJob
from app.normalize import make_fingerprint, make_loose_key
from app.scoring import ScoreBreakdown
from app.worker import (
    COLLECTION_WINDOW,
    DAILY_HOURS,
    EVENING_HOUR,
    EVENING_LINKEDIN_TERM,
    INDEED_HOURS,
    LINKEDIN_TERMS,
    MissingConfig,
    RecordingWriter,
    SourcePlan,
    SupabaseWriter,
    WorkerConfig,
    adapt_rows,
    build_scraper,
    dedupe,
    due_plans,
    in_collection_window,
    job_payload,
    linkedin_term_for,
    load_env,
    merge_jobs,
    run_collection,
    seconds_until_next_hour,
)

TODAY = date(2026, 11, 11)
AR = timezone(timedelta(hours=-3))


def at(hour: int, minute: int = 0, *, day: int = 11) -> datetime:
    """A local Argentine wall-clock instant, used for every schedule assertion."""
    return datetime(2026, 11, day, hour, minute, tzinfo=AR)


def make_job(
    *,
    company: str = "BairesDev",
    title: str = "Desarrollador Junior React",
    location: str = "",
    description: str = "",
    is_remote: bool = True,
    site: str = "linkedin",
    url: str = "https://www.linkedin.com/jobs/view/4489030478",
    source_job_id: str = "4489030478",
    posted_at: date | None = TODAY,
) -> NormalizedJob:
    """Build a normalized job without going through a portal row."""
    return NormalizedJob(
        fingerprint=make_fingerprint(company, title, location),
        loose_key=make_loose_key(company, title),
        company=company,
        title=title,
        location=location,
        is_remote=is_remote,
        description=description,
        posted_at=posted_at,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        job_type=None,
        sources=[JobSource(source=site, url=url, source_job_id=source_job_id, url_direct=None)],
        raw={"site": site, "title": title},
    )


FULL_STACK_JD = (
    "Buscamos un desarrollador junior con React, TypeScript, JavaScript, Node.js, "
    "Express y MongoDB para sumarse al equipo."
)

INDEED_ROW = {
    "site": "indeed",
    "id": "abc123",
    "job_url": "https://ar.indeed.com/viewjob?jk=abc123",
    "job_url_direct": "https://careers.example.com/junior-react",
    "title": "Desarrollador Junior React",
    "company": "BairesDev",
    "location": "Buenos Aires, AR",
    "description": FULL_STACK_JD,
    "date_posted": "2026-11-10",
    "is_remote": "False",
    "min_amount": 500000.0,
    "max_amount": 700000.0,
    "currency": "ARS",
    "job_type": "fulltime",
}

LINKEDIN_ROW = {
    "site": "linkedin",
    "id": "4489030478",
    "job_url": "https://ar.linkedin.com/jobs/view/4489030478",
    "job_url_direct": None,
    "title": "Desarrollador Junior React",
    "company": "BairesDev",
    "location": None,
    "description": None,
    "date_posted": None,
    "is_remote": "True",
    "min_amount": float("nan"),
    "max_amount": float("nan"),
    "currency": None,
    "job_type": None,
}


class FakeScraper:
    """Returns rows per source, or raises when the plan name is in ``failures``."""

    def __init__(self, rows_by_source: dict[str, list[dict]], failures: set[str] | None = None) -> None:
        self.rows_by_source = rows_by_source
        self.failures = failures or set()
        self.calls: list[str] = []

    def __call__(self, plan: SourcePlan) -> list[dict]:
        self.calls.append(plan.name)
        if plan.name in self.failures:
            raise RuntimeError(f"{plan.name} exploded")
        return list(self.rows_by_source.get(plan.name, []))


class FakeWriter:
    """Records every call instead of talking to Supabase."""

    def __init__(self) -> None:
        self.events: list[tuple] = []
        self.payloads: list[dict] = []
        self.scores: list[tuple[str, int, dict]] = []
        self.runs: list[dict] = []
        self._next_id = 1

    def start_run(self, source: str, params: dict) -> int:
        run_id = self._next_id
        self._next_id += 1
        self.runs.append({"id": run_id, "source": source, "params": dict(params), "finished": None})
        self.events.append(("start_run", source))
        return run_id

    def upsert_job(self, payload: dict) -> str:
        self.payloads.append(payload)
        self.events.append(("upsert_job", payload["fingerprint"]))
        return f"uuid-{len(self.payloads)}"

    def save_score(self, job_id: str, total: int, breakdown: dict) -> None:
        self.scores.append((job_id, total, breakdown))
        self.events.append(("save_score", job_id))

    def finish_run(self, run_id: int, *, status: str, rows_fetched: int, rows_new: int, error: str | None) -> None:
        for run in self.runs:
            if run["id"] == run_id:
                run.update(status=status, rows_fetched=rows_fetched, rows_new=rows_new, error=error, finished=True)
        self.events.append(("finish_run", run_id))


def plan(name: str, kind: str = "jobdrop", *, slug: str | None = None, company: str | None = None) -> SourcePlan:
    return SourcePlan(name=name, kind=kind, params={}, slug=slug, company=company)


class TestSchedule:
    def test_the_window_is_inclusive_at_the_start_and_exclusive_at_the_end(self) -> None:
        assert in_collection_window(at(COLLECTION_WINDOW[0])) is True
        assert in_collection_window(at(COLLECTION_WINDOW[1])) is False
        assert in_collection_window(at(COLLECTION_WINDOW[0] - 1, 59)) is False
        assert in_collection_window(at(COLLECTION_WINDOW[1] - 1, 59)) is True

    def test_the_terms_rotate_every_hour_and_never_leave_the_list(self) -> None:
        rotation = range(COLLECTION_WINDOW[0], EVENING_HOUR)
        seen = {linkedin_term_for(at(hour)) for hour in rotation}
        assert len(seen) == len(rotation)
        assert seen <= set(LINKEDIN_TERMS)

    def test_the_rotation_keeps_advancing_across_days(self) -> None:
        morning = linkedin_term_for(at(9, day=11))
        next_morning = linkedin_term_for(at(9, day=12))
        assert morning in LINKEDIN_TERMS
        assert next_morning in LINKEDIN_TERMS
        assert {morning, next_morning} != {morning} or len(LINKEDIN_TERMS) == 1

    def test_nothing_is_due_outside_the_window(self) -> None:
        assert due_plans(at(7)) == []
        assert due_plans(at(COLLECTION_WINDOW[1])) == []
        assert due_plans(at(22)) == []

    def test_the_ats_boards_run_only_on_the_daily_hours(self) -> None:
        for hour in DAILY_HOURS:
            assert any(p.kind in {"greenhouse", "ashby"} for p in due_plans(at(hour)))
        later = due_plans(at(DAILY_HOURS[0] + 1))
        assert not any(p.kind in {"greenhouse", "ashby"} for p in later)

    def test_indeed_runs_only_on_its_assigned_hours(self) -> None:
        assert any(p.name == "indeed" for p in due_plans(at(INDEED_HOURS[0])))
        assert any(p.name == "indeed" for p in due_plans(at(INDEED_HOURS[-1])))
        assert not any(p.name == "indeed" for p in due_plans(at(INDEED_HOURS[0] + 1)))

    def test_linkedin_is_always_due_inside_the_window(self) -> None:
        for hour in range(COLLECTION_WINDOW[0], COLLECTION_WINDOW[1]):
            names = [p.name for p in due_plans(at(hour))]
            assert names[0] == "linkedin"

    def test_indeed_plans_never_pass_hours_old(self) -> None:
        indeed = [p for p in due_plans(at(INDEED_HOURS[0])) if p.name == "indeed"]
        assert indeed
        for candidate in indeed:
            assert "hours_old" not in candidate.params

    def test_public_sources_are_daily_optional_and_exclude_project_only_sites(self) -> None:
        enabled = due_plans(at(DAILY_HOURS[0]), jooble_enabled=True)
        disabled = due_plans(at(DAILY_HOURS[0]))

        assert any(candidate.kind == "remoteok" for candidate in enabled)
        assert len([candidate for candidate in enabled if candidate.kind == "jooble"]) == 3
        assert not any(candidate.kind == "jooble" for candidate in disabled)
        assert not any(candidate.name in {"workana", "freelancer"} for candidate in enabled)
        assert not any(
            candidate.kind in {"remoteok", "jooble"}
            for candidate in due_plans(at(DAILY_HOURS[0] + 1), jooble_enabled=True)
        )


    def test_the_evening_pass_runs_the_full_daily_set_with_a_fixed_term(self) -> None:
        evening = due_plans(at(EVENING_HOUR), jooble_enabled=True)
        names = [p.name for p in evening]

        linkedin = [p for p in evening if p.name == "linkedin"]
        assert len(linkedin) == 1
        assert linkedin[0].params["search_term"] == EVENING_LINKEDIN_TERM
        assert names.count("indeed") == 3
        assert {"greenhouse:anthropic", "ashby:linear", "remoteok"} <= set(names)
        assert len([name for name in names if name.startswith("jooble:")]) == 3
        assert evening[0].name == "linkedin"


class TestPayload:
    def test_the_payload_matches_the_rpc_contract(self) -> None:
        payload = job_payload(make_job(description=FULL_STACK_JD, location="Buenos Aires, AR"))
        assert set(payload) == {
            "fingerprint",
            "loose_key",
            "company",
            "title",
            "location",
            "is_remote",
            "description",
            "posted_at",
            "salary_min",
            "salary_max",
            "salary_currency",
            "job_type",
            "raw",
            "sources",
        }

    def test_the_payload_does_not_carry_the_score(self) -> None:
        assert "score" not in job_payload(make_job())
        assert "score_breakdown" not in job_payload(make_job())

    def test_sources_are_serialized_with_their_canonical_keys(self) -> None:
        payload = job_payload(make_job())
        assert payload["sources"] == [
            {
                "source": "linkedin",
                "url": "https://www.linkedin.com/jobs/view/4489030478",
                "url_direct": None,
                "source_job_id": "4489030478",
            }
        ]

    def test_dates_become_iso_strings_and_missing_values_stay_none(self) -> None:
        payload = job_payload(make_job(posted_at=None, description=""))
        assert payload["posted_at"] is None
        assert payload["description"] == ""

    def test_the_payload_is_strict_json(self) -> None:
        payload = job_payload(make_job())
        json.dumps(payload, allow_nan=False)


class TestAdapt:
    def test_a_jobdrop_row_becomes_a_normalized_job(self) -> None:
        jobs = adapt_rows(plan("indeed"), [INDEED_ROW])
        assert len(jobs) == 1
        assert jobs[0].company == "BairesDev"
        assert jobs[0].sources[0].source == "indeed"
        assert jobs[0].salary_min == 500000.0

    def test_a_greenhouse_posting_uses_the_slug_as_the_company(self) -> None:
        posting = {
            "id": 42,
            "title": "Frontend Engineer",
            "absolute_url": "https://boards.greenhouse.io/anthropic/jobs/42",
            "location": {"name": "Remote"},
            "content": "<p>React &amp; TypeScript</p>",
            "updated_at": "2026-11-10T12:00:00Z",
        }
        jobs = adapt_rows(plan("greenhouse:anthropic", "greenhouse", slug="anthropic", company="Anthropic"), [posting])
        assert jobs[0].company == "Anthropic"
        assert jobs[0].is_remote is True
        assert "React" in jobs[0].description

    def test_an_ashby_posting_becomes_a_normalized_job(self) -> None:
        posting = {
            "id": "ash-1",
            "title": "Software Engineer",
            "location": "Remote - Argentina",
            "isRemote": True,
            "descriptionPlain": "Node and MongoDB",
            "publishedAt": "2026-11-09T00:00:00Z",
            "jobUrl": "https://jobs.ashbyhq.com/linear/ash-1",
            "applyUrl": "https://jobs.ashbyhq.com/linear/ash-1/apply",
            "employmentType": "FullTime",
        }
        jobs = adapt_rows(plan("ashby:linear", "ashby", slug="linear", company="Linear"), [posting])
        assert jobs[0].title == "Software Engineer"
        assert jobs[0].sources[0].source == "ashby"

    def test_unknown_kinds_are_rejected_loudly(self) -> None:
        with pytest.raises(ValueError):
            adapt_rows(plan("mystery", "mystery"), [{}])

class TestPublicApiScrapers:
    def test_public_api_rows_map_fields_and_keep_safe_listing_urls(self) -> None:
        remote_payload = [
            {"legal": "API Terms of Service"},
            {
                "id": "remote-1",
                "position": "Desarrollador Junior React",
                "company": "Acme",
                "location": "Remoto, Argentina",
                "description": "<p>React and TypeScript</p>",
                "date": "2026-11-11T10:00:00Z",
                "url": "remoteOK.com/remote-jobs/remote-1",
            },
        ]
        jooble_payload = {
            "jobs": [
                {
                    "id": 123,
                    "title": "Desarrollador Frontend Junior",
                    "company": "Acme",
                    "location": "Remoto, Argentina",
                    "snippet": "React y TypeScript",
                    "type": "Full-time",
                    "link": "ar.jooble.org/jdp/123",
                }
            ]
        }

        def transport(method, url, headers, body, *, timeout):
            if url == "https://remoteok.com/api":
                return 200, json.dumps(remote_payload).encode()
            return 200, json.dumps(jooble_payload).encode()

        scraper = build_scraper(
            scrape_jobs=lambda **_: [], jooble_api_key="test-key", transport=transport
        )
        remote_plan = plan("remoteok", "remoteok")
        jooble_plan = plan("jooble:frontend", "jooble")
        remote_job = adapt_rows(remote_plan, scraper(remote_plan))[0]
        jooble_job = adapt_rows(jooble_plan, scraper(jooble_plan))[0]
        invalid_job = adapt_rows(
            remote_plan,
            [dict(remote_payload[1], url="javascript:alert(1)")],
        )[0]

        assert remote_job.is_remote and remote_job.description == "React and TypeScript"
        assert remote_job.sources[0].url == "https://remoteOK.com/remote-jobs/remote-1"
        assert jooble_job.is_remote and jooble_job.job_type == "Full-time"
        assert jooble_job.sources[0].url == "https://ar.jooble.org/jdp/123"
        assert jooble_job.posted_at is None
        assert invalid_job.sources[0].url == ""

    def test_jooble_key_is_optional_and_confined(self, caplog) -> None:
        base_env = {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "service-secret",
        }
        assert WorkerConfig.from_env(base_env).jooble_api_key is None
        assert not any(p.kind == "jooble" for p in due_plans(at(DAILY_HOURS[0])))

        no_key_calls: list[str] = []
        no_key_scraper = build_scraper(
            scrape_jobs=lambda **_: [],
            transport=lambda method, url, headers, body, *, timeout: (
                no_key_calls.append(url) or (200, b'{"jobs":[]}')
            ),
        )
        with pytest.raises(MissingConfig, match="JOOBLE_API_KEY"):
            no_key_scraper(plan("jooble:frontend", "jooble"))
        assert no_key_calls == []

        secret = "private-key"
        config = WorkerConfig.from_env({**base_env, "JOOBLE_API_KEY": secret})
        assert secret not in repr(config)

        def failed_transport(method, url, headers, body, *, timeout):
            raise RuntimeError(f"failed request to {url}")

        scraper = build_scraper(
            scrape_jobs=lambda **_: [], jooble_api_key=secret, transport=failed_transport
        )
        candidate = SourcePlan(
            name="jooble:frontend",
            kind="jooble",
            params={"keywords": "frontend", "location": "Argentina"},
        )
        writer = FakeWriter()
        result = run_collection(
            [candidate], scrape=scraper, writer=writer, today=TODAY
        )
        telemetry = json.dumps({"runs": writer.runs, "payloads": writer.payloads})
        assert secret not in telemetry
        assert secret not in result.runs[0].error
        assert secret not in caplog.text


class TestDedupe:
    def test_two_portals_for_one_offer_become_a_single_job(self) -> None:
        linkedin = make_job(description="")
        indeed = make_job(description=FULL_STACK_JD, location="", site="indeed", url="https://ar.indeed.com/x")
        assert linkedin.fingerprint == indeed.fingerprint
        merged = dedupe([linkedin, indeed])
        assert len(merged) == 1
        assert {source.source for source in merged[0].sources} == {"linkedin", "indeed"}

    def test_the_longest_description_wins_and_its_raw_payload_follows(self) -> None:
        short = make_job(description="short")
        long = make_job(description=FULL_STACK_JD, site="indeed")
        merged = dedupe([short, long])[0]
        assert merged.description == FULL_STACK_JD
        assert merged.raw == long.raw

    def test_empty_fields_are_backfilled_without_overwriting_known_ones(self) -> None:
        empty = make_job(location="", is_remote=False, posted_at=None)
        filled = make_job(location="Buenos Aires, AR", is_remote=True, posted_at=TODAY, site="indeed")
        merged = dedupe([empty, filled])[0]
        assert merged.location == "Buenos Aires, AR"
        assert merged.is_remote is True
        assert merged.posted_at == TODAY

    def test_the_first_seen_order_is_preserved(self) -> None:
        first = make_job(company="A", title="Uno")
        second = make_job(company="B", title="Dos")
        duplicate = make_job(company="A", title="Uno", site="indeed")
        merged = dedupe([first, second, duplicate])
        assert [job.company for job in merged] == ["A", "B"]

    def test_merging_the_same_source_twice_is_idempotent(self) -> None:
        job = make_job()
        assert merge_jobs(job, job).sources == job.sources

    def test_distinct_offers_are_never_folded(self) -> None:
        jobs = [make_job(title="Uno"), make_job(title="Dos"), make_job(title="Tres")]
        assert len(dedupe(jobs)) == 3


class TestRunCollection:
    def test_one_upsert_and_one_score_per_unique_offer(self) -> None:
        writer = FakeWriter()
        scraper = FakeScraper({"indeed": [INDEED_ROW]})
        result = run_collection([plan("indeed")], scrape=scraper, writer=writer, today=TODAY)
        assert len(result.jobs) == 1
        assert len(writer.payloads) == 1
        assert len(writer.scores) == 1
        assert writer.scores[0][0] == "uuid-1"

    def test_the_score_is_written_after_the_row_so_the_id_exists(self) -> None:
        writer = FakeWriter()
        run_collection([plan("indeed")], scrape=FakeScraper({"indeed": [INDEED_ROW]}), writer=writer, today=TODAY)
        kinds = [event[0] for event in writer.events]
        assert kinds.index("upsert_job") < kinds.index("save_score")

    def test_the_breakdown_written_is_the_scoring_payload(self) -> None:
        writer = FakeWriter()
        run_collection([plan("indeed")], scrape=FakeScraper({"indeed": [INDEED_ROW]}), writer=writer, today=TODAY)
        _, total, breakdown = writer.scores[0]
        assert sum(breakdown["parts"].values()) == breakdown["raw_total"]
        assert breakdown["total"] == round(breakdown["raw_total"] * breakdown["recency_multiplier"])
        assert breakdown["total"] == total

    def test_a_posting_past_the_age_cap_is_never_stored(self) -> None:
        old = dict(
            INDEED_ROW,
            id="old-1",
            date_posted=(TODAY - timedelta(days=61)).isoformat(),
        )
        writer = FakeWriter()
        result = run_collection(
            [plan("indeed")],
            scrape=FakeScraper({"indeed": [old]}),
            writer=writer,
            today=TODAY,
        )
        assert writer.payloads == []
        assert result.jobs == []
        assert result.stale == 1

    def test_a_posting_on_the_age_cap_is_still_stored(self) -> None:
        edge = dict(
            INDEED_ROW,
            id="edge-1",
            date_posted=(TODAY - timedelta(days=60)).isoformat(),
        )
        writer = FakeWriter()
        result = run_collection(
            [plan("indeed")],
            scrape=FakeScraper({"indeed": [edge]}),
            writer=writer,
            today=TODAY,
        )
        assert len(writer.payloads) == 1
        assert result.stale == 0

    def test_only_offers_at_or_above_the_notify_threshold_are_returned(self) -> None:
        weak = dict(INDEED_ROW, description="", title="Cocinero", id="weak-1")
        strong = dict(INDEED_ROW, id="strong-1")
        result = run_collection(
            [plan("indeed")],
            scrape=FakeScraper({"indeed": [weak, strong]}),
            writer=FakeWriter(),
            today=TODAY,
        )
        assert [job.job.title for job in result.notify] == ["Desarrollador Junior React"]

    def test_a_failing_source_is_recorded_and_the_others_still_run(self) -> None:
        writer = FakeWriter()
        scraper = FakeScraper({"indeed": [INDEED_ROW]}, failures={"linkedin"})
        result = run_collection([plan("linkedin"), plan("indeed")], scrape=scraper, writer=writer, today=TODAY)
        failed = next(run for run in result.runs if run.source == "linkedin")
        assert failed.status == "error"
        assert "exploded" in failed.error
        assert len(result.jobs) == 1

    def test_public_api_jobs_dedupe_and_survive_partial_source_failure(self) -> None:
        remoteok = {
            "id": "remote-1",
            "position": "Desarrollador Junior React",
            "company": "Acme",
            "location": "Remoto, Argentina",
            "description": "React and TypeScript developer",
            "date": TODAY.isoformat(),
            "url": "remoteOK.com/remote-jobs/remote-1",
        }
        jooble = {
            "id": 123,
            "title": "Desarrollador Junior React",
            "company": "Acme",
            "location": "Remoto, Argentina",
            "snippet": "React developer",
            "type": "Full-time",
            "link": "ar.jooble.org/jdp/123",
        }
        writer = FakeWriter()
        plans = [
            plan("remoteok", "remoteok"),
            plan("jooble:frontend", "jooble"),
            plan("jooble:other", "jooble"),
        ]
        result = run_collection(
            plans,
            scrape=FakeScraper(
                {"remoteok": [remoteok], "jooble:frontend": [jooble]},
                failures={"jooble:other"},
            ),
            writer=writer,
            today=TODAY,
        )

        assert len(result.jobs) == 1
        assert len(writer.payloads) == 1
        assert {source["source"] for source in writer.payloads[0]["sources"]} == {
            "remoteok",
            "jooble",
        }
        assert writer.payloads[0]["description"] == remoteok["description"]
        assert [run.status for run in result.runs] == ["ok", "ok", "error"]

    def test_every_plan_closes_its_scrape_run(self) -> None:
        writer = FakeWriter()
        run_collection(
            [plan("linkedin"), plan("indeed")],
            scrape=FakeScraper({"indeed": [INDEED_ROW]}),
            writer=writer,
            today=TODAY,
        )
        assert len(writer.runs) == 2
        assert all(run["finished"] for run in writer.runs)
        assert {run["source"] for run in writer.runs} == {"linkedin", "indeed"}

    def test_rows_fetched_counts_scraped_rows_and_rows_new_counts_unique_offers(self) -> None:
        writer = FakeWriter()
        duplicate = dict(INDEED_ROW, site="indeed")
        run_collection(
            [plan("indeed")],
            scrape=FakeScraper({"indeed": [INDEED_ROW, duplicate]}),
            writer=writer,
            today=TODAY,
        )
        run = writer.runs[0]
        assert run["rows_fetched"] == 2
        assert run["rows_new"] == 1

    def test_an_offer_found_by_two_sources_is_attributed_to_the_first(self) -> None:
        writer = FakeWriter()
        run_collection(
            [plan("linkedin"), plan("indeed")],
            scrape=FakeScraper({"linkedin": [LINKEDIN_ROW], "indeed": [INDEED_ROW]}),
            writer=writer,
            today=TODAY,
        )
        by_source = {run["source"]: run for run in writer.runs}
        assert by_source["linkedin"]["rows_new"] == 1
        assert by_source["indeed"]["rows_new"] == 0

    def test_rows_without_a_title_are_dropped_instead_of_written(self) -> None:
        writer = FakeWriter()
        broken = dict(INDEED_ROW, title=None, id="broken-1")
        result = run_collection(
            [plan("indeed")],
            scrape=FakeScraper({"indeed": [broken, INDEED_ROW]}),
            writer=writer,
            today=TODAY,
        )
        assert len(writer.payloads) == 1
        assert result.runs[0].status == "error"
        assert "dropped" in result.runs[0].error

    def test_without_a_fetcher_no_linkedin_request_is_made(self) -> None:
        result = run_collection(
            [plan("linkedin")],
            scrape=FakeScraper({"linkedin": [LINKEDIN_ROW]}),
            writer=FakeWriter(),
            today=TODAY,
        )
        assert result.enrichment.fetched == 0

    def test_a_description_is_copied_from_a_sibling_source_before_scoring(self) -> None:
        result = run_collection(
            [plan("linkedin"), plan("indeed")],
            scrape=FakeScraper({"linkedin": [LINKEDIN_ROW], "indeed": [INDEED_ROW]}),
            writer=FakeWriter(),
            today=TODAY,
        )
        assert result.enrichment.copied == 1
        linkedin_job = next(job for job in result.jobs if job.job.sources[0].source == "linkedin")
        assert linkedin_job.breakdown.parts["stack"] > 0

    def test_a_writer_failure_does_not_abort_the_remaining_offers(self) -> None:
        class FlakyWriter(FakeWriter):
            def upsert_job(self, payload: dict) -> str:
                if payload["title"] == "Analista Funcional Jr":
                    raise RuntimeError("postgrest said no")
                return super().upsert_job(payload)

        writer = FlakyWriter()
        rows = [dict(INDEED_ROW, title="Analista Funcional Jr", id="weak-1"), INDEED_ROW]
        result = run_collection([plan("indeed")], scrape=FakeScraper({"indeed": rows}), writer=writer, today=TODAY)
        assert len(writer.payloads) == 1
        assert result.failed == 1

    def test_an_off_topic_title_never_reaches_the_writer(self) -> None:
        writer = FakeWriter()
        rows = [
            dict(INDEED_ROW, title="Tutor/a online de Matemáticas", id="noise-1"),
            INDEED_ROW,
        ]
        result = run_collection(
            [plan("indeed")],
            scrape=FakeScraper({"indeed": rows}),
            writer=writer,
            today=TODAY,
        )
        assert [payload["title"] for payload in writer.payloads] == ["Desarrollador Junior React"]
        assert result.off_topic == 1
        assert result.jobs and all(job.job.title != "Tutor/a online de Matemáticas" for job in result.jobs)

    def test_the_run_result_records_every_plan_it_was_given(self) -> None:
        result = run_collection(
            [plan("linkedin"), plan("indeed")],
            scrape=FakeScraper({}),
            writer=FakeWriter(),
            today=TODAY,
        )
        assert [run.source for run in result.runs] == ["linkedin", "indeed"]
        assert all(run.status == "ok" for run in result.runs)

    def test_a_recording_writer_never_leaves_the_process(self) -> None:
        recorder = RecordingWriter()
        result = run_collection([plan("indeed")], scrape=FakeScraper({"indeed": [INDEED_ROW]}), writer=recorder, today=TODAY)
        assert len(recorder.payloads) == 1
        assert result.jobs[0].job_id == "dry-run-1"
        assert recorder.scores == []

    def test_a_collection_run_purges_expired_offers_with_its_reference_date(self) -> None:
        class PurgingWriter(FakeWriter):
            def __init__(self) -> None:
                super().__init__()
                self.purged_with: list[date | None] = []

            def purge_expired_jobs(self, *, today: date | None = None) -> int:
                self.purged_with.append(today)
                return 4

        writer = PurgingWriter()
        result = run_collection(
            [plan("indeed")],
            scrape=FakeScraper({"indeed": [INDEED_ROW]}),
            writer=writer,
            today=TODAY,
        )
        assert writer.purged_with == [TODAY]
        assert result.purged == 4

    def test_a_failing_purge_does_not_abort_the_run(self) -> None:
        class BrokenPurge(FakeWriter):
            def purge_expired_jobs(self, *, today: date | None = None) -> int:
                raise RuntimeError("postgrest said no")

        writer = BrokenPurge()
        result = run_collection(
            [plan("indeed")],
            scrape=FakeScraper({"indeed": [INDEED_ROW]}),
            writer=writer,
            today=TODAY,
        )
        assert len(writer.payloads) == 1
        assert result.purged == 0

    def test_a_recording_writer_has_no_purge_so_a_dry_run_never_deletes(self) -> None:
        assert not hasattr(RecordingWriter(), "purge_expired_jobs")


class TestEnvAndConfig:
    def test_the_env_file_is_parsed_and_comments_are_ignored(self, tmp_path) -> None:
        path = tmp_path / ".env"
        path.write_text(
            "# comment\n\nSUPABASE_URL=https://example.supabase.co\nSUPABASE_SERVICE_ROLE_KEY='secret'\n",
            encoding="utf-8",
        )
        env = load_env(path, environ={})
        assert env["SUPABASE_URL"] == "https://example.supabase.co"
        assert env["SUPABASE_SERVICE_ROLE_KEY"] == "secret"

    def test_the_process_environment_overrides_the_file(self, tmp_path) -> None:
        path = tmp_path / ".env"
        path.write_text("SUPABASE_SERVICE_ROLE_KEY=from-file\n", encoding="utf-8")
        env = load_env(path, environ={"SUPABASE_SERVICE_ROLE_KEY": "from-process"})
        assert env["SUPABASE_SERVICE_ROLE_KEY"] == "from-process"

    def test_a_missing_file_is_not_an_error(self, tmp_path) -> None:
        assert load_env(tmp_path / "nope.env", environ={}) == {}

    def test_a_missing_service_key_stops_the_worker_before_any_write(self) -> None:
        with pytest.raises(MissingConfig):
            WorkerConfig.from_env({"SUPABASE_URL": "https://example.supabase.co"})

    def test_a_missing_url_stops_the_worker_too(self) -> None:
        with pytest.raises(MissingConfig):
            WorkerConfig.from_env({"SUPABASE_SERVICE_ROLE_KEY": "secret"})

    def test_a_publishable_key_is_rejected_before_any_write(self) -> None:
        with pytest.raises(MissingConfig):
            WorkerConfig.from_env(
                {
                    "SUPABASE_URL": "https://example.supabase.co",
                    "SUPABASE_SERVICE_ROLE_KEY": "sb_publishable_example",
                }
            )

    def test_the_secret_key_variable_is_accepted_on_its_own(self) -> None:
        config = WorkerConfig.from_env(
            {
                "SUPABASE_URL": "https://example.supabase.co",
                "SUPABASE_SECRET_KEY": "sb_secret_example",
            }
        )
        assert config.service_key == "sb_secret_example"

    def test_the_secret_key_wins_over_a_stale_legacy_variable(self) -> None:
        config = WorkerConfig.from_env(
            {
                "SUPABASE_URL": "https://example.supabase.co",
                "SUPABASE_SERVICE_ROLE_KEY": "sb_publishable_stale",
                "SUPABASE_SECRET_KEY": "sb_secret_example",
            }
        )
        assert config.service_key == "sb_secret_example"

    def test_a_publishable_key_in_the_secret_variable_is_rejected(self) -> None:
        with pytest.raises(MissingConfig):
            WorkerConfig.from_env(
                {
                    "SUPABASE_URL": "https://example.supabase.co",
                    "SUPABASE_SECRET_KEY": "sb_publishable_example",
                }
            )

    def test_the_default_env_path_prefers_dot_env_over_dot_env_txt(
        self, tmp_path, monkeypatch
    ) -> None:
        from app import worker as worker_module

        app_dir = tmp_path / "app"
        app_dir.mkdir()
        (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")
        (tmp_path / ".env.txt").write_text("A=2\n", encoding="utf-8")
        monkeypatch.setattr(worker_module, "__file__", str(app_dir / "worker.py"))
        assert worker_module._default_env_path() == tmp_path / ".env"

    def test_a_complete_environment_yields_a_config_with_a_trimmed_url(self) -> None:
        config = WorkerConfig.from_env(
            {
                "SUPABASE_URL": "https://example.supabase.co/",
                "SUPABASE_SERVICE_ROLE_KEY": "secret",
                "SCRAPE_TIMEZONE": "America/Argentina/Buenos_Aires",
            }
        )
        assert config.base_url == "https://example.supabase.co"
        assert config.timezone == "America/Argentina/Buenos_Aires"

    def test_the_default_timezone_is_argentina(self) -> None:
        config = WorkerConfig.from_env(
            {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "secret"}
        )
        assert config.timezone == "America/Argentina/Buenos_Aires"

class TestSupabaseWriter:
    def test_the_rpc_call_posts_the_payload_under_its_argument_name(self) -> None:
        calls: list[tuple] = []

        def transport(method, url, headers, body):
            calls.append((method, url, headers, body))
            return 200, b'"8f14e45f-ceea-467a-9575-1b2b1d3d0e11"'

        writer = SupabaseWriter("https://example.supabase.co/", "secret", transport=transport)
        job_id = writer.upsert_job({"fingerprint": "fp"})
        method, url, headers, body = calls[0]
        assert method == "POST"
        assert url == "https://example.supabase.co/rest/v1/rpc/upsert_job"
        assert json.loads(body) == {"payload": {"fingerprint": "fp"}}
        assert headers["apikey"] == "secret"
        assert job_id == "8f14e45f-ceea-467a-9575-1b2b1d3d0e11"

    def test_saving_the_score_patches_the_row_by_id(self) -> None:
        calls = []

        def transport(method, url, headers, body):
            calls.append((method, url, headers, body))
            return 204, b""

        writer = SupabaseWriter("https://example.supabase.co", "secret", transport=transport)
        breakdown = ScoreBreakdown(parts={"stack": 40}, total=90, matched_skills=(), seniority_level="junior", required_years=None)
        writer.save_score("uuid-1", 90, breakdown.to_payload())
        method, url, headers, body = calls[0]
        assert method == "PATCH"
        assert url.endswith("/rest/v1/jobs?id=eq.uuid-1")
        assert json.loads(body)["score"] == 90
        assert headers["Prefer"] == "return=minimal"

    def test_expired_offers_are_deleted_in_one_call(self) -> None:
        calls = []

        def transport(method, url, headers, body):
            calls.append((method, url, headers, body))
            return 200, b'[{"id": "a"}, {"id": "b"}]'

        writer = SupabaseWriter("https://example.supabase.co", "secret", transport=transport)
        assert writer.purge_expired_jobs(today=TODAY) == 2
        method, url, headers, body = calls[0]
        assert method == "DELETE"
        assert body is None
        assert headers["Prefer"] == "return=representation"
        assert "select=id" in url
        # TODAY is 2026-11-11, so the window keeps everything from 11-04 on. The
        # slugless rows fall back to first_seen_at, which is a timestamp.
        assert "and(posted_at.not.is.null,posted_at.lt.2026-11-04)" in url
        assert "and(posted_at.is.null,first_seen_at.lt.2026-11-04T00:00:00Z)" in url

    def test_the_retention_cutoff_defaults_to_seven_days_before_now(self) -> None:
        urls: list[str] = []

        def transport(method, url, headers, body):
            urls.append(url)
            return 200, b"[]"

        writer = SupabaseWriter(
            "https://example.supabase.co",
            "secret",
            transport=transport,
            clock=lambda: datetime(2026, 11, 19, 15, 30, tzinfo=timezone.utc),
        )
        assert writer.purge_expired_jobs() == 0
        assert "posted_at.lt.2026-11-12" in urls[0]
        assert "first_seen_at.lt.2026-11-12T15:30:00Z" in urls[0]

    def test_starting_a_run_returns_the_generated_row_id(self) -> None:
        def transport(method, url, headers, body):
            return 201, b'[{"id": 7}]'

        writer = SupabaseWriter("https://example.supabase.co", "secret", transport=transport)
        assert writer.start_run("indeed", {"search_term": "react"}) == 7

    def test_a_run_is_never_inserted_with_an_explicit_id(self) -> None:
        bodies: list[bytes] = []

        def transport(method, url, headers, body):
            bodies.append(body)
            return 201, b'[{"id": 7}]'

        writer = SupabaseWriter("https://example.supabase.co", "secret", transport=transport)
        writer.start_run("indeed", {})
        assert "id" not in json.loads(bodies[0])

    def test_finishing_a_run_patches_the_telemetry_fields(self) -> None:
        calls = []

        def transport(method, url, headers, body):
            calls.append((method, url, body))
            return 204, b""

        writer = SupabaseWriter("https://example.supabase.co", "secret", transport=transport)
        writer.finish_run(7, status="ok", rows_fetched=30, rows_new=12, error=None)
        method, url, body = calls[0]
        payload = json.loads(body)
        assert method == "PATCH"
        assert url.endswith("/rest/v1/scrape_runs?id=eq.7")
        assert payload["status"] == "ok"
        assert payload["rows_fetched"] == 30
        assert payload["rows_new"] == 12
        assert payload["error"] is None
        assert payload["finished_at"]

    def test_a_non_2xx_response_raises_with_the_status_and_body(self) -> None:
        def transport(method, url, headers, body):
            return 401, b'{"message":"bad key"}'

        writer = SupabaseWriter("https://example.supabase.co", "secret", transport=transport)
        with pytest.raises(Exception) as excinfo:
            writer.upsert_job({"fingerprint": "fp"})
        assert "401" in str(excinfo.value)
        assert "bad key" in str(excinfo.value)

    def test_a_legacy_jwt_key_is_sent_as_both_apikey_and_bearer(self) -> None:
        seen: list[dict] = []

        def transport(method, url, headers, body):
            seen.append(headers)
            return 204, b""

        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.signature"
        writer = SupabaseWriter("https://example.supabase.co", jwt, transport=transport)
        writer.finish_run(1, status="ok", rows_fetched=0, rows_new=0, error=None)
        assert seen[0]["apikey"] == jwt
        assert seen[0]["Authorization"] == f"Bearer {jwt}"

    def test_a_secret_key_is_sent_only_on_the_apikey_header(self) -> None:
        seen: list[dict] = []

        def transport(method, url, headers, body):
            seen.append(headers)
            return 204, b""

        writer = SupabaseWriter(
            "https://example.supabase.co", "sb_secret_example", transport=transport
        )
        writer.finish_run(1, status="ok", rows_fetched=0, rows_new=0, error=None)
        assert seen[0]["apikey"] == "sb_secret_example"
        assert "Authorization" not in seen[0]


class TestLoop:
    def test_the_next_wake_is_never_more_than_an_hour_away(self) -> None:
        assert 0 < seconds_until_next_hour(at(9, 30)) <= 3600
        assert seconds_until_next_hour(at(9, 30)) == 1800

    def test_an_exact_hour_boundary_waits_a_full_hour(self) -> None:
        assert seconds_until_next_hour(at(9, 0)) == 3600
