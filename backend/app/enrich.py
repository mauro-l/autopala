"""Fill in the descriptions LinkedIn never gives us.

LinkedIn guest cards carry no description, which caps the ``stack`` dimension at
30 and keeps every LinkedIn row below the notify threshold. Enrichment closes
that gap in two passes, cheapest first:

1. copy the JD from a sibling row that already has one, but only when exactly one
   sibling shares the same location-free key -- two different JDs under the same
   company and title are ambiguous, and guessing would attach the wrong offer;
2. for whatever is left, fetch the JD from LinkedIn's guest detail endpoint,
   capped per run and paced with a delay so a burst never becomes a 429.

Pass 2 is opt-in: without an explicit fetcher the module performs no network
access, so tests and dry runs stay offline.
"""

from __future__ import annotations

import dataclasses
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser

from app.models import NormalizedJob
from app.scoring import score_job

LINKEDIN_GUEST_ENDPOINT = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

# A realistic browser identity is required; the default urllib agent is refused.
_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
}

_MARKUP_CLASS = "show-more-less-html__markup"
_JOB_ID_RE = re.compile(r"(\d{6,})")

# 429 is the documented rate limit; 999 is LinkedIn's unofficial "go away".
_THROTTLE_STATUSES = frozenset({429, 999})

DEFAULT_MAX_FETCHES = 8
DEFAULT_FETCH_DELAY_SECONDS = 2.0
# Every row scores at least 42 from seniority/experience/location/recency alone,
# so the cutoff sits above that floor before a request is worth spending.
DEFAULT_MIN_TITLE_SCORE = 50


@dataclass(frozen=True)
class FetchResult:
    """Outcome of one detail request."""

    description: str | None
    status: int | None
    error: str | None = None
    throttled: bool = False


@dataclass(frozen=True)
class FetchAttempt:
    """One attempted fetch, kept for run telemetry."""

    job_id: str
    title: str
    status: int | None
    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class EnrichmentReport:
    """What enrichment did, so the worker can log and record it."""

    copied: int = 0
    fetched: int = 0
    failed: int = 0
    skipped_over_cap: int = 0
    throttled: bool = False
    attempts: tuple[FetchAttempt, ...] = field(default_factory=tuple)


class _MarkupParser(HTMLParser):
    """Collect the text of the JD div, honouring nested elements."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self._capturing = False
        self._done = False
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._done:
            return
        if not self._capturing:
            classes = (dict(attrs).get("class") or "").split()
            if tag == "div" and _MARKUP_CLASS in classes:
                self._capturing = True
                self._depth = 1
            return
        if tag == "div":
            self._depth += 1
        elif tag in {"br", "p", "li"}:
            self._chunks.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if not self._capturing or self._done:
            return
        if tag == "div":
            self._depth -= 1
            if self._depth <= 0:
                self._capturing = False
                self._done = True
        elif tag in {"p", "li"}:
            self._chunks.append(" ")

    def handle_data(self, data: str) -> None:
        if self._capturing and not self._done:
            self._chunks.append(data)

    @property
    def text(self) -> str:
        return " ".join("".join(self._chunks).split())


def parse_linkedin_description(markup: str) -> str | None:
    """Return the JD text from a guest detail page, or None when absent.

    Returns None rather than an empty string so callers can tell "no markup" from
    "markup that happened to be blank".
    """
    if not markup:
        return None
    parser = _MarkupParser()
    parser.feed(markup)
    parser.close()
    return parser.text or None


def linkedin_job_id(job: NormalizedJob) -> str | None:
    """Extract the numeric posting id needed by the guest detail endpoint."""
    for source in job.sources:
        if source.source != "linkedin":
            continue
        for value in (source.source_job_id, source.url):
            if not value:
                continue
            match = _JOB_ID_RE.search(value)
            if match:
                return match.group(1)
    return None


def needs_linkedin_description(job: NormalizedJob) -> bool:
    """True when this row is a LinkedIn card we can still fetch a JD for."""
    if job.description.strip():
        return False
    if not any(source.source == "linkedin" for source in job.sources):
        return False
    return linkedin_job_id(job) is not None


class LinkedInDescriptionFetcher:
    """Paced client for the LinkedIn guest detail endpoint.

    The transport is injectable so tests never touch the network, and pacing
    lives here so no caller can accidentally burst the endpoint.
    """

    def __init__(
        self,
        *,
        delay_seconds: float = DEFAULT_FETCH_DELAY_SECONDS,
        timeout: float = 25.0,
        sleep: Callable[[float], None] = time.sleep,
        transport: Callable[[str], tuple[int, str]] | None = None,
    ) -> None:
        self._delay = max(0.0, float(delay_seconds))
        self._timeout = float(timeout)
        self._sleep = sleep
        self._transport = transport or self._http_transport
        self._calls = 0

    def _http_transport(self, job_id: str) -> tuple[int, str]:
        request = urllib.request.Request(
            LINKEDIN_GUEST_ENDPOINT.format(job_id=job_id), headers=_REQUEST_HEADERS
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")

    def fetch(self, job_id: str) -> FetchResult:
        """Fetch one posting. Never raises: a failure is data, not a crash."""
        if self._calls:
            self._sleep(self._delay)
        self._calls += 1
        try:
            status, body = self._transport(job_id)
        except urllib.error.HTTPError as exc:
            status, body = exc.code, ""
        except Exception as exc:  # timeout, DNS, TLS, parse errors
            return FetchResult(description=None, status=None, error=type(exc).__name__)
        if status in _THROTTLE_STATUSES:
            return FetchResult(description=None, status=status, error="throttled", throttled=True)
        if status != 200:
            return FetchResult(description=None, status=status, error=f"http {status}")
        description = parse_linkedin_description(body)
        if description is None:
            return FetchResult(description=None, status=status, error="no description markup")
        return FetchResult(description=description, status=status)


def copy_descriptions(
    jobs: Sequence[NormalizedJob],
) -> tuple[list[NormalizedJob], EnrichmentReport]:
    """Copy a JD between rows that share a location-free key, when unambiguous.

    Rows are never mutated in place; the input list is returned as a new list.
    """
    described: dict[str, set[str]] = {}
    for job in jobs:
        text = job.description.strip()
        if text:
            described.setdefault(job.loose_key, set()).add(text)

    enriched: list[NormalizedJob] = []
    copied = 0
    for job in jobs:
        if job.description.strip():
            enriched.append(job)
            continue
        options = described.get(job.loose_key)
        if options is None or len(options) != 1:
            enriched.append(job)
            continue
        enriched.append(dataclasses.replace(job, description=next(iter(options))))
        copied += 1
    return enriched, EnrichmentReport(copied=copied)


def select_fetch_candidates(
    jobs: Sequence[NormalizedJob],
    *,
    min_title_score: int = DEFAULT_MIN_TITLE_SCORE,
    today: date | None = None,
) -> list[NormalizedJob]:
    """Order the rows worth a request by their title-only score, best first.

    The title-only score is a *heuristic for priority, not a lower bound*: a JD can
    lower the total, because "no mention of experience" defaults to the generous
    15 points while the JD may state "mas de 4 anos" and drop it to 6. Ranking
    still works -- a row cannot reach the threshold without a stack score that
    only the JD can supply.
    """
    ranked: list[tuple[int, str, NormalizedJob]] = []
    for job in jobs:
        if not needs_linkedin_description(job):
            continue
        total = score_job(job, today=today).total
        if total < min_title_score:
            continue
        ranked.append((total, job.fingerprint, job))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [job for _, _, job in ranked]


def enrich_jobs(
    jobs: Sequence[NormalizedJob],
    *,
    fetcher: LinkedInDescriptionFetcher | None = None,
    max_fetches: int = DEFAULT_MAX_FETCHES,
    min_title_score: int = DEFAULT_MIN_TITLE_SCORE,
    today: date | None = None,
) -> tuple[list[NormalizedJob], EnrichmentReport]:
    """Run both enrichment passes over one batch, cheapest pass first."""
    enriched, copy_report = copy_descriptions(jobs)
    if fetcher is None or max_fetches <= 0:
        return enriched, copy_report

    candidates = select_fetch_candidates(enriched, min_title_score=min_title_score, today=today)
    fetched: dict[str, str] = {}
    attempts: list[FetchAttempt] = []
    failed = 0
    throttled = False

    for job in candidates[:max_fetches]:
        job_id = linkedin_job_id(job)
        if job_id is None:  # unreachable: candidates are validated above
            continue
        result = fetcher.fetch(job_id)
        attempts.append(
            FetchAttempt(
                job_id=job_id,
                title=job.title,
                status=result.status,
                ok=result.description is not None,
                error=result.error,
            )
        )
        if result.description is not None:
            fetched[job.fingerprint] = result.description
        elif result.throttled:
            throttled = True
            break
        else:
            failed += 1

    if fetched:
        enriched = [
            dataclasses.replace(job, description=fetched[job.fingerprint])
            if job.fingerprint in fetched
            else job
            for job in enriched
        ]

    report = dataclasses.replace(
        copy_report,
        fetched=len(fetched),
        failed=failed,
        skipped_over_cap=max(0, len(candidates) - max_fetches),
        throttled=throttled,
        attempts=tuple(attempts),
    )
    return enriched, report
