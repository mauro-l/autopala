"""Technology-role gate for scraped job titles.

The board is frontend-first, but a firehose source (WeRemoto accepts no search
term) happily returns maths tutors, cold callers and dental virtual assistants.
Those offers are dropped here, at ingestion: before enrichment spends a capped
LinkedIn detail fetch on them and before they ever reach the database.

The decision is deterministic and title-only. It classifies the profession noun
of the title in three buckets:

1. a technology noun is present -> keep. This allowlist also outranks the noise
   list, so "Sales Engineer" survives;
2. only noise nouns are present -> drop;
3. no known noun at all -> keep, on purpose.

Bucket 3 is the whole design. An unknown title is far more likely to be a rare
but legitimate role than junk, and losing a real offer costs more than storing a
noisy one. Only a title that names a profession we explicitly know to be
off-topic is dropped.
"""

from __future__ import annotations

import re

from app.normalize import normalize_text

# Nouns that name a software or design professional. Kept deliberately narrow:
# adding "support", "administrator", "data", "web", "ui" or "security" here
# would veto the noise list for junk titles like "Bilingual Administrative
# Support Virtual Assistant".
_TECH_ROLE_NOUNS = (
    "developer",
    "desarrollador",
    "desarrolladora",
    "engineer",
    "engineering",
    "ingenier",
    "ingenieria",
    "designer",
    "disenador",
    "disenadora",
    "technologist",
    "tecnologo",
    "tecnologa",
    "programmer",
    "programador",
    "programadora",
    "architect",
    "arquitect",
    "analyst",
    "analista",
    "scientist",
    "cientific",
    "devops",
    "sre",
    "qa",
    "tester",
)

# Nouns that name a profession this board does not serve. A title matching one
# of these is dropped unless a technology noun also matches.
_NON_TECH_ROLE_NOUNS = (
    "tutor",
    "teacher",
    "profesor",
    "profesora",
    "docente",
    "maestro",
    "maestra",
    "assistant",
    "asistente",
    "coordinator",
    "coordinador",
    "coordinadora",
    "representative",
    "representante",
    "recruiter",
    "reclutador",
    "reclutadora",
    "accountant",
    "contador",
    "contadora",
    "bookkeeper",
    "bookkeeping",
    "nurse",
    "enfermero",
    "enfermera",
    "dentist",
    "odontologo",
    "odontologa",
    "driver",
    "chofer",
    "cook",
    "cocinero",
    "cocinera",
    "chef",
    "receptionist",
    "recepcionista",
    "caller",
    "lawyer",
    "abogado",
    "abogada",
    "sales",
    "vendedor",
    "vendedora",
    "telemarketer",
)

# Spanish and English plural or gendered endings, so one stem also covers
# "desarrolladores", "disenadoras" and "testers". Matching stays inside word
# boundaries, so "tutor" never matches "tutorial".
_SUFFIX = r"(?:o|a|os|as|es|s)?"


def _compile(stems: tuple[str, ...]) -> re.Pattern[str]:
    """Build one word-boundary alternation, longest stem first.

    Longest-first stops the engine from trying a shorter stem that can never
    satisfy the trailing boundary, such as "engineer" inside "engineering".
    """
    ordered = "|".join(sorted(stems, key=len, reverse=True))
    return re.compile(r"\b(?:" + ordered + r")" + _SUFFIX + r"\b")


_TECH_ROLE_RE = _compile(_TECH_ROLE_NOUNS)
_NON_TECH_ROLE_RE = _compile(_NON_TECH_ROLE_NOUNS)


def is_technology_role(title: object) -> bool:
    """Whether a title names a technology role this board wants.

    An empty title returns ``True``: emptiness is dropped upstream with its own
    counter, and this gate must not claim a row it never judged.
    """
    text = normalize_text(title)
    if not text:
        return True
    if _TECH_ROLE_RE.search(text):
        return True
    return not _NON_TECH_ROLE_RE.search(text)
