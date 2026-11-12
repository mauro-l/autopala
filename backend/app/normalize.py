"""Text normalization and job identity helpers.

The fingerprint is the dedupe key: one offer appears on LinkedIn, Indeed and
Jooble with different URLs, so identity comes from company + title + location.
"""

from __future__ import annotations

import hashlib
import math
import unicodedata

# Legal suffixes that carry no identity value. Matched on trailing tokens only,
# so "SAS Institute" keeps its "sas".
_LEGAL_SUFFIXES = frozenset(
    {"sa", "sas", "srl", "inc", "llc", "ltd", "ltda", "gmbh", "corp", "cia", "co", "plc", "ag"}
)

# Trailing tokens that describe the work mode, not the role.
_REMOTE_MARKERS = frozenset({"remote", "remoto", "remota", "hybrid", "hibrido", "anywhere"})

_TRUE_STRINGS = frozenset({"true", "1", "yes", "y", "t"})


def normalize_text(value: object) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    if value is None:
        return ""
    text = str(value)
    if not text.strip():
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = "".join(ch if ch.isalnum() else " " for ch in without_marks.lower())
    return " ".join(cleaned.split())


def normalize_company(value: object) -> str:
    """Normalize a company name and drop trailing legal suffixes."""
    tokens = normalize_text(value).split()
    changed = True
    while changed and len(tokens) > 1:
        changed = False
        if tokens[-2:] == ["s", "a"]:
            del tokens[-2:]
            changed = True
        elif tokens[-1] in _LEGAL_SUFFIXES:
            tokens.pop()
            changed = True
    return " ".join(tokens)


def normalize_title(value: object) -> str:
    """Normalize a job title and drop trailing work-mode markers."""
    tokens = normalize_text(value).split()
    while len(tokens) > 1 and tokens[-1] in _REMOTE_MARKERS:
        tokens.pop()
    return " ".join(tokens)


def normalize_location(value: object) -> str:
    """Normalize a location string; missing locations become an empty string."""
    return normalize_text(value)


def _digest(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def make_fingerprint(company: object, title: object, location: object = "") -> str:
    """Stable dedupe key. Location separates genuinely distinct postings."""
    return _digest(normalize_company(company), normalize_title(title), normalize_location(location))


def make_loose_key(company: object, title: object) -> str:
    """Location-free key, used to surface probable duplicates for review.

    It never merges rows on its own: two postings at the same company with the
    same title but different cities are usually different offers.
    """
    return _digest(normalize_company(company), normalize_title(title))


def parse_is_remote(value: object) -> bool:
    """Cast a remote flag that arrives as a real bool, a string, or NaN."""
    if value is None or isinstance(value, bool):
        return bool(value)
    if isinstance(value, float) and math.isnan(value):
        return False
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_STRINGS
    if isinstance(value, (int, float)):
        return bool(value)
    return False
