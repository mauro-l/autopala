"""Deterministic scoring: the app's judgment layer with no model in the loop.

Every point comes from a rule over the job row, so the same offer always scores
the same and a re-tune needs no new scrape (the raw payload is stored beside the
row). An LLM pass may later add nuance, but it is optional and can never be
required: this module alone decides whether an offer is worth a notification.

Weight budgets: role 15, stack 30, seniority 25, experience 10, location 10,
recency 10. The shape follows the target profile: a frontend junior job in
Argentina is a strong match even with no description, and a senior backend job
can never reach the notification threshold no matter which keywords it names.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Mapping

from app.models import NormalizedJob
from app.normalize import normalize_text

NOTIFY_THRESHOLD = 80
SILENT_THRESHOLD = 70

MAX_SCORES: Mapping[str, int] = {
    "role": 15,
    "stack": 30,
    "seniority": 25,
    "experience": 10,
    "location": 10,
    "recency": 10,
}

# Skill weights deliberately sum well above the 30-point budget so that a posting
# naming the frontend core saturates it instead of needing every single tool.
# Frontend carries most of the weight; the backend stack is a complement and is
# worth a fraction, so "Angular/NodeJS" cannot outscore "React/TypeScript".
_SKILL_WEIGHTS: tuple[tuple[str, int, tuple[str, ...]], ...] = (
    ("react", 11, ("react", "reactjs")),
    ("typescript", 9, ("typescript",)),
    ("javascript", 8, ("javascript", "js", "ecmascript", "es6")),
    ("next", 6, ("nextjs",)),
    ("react native", 5, ("react native",)),
    ("tailwind", 3, ("tailwind", "daisyui")),
    ("html", 3, ("html", "html5")),
    ("css", 3, ("css", "css3", "sass", "scss")),
    ("node", 3, ("node", "nodejs")),
    ("supabase", 2, ("supabase",)),
    ("express", 2, ("express", "expressjs")),
    ("prisma", 1, ("prisma",)),
    ("mongodb", 1, ("mongodb", "mongo")),
)

# "node.js" normalizes to "node js"; left alone, its trailing "js" would also
# count as a JavaScript mention. Each compound collapses to its base token.
# `next js` folds into a single token because bare "next" is ordinary English
# ("next steps") and would match unrelated postings.
_JS_COMPOUNDS = {
    "node js": "node",
    "next js": "nextjs",
    "vue js": "vue",
    "express js": "express",
    "three js": "three",
    "nest js": "nest",
    "react js": "react",
    "tailwind css": "tailwind",
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
    "intern",
    "internship",
    "all levels",
    "todos los niveles",
)

_MID_MARKERS = ("mid level", "mid", "semi senior", "ssr")
_MID_PHRASES = ("mid level", "nivel mid", "perfil mid", "semi senior", "ssr")

# Masked before the senior check, because "semi senior" and "ssr" contain a
# senior token but describe a mid-level role.
_SENIORITY_COMPOUNDS = {"semi senior": "mid", "ssr": "mid"}

# A title is a reliable place to look for a bare senior word. A job body is
# not: "lead the activities of a team", "principal responsibilities" and "our
# staff" are ordinary prose, and matching them used to zero out the seniority
# budget of perfectly good postings. The body therefore needs a full phrase.
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

_SENIOR_PHRASES = (
    "senior developer",
    "senior engineer",
    "senior software",
    "senior frontend",
    "senior front end",
    "senior backend",
    "senior react",
    "senior full",
    "senior level",
    "senior position",
    "senior role",
    "experiencia senior",
    "perfil senior",
    "nivel senior",
    "tech lead",
    "team lead",
    "lead developer",
    "lead engineer",
    "engineering manager",
    "staff engineer",
    "principal engineer",
    "software architect",
    "arquitecto de software",
    "lider tecnico",
)

# Role is read from the title first: "FullStack Developer SENIOR/SSR" is a
# fullstack job even when the body mentions React components.
_FULLSTACK_MARKERS = ("fullstack", "full stack")
_FRONTEND_MARKERS = (
    "frontend",
    "front end",
    "frontend developer",
    "desarrollador frontend",
    "desarrollador front",
    "desarrollador web",
    "maquetador",
    "ui developer",
    "developer react",
    "react developer",
)
_MOBILE_MARKERS = (
    "react native",
    "mobile developer",
    "mobile engineer",
    "desarrollador movil",
    "android developer",
    "ios developer",
)
_BACKEND_MARKERS = (
    "backend",
    "back end",
    "backend developer",
    "desarrollador backend",
    "node developer",
    "api developer",
    "devops",
    "data engineer",
    "database administrator",
)

_ROLE_SCORES = {
    "frontend": 15,
    "mobile": 12,
    "fullstack": 10,
    "unknown": 8,
    "backend": 0,
}

# Quilmes and the AMBA commute are explicitly acceptable, so they outrank a
# posting in another province that would mean relocating.
_AMBA_MARKERS = (
    "quilmes",
    "avellaneda",
    "lanus",
    "lomas de zamora",
    "berazategui",
    "ezpeleta",
    "wilde",
    "don bosco",
    "bernal",
    "temperley",
    "turdera",
    "banfield",
    "adrogue",
    "claypole",
    "ezeiza",
    "canning",
    "caba",
    "c a b a",
    "capital federal",
    "ciudad autonoma",
    "buenos aires",
    "amba",
    "gba",
    "zona sur",
    "zona norte",
    "zona oeste",
    "vicente lopez",
    "san isidro",
    "olivos",
    "martinez",
    "tigre",
    "pilar",
    "escobar",
    "moron",
    "san justo",
    "la matanza",
    "ramos mejia",
    "hurlingham",
    "ituzaingo",
    "tres de febrero",
    "caseros",
    "san martin",
    "lomas del mirador",
)

_AR_LOCATION_MARKERS = (
    "argentina",
    "rosario",
    "cordoba",
    "mendoza",
    "la plata",
    "tucuman",
    "salta",
    "mar del plata",
    "santa fe",
    "neuquen",
    "bahia blanca",
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

_SENIORITY_SCORES = {"junior": 25, "unknown": 12, "mid": 4, "senior": 0}

_ENGLISH_LEVELS = (
    ("advanced", ("ingles avanzado", "advanced english", "fluent english", "ingles fluido", "english fluent", "b2", "c1", "c2")),
    ("intermediate", ("ingles intermedio", "intermediate english", "b1")),
    ("basic", ("ingles basico", "basic english", "a2")),
)

_ENGLISH_REQUIRED_MARKERS = (
    "ingles excluyente",
    "english required",
    "requiere ingles",
    "requerimos ingles",
    "english is required",
)

_UNPAID_MARKERS = (
    "pasantia no remunerada",
    "no remunerada",
    "sin remuneracion",
    "unpaid",
    "ad honorem",
    "sin paga",
)

_DEGREE_MARKERS = (
    "titulo universitario",
    "estudiante universitario",
    "university degree",
    "bachelor",
    "licenciatura",
    "graduado universitario",
    "ingenieria en sistemas",
)

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
    role_focus: str = "unknown"
    english_level: str | None = None
    english_required: bool = False
    unpaid: bool = False
    degree_required: bool = False

    @property
    def role(self) -> int:
        return self.parts["role"]

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
            "role_focus": self.role_focus,
            "english_level": self.english_level,
            "english_required": self.english_required,
            "unpaid": self.unpaid,
            "degree_required": self.degree_required,
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


def _mask_compounds(text: str) -> str:
    masked = text
    for compound, base in _JS_COMPOUNDS.items():
        masked = masked.replace(compound, base)
    return masked


def _match_skills(text: str) -> tuple[tuple[str, ...], int]:
    """Return the matched skill names and the capped stack score."""
    masked = _mask_compounds(text)

    matched: list[str] = []
    points = 0
    for name, weight, synonyms in _SKILL_WEIGHTS:
        if _matches_any(masked, synonyms):
            matched.append(name)
            points += weight
    return tuple(matched), min(points, MAX_SCORES["stack"])


def _seniority_of(text: str, *, from_title: bool) -> str | None:
    masked = text
    for compound, base in _SENIORITY_COMPOUNDS.items():
        masked = masked.replace(compound, base)
    if _matches_any(masked, _JUNIOR_MARKERS):
        return "junior"
    senior_markers = _SENIOR_MARKERS if from_title else _SENIOR_PHRASES
    if _matches_any(masked, senior_markers):
        return "senior"
    mid_markers = _MID_MARKERS if from_title else _MID_PHRASES
    if _matches_any(masked, mid_markers):
        return "mid"
    return None


def _seniority(title: str, description: str) -> str:
    """Classify the role's level; the title outranks the body text.

    A junior title stays junior even when the description mentions working with
    senior engineers, which is exactly what made keyword counting unusable.
    """
    return (
        _seniority_of(title, from_title=True)
        or _seniority_of(description, from_title=False)
        or "unknown"
    )


def _role_from_title(title: str) -> str | None:
    if _matches_any(title, _FULLSTACK_MARKERS):
        return "fullstack"
    if _matches_any(title, _FRONTEND_MARKERS):
        return "frontend"
    if _matches_any(title, _MOBILE_MARKERS):
        return "mobile"
    if _matches_any(title, _BACKEND_MARKERS):
        return "backend"
    return None


def _role_focus(title: str, description: str) -> str:
    """Which half of the stack the job actually lives in.

    The title decides when it says anything at all. Only a silent title falls
    back to the body, and a body naming both sides is a fullstack role: the
    profile explicitly allows those when the backend stays bounded.
    """
    from_title = _role_from_title(title)
    if from_title:
        return from_title

    frontend = _matches_any(description, ("frontend", "front end", "react"))
    backend = _matches_any(description, ("backend", "back end", "api rest", "microservicios"))
    if frontend and backend:
        return "fullstack"
    if frontend:
        return "frontend"
    if backend:
        return "backend"
    return "unknown"


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
    """Up to two years is the profile's target range; three is already a stretch."""
    if required_years is None or required_years <= 1:
        return MAX_SCORES["experience"]
    if required_years <= 2:
        return 8
    if required_years <= 3:
        return 2
    return 0


def _location_score(location: str, is_remote: bool) -> int:
    normalized = normalize_text(location)
    foreign = _matches_any(normalized, _FOREIGN_LOCATION_MARKERS)
    if foreign:
        # A remote offer abroad is only viable when the employer can hire in
        # Argentina, which the row cannot prove, so it ranks below local remote.
        return 5 if is_remote else 0
    if is_remote:
        return MAX_SCORES["location"]
    if not normalized:
        return 4
    if _matches_any(normalized, _AMBA_MARKERS):
        return 9
    if _matches_any(normalized, _AR_LOCATION_MARKERS):
        return 7
    return 3


def _english(description: str) -> tuple[str | None, bool]:
    text = _deaccent(description)
    level = None
    for name, markers in _ENGLISH_LEVELS:
        if _matches_any(text, markers):
            level = name
            break
    required = level == "advanced" or _matches_any(text, _ENGLISH_REQUIRED_MARKERS)
    return level, required


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
    role_focus = _role_focus(title, description)
    required_years = _required_years(job.description)
    english_level, english_required = _english(job.description)
    flags = _deaccent(job.description)

    parts = {
        "role": _ROLE_SCORES[role_focus],
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
        role_focus=role_focus,
        english_level=english_level,
        english_required=english_required,
        unpaid=_matches_any(flags, _UNPAID_MARKERS),
        degree_required=_matches_any(flags, _DEGREE_MARKERS),
    )


def should_notify(total: int) -> bool:
    """Only strong matches interrupt the user; the rest are stored silently."""
    return total >= NOTIFY_THRESHOLD
