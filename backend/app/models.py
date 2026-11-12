"""The canonical job row every source adapter produces.

One offer can appear on LinkedIn, Indeed and a company's ATS board with three
different URLs, so identity is the fingerprint from ``normalize`` and each portal
URL is kept as a child ``JobSource``. The raw payload travels with the row so the
scoring rules can be re-tuned without spending another scrape.
"""

from __future__ import annotations

import dataclasses
import html
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Mapping

from app.normalize import (
    make_fingerprint,
    make_loose_key,
    normalize_location,
    parse_is_remote,
)

_TAG_RE = re.compile(r"<[^>]+>")
_AMOUNT_RE = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass(frozen=True)
class JobSource:
    """One portal occurrence of an offer."""

    source: str
    url: str
    source_job_id: str | None = None
    url_direct: str | None = None


@dataclass
class NormalizedJob:
    """A deduplicated offer, independent of where it was scraped from."""

    fingerprint: str
    loose_key: str
    company: str
    title: str
    location: str
    is_remote: bool
    description: str
    posted_at: date | None
    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    job_type: str | None
    sources: list[JobSource] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def merge(self, source: JobSource) -> "NormalizedJob":
        """Return a copy carrying one more portal URL, without duplicating it."""
        if source in self.sources:
            return self
        return dataclasses.replace(self, sources=[*self.sources, source])


def _clean_str(value: object) -> str | None:
    """Trim a scalar, turning NaN/None/blank into None."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    return text or None


def strip_html(value: object) -> str:
    """Unescape and strip HTML, keeping one space where a tag used to be.

    Greenhouse serves its job body HTML-escaped, so unescaping comes first.
    """
    text = _clean_str(value)
    if text is None:
        return ""
    return " ".join(_TAG_RE.sub(" ", html.unescape(text)).split())


def parse_date(value: object) -> date | None:
    """Coerce a scraped publication date into a real date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _clean_str(value)
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def parse_amount(value: object) -> float | None:
    """Coerce a salary figure that may arrive as a number or a formatted string."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if math.isnan(value) or math.isinf(value) else float(value)
    text = _clean_str(value)
    if text is None:
        return None
    match = _AMOUNT_RE.search(text.replace(",", ""))
    return float(match.group()) if match else None


def sanitize_json(value: Any) -> Any:
    """Make a scraped payload safe for a JSON column.

    pandas hands back NaN for every missing number, and ``json.dumps`` writes that
    as the invalid token ``NaN``, which Postgres rejects.
    """
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): sanitize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_json(item) for item in value]
    return value


def _build(
    *,
    company: object,
    title: object,
    location: object,
    is_remote: object,
    description: object,
    posted_at: object,
    salary_min: object,
    salary_max: object,
    salary_currency: object,
    job_type: object,
    source: JobSource,
    raw: Mapping[str, Any],
) -> NormalizedJob:
    company_text = _clean_str(company) or ""
    title_text = _clean_str(title) or ""
    location_text = _clean_str(location) or ""
    return NormalizedJob(
        fingerprint=make_fingerprint(company_text, title_text, location_text),
        loose_key=make_loose_key(company_text, title_text),
        company=company_text,
        title=title_text,
        location=location_text,
        is_remote=parse_is_remote(is_remote),
        description=_clean_str(description) or "",
        posted_at=parse_date(posted_at),
        salary_min=parse_amount(salary_min),
        salary_max=parse_amount(salary_max),
        salary_currency=_clean_str(salary_currency),
        job_type=_clean_str(job_type),
        sources=[source],
        raw=dict(sanitize_json(dict(raw))),
    )


def from_jobdrop_row(row: Mapping[str, Any]) -> NormalizedJob:
    """Build a job from one jobdrop (JobSpy) DataFrame row."""
    site = _clean_str(row.get("site")) or "unknown"
    return _build(
        company=row.get("company"),
        title=row.get("title"),
        location=row.get("location"),
        is_remote=row.get("is_remote"),
        description=row.get("description"),
        posted_at=row.get("date_posted"),
        salary_min=row.get("min_amount"),
        salary_max=row.get("max_amount"),
        salary_currency=row.get("currency"),
        job_type=row.get("job_type"),
        source=JobSource(
            source=site,
            url=_clean_str(row.get("job_url")) or "",
            source_job_id=_clean_str(row.get("id")),
            url_direct=_clean_str(row.get("job_url_direct")),
        ),
        raw=row,
    )


def from_greenhouse_job(job: Mapping[str, Any], company: str | None) -> NormalizedJob:
    """Build a job from a boards-api.greenhouse.io posting."""
    place = job.get("location")
    location_name = place.get("name") if isinstance(place, Mapping) else place
    employer = company or job.get("company_name")
    return _build(
        company=employer,
        title=job.get("title"),
        location=location_name,
        is_remote="remote" in normalize_location(location_name),
        description=strip_html(job.get("content")),
        posted_at=job.get("updated_at"),
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        job_type=None,
        source=JobSource(
            source="greenhouse",
            url=_clean_str(job.get("absolute_url")) or "",
            source_job_id=_clean_str(job.get("id")),
            url_direct=_clean_str(job.get("absolute_url")),
        ),
        raw=job,
    )


def from_ashby_job(job: Mapping[str, Any], company: str | None) -> NormalizedJob:
    """Build a job from an api.ashbyhq.com posting."""
    return _build(
        company=company or job.get("companyName"),
        title=job.get("title"),
        location=job.get("location"),
        is_remote=job.get("isRemote"),
        description=_clean_str(job.get("descriptionPlain")) or strip_html(job.get("descriptionHtml")),
        posted_at=job.get("publishedAt"),
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        job_type=job.get("employmentType"),
        source=JobSource(
            source="ashby",
            url=_clean_str(job.get("jobUrl")) or "",
            source_job_id=_clean_str(job.get("id")),
            url_direct=_clean_str(job.get("applyUrl")) or _clean_str(job.get("jobUrl")),
        ),
        raw=job,
    )
