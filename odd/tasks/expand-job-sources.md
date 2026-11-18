Objective: Expand automated collection to compatible public sources among the 15 job portals named by the user, while retaining the $0 and traditional-employment scope.
Branch: `feature/expand-job-sources` | Delivery: `ask-on-risk` | Test runner: `python3 -m pytest backend/tests`.

## Specs

### S1 — Requested portal scope
User request: “ok con el link que te pase, vamos a ampliar la busqueda a mas alla de linkedin, que busque en esas 15 paginas ofertas laborales”

The referenced list is LinkedIn, Jooble, WeRemoto, Workana, Freelancer, Get on Board, Domestika, Laborum, Remote OK, Torre, Remoters, CompuTrabajo, SoyFreelancer, Talent.com, and Indeed. Assess each name separately; LinkedIn and Indeed already exist in production and must not be counted as new integrations.

Acceptance: The final source matrix reports each listed portal as integrated, already covered, excluded, blocked, or pending, with evidence and no unsupported claim of full 15-source coverage.

### S2 — Traditional employment only
User decision: “Solo empleos tradicionales”

Do not ingest freelance projects or client requests as employment vacancies. Exclude project-only marketplaces (including Workana and Freelancer). Include SoyFreelancer only if a public listing can be restricted to its actual employment type without login; otherwise mark it excluded or blocked.

Acceptance: Regression cases prove freelance/project-only rows do not enter the employment collection.

### S3 — Public access and $0 constraints
User decision: “Páginas públicas permitidas (Recommended)” — “Usar endpoints públicos o páginas accesibles sin cuenta, CAPTCHA, proxies ni evasión de bloqueos, con frecuencia conservadora; dejar fuera y documentar los portales sin una vía compatible.”

Use free public APIs or public pages only where access is permitted. Preserve the existing $0 budget, do not use paid APIs, proxies, authenticated LinkedIn sessions, CAPTCHA solving, or bypasses. Respect applicable source restrictions and stop using a source when it blocks automated requests. A free API that requires a user key may be implemented as an optional, disabled-until-configured source; do not obtain, infer, or insert the user's key.

Acceptance: Every new source has a documented access mode, rate policy, configuration needs, and exclusion reason where not compatible.

### S4 — Existing collection contract
Adapt eligible results into the current normalized job model and preserve current pipeline ordering, fingerprint deduplication, raw source payloads, all source URLs, scoring, run telemetry, and stale/titleless handling. Do not change the database schema or scoring model for this feature.

Acceptance: New-source fixtures pass through the existing collection path; tests prove no changes to existing LinkedIn, Indeed, and ATS behavior.

### S5 — Deterministic and safe acquisition
Use fixed source endpoints and bounded pagination with conservative pacing; inject transports for tests. Handle malformed responses, HTTP failures, timeouts, and partial source failures without dropping successful results from other plans or marking a failed source run as successful.

Acceptance: Keep the new coverage to four focused tests: (1) daily source planning, including optional Jooble and excluding project-only sources; (2) Remote OK/Jooble mapping and URL validation; (3) Jooble key isolation and absent-key behavior; (4) cross-source deduplication and partial-source failure survival. Do not add separate tests for every response variant when these behaviors are already covered.

### S6 — Jooble key confinement
`JOOBLE_API_KEY` is optional and user-provisioned. Never put it in `SourcePlan.params`, `scrape_runs.params`, job payloads, exception text, or logs. The source stays unscheduled when the key is absent.

Acceptance: Tests prove absent-key behavior, no secret in run-plan params or config representation, and sanitized transport errors.

## Tasks
- T1 — Verify and record source-by-source access, relevance, policy constraints, and source shape (S1-S3); route: inline; status: completed; evidence: L5-L6; commit: N/A.
- T2 — Implement/test compatible public API adapters and optional Jooble configuration (S3-S6); route: inline; status: pending (paused by user; pytest unavailable; test scope updated in L10); Risk: item 2 (handles an optional third-party API key); commit: pending.
- T3 — Implement/test only the public-page adapters approved by T1 (S2-S5); route: inline; status: completed; evidence: L13; commit: pending (this work unit).
- T4 — Verify the complete source matrix, pipeline non-regression, and documentation; route: inline; status: pending; commit: pending.

## Log
### L1 — Original request
“ok con el link que te pase, vamos a ampliar la busqueda a mas alla de linkedin, que busque en esas 15 paginas ofertas laborales”

### L2 — Access decision
The user selected “Páginas públicas permitidas (Recommended)”: public endpoints or pages only, without accounts, CAPTCHA, proxies, or block evasion; poll conservatively and document sources without a compatible route.

### L3 — Listing-type decision
The user selected “Solo empleos tradicionales”; freelance projects are not employment vacancies and must not be mixed into the current list/scoring.

### L4 — Initial code and source evidence
Production source configuration is distributed across `backend/app/worker.py` plans, endpoint constants, and adapter branches; there is no single source registry. Official documentation confirms Jooble's free but country-keyed API, Get on Board's public API and freshness restriction, and Remote OK's public API attribution terms. Talent.com publisher access is partner-oriented; Torre's search/API routes are restricted. The source-by-source status is recorded in L5-L6. Prior measurements in `AGENTS.md` remain authoritative for LinkedIn, Indeed, Jooble's JobSpy behavior, Remote OK, Google/SERP sources, and direct ATS APIs.

### L5 — Initial source eligibility matrix
- LinkedIn, Indeed: already collected; preserve their current adapters and tests.
- Jooble: official REST search has a free plan capped at 100 requests/day per country and requires a country-specific API key; response includes a snippet and a Jooble listing URL. Optional only after the user provisions a key. Source: `https://help.jooble.org/en/support/solutions/articles/60001448238-rest-api-documentation`.
- Get on Board: official public search API is no-auth, but its terms require each job to link to its public page, show data unchanged, refresh it at least every 24 hours, and prohibit resale/model training. The app's worker is manually started and has no per-source freshness expiry, so this source is blocked under current behavior rather than enabled with stale cached listings. Source: `https://getonbrd.com/doc/openapi.yaml`.
- Remote OK: official JSON API is public; its response requires a followed link to the Remote OK listing and explicit source attribution. Its robots file sets a one-second crawl delay and disallows specific query/AJAX paths; use only the permitted API route. Sources: `https://remoteok.com/api`, `https://remoteok.com/robots.txt`.
- WeRemoto: public listing pages; robots allows `/` except `/diagnostics`. Candidate for page adapter with conservative requests. Source: `https://www.weremoto.com/robots.txt`.
- Remoters: public `/jobs` and category pages; robots disallows admin, feed, and search-query paths. Candidate only on allowed listing paths. Source: `https://remoters.net/robots.txt`.
- CompuTrabajo Argentina: public listing pages exist; robots disallows internal AJAX/services and selected query filters. Candidate only on allowed public listing routes, with unsupported filtering done locally. Source: `https://www.computrabajo.com.ar/robots.txt`.
- Laborum: public listing pages exist; robots disallows selected faceted/query variants. Candidate is Chile-focused, so Argentina eligibility and remote location must be verified locally. Source: `https://www.laborum.cl/robots.txt`.
- Domestika: job-list pages are publicly indexable, but robots disallows `/api/*` and `/jobs/offer`; only allowed listing-page fields are candidates, not detail fetching. Source: `https://www.domestika.org/robots.txt`.
- SoyFreelancer: public list pages are available, but robots disallows project detail pages. Include only if the public listing can be restricted to `Empleo` (not `Freelance`) without accessing blocked detail paths. Source: `https://www.soyfreelancer.com/robots.txt`.
- Torre: robots allows only unparameterized `/search/jobs` and disallows query-bearing search paths plus `/api/`; its API access page is request-based. No compliant search adapter is established. Sources: `https://torre.ai/robots.txt`, `https://torre.ai/request-api-professionals`.
- Talent.com: publisher API/feed is partner-oriented; robots disallows `/search-jobs/*` and its search API path. No compatible free search route is established. Sources: `https://employers.talent.com/publishers`, `https://www.talent.com/robots.txt`.
- Workana and Freelancer: excluded by S2 because their listed opportunities are project/freelance work, not traditional vacancies. Do not crawl their project listings or APIs.

The matrix is an access/robots assessment, not legal advice or a claim that source terms grant redistribution rights. Keep public-page integrations narrow, attributed where required, and disabled/excluded if a source blocks them or its terms disallow this use.

### L6 — Get on Board freshness restriction
The official OpenAPI terms require the public listings to be refreshed at least every 24 hours. The local collector is explicitly user-started and can remain off while the PWA continues to show stored rows; there is no per-source expiry or freshness filter. Do not enable this source until a compliant expiry/refresh behavior is designed and authorized.

### L7 — Verification environment
The configured runner is `python3 -m pytest backend/tests`, but the system Python has no `pytest` or `pip`, and no project virtualenv was found. The user explicitly stated that neither pytest nor pip will be installed. Do not install packages or fetch dependencies. Syntax compilation and deterministic inline smoke assertions are available; pytest evidence remains pending.

### L8 — T2 implementation and verification status
Added the Remote OK public JSON adapter and the optional Jooble REST adapter. Remote OK runs in the daily source slot with the source URL and attribution retained. Jooble runs only when `JOOBLE_API_KEY` is configured, schedules three Argentina keyword searches, keeps the key out of plan/run/job data and exception text, and does not mislabel Jooble's `updated` timestamp as a publication date. The current untrimmed test additions cover success, empty/error responses, partial-source survival, normalization/deduplication, malformed URLs, and key confinement; on resumption, reduce them to the four checks selected in L10.

Verification observed: Python syntax compilation, `git diff --check`, manual no-network API/model/dedupe/key-isolation smoke checks, and independent static probes passed. The independent verifier confirmed the regression cases are present. `pytest` execution remains unavailable because the system Python has no pytest or pip; the user has prohibited installing them, so T2 remains paused at the user's request.

### L9 — Explicit pause
The user declined installing pytest into an isolated temporary virtualenv and selected “No, pausar.” Do not continue T2, start T3, install dependencies, or claim completion until the user explicitly resumes and agrees to a no-install verification path.

### L10 — Test scope correction
The user said “demasiados test para esto..” and selected “Sí, recortar a 4 pruebas (Recommended).” On resumption, reduce the new test set to the four S5 acceptance cases; retain broader existing backend tests unchanged. This decision does not authorize resuming implementation or setting up pytest.

### L11 — No package installation
The user stated: “te aviso desde ya que no se va instalar pytest ni pip”. Do not install pytest, pip, or other test dependencies. The feature remains paused; on resumption, use only already-available verification tools and report clearly that these checks do not constitute a pytest run.

### L12 — Collection window extended to 19:00
User request, verbatim: "ok, vamos a continuar con la implementacion T3. para eso antes debemos expandir el horario de busqueda. que la ultima busqueda lo haga a las 19.00 y ahi termina en vez de las 16.00"

`COLLECTION_WINDOW` moved from `(9, 16)` to `(9, 20)`, so the last slot starts at 19:00 and collection stops at 20:00; the window had eleven slots instead of seven. `LINKEDIN_TERMS` grew from 9 to 11 entries (`desarrollador typescript`, `react native developer`) because `test_the_terms_rotate_every_hour_and_never_leave_the_list` requires one distinct term per slot in the window; the list still does not divide 24. `INDEED_HOURS`, `ATS_HOUR`, `REMOTEOK_HOUR` and `JOOBLE_HOUR` are unchanged. Observed with the real clock helpers: 19:00 in-window with a LinkedIn plan, 20:00 out of window with no plans, 11 distinct terms across the day. `AGENTS.md` records the new window.

### L13 — T3 public-page adapters
Implemented `backend/app/pages.py` with two stdlib `HTMLParser` adapters and `from_web_page_job` in `backend/app/models.py`; `due_plans` schedules `weremoto` at 10 and three `computrabajo:<term>` plans at 11, and `build_scraper` routes both kinds.

Live probe, one request per site: WeRemoto 200 with 30 server-rendered cards (title, company, modality/country badge, `18 nov`, contract type and the full description inside `details`); CompuTrabajo 200 with 20 cards per term and an absolute offer URL. The other T1 page candidates are blocked or excluded, not deferred: Remoters answered HTTP 520 twice, Laborum is a JavaScript-only shell, Domestika returned HTTP 403, and SoyFreelancer has no employment listing (`/empleos` is 404; only `/trabajos-freelance` projects exist and its detail pages are disallowed by robots), so it stays excluded under S2. CompuTrabajo's robots allows the `/trabajo-de-…` listing and disallows only `Ajax`, `_services` and the faceted query variants, which the adapter never calls; WeRemoto allows `/` except `/diagnostics`.

Verification observed: `backend/.venv/bin/python -m pytest backend/tests` reports **304 passed**, including the four new `backend/tests/test_pages.py` cases (planning, WeRemoto card mapping, CompuTrabajo card mapping, dropped invalid cards plus non-200 failures). No package was installed: that pre-existing virtualenv already carried pytest 9.2.0, so L11's no-install rule is respected. Both parsers were additionally run against the saved live pages: 30/30 WeRemoto rows with title, company, date and description; 20/20 CompuTrabajo rows with title, company, location and posted date. One real CompuTrabajo card needed the plain-text employer fallback, and "Hace 22 horas" plus "Hace 6 días" both map to the stored `date`.
