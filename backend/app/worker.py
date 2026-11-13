"""The hourly collection worker: scrape, enrich, dedupe, score, persist.

The pipeline order is deliberate and load-bearing:

    scrape -> adapt -> enrich -> dedupe -> score -> upsert

Enrichment runs *before* the global fold because ``copy_descriptions`` only fills
a row whose description is empty. LinkedIn cards never carry a JD, so folding
LinkedIn into Indeed first would leave the surviving row with Indeed's text and
the copy pass with nothing to do. Enriching the per-plan batch keeps both
siblings visible to the copy pass.

The fold rule exists because LinkedIn returns an empty ``location`` on most
cards: two rows with the same location-free key fold when their fingerprints
match *or* when either side has no location. An empty location is missing data,
not a different city. The surviving row keeps the first-seen fingerprint, so
identity is stable across runs and the database never grows a duplicate.

No third-party scheduler or HTTP client is used: ``apscheduler``, ``httpx`` and
``supabase-py`` are not in the pinned requirements, so the loop is a stdlib
``while`` + ``time.sleep`` and the transport is ``urllib.request``. The one
pinned exception is ``pywebpush``, and only for signing and encrypting Web Push
notifications; the collection path never touches it.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.enrich import (
    DEFAULT_MAX_FETCHES,
    EnrichmentReport,
    LinkedInDescriptionFetcher,
    enrich_jobs,
)
from app.models import (
    NormalizedJob,
    from_ashby_job,
    from_greenhouse_job,
    from_jobdrop_row,
    sanitize_json,
)
from app.push import (
    PushConfig,
    WebPushNotifier,
    deliver_push_notifications,
)
from app.scoring import ScoreBreakdown, is_stale, score_job, should_notify

LOG = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "America/Argentina/Buenos_Aires"
# One run per hour between 09:00 and 15:59 local time: seven slots a day.
COLLECTION_WINDOW = (9, 16)
# Indeed is the slow, rate-limited source: it runs three times, not seven.
INDEED_HOURS = (9, 12, 15)
# The curated ATS list is cheap and stable, so a single daily pass is enough.
ATS_HOUR = 9

# Frontend-first, in Spanish and English, because the target roles are frontend
# junior/trainee postings. A backend-only term like "node.js developer" pulls
# exactly the roles the profile rules out, so it is deliberately absent.
#
# The list length must not divide 24: the slot index is day*24 + hour, so a
# length that divides 24 would hand the same term to the same hour every day and
# starve the rest of the list.
LINKEDIN_TERMS = (
    "desarrollador frontend",
    "frontend developer react",
    "react developer junior",
    "desarrollador react junior",
    "frontend junior",
    "desarrollador javascript",
    "next.js developer",
    "pasantia desarrollo web",
    "react native junior",
)
INDEED_TERMS = ("desarrollador frontend", "frontend junior", "react developer")

# Both were verified live against the public board APIs. Never add a slug that
# has not returned 200, because a wrong slug is a silent empty run.
ATS_BOARDS = (
    ("greenhouse", "anthropic", "Anthropic"),
    ("ashby", "linear", "Linear"),
)

LINKEDIN_RESULTS = 20
INDEED_RESULTS = 30
LINKEDIN_HOURS_OLD = 168

_ATS_ENDPOINTS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
}

_FALLBACK_OFFSET = timedelta(hours=-3)


# --------------------------------------------------------------------------- #
# Schedule
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SourcePlan:
    """One scrape call: what to ask for and how to adapt the answer."""

    name: str
    kind: str
    params: Mapping[str, Any] = field(default_factory=dict)
    slug: str | None = None
    company: str | None = None


def _zone(tz: str) -> timezone | ZoneInfo:
    """Resolve a timezone name, falling back to Argentina's fixed offset.

    A minimal container may ship without the tz database, and a missing zone
    must degrade to the correct local hour instead of crashing the worker.
    """
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone(_FALLBACK_OFFSET)


def _local(now: datetime, tz: str) -> datetime:
    zone = _zone(tz)
    if now.tzinfo is None:
        return now.replace(tzinfo=zone)
    return now.astimezone(zone)


def in_collection_window(
    now: datetime,
    *,
    tz: str = DEFAULT_TIMEZONE,
    window: tuple[int, int] = COLLECTION_WINDOW,
) -> bool:
    """True inside the local collection window; the end hour is exclusive."""
    return window[0] <= _local(now, tz).hour < window[1]


def linkedin_term_for(now: datetime, *, tz: str = DEFAULT_TIMEZONE) -> str:
    """Rotate the LinkedIn search term once per hour across the seven slots."""
    local = _local(now, tz)
    slot = local.date().toordinal() * 24 + local.hour
    return LINKEDIN_TERMS[slot % len(LINKEDIN_TERMS)]


def due_plans(
    now: datetime,
    *,
    tz: str = DEFAULT_TIMEZONE,
    window: tuple[int, int] = COLLECTION_WINDOW,
    linkedin_terms: Sequence[str] = LINKEDIN_TERMS,
    indeed_terms: Sequence[str] = INDEED_TERMS,
    ats_boards: Sequence[tuple[str, str, str]] = ATS_BOARDS,
) -> list[SourcePlan]:
    """Build the plans due at ``now``; empty outside the window."""
    local = _local(now, tz)
    if not window[0] <= local.hour < window[1]:
        return []

    slot = local.date().toordinal() * 24 + local.hour
    term = linkedin_terms[slot % len(linkedin_terms)]
    plans = [
        SourcePlan(
            name="linkedin",
            kind="jobdrop",
            params={
                "site_name": ["linkedin"],
                "search_term": term,
                "location": "Argentina",
                "results_wanted": LINKEDIN_RESULTS,
                "hours_old": LINKEDIN_HOURS_OLD,
            },
        )
    ]

    if local.hour in INDEED_HOURS:
        for indeed_term in indeed_terms:
            plans.append(
                SourcePlan(
                    name="indeed",
                    kind="jobdrop",
                    params={
                        "site_name": ["indeed"],
                        "search_term": indeed_term,
                        "location": "Argentina",
                        "country_indeed": "Argentina",
                        "results_wanted": INDEED_RESULTS,
                        # Never pass hours_old: it collapses Indeed to a handful
                        # of rows. The date filter runs locally instead.
                    },
                )
            )

    if local.hour == ATS_HOUR:
        for kind, slug, company in ats_boards:
            plans.append(
                SourcePlan(
                    name=f"{kind}:{slug}",
                    kind=kind,
                    params={},
                    slug=slug,
                    company=company,
                )
            )

    return plans


def seconds_until_next_hour(now: datetime) -> int:
    """Seconds to the next full hour, so the loop wakes on the hour."""
    remaining = 3600 - (now.minute * 60 + now.second)
    return remaining if remaining > 0 else 3600


# --------------------------------------------------------------------------- #
# Adapt and fold
# --------------------------------------------------------------------------- #


def adapt_rows(plan: SourcePlan, rows: Iterable[Mapping[str, Any]]) -> list[NormalizedJob]:
    """Convert one plan's raw rows into normalized jobs."""
    if plan.kind == "jobdrop":
        return [from_jobdrop_row(row) for row in rows]
    if plan.kind == "greenhouse":
        employer = plan.company or plan.slug
        return [from_greenhouse_job(row, employer) for row in rows]
    if plan.kind == "ashby":
        employer = plan.company or plan.slug
        return [from_ashby_job(row, employer) for row in rows]
    raise ValueError(f"unknown source kind: {plan.kind}")


def merge_jobs(primary: NormalizedJob, other: NormalizedJob) -> NormalizedJob:
    """Fold ``other`` into ``primary`` without changing the primary identity.

    The fingerprint and loose key stay those of the first-seen row. Recomputing
    them after a location backfill would change identity between runs and create
    a second database row for the same offer.
    """
    description = primary.description
    raw = primary.raw
    # The longest description wins, and its raw payload travels with it so the
    # stored payload matches the stored text.
    if len(other.description) > len(description):
        description = other.description
        raw = other.raw

    merged = dataclasses.replace(
        primary,
        description=description,
        raw=raw,
        location=primary.location or other.location,
        posted_at=primary.posted_at or other.posted_at,
        salary_min=primary.salary_min if primary.salary_min is not None else other.salary_min,
        salary_max=primary.salary_max if primary.salary_max is not None else other.salary_max,
        salary_currency=primary.salary_currency or other.salary_currency,
        job_type=primary.job_type or other.job_type,
        is_remote=primary.is_remote or other.is_remote,
    )
    for source in other.sources:
        merged = merged.merge(source)
    return merged


def dedupe(jobs: Sequence[NormalizedJob]) -> list[NormalizedJob]:
    """Fold probable duplicates, preserving first-seen order.

    Two rows fold only when their location-free keys match and their
    fingerprints match or either side has no location.
    """
    groups: list[NormalizedJob] = []
    index: dict[str, list[int]] = {}

    for job in jobs:
        target: int | None = None
        for position in index.get(job.loose_key, ()):
            candidate = groups[position]
            if (
                candidate.fingerprint == job.fingerprint
                or not candidate.location
                or not job.location
            ):
                target = position
                break
        if target is None:
            index.setdefault(job.loose_key, []).append(len(groups))
            groups.append(job)
        else:
            groups[target] = merge_jobs(groups[target], job)

    return groups


def job_payload(job: NormalizedJob) -> dict[str, Any]:
    """The exact jsonb body ``public.upsert_job`` expects.

    Scoring is a separate PATCH, because ``upsert_job`` deliberately does not
    write ``score``: a re-tune must be able to replace the score without
    touching the scraped row.
    """
    return {
        "fingerprint": job.fingerprint,
        "loose_key": job.loose_key,
        "company": job.company,
        "title": job.title,
        "location": job.location,
        "is_remote": job.is_remote,
        "description": job.description,
        "posted_at": job.posted_at.isoformat() if job.posted_at else None,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_currency": job.salary_currency,
        "job_type": job.job_type,
        "raw": sanitize_json(dict(job.raw)),
        "sources": [
            {
                "source": source.source,
                "url": source.url,
                "source_job_id": source.source_job_id,
                "url_direct": source.url_direct,
            }
            for source in job.sources
        ],
    }


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


class MissingConfig(RuntimeError):
    """Raised when a required environment variable is absent."""


class SupabaseError(RuntimeError):
    """Raised when the PostgREST endpoint answers with a non-2xx status."""


def _default_env_path() -> Path:
    backend = Path(__file__).resolve().parents[1]
    for name in (".env", ".env.txt"):
        candidate = backend / name
        if candidate.exists():
            return candidate
    return backend / ".env"


def load_env(path: str | Path, *, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Read a dotenv-style file, then let the real process env win.

    Tolerates comments, blank lines, an ``export `` prefix, quotes and a missing
    file, because the worker must start with a clear config error rather than a
    traceback from the parser.
    """
    env: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        text = ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            env[key] = value

    overlay = os.environ if environ is None else environ
    for key, value in overlay.items():
        env[str(key)] = str(value)
    return env


@dataclass(frozen=True)
class WorkerConfig:
    """Everything the worker needs to reach the database."""

    base_url: str
    service_key: str
    timezone: str = DEFAULT_TIMEZONE
    window: tuple[int, int] = COLLECTION_WINDOW

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "WorkerConfig":
        base_url = (env.get("SUPABASE_URL") or "").strip().rstrip("/")
        # Prefer the modern secret variable. A leftover legacy variable often
        # still holds the publishable key copied from the dashboard, which the
        # database resolves to the ``anon`` role and rejects on every write.
        secret_key = (env.get("SUPABASE_SECRET_KEY") or "").strip()
        legacy_key = (env.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
        if secret_key.startswith("sb_publishable_"):
            raise MissingConfig(
                "SUPABASE_SECRET_KEY holds a publishable key; use the project secret key"
            )
        service_key = secret_key or legacy_key
        if not base_url:
            raise MissingConfig("SUPABASE_URL is not set")
        if not service_key:
            raise MissingConfig(
                "no Supabase secret key: set SUPABASE_SECRET_KEY to the project secret key"
            )
        if service_key.startswith("sb_publishable_"):
            raise MissingConfig(
                "SUPABASE_SERVICE_ROLE_KEY holds a publishable key; use the project secret key"
            )
        tz = (env.get("SCRAPE_TIMEZONE") or "").strip() or DEFAULT_TIMEZONE
        return cls(base_url=base_url, service_key=service_key, timezone=tz)


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def _urllib_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    *,
    timeout: float = 20.0,
) -> tuple[int, bytes]:
    """One HTTP request through the stdlib; HTTP errors are returned, not raised."""
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _decode(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw).decode("utf-8", "replace")
    return str(raw)


def _parse_rpc_id(raw: bytes | str | None) -> str:
    """Read the uuid returned by ``upsert_job``, tolerating either shape."""
    text = _decode(raw)
    if not text:
        return ""
    try:
        parsed: Any = json.loads(text)
    except ValueError:
        return ""
    if isinstance(parsed, list):
        parsed = parsed[0] if parsed else None
    if isinstance(parsed, Mapping):
        parsed = parsed.get("id")
    return str(parsed).strip() if parsed is not None else ""


def _parse_run_id(raw: bytes | str | None) -> int:
    text = _decode(raw)
    parsed: Any = json.loads(text) if text else None
    if isinstance(parsed, list):
        parsed = parsed[0] if parsed else None
    if isinstance(parsed, Mapping):
        return int(parsed["id"])
    raise SupabaseError(f"unexpected scrape_runs response: {text[:120]}")


class SupabaseWriter:
    """The write side of the pipeline, talking PostgREST with ``service_role``."""

    def __init__(
        self,
        base_url: str,
        service_key: str,
        *,
        transport: Callable[..., tuple[int, bytes]] | None = None,
        timeout: float = 20.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.base_url = str(base_url).rstrip("/")
        self.service_key = service_key
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.transport = transport or partial(_urllib_transport, timeout=timeout)

    def _call(
        self,
        method: str,
        path: str,
        *,
        body: Any | None = None,
        prefer: str | None = None,
    ) -> bytes:
        url = f"{self.base_url}/rest/v1/{path}"
        headers = {
            "apikey": self.service_key,
            "Accept": "application/json",
        }
        # Legacy service_role keys are JWTs, which PostgREST also accepts as a
        # bearer token. New sb_secret_* keys are not JWTs and are rejected on
        # the Authorization header, so they must travel on ``apikey`` alone.
        if self.service_key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {self.service_key}"
        payload: bytes | None = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(body).encode("utf-8")
        if prefer:
            headers["Prefer"] = prefer

        status, raw = self.transport(method, url, headers, payload)
        if not 200 <= status < 300:
            raise SupabaseError(f"supabase {status}: {_decode(raw)}")
        return raw

    def upsert_job(self, payload: Mapping[str, Any]) -> str:
        raw = self._call("POST", "rpc/upsert_job", body={"payload": payload})
        return _parse_rpc_id(raw)

    def save_score(
        self,
        job_id: str,
        total: int,
        breakdown: Mapping[str, Any],
        *,
        scored_at: datetime | None = None,
    ) -> None:
        stamp = (scored_at or self.clock()).isoformat()
        self._call(
            "PATCH",
            f"jobs?id=eq.{job_id}",
            body={"score": total, "score_breakdown": dict(breakdown), "scored_at": stamp},
            prefer="return=minimal",
        )

    def notified_at(self, job_id: str) -> str | None:
        """When this offer was pushed to the phone, or ``None`` if never.

        One GET per job above the threshold, which today is zero or one row per
        run: cheap, and it keeps the "notify once" rule in the database instead
        of in process memory that a restart would lose.
        """
        raw = self._call("GET", f"jobs?id=eq.{job_id}&select=notified_at")
        rows = json.loads(_decode(raw) or "[]")
        if not rows:
            return None
        return rows[0].get("notified_at") or None

    def mark_notified(self, job_id: str, when: datetime | None = None) -> None:
        """Record that the phone already received this offer."""
        stamp = (when or self.clock()).isoformat()
        self._call(
            "PATCH",
            f"jobs?id=eq.{job_id}",
            body={"notified_at": stamp},
            prefer="return=minimal",
        )

    def push_subscriptions(self) -> list[dict[str, Any]]:
        """Every device that opted in, read with the server-only key.

        The browser writes these rows through RLS; the worker bypasses RLS with
        ``service_role``, which is why this lives here and not in the PWA.
        """
        raw = self._call(
            "GET", "push_subscriptions?select=endpoint,p256dh,auth&order=created_at.asc"
        )
        rows = json.loads(_decode(raw) or "[]")
        return [dict(row) for row in rows if isinstance(row, Mapping)]

    def delete_push_subscription(self, endpoint: str) -> None:
        """Forget an endpoint the push service reported as permanently gone."""
        quoted = quote(endpoint, safe="")
        self._call(
            "DELETE",
            f"push_subscriptions?endpoint=eq.{quoted}",
            prefer="return=minimal",
        )

    def last_run(self) -> dict[str, Any] | None:
        """The most recent collection row, for the local panel."""
        raw = self._call(
            "GET",
            "scrape_runs?select=source,status,started_at,finished_at,"
            "rows_fetched,rows_new,error&order=started_at.desc&limit=1",
        )
        rows = json.loads(_decode(raw) or "[]")
        return dict(rows[0]) if rows else None

    def start_run(self, source: str, params: Mapping[str, Any]) -> int:
        raw = self._call(
            "POST",
            "scrape_runs",
            # ``id`` is an identity column: inserting it is always a mistake.
            body={"source": source, "params": dict(params)},
            prefer="return=representation",
        )
        return _parse_run_id(raw)

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        rows_fetched: int,
        rows_new: int,
        error: str | None = None,
    ) -> None:
        self._call(
            "PATCH",
            f"scrape_runs?id=eq.{run_id}",
            body={
                "status": status,
                "rows_fetched": rows_fetched,
                "rows_new": rows_new,
                "error": error,
                "finished_at": self.clock().isoformat(),
            },
            prefer="return=minimal",
        )


class RecordingWriter:
    """A writer that records instead of writing, for dry runs and tests."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.scores: list[tuple[str, int, dict[str, Any]]] = []
        self.runs: list[dict[str, Any]] = []
        self.notified: list[tuple[str, str]] = []
        self._next_run_id = 0

    def start_run(self, source: str, params: Mapping[str, Any]) -> int:
        self._next_run_id += 1
        self.runs.append(
            {
                "id": self._next_run_id,
                "source": source,
                "params": dict(params),
                "status": "running",
                "rows_fetched": 0,
                "rows_new": 0,
                "error": None,
                "finished_at": None,
            }
        )
        return self._next_run_id

    def upsert_job(self, payload: Mapping[str, Any]) -> str:
        self.payloads.append(dict(payload))
        return f"dry-run-{len(self.payloads)}"

    def save_score(self, job_id: str, total: int, breakdown: Mapping[str, Any]) -> None:
        # Deliberately a no-op: a dry run must leave no trace that looks like a
        # persisted score.
        return None

    def notified_at(self, job_id: str) -> str | None:
        # A dry run writes nothing, so nothing can have been notified already.
        return None

    def mark_notified(self, job_id: str, when: datetime | None = None) -> None:
        self.notified.append((job_id, (when or datetime.now(timezone.utc)).isoformat()))

    def push_subscriptions(self) -> list[dict[str, Any]]:
        # A dry run writes nothing, so it must not push to a real phone either.
        return []

    def delete_push_subscription(self, endpoint: str) -> None:
        return None

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        rows_fetched: int,
        rows_new: int,
        error: str | None = None,
    ) -> None:
        for run in self.runs:
            if run["id"] == run_id:
                run.update(
                    status=status,
                    rows_fetched=rows_fetched,
                    rows_new=rows_new,
                    error=error,
                    finished_at="recorded",
                )
                return


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RunReport:
    """One ``scrape_runs`` row, mirrored in memory."""

    run_id: int
    source: str
    status: str
    rows_fetched: int
    rows_new: int
    error: str | None = None


@dataclass(frozen=True)
class ScoredJob:
    """An offer that made it through the pipeline, with its stored identity."""

    job: NormalizedJob
    breakdown: ScoreBreakdown
    job_id: str


@dataclass
class RunResult:
    """What one collection run did, for logging and notifications."""

    runs: list[RunReport] = field(default_factory=list)
    jobs: list[ScoredJob] = field(default_factory=list)
    notify: list[ScoredJob] = field(default_factory=list)
    enrichment: EnrichmentReport = field(default_factory=EnrichmentReport)
    errors: list[str] = field(default_factory=list)
    failed: int = 0
    stale: int = 0


def run_collection(
    plans: Sequence[SourcePlan],
    *,
    scrape: Callable[[SourcePlan], Iterable[Mapping[str, Any]]],
    writer: Any,
    fetcher: Any | None = None,
    today: date | None = None,
    max_fetches: int = DEFAULT_MAX_FETCHES,
    clock: Callable[[], datetime] | None = None,
) -> RunResult:
    """Run one batch end to end and never raise for a single bad source or offer.

    ``rows_new`` counts offers unique *within this run*, attributed to the first
    plan that surfaced them. ``upsert_job`` returns a uuid for inserts and
    updates alike, so database novelty cannot be known here; a stable in-run
    count is the honest number.
    """
    result = RunResult()
    # One reference date for the whole run: the age cap, the title-only
    # enrichment ranking and the final score must agree about "today", even
    # when the batch straddles midnight.
    reference = today or date.today()
    collected: list[NormalizedJob] = []
    seen: set[str] = set()

    for plan in plans:
        run_id = writer.start_run(plan.name, dict(plan.params))
        rows: list[Mapping[str, Any]] = []
        kept: list[NormalizedJob] = []
        status = "ok"
        error: str | None = None

        try:
            rows = list(scrape(plan) or [])
            adapted = adapt_rows(plan, rows)
        except Exception as exc:  # one dead source must not stop the others
            status = "error"
            error = str(exc)
        else:
            dropped = 0
            for job in adapted:
                # A posting past the age cap is not a bad match, it is a closed
                # one. It never reaches enrichment or the database, so a board
                # fed by a stale ATS listing cannot resurrect it.
                if is_stale(job, reference):
                    result.stale += 1
                    continue
                if not job.title.strip():
                    dropped += 1
                    continue
                kept.append(job)
            if dropped:
                status = "error"
                error = f"dropped {dropped} row(s) without a title"

        rows_new = 0
        for job in kept:
            if job.loose_key not in seen:
                seen.add(job.loose_key)
                rows_new += 1
        collected.extend(kept)

        report = RunReport(
            run_id=run_id,
            source=plan.name,
            status=status,
            rows_fetched=len(rows),
            rows_new=rows_new,
            error=error,
        )
        result.runs.append(report)
        writer.finish_run(
            run_id,
            status=status,
            rows_fetched=len(rows),
            rows_new=rows_new,
            error=error,
        )

    enriched, enrichment = enrich_jobs(
        collected,
        fetcher=fetcher,
        max_fetches=max_fetches,
        today=reference,
    )
    result.enrichment = enrichment

    for job in dedupe(enriched):
        try:
            breakdown = score_job(job, today=reference)
            payload = job_payload(job)
            job_id = writer.upsert_job(payload)
            writer.save_score(job_id, breakdown.total, breakdown.to_payload())
        except Exception as exc:  # a rejected row must not abort the batch
            result.failed += 1
            result.errors.append(f"{job.fingerprint}: {exc}")
            continue

        scored = ScoredJob(job=job, breakdown=breakdown, job_id=job_id)
        result.jobs.append(scored)
        if should_notify(breakdown.total):
            result.notify.append(scored)

    return result


# --------------------------------------------------------------------------- #
# Live wiring
# --------------------------------------------------------------------------- #


def _fetch_ats(plan: SourcePlan) -> list[Mapping[str, Any]]:
    template = _ATS_ENDPOINTS[plan.kind]
    url = template.format(slug=plan.slug)
    status, raw = _urllib_transport(
        "GET", url, {"Accept": "application/json"}, None, timeout=30.0
    )
    if status != 200:
        raise RuntimeError(f"{plan.name} returned http {status}")
    parsed: Any = json.loads(_decode(raw))
    if isinstance(parsed, Mapping):
        return list(parsed.get("jobs") or [])
    return list(parsed or [])


def build_scraper(
    scrape_jobs: Callable[..., Any] | None = None,
) -> Callable[[SourcePlan], list[Mapping[str, Any]]]:
    """Build the real scraper, importing jobdrop lazily so tests stay light."""
    if scrape_jobs is None:
        from jobdrop import scrape_jobs as imported  # noqa: PLC0415 - optional dep

        scrape_jobs = imported

    def scrape(plan: SourcePlan) -> list[Mapping[str, Any]]:
        if plan.kind == "jobdrop":
            frame = scrape_jobs(**dict(plan.params))
            if hasattr(frame, "to_dict"):
                return frame.to_dict("records")
            return list(frame)
        return _fetch_ats(plan)

    return scrape


def build_notifier(env: Mapping[str, str] | None = None) -> WebPushNotifier | None:
    """Build the Web Push notifier, or ``None`` when no VAPID key is configured.

    A missing ``VAPID_PRIVATE_KEY`` is the off switch: it must never be an error,
    so a worker without notifications keeps collecting exactly as before. Even
    with a key, nothing is sent while no device has subscribed.
    """
    config = PushConfig.from_env(env if env is not None else load_env(_default_env_path()))
    return WebPushNotifier(config) if config else None


def _send_notifications(
    result: RunResult,
    *,
    notifier: WebPushNotifier | None,
    writer: Any,
) -> None:
    """Deliver the offers above the threshold and log the pass.

    Never raises: the database write already succeeded, so a notification
    problem must not turn a good collection run into an error.
    """
    try:
        report = deliver_push_notifications(result.notify, notifier=notifier, writer=writer)
    except Exception:  # a notification must never break a run
        LOG.exception("notification pass failed")
        return
    LOG.info(
        "notifications: %d sent, %d skipped, %d failed, %d expired",
        report.sent,
        report.skipped,
        report.failed,
        report.expired,
    )


def run_forever(
    *,
    scrape: Callable[[SourcePlan], Iterable[Mapping[str, Any]]] | None = None,
    writer: Any | None = None,
    fetcher: Any | None = None,
    notifier: WebPushNotifier | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] | None = None,
    env: Mapping[str, str] | None = None,
    max_fetches: int = DEFAULT_MAX_FETCHES,
) -> None:
    """Wake on the hour and run whatever is due. Never returns on its own."""
    env_map = env if env is not None else load_env(_default_env_path())
    config = WorkerConfig.from_env(env_map)
    scrape = scrape or build_scraper()
    if writer is None:
        writer = SupabaseWriter(config.base_url, config.service_key)
    if notifier is None:
        notifier = build_notifier(env_map)
    clock = now or (lambda: datetime.now(timezone.utc))

    while True:
        moment = clock()
        plans = due_plans(moment, tz=config.timezone, window=config.window)
        if plans:
            try:
                result = run_collection(
                    plans,
                    scrape=scrape,
                    writer=writer,
                    fetcher=fetcher,
                    max_fetches=max_fetches,
                )
                LOG.info(
                    "run finished: %d plans, %d offers, %d notify, %d stale, %d failed",
                    len(result.runs),
                    len(result.jobs),
                    len(result.notify),
                    result.stale,
                    result.failed,
                )
                _send_notifications(result, notifier=notifier, writer=writer)
            except Exception:  # the loop must survive a bad batch
                LOG.exception("collection run failed")
        sleep(seconds_until_next_hour(moment))


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: one run, or the hourly loop."""
    parser = argparse.ArgumentParser(description="Collect, score and store job offers.")
    parser.add_argument("--once", action="store_true", help="run one due batch and exit")
    parser.add_argument("--dry-run", action="store_true", help="record instead of writing")
    parser.add_argument("--no-enrich", action="store_true", help="skip the LinkedIn fetch")
    parser.add_argument("--max-fetches", type=int, default=DEFAULT_MAX_FETCHES)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    config = WorkerConfig.from_env(load_env(_default_env_path()))
    writer: Any = RecordingWriter() if args.dry_run else SupabaseWriter(
        config.base_url, config.service_key
    )
    scrape = build_scraper()
    # Pacing lives inside the fetcher, so the worker cannot burst the endpoint.
    fetcher: Any = None if args.no_enrich else LinkedInDescriptionFetcher()
    # A dry run must not ring a real phone, so notifications stay off there.
    notifier: WebPushNotifier | None = None if args.dry_run else build_notifier()

    if not args.once:
        run_forever(
            scrape=scrape,
            writer=writer,
            fetcher=fetcher,
            notifier=notifier,
            max_fetches=args.max_fetches,
        )
        return 0

    plans = due_plans(datetime.now(timezone.utc), tz=config.timezone, window=config.window)
    if not plans:
        LOG.info("nothing due at this hour")
        return 0
    result = run_collection(
        plans,
        scrape=scrape,
        writer=writer,
        fetcher=fetcher,
        max_fetches=args.max_fetches,
    )
    LOG.info(
        "%d offers, %d to notify, %d stale dropped",
        len(result.jobs),
        len(result.notify),
        result.stale,
    )
    _send_notifications(result, notifier=notifier, writer=writer)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI guard
    raise SystemExit(main())
