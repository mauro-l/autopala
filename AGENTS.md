# AGENTS.md

Repository: **pala-automatica**, a zero-budget automated job-search app. `backend/` holds the Python pipeline (spike, normalized model, Supabase schema and deterministic scoring are implemented and tested); there is no git repository yet and no frontend. Everything below is agreed scope and verified behavior, not a plan.

## Environment (verified)

- WSL2 Ubuntu 24.04, **not Windows**. Do not generate Windows Task Scheduler tasks or `scripts/*.bat`.
- Python 3.12.3, Node 24.22.1, npm 11.19.1. `uv` is **not installed** — use `python3 -m venv` + pip unless the user asks to install uv.
- The working tree is not a git repository yet.
- Hardware: Ryzen 5 5600, 16 GB RAM, RX 6700 XT (12 GB VRAM).

## Non-negotiable constraints

- **Budget is $0.** No paid APIs, proxies, VPS, hosted DB, or deploy services. If a feature needs one, find the free alternative or leave it out.
- **No local LLM. Ollama is explicitly discarded by the user.** 16 GB of RAM; running a local model alongside the browser makes the machine unusable. Never propose Ollama, llama.cpp, or any local inference as a fallback. The AI layer must degrade to a deterministic rules evaluator instead.
- Never use the user's LinkedIn session or credentials. JobSpy only, conservative rate limits, no proxy purchases.

## Agreed stack

- Scraping runs on the user's PC: **jobdrop** (a JobSpy fork, same `scrape_jobs` API) as a Python library + APScheduler worker, normalized and deduplicated before writing. Plain `python-jobspy` is the fallback if the fork stalls.
- Storage is **Supabase** (remote Postgres + PostgREST + Auth + RLS), not SQLite. The DB is the source of truth so the phone can read offers while the PC is off.
- Frontend: conventional client-server app — Astro + React islands, installable PWA, hosted statically on a free tier, reading from the remote DB.
- AI: any **OpenAI-compatible endpoint** selected by `base_url` + `model` + `api_key` (NVIDIA free tier, Groq, Cerebras, Gemini). Do **not** build a vendor-named abstraction like `NvidiaProvider` — the vendor must be configuration.
- Notifications: threshold-based. Only offers above the compatibility cutoff notify; the rest are stored silently. Android only.
- Tailscale is **not** required in this shape — it was only the ChatGPT plan's way to reach a service running on the PC. Reintroduce it only if something stays exposed from the PC.

## Collection layer (decided)

- Use jobdrop as a **Python library** (`from jobdrop import scrape_jobs`), never as an MCP server. MCP suits interactive agents; this is a scheduled pipeline where an agent adds nondeterminism and cost for nothing.
- **LinkedIn is the priority source**; the rest complement it rather than replace it. Verified working complement: `indeed` AR, the only source that reliably returns a full description **and** a canonical apply URL. Google SERP and the dorking-based `greenhouse`/`ashby`/`lever` sources are **unusable here** (measured: Google answers the headless browser with a `/sorry/` CAPTCHA, all four return 0 rows). ATS coverage therefore comes from the **public board APIs called directly** — `boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true`, `api.ashbyhq.com/posting-api/job-board/{slug}`, `api.lever.co/v0/postings/{slug}?mode=json`, Workday `.../wday/cxs/{tenant}/{site}/jobs` — driven by a curated slug list. No browser, no CAPTCHA, full JD and canonical URL guaranteed.
- LinkedIn guest cards carry **no description**. Never set `linkedin_fetch_description=True` (one request per offer → 429). Instead, handle LinkedIn rows by enqueuing a selective detail fetch with a pause, or copy the description from a richer source matched by fingerprint.
- Rejected on price or account risk: Apify actors (monthly free credits that run out and then hard-block), Bright Data, and any LinkedIn scraper that drives the user's authenticated browser session.
- Do **not** enable jobdrop's `camoufox` / `selenium-driverless` sources: they pull a headless browser and consume RAM this machine does not have.
- Deduplicate by fingerprint `sha256(normalized_company | normalized_title | normalized_location)`, **not** by URL — one offer appears on LinkedIn, Indeed and Jooble with different URLs. Keep every source URL in a child table.
- Persist each source's **raw payload** next to the normalized row, so scoring can be re-tuned without burning scrape requests.
- Scoring is deterministic first: skill dictionary with synonyms, plus seniority/language/years-of-experience regex. An LLM is an optional second pass that can never be required.

### Measured source viability (spike, 2026-11-11, target `Argentina`)

| source | rows | description | canonical URL | verdict |
| --- | --- | --- | --- | --- |
| `linkedin` | 20 | 0% | 0% | primary; 13/20 returned an **empty `location`** |
| `indeed` (`country_indeed="Argentina"`) | 30 | 100% | yes (EPAM, SmartRecruiters, Cognizant) | keep; **passing `hours_old` collapses it to 4 rows — do not pass it** |
| `remoteok` | 20 | 100% | no | works **only without `search_term`**; unfiltered firehose, filter locally |
| `weworkremotely` | 20 | — | no | same: breaks with `search_term` |
| `jooble` | 0 | — | — | **`location` breaks it** (`"Argentina"` → 0, empty → 14,013 global); low value |
| `google` | 0 | — | — | CAPTCHA |
| `greenhouse` / `ashby` / `lever` via jobdrop | 0 | — | — | CAPTCHA on the SERP step |
| `wellfound` | crash | — | — | Playwright missing `libatomic.so.1`; also login-walled |
| ATS board APIs called directly | 675 (anthropic/gh) | 100% | yes | **replacement**; no browser involved |

Other verified quirks: `is_remote` arrives as the string `"False"` (cast it). Because every jobdrop source that needs a browser is dead here, the `camoufox`/`selenium-driverless` paths must stay off.

## Domain facts (drive filters and scoring)

- Target user stack: React, JavaScript, TypeScript, Node.js, Express, MongoDB.
- Target level: trainee / junior, plus flexible roles asking 2–3 years (do not auto-discard on the years requirement).
- Location: Argentina (remote + hybrid AMBA). Primary sources: LinkedIn, Indeed (`country_indeed="Argentina"`).

## Scoring (implemented, `backend/app/scoring.py`)

Five weighted dimensions totalling 0–100. `score_job()` is pure and deterministic: the same row always scores the same, and a re-tune needs no new scrape because the raw payload is stored beside the row. An LLM pass may later add nuance but can never be required.

| dimension | budget | rule |
| --- | --- | --- |
| `stack` | 40 | skill weights sum to 50, so a posting naming every target technology saturates the budget instead of needing all six: react 12, typescript 10, javascript 10, node 8, express 5, mongodb 5 |
| `seniority` | 25 | junior 25, mid 18, unknown 12, senior 0 |
| `experience` | 15 | no mention or ≤1 year → 15, ≤3 → 12, ≤5 → 6, more → 0 |
| `location` | 10 | remote 10, empty 5, Argentina 8, anything else 0 |
| `recency` | 10 | no date 4, ≤2 days 10, ≤7 → 8, ≤14 → 5, ≤30 → 2, older 0 |

Thresholds: `NOTIFY_THRESHOLD = 85` and `SILENT_THRESHOLD = 70`. Only `total >= 85` notifies; everything else is stored silently. `ScoreBreakdown.to_payload()` is the exact shape written to the `score_breakdown` column, and `sum(parts) == total` always holds.

Rules that are easy to break, all pinned by the 51 tests in `backend/tests/test_scoring.py`:

- **The title outranks the description for seniority.** A junior title stays junior even when the body mentions working alongside senior engineers — that is exactly what made naive keyword counting unusable.
- **JS compounds are collapsed before matching** (`node.js`, `next.js`, `vue.js`, `express.js`, `three.js`, `nest.js`, `react.js` → their base token). `"node.js"` normalizes to `"node js"`, so left alone its trailing `js` would also count as a JavaScript mention.
- **Phrases match on letter/digit boundaries**, so `js` never matches inside another word.
- **Years parsing reads a lighter normalization** (`_deaccent`) than `normalize_text`, because `normalize_text` deletes hyphens and would destroy `"2-4 años"`. Ranges are read first; any number above 10 years is treated as company boilerplate rather than a requirement.
- **Foreign location markers are tested before Argentine ones**, so `"Córdoba, España"` scores 0 while `"B, AR"` scores 8.

**Measured ceiling, and the open problem it exposes:** over the 23 real spike rows only 3 carry a description. With no description the stack dimension caps at 30, so the best title-only total is **61** and the best with-description total is **65**. A non-remote Argentine match tops out at **98** (only a remote one reaches 100). Since LinkedIn is the priority source and its cards carry no description, no LinkedIn row can reach 85 on its own — addressed by the enrichment layer below, which is the only thing that can lift the stack dimension.

## Enrichment (implemented, `backend/app/enrich.py`, tested)

Resolves the gap above. Pipeline order is scrape → adapt → dedupe by fingerprint → **enrich** → score → upsert. Two passes, in this order:

1. **Copy** (`copy_descriptions`) — group described jobs by `loose_key`, and copy a description only when the group holds **exactly one distinct** description. Ambiguity skips. Keying on `loose_key` and not `fingerprint` is deliberate: LinkedIn rows arrive with an empty `location`, so their fingerprint can never match the Indeed row for the same offer.
2. **Fetch** (`select_fetch_candidates` + `enrich_jobs`) — LinkedIn guest detail page for rows still missing a JD, ordered by title-only score descending, ties broken by fingerprint, capped at `max_fetches=8`.

Rules that are easy to break, all pinned in `backend/tests/test_enrich.py`:

- **`fetcher=None` is the default and means no network.** Tier 2 never happens implicitly; the worker passes a real fetcher and tests pass a fake.
- **The guest endpoint itself**: `GET https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}` needs a realistic `User-Agent` and `Accept-Language`, plain `urllib.request`, no new dependency. Measured 200 and ~2.7k chars of JD on 8 requests with 2s pacing, zero throttling.
- **Pacing lives in the fetcher**, never in `enrich_jobs`: sleep only *between* calls, `delay_seconds=2.0`, `timeout=25.0`, with `sleep` and `transport` injectable so tests never touch the network.
- **`429` and `999` stop the batch immediately** (`throttled=True`) and return what was gathered; other failures count as `failed` without dropping the row.
- **`DEFAULT_MIN_TITLE_SCORE = 50`, not 30**: measured with the real scorer, every row clears 42 from seniority/experience/location/recency alone — "Cocinero" scores 42 — so a 30 cutoff filters nothing.
- **The job id** comes from digits in `source_job_id`, else `(\d{6,})` in the URL (handles slugged URLs). A 6-digit floor is what stops a year or a query fragment from being mistaken for an id.
- The description parser is a depth-aware `html.parser.HTMLParser` matching the `show-more-less-html__markup` class. A regex capture was rejected: nested `div`s truncated it and left a stray `>`.
- `enrich_jobs` returns `dataclasses.replace`d copies and never mutates its input.

**Enrichment is NOT monotonic — do not "fix" this.** Measured live: 'Desarrollador Senior React + TypeScript - Trabajo Remoto' scored **52 title-only and 43 enriched**, because the JD says "Más de 4 años de experiencia" and an empty description defaults to the generous 15 experience points, while a stated 4 years scores 6. The company-age phrase "llevamos 15 años" is correctly ignored by `_MAX_PLAUSIBLE_YEARS`. A JD raises `stack` — the only path to the threshold — but it can lower `experience`, so the title-only score is a priority heuristic, not a lower bound.

**Current state on the 23-row spike corpus:** tier 1 copies **0** descriptions (all 3 described rows are Indeed-only, so no LinkedIn sibling shares their key — the mechanism only fires on a genuine cross-source duplicate). 6 rows clear the fetch cutoff, best title-only 61. The stack dimension is what blocks 85, and only a JD can supply it.

## Collection worker (implemented, `backend/app/worker.py`, tested)

Orchestrates `scrape → adapt → enrich → dedupe → score → upsert`, then writes one `scrape_runs` row per plan. 238 tests pass across the backend.

**Pipeline order is load-bearing.** Enrichment runs on the **pre-fold** collected list, before the global dedupe, because `copy_descriptions` only fills a row whose description is empty. Folding LinkedIn into Indeed first leaves the surviving row with Indeed's text and the copy pass with nothing to do — so `enrichment.copied == 1` is impossible after a fold.

Schedule is local Argentina time (`SCRAPE_TIMEZONE`, default `America/Argentina/Buenos_Aires`, with a fixed `-03` fallback when the IANA database is unavailable):

- one collection slot per hour in `COLLECTION_WINDOW = (9, 16)` — seven slots, inclusive start, exclusive end;
- LinkedIn runs every slot with `LINKEDIN_TERMS` (7 terms) rotating one per hour, `items=20`, `hours_old=168`;
- Indeed runs only at `INDEED_HOURS = (9, 12, 15)`, `items=30`, `country_indeed="Argentina"`, and **never** receives `hours_old`;
- the curated ATS boards run once at `ATS_HOUR = 9` via the public board APIs (`greenhouse`, `ashby`), never through jobdrop.

Dedupe folds two jobs only when `loose_key` matches **and** (`fingerprint` matches **or** either `location` is empty): 13 of 20 LinkedIn cards arrive with a blank location, which is missing data, not a different city. The surviving row keeps the **first-seen** `fingerprint`/`loose_key` and is never recomputed after the merge — `fingerprint` is the database's unique key, so recomputing from a backfilled location would mint a new identity every run and duplicate rows.

`rows_new` is **not** database novelty: `upsert_job` returns a uuid for an insert *and* an update, so the worker cannot tell them apart. It counts distinct offers unique within this run, attributed to the first plan that surfaced them.

`upsert_job` does not touch `score`, so scoring is a second call: `PATCH /rest/v1/jobs?id=eq.<uuid>` with `score`, `score_breakdown` and `scored_at`, authenticated with the `service_role` key.

Rows whose title arrives empty are dropped after adaptation and that plan's run is recorded as `error` with a message containing `dropped`; the good rows are still written.

There is **no third-party scheduler or HTTP client**: `apscheduler`, `httpx` and `supabase-py` are absent from the pinned requirements, so the loop is a stdlib `while` + `time.sleep` and the transport is `urllib.request` with an injectable callable. This is a deliberate deviation from the "APScheduler worker" line above.

CLI: `python -m app.worker` (hourly loop), `--once`, `--dry-run` (records instead of writing), `--no-enrich`, `--max-fetches`. Credentials load from `backend/.env` (falling back to a legacy `backend/.env.txt`), with the process environment taking precedence; a missing `SUPABASE_URL` or secret key raises `MissingConfig` before any write. The key comes from `SUPABASE_SECRET_KEY` first and from the legacy `SUPABASE_SERVICE_ROLE_KEY` second, so a stale value in the legacy variable can never shadow a real secret. **A key beginning with `sb_publishable_` also raises `MissingConfig`**, in either variable: publishable keys resolve to the `anon` role, which has zero table grants, so the worker would fail with `42501 permission denied for table jobs` on every insert. The worker needs the project secret key.

### Control panel (`backend/app/panel.py`, tested)

The user wants explicit control over when the worker runs, so **nothing autostarts**: no systemd unit, no cron, no `linger`. `backend/panel` (or `python -m app.panel`) opens `http://127.0.0.1:8765` with **Iniciar** / **Detener** and the last collection, read through `SupabaseWriter.last_run()` and cached for 15 s so the 3-second poll does not hammer PostgREST.

- The worker is a **child process of the panel**: `Ctrl+C` on the panel stops it, so closing the terminal is the off switch. One instance at a time — a second `start` is refused rather than stacking a second writer.
- It binds **loopback only** (`--host` can widen it) and therefore has no authentication. Widening the host exposes Start/Stop to the whole LAN.
- A button inside the hosted PWA cannot do this: the PWA is static and remote, so it has no way to start a process on the PC. The only alternative would be an always-on bridge, which the user rejected.
- Consequence to keep in mind: while the panel is closed there is no collection, and missed scheduled slots are not replayed. LinkedIn's `hours_old=168` recovers the last seven days; the 9:00 ATS pass and the Indeed slots are simply skipped.

**First live collection (executed 2026-11-12 11:24 ART, `--once`):** the 11:00 slot ran LinkedIn only, 20 rows in 14 s, all 20 written (20 `job_sources`), 1 `scrape_runs` row with `status: ok`. Enrichment fetched 5 of the 8 allowed details with no throttling, so 5 of 20 rows carry a JD. Best score **73** (`parts`: stack 28, seniority 18, experience 12, location 5, recency 10) and **0 notifications** — exactly the ceiling `AGENTS.md` predicts for LinkedIn-only rows, since `stack` still has 12 of its 40 budget unclaimed. The `score_breakdown` shape is `{parts: {stack, seniority, experience, location, recency}, total, matched_skills, required_years, seniority_level}`; query `score_breakdown->'parts'->>'stack'`, not `score_breakdown->>'stack'`. The first 85+ candidate therefore has to come from an Indeed slot (9/12/15) or the 9:00 ATS boards.

`SupabaseWriter` sends the key on `apikey` always, and adds `Authorization: Bearer <key>` **only when the key starts with `eyJ`**. Legacy `service_role` keys are JWTs and work on either header; new `sb_secret_*` keys are not JWTs and PostgREST rejects them on `Authorization`, so they must travel on `apikey` alone. Both paths are pinned in `backend/tests/test_worker.py`.

## Notifications (implemented, `backend/app/push.py`, tested)

Transport is **Web Push (VAPID)**, so the alert lands in the installed PWA: no separate app, no account, no per-message cost. `VAPID_PRIVATE_KEY` empty means notifications are off; the worker then collects exactly as before and never opens a socket. A notification failure is logged, never raised, so it can never turn a good collection run into an error.

Wiring lives in the worker: `build_notifier()` reads `VAPID_PRIVATE_KEY`/`VAPID_SUBJECT` from the same env as `WorkerConfig`, `run_forever` builds it once and `main` builds it per invocation, and `_send_notifications` runs `deliver_push_notifications(result.notify, ...)` right after the write. **`--dry-run` passes `notifier=None`**, and `RecordingWriter.push_subscriptions()` returns `[]`, so a dry run can never ring a real phone.

- **The browser owns the subscription.** `frontend/src/lib/push.ts` asks for permission only from the explicit toggle in `PushToggle.tsx`, subscribes with `PUBLIC_VAPID_PUBLIC_KEY`, and upserts `user_id`/`endpoint`/`p256dh`/`auth` into `public.push_subscriptions`. `frontend/public/sw.js` shows the push and opens `click` on tap. Neither the private key nor the secret key ever reaches the browser.
- **One message per subscribed device.** The worker reads the table with the server key and pushes to every row, so a second phone does not steal the first one's alerts. A `404`/`410` answer means the browser dropped that subscription: the row is deleted and the pass continues.
- Message shape: title `f"{total}/100 · {company}"`, body = job title plus location when present, `click` = the first source URL.
- `deliver_push_notifications` gates on `should_notify(total)`, skips a job whose `notified_at` is already set (re-runs never re-notify), counts a delivery that reached **no** device as `failed` **without** stamping it so the next run retries, and stamps `notified_at` only after at least one device accepted the push. A stamping error is logged and does not abort the remaining offers.
- `push.py` deliberately does **not** import `worker`: the sender is injectable, `pywebpush` is imported inside the sender so a dry run never pays for the crypto stack, and the job type arrives via `TYPE_CHECKING`. `worker` imports `push`, so the dependency only points one way.
- **Manual prerequisites**, all outside the code: an HTTPS origin (LAN IPs and `localhost` are not a secure context for push), `PUBLIC_VAPID_PUBLIC_KEY` present at build time, `VAPID_PRIVATE_KEY` present for the worker, and the local worker actually running. Generating a key pair is `openssl ecparam -name prime256v1 -genkey -noout -out private.pem`, but the stored value here is the base64 PKCS#8 DER in `VAPID_PRIVATE_KEY`, with the URL-safe uncompressed public point in `PUBLIC_VAPID_PUBLIC_KEY`.

## Database (applied, project `utrybuzjkttrmdcrugio`)

Schema lives in `public`; Postgres 17.11. Four tables, all with RLS enabled:

- `jobs` — one row per deduplicated offer. `fingerprint` is UNIQUE and is the natural key. Carries `loose_key`, normalized fields, `raw jsonb`, `score`/`score_breakdown`/`scored_at`, `notified_at`, and `first_seen_at`/`last_seen_at`.
- `job_sources` — every portal occurrence, `unique (job_id, source, url)`, cascade delete.
- `scrape_runs` — telemetry per run: `source`, `params jsonb`, `status` (`running`/`ok`/`error`), `rows_fetched`, `rows_new`, `error`.
- `push_subscriptions` — one row per browser subscription: `user_id` (cascade delete on `auth.users`), `endpoint` UNIQUE, `p256dh`, `auth`, `user_agent`, timestamps. It is the only table the browser may write. `authenticated` holds `SELECT`/`INSERT`/`UPDATE`/`DELETE` but every policy requires `is_owner() and user_id = auth.uid()`, so a second account cannot plant a subscription and receive the owner's offers. `service_role` reads it to push; `anon` has no grants.

Write path is one RPC: `public.upsert_job(payload jsonb) returns uuid`. It upserts by `fingerprint` and folds in every `sources[]` entry. Merge rules, in order of importance:

- the **longest description wins** (LinkedIn has none; Indeed has the full JD), and `raw` follows the same winner so the payload matches the row;
- `location` is **backfilled** when the stored value is empty (13 of 20 LinkedIn cards arrive blank) and never overwritten once set;
- `posted_at`, salary fields and `job_type` are `coalesce`d in;
- `is_remote` is OR-ed, so any source saying remote wins;
- `last_seen_at` always advances.

Auth model is a single owner. `private.owner_ids` holds exactly one `auth.users.id`; `public.is_owner()` gates every SELECT policy; `public.claim_ownership()` lets the **first** authenticated caller claim the app under an advisory lock and rejects everyone after. That keeps the app private even if public signups are left on.

**Manual step still pending:** disable public signup in the Supabase dashboard (Authentication → Sign In / Providers → Email). Otherwise whoever registers first could claim ownership before the real user does.

### Grant gotcha that bit us

Supabase's default privileges grant `ALL` on new `public` objects to `anon` and `authenticated`, and a plain `revoke ... from public` does **not** remove those explicit role grants. Two consequences, both fixed by `harden_grants_and_defaults`:

- **`TRUNCATE` is not filtered by RLS.** `authenticated` could have truncated `jobs` while every SELECT policy looked correct. Read access must be granted as `SELECT` only, after revoking `ALL`.
- **`SECURITY DEFINER` functions bypass RLS.** `upsert_job` was executable by `anon` — that alone was an unauthenticated write path. Worker-only functions now grant `EXECUTE` to `service_role` alone.

Default privileges for `tables`, `sequences` and `functions` are now revoked for `anon`/`authenticated`, so future objects start closed. Verified state: `anon` has zero table grants and zero function `EXECUTE`; `authenticated` has `SELECT` only plus `is_owner`/`claim_ownership`.

## Gotchas

- **Free Postgres tiers suspend or pause on inactivity.** Neon computes sleep and wake automatically; Supabase free projects pause after roughly a week without activity. The hourly worker keeps them warm, but a long PC outage pauses them — never assume the DB is up.
- **If the frontend is public it must be authenticated.** Without auth plus row-level security, anyone holding the URL reads the job list. Design the single-user login from the start, not later.
- The scraper worker writes with Supabase's `service_role` key, which bypasses RLS. It must never reach the frontend; the PWA uses the publishable key plus authenticated RLS policies.
- **A publishable key in a key variable does not fail like a bad key.** PostgREST authenticates it fine and resolves it to `anon`, so the symptom is `42501 permission denied for table jobs` (with a `GRANT ... TO anon` hint), not `401`. `WorkerConfig.from_env` now rejects that prefix up front, in either `SUPABASE_SECRET_KEY` or the legacy `SUPABASE_SERVICE_ROLE_KEY`. `backend/.env` now holds the project secret key as `SUPABASE_SECRET_KEY`, and a read-only `GET /rest/v1/jobs?select=id&limit=1` with it answers `200`, so live writes are unblocked; the old `SUPABASE_SERVICE_ROLE_KEY` line is commented out rather than deleted.
- **Supabase secret keys do not exist until they are created.** Legacy `anon`/`service_role` JWTs keep working until the end of 2026; the new model splits them into a publishable key (safe to ship) and a secret key (`sb_secret_…`) created under Settings → API Keys → "Publishable and secret API keys", where the button reads **Create new API keys** on projects that are still legacy-only. A secret key is shown once, is not a JWT, and must go on `apikey` alone — `Authorization: Bearer` rejects it. Neither key can be read back through the MCP tools, so provisioning is always a manual dashboard step.
- **PWA Web Push only works in a secure context** — an HTTPS hostname. `localhost` and LAN IPs fail silently, so the phone cannot receive pushes from a local preview. On Android it works in Chrome; on iOS it would require installing the PWA first. Permission must be granted from a user gesture, which is exactly what the toggle in the dashboard is for.
- **WSL2 has its own NAT**, so anything left running on the PC is not directly reachable from the phone. Only relevant if a service stays on the PC instead of moving to free hosting.
- LinkedIn returns HTTP 429 on bursts, and JobSpy `fetch_description=True` multiplies requests per offer. Keep it off until measured.

## Frontend (implemented scaffold, `frontend/`)

Astro 5 + React 19 islands, `output: "static"`, built with **npm** (no Tailwind, no CDN assets). One page (`src/pages/index.astro`) renders `<OfferBoard client:only="react" />`; the island is the whole app and reads Supabase directly from the browser.

- **Read-only by design.** The UI only runs `SELECT`s through the publishable key plus the authenticated RLS policies; it never writes. Writable owner state (`seen`/`applied`) does not exist yet.
- **Login is a magic link** (`signInWithOtp` + `emailRedirectTo`) with **`shouldCreateUser: false`**, so only an account that already exists in Supabase can request a link — otherwise anyone could mint a session and race for ownership. The implicit flow's `#access_token` is parsed by `detectSessionInUrl` — no PKCE, because a static host has no server to complete the exchange. After the session appears the island calls `rpc("claim_ownership")` exactly once; `true` means this account claimed the app. The **owner account must exist before public signup is disabled**, and the deployed origin must be in the Supabase redirect allow-list.
- **`frontend/.env`** carries `PUBLIC_SUPABASE_URL`, `PUBLIC_SUPABASE_ANON_KEY` (the publishable key — safe to ship) and `PUBLIC_VAPID_PUBLIC_KEY`. `PUBLIC_*` values are inlined at build time; when they are missing `isConfigured` is false and the app renders a "Falta configuración" panel, so a keyless build still succeeds and leaks nothing.
- **Type narrowing is manual:** capture `const client = supabase;` inside each callback or effect, because the `isConfigured` boolean does not narrow the module-level `supabase` and `astro check` fails otherwise. `FormEvent` is deprecated in this toolchain — handlers take no event and call `preventDefault()` at the JSX call site.
- `npm run build` and `npx astro check` both pass with 0 errors; `dist/` is a static bundle with no key material when `.env` is absent.

## Deployment (Netlify, decided)

The repository is public at `https://github.com/mauro-l/autopala` (the directory is still `pala-automatica`). Only the **frontend** deploys; the worker and the scrape stay on the PC.

`netlify.toml` at the repository root sets `base = "frontend"`, `command = "npm run build"`, `publish = "dist"` and `NODE_VERSION = 22`, so Netlify needs no manual build settings.

- **Environment variables**, set in Netlify (Site configuration → Environment variables), never committed: `PUBLIC_SUPABASE_URL`, `PUBLIC_SUPABASE_ANON_KEY`, `PUBLIC_VAPID_PUBLIC_KEY`. They are inlined at build time, so **changing one requires a redeploy**, not just a save.
- **Never** put `SUPABASE_SECRET_KEY` or `VAPID_PRIVATE_KEY` in Netlify: the bundle is public.
- **The site URL must be in Supabase → Authentication → URL Configuration → Redirect URLs**, otherwise the magic link lands on an error page. Local `http://localhost:4321` can stay there for previews.
- `/sw.js` is served `no-cache` through `netlify.toml` headers, so a new build is not shadowed by a cached service worker.
- Verified locally with `frontend/.env` moved away and the three values passed as process environment, which is how Netlify builds: one page, `sw.js` emitted, public key inlined. A scan of everything Git would track found no secret material; the only JWT-looking string is a dummy header in `backend/tests/test_worker.py`.
- Push. The repository is empty, so the first push is: `git init`, `git add -A`, `git commit -m "feat: initial pala-automatica"`, `git branch -M main`, `git remote add origin https://github.com/mauro-l/autopala.git`, `git push -u origin main`.

## Not yet decided

- Whether the PWA needs writable owner state (e.g. applied/seen flags). Tables are currently `SELECT`-only for `authenticated` plus the owner-scoped `push_subscriptions`.
- Name: the directory is `pala-automatica`; the original ChatGPT plan called it `Job Hunter`.

## Reference

Requirements originate from a ChatGPT share: <https://chatgpt.com/share/6af4c090-0450-83e9-8746-ac18722f40ac>. Use it as context only. Its Windows assumptions, Ollama fallback, and 31-file scaffold are superseded by the constraints above — it is not a plan of record.
