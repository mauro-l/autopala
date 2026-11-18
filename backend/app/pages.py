"""Adapters for the public HTML job-listing pages approved in T1.

Both boards render their listing server-side, so the standard library is enough:
``html.parser`` reads the cards, no browser, no JavaScript and no paid API. The
parsers are pure functions over the page text and the transport is injected, so
the tests never touch the network.

Policy: one request per plan. The scheduler picks the hour, and a page answering
anything other than 200 fails only its own run.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urljoin

WEREMOTO_URL = "https://www.weremoto.com/"
COMPUTRABAJO_TERMS = ("desarrollador-frontend", "react", "desarrollador-junior")
COMPUTRABAJO_URL = "https://ar.computrabajo.com/trabajo-de/{term}"

_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

# Argentina has kept a fixed -03 offset since 2009, and this offset only feeds
# the year and "hace N días" arithmetic, never a stored timestamp.
_AR = timezone(timedelta(hours=-3))

_MONTHS = {
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "sep": 9,
    "set": 9,
    "oct": 10,
    "nov": 11,
    "dic": 12,
}
_MODALITIES = {
    "remoto",
    "remote",
    "híbrido",
    "hibrido",
    "presencial",
    "presencial y remoto",
    "remoto y presencial",
    "100% remoto",
}
_JOB_TYPES = {
    "full time",
    "part time",
    "medio tiempo",
    "tiempo completo",
    "freelance",
    "contrato",
    "por hora",
    "temporal",
    "pasantía",
    "pasantia",
}
_DAY_MONTH = re.compile(
    r"\b(\d{1,2})\s+(ene|feb|mar|abr|may|jun|jul|ago|sep|set|oct|nov|dic)\b",
    re.IGNORECASE,
)
_DAYS_AGO = re.compile(r"hace\s+(m[aá]s\s+de\s+)?(\d+)\s+d[ií]as?", re.IGNORECASE)
_WEREMOTO_JOB_PATH = "/job-posts/"
_COMPUTRABAJO_JOB_PATH = "/ofertas-de-trabajo/oferta-de-trabajo-"

Transport = Callable[..., tuple[int, bytes]]


def _squash(parts: Iterable[str]) -> str:
    """Collapse a buffered run of text nodes into one single-spaced string."""
    return " ".join("".join(parts).split())


def _decode(raw: bytes | str) -> str:
    return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw


def _local_today() -> date:
    return datetime.now(_AR).date()


def _day_month_date(text: str, today: date) -> date | None:
    """Read a "18 nov" style date, assuming the current year unless that is future."""
    match = _DAY_MONTH.search(text)
    if match is None:
        return None
    month = _MONTHS.get(match.group(2).lower())
    if month is None:
        return None
    try:
        found = date(today.year, month, int(match.group(1)))
    except ValueError:
        return None
    if found > today + timedelta(days=1):
        try:
            found = date(today.year - 1, month, int(match.group(1)))
        except ValueError:
            return None
    return found


def _days_ago_date(text: str, today: date) -> date | None:
    """Read "Hace 6 días" / "Hoy" / "Ayer"; "más de N" keeps N as a floor."""
    match = _DAYS_AGO.search(text)
    if match is not None:
        days = int(match.group(2)) + (1 if match.group(1) else 0)
        return today - timedelta(days=days)
    lowered = text.strip().lower()
    if lowered == "hoy" or "hora" in lowered or "minuto" in lowered:
        # The model stores a date, so anything inside the last day is today.
        return today
    if lowered == "ayer":
        return today - timedelta(days=1)
    return None


def _word(value: str) -> str:
    """Letters only, so a badge with an emoji ("Remote 📍") still reads as text."""
    return " ".join(re.sub(r"[^a-záéíóúüñ ]+", " ", value.lower()).split())


def _modality(value: str) -> str | None:
    """Canonical work modality of a badge, or ``None`` when it is not one."""
    word = _word(value)
    if word in _MODALITIES:
        if "hibrid" in word:
            return "Híbrido"
        return "Presencial" if word == "presencial" else "Remoto"
    if word.startswith("remot") and len(word) <= 16:
        return "Remoto"
    return None


def _place(value: str) -> str:
    """A country or city badge ("🇨🇱 Chile"), never an emoji or a digit run."""
    word = _word(value)
    if not word or len(word) > 40 or any(ch.isdigit() for ch in word):
        return ""
    return word.title()


def _classes(attributes: Mapping[str, str | None]) -> set[str]:
    return set((attributes.get("class") or "").split())


class _WeRemotoParser(HTMLParser):
    """Read WeRemoto's server-rendered cards: title, company, badges, description."""

    _IMPLICIT_CLOSERS = {"div", "article", "section", "ul", "details", "h2", "h3", "p"}

    def __init__(self, *, today: date) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, Any]] = []
        self._today = today
        self._depth = 0
        self._card: dict[str, Any] | None = None
        self._h3: list[str] = []
        self._company: list[str] = []
        self._description: list[str] = []
        self._spans: list[list[str]] = []
        self._badges: list[str] = []
        self._in_h3 = False
        self._in_company = False
        self._in_details = False
        self._in_summary = False

    def _reset_card(self) -> None:
        self._card = {"url": None, "title": "", "company": ""}
        self._h3 = []
        self._company = []
        self._description = []
        self._spans = []
        self._badges = []
        self._in_h3 = self._in_company = self._in_details = self._in_summary = False

    def _finish_h3(self) -> None:
        if self._card is not None and not self._card["title"]:
            self._card["title"] = _squash(self._h3)
        self._in_h3 = False

    def _finish_company(self) -> None:
        if self._card is not None and not self._card["company"]:
            self._card["company"] = _squash(self._company)
        self._in_company = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "article":
            self._depth += 1
            if self._depth == 1:
                self._reset_card()
            return
        if self._card is None:
            return
        if tag in self._IMPLICIT_CLOSERS:
            if self._in_company:
                self._finish_company()
            if self._in_h3:
                self._finish_h3()
        if tag == "h3":
            self._in_h3 = True
            self._h3 = []
        elif tag == "a":
            href = attributes.get("href") or ""
            if self._in_h3 and not self._card["url"] and _WEREMOTO_JOB_PATH in href:
                self._card["url"] = urljoin(WEREMOTO_URL, href.split("#")[0])
        elif tag == "p":
            if self._card["url"] and not self._card["company"]:
                self._in_company = True
                self._company = []
        elif tag == "span":
            self._spans.append([])
        elif tag == "details":
            self._in_details = True
        elif tag == "summary":
            self._in_summary = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "article":
            if self._depth == 1:
                self._finish_card()
            self._depth = max(0, self._depth - 1)
            return
        if self._card is None:
            return
        if tag == "h3":
            self._finish_h3()
        elif tag == "p":
            self._finish_company()
        elif tag == "span":
            if self._spans:
                text = _squash(self._spans.pop())
                if text:
                    self._badges.append(text)
        elif tag == "summary":
            self._in_summary = False
        elif tag == "details":
            self._in_details = False

    def handle_data(self, data: str) -> None:
        if self._card is None:
            return
        if self._in_h3:
            self._h3.append(data)
        if self._in_company:
            self._company.append(data)
        if self._spans:
            self._spans[-1].append(data)
        if self._in_details and not self._in_summary:
            self._description.append(data)

    def _finish_card(self) -> None:
        card = self._card
        self._card = None
        if card is None:
            return
        title = str(card["title"]).strip()
        url = card["url"]
        if not title or not url:
            return
        location = ""
        posted_at: date | None = None
        job_type: str | None = None
        # The card lays the badge out as [modality or country][date][type][category],
        # so the badge right before the date is the only place the location lives.
        date_index: int | None = None
        for index, badge in enumerate(self._badges):
            if _DAY_MONTH.search(badge) or _DAYS_AGO.search(badge):
                posted_at = _day_month_date(badge, self._today) or _days_ago_date(
                    badge, self._today
                )
                date_index = index
                break
        if date_index is not None and date_index >= 1:
            label = self._badges[date_index - 1]
            location = _modality(label) or _place(label)
        for badge in self._badges:
            if _word(badge) in _JOB_TYPES:
                job_type = badge
                break
        self.rows.append(
            {
                "source": "weremoto",
                "title": title,
                "company": card["company"],
                "location": location,
                "is_remote": "presencial" not in location.lower(),
                "description": _squash(self._description),
                "posted_at": posted_at,
                "job_type": job_type,
                "url": url,
                "source_job_id": str(url).rstrip("/").rsplit("/", 1)[-1] or None,
            }
        )


class _CompuTrabajoParser(HTMLParser):
    """Read CompuTrabajo's server-rendered offer cards.

    The company link, the location paragraph and the age line each carry a
    distinct hook, so neither the visual classes nor the surrounding layout are
    guessed: a missing hook leaves the field empty instead of inventing a value.
    """

    def __init__(self, *, today: date) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, Any]] = []
        self._today = today
        self._stack: list[str] = []
        self._card: dict[str, Any] | None = None
        self._title: list[str] = []
        self._company: list[str] = []
        self._date: list[str] = []
        self._spans: list[tuple[set[str], list[str]]] = []
        self._in_title = False
        self._in_company = False
        self._in_date = False

    def _reset_card(self, data_id: str | None) -> None:
        self._card = {"id": data_id, "url": None, "title": "", "company": ""}
        self._title = []
        self._company = []
        self._date = []
        self._in_title = self._in_company = self._in_date = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        self._stack.append(tag)
        if tag == "article":
            if "box_offer" in _classes(attributes):
                self._reset_card(attributes.get("data-id"))
            return
        if self._card is None:
            return
        if tag == "a":
            href = attributes.get("href") or ""
            if not self._card["url"] and _COMPUTRABAJO_JOB_PATH in href:
                self._card["url"] = urljoin(
                    COMPUTRABAJO_URL.format(term=""), href.split("#")[0]
                )
                self._in_title = True
                self._title = []
            elif "offer-grid-article-company-url" in attributes:
                self._in_company = True
                self._company = []
        elif tag == "p" and "fc_aux" in _classes(attributes):
            self._in_date = True
            self._date = []
        elif tag == "p" and "dFlex" in _classes(attributes) and not self._card["company"]:
            # A confidential employer is plain text inside the same paragraph.
            self._in_company = True
            self._company = []
        elif tag == "span":
            self._spans.append((_classes(attributes), []))

    def handle_endtag(self, tag: str) -> None:
        if self._card is not None:
            if tag == "a":
                if self._in_title:
                    self._card["title"] = _squash(self._title)
                    self._in_title = False
                elif self._in_company:
                    self._card["company"] = _squash(self._company)
                    self._in_company = False
            elif tag == "p":
                self._in_date = False
                if self._in_company:
                    self._card["company"] = _squash(self._company)
                    self._in_company = False
            elif tag == "span" and self._spans:
                classes, buffer = self._spans.pop()
                text = _squash(buffer)
                parent = self._stack[-2] if len(self._stack) >= 2 else ""
                if text and parent == "p" and "mr10" in classes:
                    self._card.setdefault("location", text)
                elif _modality(text):
                    self._card["modality"] = _modality(text)
            elif tag == "article":
                self._finish_card()
        if self._stack:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        if self._card is None:
            return
        if self._in_title:
            self._title.append(data)
        if self._in_company:
            self._company.append(data)
        if self._in_date:
            self._date.append(data)
        if self._spans:
            self._spans[-1][1].append(data)

    def _finish_card(self) -> None:
        card = self._card
        self._card = None
        if card is None:
            return
        title = str(card.get("title") or "").strip()
        url = card.get("url")
        if not title or not url:
            return
        location = str(card.get("location") or "").strip()
        modality = str(card.get("modality") or "")
        self.rows.append(
            {
                "source": "computrabajo",
                "title": title,
                "company": card.get("company", ""),
                "location": location,
                "is_remote": "remoto" in modality.lower(),
                "description": "",
                "posted_at": _days_ago_date(_squash(self._date), self._today),
                "job_type": None,
                "url": url,
                "source_job_id": card.get("id"),
            }
        )


def parse_weremoto(page: str, *, today: date) -> list[dict[str, Any]]:
    parser = _WeRemotoParser(today=today)
    parser.feed(page)
    parser.close()
    return parser.rows


def parse_computrabajo(page: str, *, today: date) -> list[dict[str, Any]]:
    parser = _CompuTrabajoParser(today=today)
    parser.feed(page)
    parser.close()
    return parser.rows


def fetch_weremoto(
    *, transport: Transport, today: date | None = None
) -> list[Mapping[str, Any]]:
    """One request for WeRemoto's listing; a non-200 fails only this run."""
    status, raw = transport("GET", WEREMOTO_URL, dict(_HEADERS), None, timeout=30.0)
    if status != 200:
        raise RuntimeError(f"weremoto returned http {status}")
    return parse_weremoto(_decode(raw), today=today or _local_today())


def fetch_computrabajo(
    term: str, *, transport: Transport, today: date | None = None
) -> list[Mapping[str, Any]]:
    """One request per search term, never a paged crawl of the board."""
    url = COMPUTRABAJO_URL.format(term=term)
    status, raw = transport("GET", url, dict(_HEADERS), None, timeout=30.0)
    if status != 200:
        raise RuntimeError(f"computrabajo returned http {status}")
    return parse_computrabajo(_decode(raw), today=today or _local_today())
