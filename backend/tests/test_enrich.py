"""Enrichment: give description-less offers the JD they are worth.

LinkedIn guest cards carry no description, which caps the stack dimension at 30
and keeps every LinkedIn row below the notify threshold. Enrichment closes that
gap in two passes: copy a JD from an unambiguous sibling first, then fetch the
remaining candidates from LinkedIn's guest detail endpoint.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib

import pytest

from app.enrich import (
    EnrichmentReport,
    FetchResult,
    LinkedInDescriptionFetcher,
    copy_descriptions,
    enrich_jobs,
    linkedin_job_id,
    needs_linkedin_description,
    parse_linkedin_description,
    select_fetch_candidates,
)
from app.models import JobSource, NormalizedJob, from_jobdrop_row
from app.normalize import make_fingerprint, make_loose_key
from datetime import date

TODAY = date(2026, 11, 11)
FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "spike_20261111T203008Z.json"

LINKEDIN_HTML = """
<div class="topcard">
  <h2>Desarrollador Senior React</h2>
  <div class="show-more-less-html">
    <div class="show-more-less-html__markup show-more-less-html__markup--rtl">
      <p>Buscamos <strong>React</strong> &amp; TypeScript.</p>
      <div><ul><li>Node.js</li><li>MongoDB</li></ul></div>
    </div>
  </div>
  <div class="similar-jobs">NO DEBE APARECER</div>
</div>
"""


def make_job(
    *,
    company: str = "BairesDev",
    title: str = "React Developer",
    location: str = "",
    description: str = "",
    site: str = "linkedin",
    url: str = "https://www.linkedin.com/jobs/view/4489030478",
    source_job_id: str | None = "li-4489030478",
    posted_at: date | None = TODAY,
    is_remote: bool = False,
) -> NormalizedJob:
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
        sources=[JobSource(source=site, url=url, source_job_id=source_job_id)],
        raw={},
    )


class FakeFetcher:
    """Records calls instead of touching the network."""

    def __init__(self, results: dict[str, FetchResult] | None = None) -> None:
        self.results = results or {}
        self.calls: list[str] = []

    def fetch(self, job_id: str) -> FetchResult:
        self.calls.append(job_id)
        return self.results.get(job_id, FetchResult(description=None, status=200, error="empty"))


class TestParseLinkedInDescription:
    def test_extracts_text_and_decodes_entities(self):
        text = parse_linkedin_description(LINKEDIN_HTML)
        assert text == "Buscamos React & TypeScript. Node.js MongoDB"

    def test_handles_nested_elements_without_swallowing_siblings(self):
        text = parse_linkedin_description(LINKEDIN_HTML)
        assert text is not None
        assert "NO DEBE APARECER" not in text

    def test_returns_none_when_markup_is_absent(self):
        assert parse_linkedin_description("<html><body><p>nada</p></body></html>") is None

    def test_returns_none_on_empty_markup(self):
        html = '<div class="show-more-less-html__markup">   </div>'
        assert parse_linkedin_description(html) is None

    def test_ignores_other_classes_that_share_the_prefix(self):
        html = '<div class="show-more-less-html">texto</div>'
        assert parse_linkedin_description(html) is None


class TestLinkedInJobId:
    def test_reads_digits_from_source_job_id(self):
        assert linkedin_job_id(make_job()) == "4489030478"

    def test_falls_back_to_the_url(self):
        job = make_job(source_job_id=None, url="https://www.linkedin.com/jobs/view/4489030478")
        assert linkedin_job_id(job) == "4489030478"

    def test_reads_digits_from_a_slugged_url(self):
        job = make_job(
            source_job_id=None,
            url="https://www.linkedin.com/jobs/view/desarrollador-react-4489030478",
        )
        assert linkedin_job_id(job) == "4489030478"

    def test_returns_none_for_non_linkedin_sources(self):
        job = make_job(site="indeed", url="https://ar.indeed.com/viewjob?jk=abc123", source_job_id="abc123")
        assert linkedin_job_id(job) is None


class TestNeedsLinkedInDescription:
    def test_true_for_a_bare_linkedin_card(self):
        assert needs_linkedin_description(make_job()) is True

    def test_false_when_a_description_is_already_present(self):
        assert needs_linkedin_description(make_job(description="jd")) is False

    def test_false_for_non_linkedin_sources(self):
        assert needs_linkedin_description(make_job(site="indeed", source_job_id="x")) is False

    def test_false_without_an_identifiable_posting(self):
        job = make_job(source_job_id=None, url="https://www.linkedin.com/jobs/view/")
        assert needs_linkedin_description(job) is False


class TestCopyDescriptions:
    def test_copies_from_a_sibling_with_a_different_location(self):
        # The real case: LinkedIn drops the location, so fingerprints differ while
        # company + title still agree.
        bare = make_job(location="")
        rich = make_job(location="Buenos Aires, Argentina", description="full jd", site="indeed")
        assert bare.fingerprint != rich.fingerprint

        jobs, report = copy_descriptions([bare, rich])

        assert jobs[0].description == "full jd"
        assert report.copied == 1

    def test_does_not_copy_when_siblings_disagree(self):
        bare = make_job()
        first = make_job(location="CABA", description="jd one", site="indeed")
        second = make_job(location="Rosario", description="jd two", site="greenhouse")

        jobs, report = copy_descriptions([bare, first, second])

        assert jobs[0].description == ""
        assert report.copied == 0

    def test_keeps_an_existing_description_untouched(self):
        bare = make_job(description="mine")
        rich = make_job(location="CABA", description="other", site="indeed")

        jobs, report = copy_descriptions([bare, rich])

        assert jobs[0].description == "mine"
        assert report.copied == 0

    def test_ignores_a_different_company(self):
        bare = make_job(company="Acme")
        rich = make_job(company="Globex", location="CABA", description="jd", site="indeed")

        jobs, report = copy_descriptions([bare, rich])

        assert jobs[0].description == ""
        assert report.copied == 0

    def test_does_not_mutate_the_input_rows(self):
        bare = make_job()
        rich = make_job(location="CABA", description="jd", site="indeed")

        copy_descriptions([bare, rich])

        assert bare.description == ""


class TestSelectFetchCandidates:
    def test_ranks_by_title_only_score(self):
        strong = make_job(title="Junior React TypeScript Developer", url="https://x/1", source_job_id="li-4400000001")
        weak = make_job(title="Cocinero", url="https://x/2", source_job_id="li-4400000002")

        candidates = select_fetch_candidates([weak, strong], min_title_score=0, today=TODAY)

        assert [job.sources[0].source_job_id for job in candidates] == ["li-4400000001", "li-4400000002"]

    def test_drops_rows_below_the_cutoff(self):
        # "Cocinero" still scores 42 on seniority/experience/location/recency
        # alone, so the cutoff has to sit above that baseline to mean anything.
        weak = make_job(title="Cocinero", source_job_id="li-4400000002")
        assert select_fetch_candidates([weak], min_title_score=50, today=TODAY) == []

    def test_skips_rows_that_already_have_a_description(self):
        assert select_fetch_candidates([make_job(description="jd")], min_title_score=0, today=TODAY) == []

    def test_is_deterministic_for_tied_scores(self):
        a = make_job(company="Acme", source_job_id="li-4400000011")
        b = make_job(company="Globex", source_job_id="li-4400000012")
        first = [job.sources[0].source_job_id for job in select_fetch_candidates([b, a], min_title_score=0, today=TODAY)]
        second = [job.sources[0].source_job_id for job in select_fetch_candidates([a, b], min_title_score=0, today=TODAY)]
        assert first == second


class TestEnrichJobs:
    def test_fetches_bare_linkedin_cards_and_rescores_higher(self):
        job = make_job()
        fetcher = FakeFetcher({"4489030478": FetchResult(description="React TypeScript Node.js", status=200)})

        jobs, report = enrich_jobs([job], fetcher=fetcher, today=TODAY)

        assert jobs[0].description == "React TypeScript Node.js"
        assert report.fetched == 1
        assert fetcher.calls == ["4489030478"]

    def test_skips_the_network_when_no_fetcher_is_given(self):
        jobs, report = enrich_jobs([make_job()], fetcher=None, today=TODAY)

        assert jobs[0].description == ""
        assert report.fetched == 0
        assert report.attempts == ()

    def test_respects_the_per_run_cap(self):
        jobs = [
            make_job(company=f"Acme{i}", source_job_id=f"li-{4400000000 + i}", url=f"https://x/{i}")
            for i in range(5)
        ]
        fetcher = FakeFetcher(
            {f"{4400000000 + i}": FetchResult(description="jd", status=200) for i in range(5)}
        )

        _, report = enrich_jobs(jobs, fetcher=fetcher, max_fetches=2, today=TODAY)

        assert len(fetcher.calls) == 2
        assert report.skipped_over_cap == 3

    def test_stops_the_batch_once_throttled(self):
        jobs = [
            make_job(company=f"Acme{i}", source_job_id=f"li-{4400000000 + i}", url=f"https://x/{i}")
            for i in range(3)
        ]
        fetcher = FakeFetcher(
            {"4400000000": FetchResult(description=None, status=429, error="too many", throttled=True)}
        )

        _, report = enrich_jobs(jobs, fetcher=fetcher, today=TODAY)

        assert fetcher.calls == ["4400000000"]
        assert report.throttled is True

    def test_counts_failures_without_dropping_the_row(self):
        fetcher = FakeFetcher({"4489030478": FetchResult(description=None, status=404, error="gone")})

        jobs, report = enrich_jobs([make_job()], fetcher=fetcher, today=TODAY)

        assert len(jobs) == 1
        assert jobs[0].description == ""
        assert report.failed == 1

    def test_copies_before_spending_a_request(self):
        bare = make_job(location="")
        rich = make_job(location="CABA", description="full jd", site="indeed")
        fetcher = FakeFetcher()

        jobs, report = enrich_jobs([bare, rich], fetcher=fetcher, today=TODAY)

        assert jobs[0].description == "full jd"
        assert fetcher.calls == []
        assert report.copied == 1

    def test_never_fetches_a_non_linkedin_row(self):
        job = make_job(site="indeed", source_job_id="abc", url="https://ar.indeed.com/viewjob?jk=abc")
        fetcher = FakeFetcher()

        enrich_jobs([job], fetcher=fetcher, today=TODAY)

        assert fetcher.calls == []

    def test_report_is_immutable(self):
        _, report = enrich_jobs([make_job()], fetcher=None, today=TODAY)
        assert isinstance(report, EnrichmentReport)
        with pytest.raises(Exception):
            report.fetched = 9  # type: ignore[misc]


class TestFetcherPacing:
    def test_sleeps_between_calls_but_not_before_the_first(self):
        slept: list[float] = []
        fetcher = LinkedInDescriptionFetcher(delay_seconds=1.5, sleep=slept.append, transport=_offline_transport)

        fetcher.fetch("1")
        fetcher.fetch("2")

        assert slept == [1.5]

    def test_reports_throttling_as_a_flag(self):
        def always_429(job_id: str) -> tuple[int, str]:
            return 429, ""

        fetcher = LinkedInDescriptionFetcher(delay_seconds=0, sleep=lambda _s: None, transport=always_429)
        result = fetcher.fetch("1")

        assert result.throttled is True
        assert result.description is None

    def test_returns_none_when_the_page_has_no_markup(self):
        def empty(job_id: str) -> tuple[int, str]:
            return 200, "<html><body>nada</body></html>"

        fetcher = LinkedInDescriptionFetcher(delay_seconds=0, sleep=lambda _s: None, transport=empty)
        result = fetcher.fetch("1")

        assert result.status == 200
        assert result.description is None
        assert result.error == "no description markup"


def _offline_transport(job_id: str) -> tuple[int, str]:
    return 200, LINKEDIN_HTML


@pytest.mark.skipif(not FIXTURE.exists(), reason="spike fixture not available")
class TestRealSpikeRows:
    def test_finds_the_bairesdev_card_and_its_job_id(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        rows = payload["sources"]["linkedin"]["records"]
        jobs = [from_jobdrop_row(row) for row in rows]
        target = next(job for job in jobs if job.sources[0].source_job_id == "li-4489030478")

        assert needs_linkedin_description(target) is True
        assert linkedin_job_id(target) == "4489030478"

    def test_copy_pass_never_invents_a_description(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        jobs = [
            from_jobdrop_row(row)
            for name in ("linkedin", "indeed")
            for row in payload["sources"][name]["records"]
        ]
        enriched, report = copy_descriptions(jobs)

        assert report.copied >= 0
        for job in enriched:
            if job.description:
                assert any(other.description == job.description for other in jobs)


class TestEnrichmentScoringEffect:
    """What enrichment does to the score -- measured against real JDs.

    Both facts below were observed on the live guest endpoint on 2026-11-11, so
    they pin behaviour that is easy to "fix" into a regression.
    """

    def test_a_jd_naming_the_stack_raises_the_stack_dimension(self):
        from app.scoring import score_job

        job = make_job(title="React Developer", description="")
        enriched = dataclasses.replace(
            job,
            description=(
                "Buscamos React Developer con TypeScript y JavaScript. "
                "Node.js, Express y MongoDB en el dia a dia."
            ),
        )

        plain, rich = score_job(job, today=TODAY), score_job(enriched, today=TODAY)

        assert rich.parts["stack"] > plain.parts["stack"]
        assert rich.total > plain.total

    def test_enrichment_is_not_monotonic_when_the_jd_states_experience(self):
        from app.scoring import score_job

        # Real case: 'Desarrollador Senior React + TypeScript - Trabajo Remoto'
        # scored 52 title-only and 43 enriched, because the JD says
        # "Mas de 4 anos de experiencia" and the empty-description default is 15.
        job = make_job(title="React Developer", description="")
        enriched = dataclasses.replace(job, description="Mas de 4 anos de experiencia en desarrollo.")

        plain, rich = score_job(job, today=TODAY), score_job(enriched, today=TODAY)

        assert plain.parts["experience"] == 15
        assert rich.parts["experience"] == 6
        assert rich.total < plain.total
