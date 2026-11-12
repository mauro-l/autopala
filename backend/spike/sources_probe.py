"""Phase 1 spike: measure what each candidate source actually returns for Argentina.

No database, no scheduler, no scoring. Pure measurement: how many rows, how many
carry a description, whether the URL is a canonical (ATS) link, whether the offer
is really Argentina/LATAM/remote, how fast it was, and what broke.

Raw output is persisted to backend/fixtures/ so later phases can build tests and
re-tune scoring without spending another scrape.

Usage:
    python spike/sources_probe.py linkedin indeed jooble
    python spike/sources_probe.py all
"""

from __future__ import annotations

import json
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from jobdrop import scrape_jobs

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

# One query per source, deliberately conservative: results_wanted is small and
# only a single page is requested, so we measure quality without triggering 429.
SOURCES: dict[str, dict] = {
    "linkedin": {
        "site_name": "linkedin",
        "search_term": "react developer",
        "location": "Argentina",
        "results_wanted": 20,
        "hours_old": 168,
    },
    "indeed": {
        "site_name": "indeed",
        "search_term": "react developer",
        "location": "Buenos Aires",
        "country_indeed": "Argentina",
        "results_wanted": 20,
        "hours_old": 168,
    },
    "jooble": {
        "site_name": "jooble",
        "search_term": "react developer",
        "location": "Argentina",
        "results_wanted": 20,
        "hours_old": 168,
    },
    "remoteok": {
        "site_name": "remoteok",
        "search_term": "react",
        "results_wanted": 20,
    },
    "weworkremotely": {
        "site_name": "weworkremotely",
        "search_term": "react",
        "results_wanted": 20,
    },
    "wellfound": {
        "site_name": "wellfound",
        "search_term": "react",
        "location": "Argentina",
        "results_wanted": 20,
    },
    "google": {
        "site_name": "google",
        "search_term": "react developer",
        "google_search_term": "react developer jobs Argentina",
        "location": "Argentina",
        "results_wanted": 20,
        "hours_old": 168,
    },
    "greenhouse": {
        "site_name": "greenhouse",
        "search_term": "react developer",
        "location": "Argentina",
        "results_wanted": 20,
    },
    "ashby": {
        "site_name": "ashby",
        "search_term": "react developer",
        "location": "Argentina",
        "results_wanted": 20,
    },
    "lever": {
        "site_name": "lever",
        "search_term": "react developer",
        "location": "Argentina",
        "results_wanted": 20,
    },
}

# Heuristic only, to answer "is this actually relevant to an AR-based candidate".
AR_RE = re.compile(
    r"argentin|buenos aires|\bcaba\b|latam|latin america|remoto|h[ií]brido|"
    r"\bremote\b|anywhere|worldwide",
    re.IGNORECASE,
)
CANONICAL_RE = re.compile(
    r"greenhouse\.io|ashbyhq\.com|lever\.co|workday|myworkdayjobs|smartrecruiters|teamtailor",
    re.IGNORECASE,
)


def to_records(result) -> list[dict]:
    """scrape_jobs returns a pandas DataFrame; tolerate an empty or object result."""
    if result is None:
        return []
    if hasattr(result, "to_dict"):
        try:
            return result.to_dict("records")
        except Exception:
            return []
    if isinstance(result, list):
        return [r if isinstance(r, dict) else vars(r) for r in result]
    return []


def nonempty(value) -> bool:
    if value is None:
        return False
    if isinstance(value, float) and value != value:  # NaN
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def summarize(name: str, records: list[dict], elapsed: float) -> dict:
    total = len(records)
    desc = sum(1 for r in records if nonempty(r.get("description")))
    direct = sum(1 for r in records if nonempty(r.get("job_url_direct")))
    canonical = sum(
        1
        for r in records
        if CANONICAL_RE.search(str(r.get("job_url_direct") or r.get("job_url") or ""))
    )
    salary = sum(1 for r in records if nonempty(r.get("min_amount")))
    ar_hits = 0
    for r in records:
        blob = " ".join(
            str(r.get(k) or "")
            for k in ("title", "location", "description")
        )
        if AR_RE.search(blob):
            ar_hits += 1

    return {
        "source": name,
        "rows": total,
        "elapsed_s": round(elapsed, 1),
        "with_description": desc,
        "with_direct_url": direct,
        "canonical_ats_url": canonical,
        "with_salary": salary,
        "ar_or_remote_hits": ar_hits,
        "columns": sorted(records[0].keys()) if records else [],
        "locations": [str(r.get("location")) for r in records[:8]],
        "titles": [str(r.get("title"))[:70] for r in records[:5]],
    }


def run(name: str) -> dict:
    params = SOURCES[name]
    started = time.monotonic()
    error = None
    records: list[dict] = []
    try:
        result = scrape_jobs(**params)
        records = to_records(result)
    except Exception:
        error = traceback.format_exc(limit=3)
    elapsed = time.monotonic() - started

    report = summarize(name, records, elapsed)
    report["error"] = error
    report["params"] = {k: v for k, v in params.items() if k != "site_name"}
    return {"report": report, "records": records}


def main(argv: list[str]) -> int:
    if not argv or argv[0] == "all":
        names = list(SOURCES)
    else:
        unknown = [a for a in argv if a not in SOURCES]
        if unknown:
            print(f"unknown sources: {unknown}", file=sys.stderr)
            print(f"available: {list(SOURCES)}", file=sys.stderr)
            return 2
        names = argv

    FIXTURES.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {"generated_at": stamp, "sources": {}}

    for name in names:
        print(f"\n>>> {name}", flush=True)
        outcome = run(name)
        payload["sources"][name] = outcome
        r = outcome["report"]
        if r["error"]:
            last = r["error"].strip().splitlines()[-1]
            print(f"    ERROR after {r['elapsed_s']}s: {last}", flush=True)
            continue
        print(
            f"    {r['rows']:>3} rows in {r['elapsed_s']:>6}s | "
            f"desc {r['with_description']:>3} | direct {r['with_direct_url']:>3} | "
            f"ats {r['canonical_ats_url']:>3} | salary {r['with_salary']:>3} | "
            f"ar/remote {r['ar_or_remote_hits']:>3}",
            flush=True,
        )
        for loc in r["locations"][:5]:
            print(f"      · {loc[:80]}", flush=True)

    out = FIXTURES / f"spike_{stamp}.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    print(f"\nraw payload -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
