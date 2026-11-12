"""Deterministic scoring: the app's judgment layer with no model in the loop.

Every point comes from a rule over the job row, so the same offer always scores
the same and a re-tune needs no new scrape (the raw payload is stored beside the
row). An LLM pass may later add nuance, but it is optional and can never be
required: this module alone decides whether an offer is worth a notification.

Weight budgets: stack 40, seniority 25, experience 15, location 10, recency 10.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Mapping

from app.models import NormalizedJob
from app.normalize import normalize_text

NOTIFY_THRESHOLD = 85
SILENT_THRESHOLD = 70

MAX_SCORES: Mapping[str, int] = {
    "stack": 40,
    "seniority": 25,
    "experience": 15,
    "location": 10,
    "recency": 10,
}

# Skill weights deliberately sum to 50 so that a posting mentioning every target
# technology saturates the 40-point budget instead of needing all six to reach it.
_SKILL_WEIGHTS: tuple[tuple[str, int, tuple[str, ...]], ...] = (
    ("react", 12, ("react", "reactjs")),
    ("typescript", 10, ("typescript",)),
    ("javascript", 10, ("javascript", "js", "ecmascript", "es6")),
    ("node", 8, ("node", "nodejs")),
    ("express", 5, ("express", "expressjs")),
    ("mongodb", 5, ("mongodb", "mongo")),
)

# "node.js" normalizes to "node js"; left alone, its trailing "js" would also
# count as a JavaScript mention. Each compound collapses to its base token.
_JS_COMPOUNDS = {
    "node js": "node",
    "next js": "next",
    "vue js": "vue",
    "express js": "express",
    "three js": "three",
    "nest js": "nest",
    "react js": "react",
}

_JUNIOR_MARKERS = (
    "junior",
    "jr",
    "trainee",
    "pasante",
    "pasantia",
    "practicante",
    "sin experiencia",
    "recien recibido",
    "estudiante avanzado",
    "entry level",
    "graduate",
    "primer empleo",
)

_MID_MARKERS = ("semi senior", "ssr", "mid level", "mid")

_SENIOR_MARKERS = (
    "senior",
    "sr",
    "lead",
    "lider",
    "architect",
    "arquitecto",
    "staff",
    "principal",
    "manager",
    "head of",
)

_AR_LOCATION_MARKERS = (
    "argentina",
    "buenos aires",
    "caba",
    "amba",
    "gba",
    "rosario",
    "cordoba",
    "mendoza",
    "la plata",
    "tucuman",
    "salta",
    "mar del plata",
    "santa fe",
    "ar",
)

_FOREIGN_LOCATION_MARKERS = (
    "espana",
    "spain",
    "madrid",
    "barcelona",
    "berlin",
    "germany",
    "alemania",
    "mexico",
    "chile",
    "colombia",
    "brasil",
    "brazil",
    "uruguay",
    "peru",
    "united states",
    "usa",
    "canada",
    "india",
)

_SENIORITY_SCORES = {"junior": 25, "mid": 18, "unknown": 12, "senior": 0}

_YEARS_RANGE_RE = re.compile(r"(\d{1,2})\s*(?:a|to|-)\s*(\d{1,2})\s*(?:anos|años|years|year)\b")
_YEARS_SINGLE_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:anos|años|years|year)\b")

# A posting claiming more than a decade is usually describing the company, not
# the role, so the number is treated as noise rather than a hard requirement.
_MAX_PLAUSIBLE_YEARS = 10


@dataclass(frozen=True)
class ScoreBreakdown:
    """One offer's score, kept explainable so a bad match can be diagnosed."""

    parts: Mapping[str, int]
    total: int
    matched_skills: tuple[str, ...]
    seniority_level: str
    required_years: int | None

    @property
    def stack(self) -> int:
        return self.parts["stack"]

    @property
    def seniority(self) -> int:
        return self.parts["seniority"]

    @property
    def experience(self) -> int:
        return self.parts["experience"]

    @property
    def location(self) -> int:
        return self.parts["location"]

    @property
    def recency(self) -> int:
        return self.parts["recency"]

    def to_payload(self) -> dict[str, object]:
        """Shape stored in the ``score_breakdown`` jsonb column."""
        return {
            "parts": dict(self.parts),
            "total": self.total,
            "matched_skills": list(self.matched_skills),
            "seniority_level": self.seniority_level,
            "required_years": self.required_years,
        }


def _phrase_re(phrase: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])")


def _matches_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(_phrase_re(marker).search(text) for marker in markers)


def _deaccent(text: str) -> str:
    """Lowercase and drop accents while keeping digits and punctuation.

    ``normalize_text`` also deletes hyphens, which would destroy "2-4 años", so
    the years rules read this lighter form instead.
    """
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _match_skills(text: str) -> tuple[tuple[str, ...], int]:
    """Return the matched skill names and the capped stack score."""
    masked = text
    for compound, base in _JS_COMPOUNDS.items():
        masked = masked.replace(compound, base)

    matched: list[str] = []
    points = 0
    for name, weight, synonyms in _SKILL_WEIGHTS:
        if _matches_any(masked, synonyms):
            matched.append(name)
            points += weight
    return tuple(matched), min(points, MAX_SCORES["stack"])


def _seniority_of(text: str) -> str | None:
    if _matches_any(text, _JUNIOR_MARKERS):
        return "junior"
    if _matches_any(text, _MID_MARKERS):
        return "mid"
    if _matches_any(text, _SENIOR_MARKERS):
        return "senior"
    return None


def _seniority(title: str, description: str) -> str:
    """Classify the role's level; the title outranks the body text.

    A junior title stays junior even when the description mentions working with
    senior engineers, which is exactly what made keyword counting unusable.
    """
    return _seniority_of(title) or _seniority_of(description) or "unknown"


def _required_years(description: str) -> int | None:
    text = _deaccent(description)

    candidates: list[int] = []
    for match in _YEARS_RANGE_RE.finditer(text):
        candidates.append(int(match.group(1)))
    text = _YEARS_RANGE_RE.sub(" ", text)

    for match in _YEARS_SINGLE_RE.finditer(text):
        candidates.append(int(match.group(0).split()[0].rstrip("+")))

    plausible = [value for value in candidates if value <= _MAX_PLAUSIBLE_YEARS]
    return max(plausible) if plausible else None


def _experience_score(required_years: int | None) -> int:
    if required_years is None or required_years <= 1:
        return MAX_SCORES["experience"]
    if required_years <= 3:
        return 12
    if required_years <= 5:
        return 6
    return 0


def _location_score(location: str, is_remote: bool) -> int:
    if is_remote:
        return MAX_SCORES["location"]
    normalized = normalize_text(location)
    if not normalized:
        return 5
    if _matches_any(normalized, _FOREIGN_LOCATION_MARKERS):
        return 0
    if _matches_any(normalized, _AR_LOCATION_MARKERS):
        return 8
    return 0


def _recency_score(posted_at: date | None, today: date) -> int:
    if posted_at is None:
        return 4
    age = (today - posted_at).days
    if age <= 2:
        return MAX_SCORES["recency"]
    if age <= 7:
        return 8
    if age <= 14:
        return 5
    if age <= 30:
        return 2
    return 0


def score_job(job: NormalizedJob, *, today: date | None = None) -> ScoreBreakdown:
    """Score one offer from 0 to 100, with every point attributed."""
    reference = today or date.today()
    title = normalize_text(job.title)
    description = normalize_text(job.description)

    matched_skills, stack = _match_skills(f"{title} {description}")
    seniority_level = _seniority(title, description)
    required_years = _required_years(job.description)

    parts = {
        "stack": stack,
        "seniority": _SENIORITY_SCORES[seniority_level],
        "experience": _experience_score(required_years),
        "location": _location_score(job.location, job.is_remote),
        "recency": _recency_score(job.posted_at, reference),
    }
    return ScoreBreakdown(
        parts=parts,
        total=sum(parts.values()),
        matched_skills=matched_skills,
        seniority_level=seniority_level,
        required_years=required_years,
    )


def should_notify(total: int) -> bool:
    """Only strong matches interrupt the user; the rest are stored silently."""
    return total >= NOTIFY_THRESHOLD
