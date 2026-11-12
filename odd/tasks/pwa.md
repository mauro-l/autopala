# PWA — Phase 6, first increment

Objective: an authenticated, read-only offers list served as a static Astro build, reading Supabase through the publishable key and row-level security.
Branch: none (the working tree is not a git repository yet).
Delivery: local build verified; hosting deferred.
Test runner: none for the frontend yet — verification is `npm run build` plus a manual smoke against the live database.

## Specs

S1. The frontend lives in `frontend/` beside `backend/`, uses **npm**, and builds with Astro + React islands to fully static output (`output: "static"`). No framework server, no SSR node, no paid hosting.

S2. Authentication is **magic link only**: email in, Supabase `signInWithOtp`, no password field, no OAuth provider. The browser never holds the `service_role` key; only the publishable/anon key is exposed through `PUBLIC_SUPABASE_URL` and `PUBLIC_SUPABASE_ANON_KEY`.

S3. On the first authenticated session the app calls `public.claim_ownership()` exactly once and reports which outcome occurred (claimed / already claimed by someone else). This is the first real exercise of the owner path, which SQL alone could not verify.

S4. The offers list is **read-only**. It selects from `jobs` with an embedded `job_sources(source, url, url_direct)`, ordered by `score` descending, limited to rows that already have a score. No client-side update, insert, or delete call exists in this increment.

S5. There is no writable owner state in this increment: no `seen` / `applied` / `dismissed` flags. The database currently grants `authenticated` `SELECT` only, and the UI must not depend on anything wider.

S6. The budget stays $0: no analytics, no telemetry, no external font or CDN dependency, no paid service.

S7. A missing configuration (no `.env`) must render an explicit message instead of crashing or silently showing an empty list.

## Tasks

| ID | Specs | Route | Status | Evidence |
| --- | --- | --- | --- | --- |
| T1 | S1, S6 | inline | done | `frontend/package.json`, `astro.config.mjs`, `tsconfig.json`; `npm run build` |
| T2 | S2, S7 | inline | done | `src/lib/supabase.ts`, `src/components/OfferBoard.tsx` |
| T3 | S3 | inline | done | `claimOwnership()` in `OfferBoard.tsx` |
| T4 | S4, S5 | inline | done | `fetchOffers()` in `OfferBoard.tsx`; grants verified `authenticated = SELECT` on `jobs`, `job_sources`, `scrape_runs` |
| T5 | S1, S2 | inline | done | `npx astro check` 0 errors/0 warnings/0 hints; `npm run build` green (keyless build leaks no key material); magic-link round trip is a manual step (L6) |

## Log

L1. User authorization for this increment, verbatim: "Continue if you have next steps, or stop and ask for clarification if you are unsure how to proceed."

L2. Grants verified before writing the query, so the UI could not be built against permissions that do not exist: `authenticated` holds `SELECT` on `public.jobs`, `public.job_sources` and `public.scrape_runs`; `anon` holds no grant on any of them.

L3. Defaults taken where `AGENTS.md` listed the item as undecided: `npm` (installed, 11.19.1), `frontend/` beside `backend/`, plain CSS instead of a utility framework. Hosting and notification transport remain undecided and untouched.

L4. Writable owner state deliberately excluded, so no new migration was needed and the database surface stays exactly as hardened.

L5. Manual prerequisites the code cannot satisfy, reported to the user rather than automated: the owner account must exist before public signup is disabled, and the deployed origin must be added to the Supabase redirect allow-list. Magic links cannot be delivered to an address that is not a registered user once signup is off.

L6. Checking the build against the browser surfaced a defect outside this increment: `backend/.env.txt` holds a **publishable** key (`sb_publishable_…`, 46 chars) as `SUPABASE_SERVICE_ROLE_KEY`, so the worker has never written live data — `public.jobs` is empty and PostgREST answers `42501 permission denied for table jobs` with a `GRANT ... TO anon` hint, because that key resolves to `anon` (zero grants by design), not `401`. The worker's `apikey` + `Bearer` header logic is correct and unchanged: a non-JWT bearer is tolerated and still reaches PostgREST. Fix is a guard, not a rewrite — `WorkerConfig.from_env` now raises `MissingConfig` for the `sb_publishable_` prefix, pinned by `test_a_publishable_key_is_rejected_before_any_write`; backend suite 209 passed. The publishable key is correct where it now also lives, in `frontend/.env` (`PUBLIC_SUPABASE_ANON_KEY`), because the island is read-only against `SELECT`-only grants. Unblocking live collection needs the project secret key, which only the user can copy from the dashboard, so it is reported rather than improvised.

L7. Supabase's official docs corrected L6's header claim: legacy `anon`/`service_role` JWTs keep working until the end of 2026, but a secret key (`sb_secret_…`) **does not exist until it is created** in the dashboard (Settings → API Keys → "Publishable and secret API keys"; the button reads *Create new API keys* when the project is still legacy-only). A secret key is not a JWT and PostgREST rejects it on `Authorization: Bearer`, so `SupabaseWriter._call` now sends `apikey` always and adds the Bearer header only when the key starts with `eyJ`. The old `test_the_service_key_is_sent_as_both_apikey_and_bearer` asserted the opposite and was replaced by `test_a_legacy_jwt_key_is_sent_as_both_apikey_and_bearer` and `test_a_secret_key_is_sent_only_on_the_apikey_header`; backend suite 210 passed. Unblocking live collection still needs one dashboard step the user owns: either create the secret key and paste it, or copy the legacy `service_role` JWT, which needs no code change.

L8. Credentials moved to the file name the rest of the toolchain expects and the key variable the dashboard now issues. `backend/.env.txt` was renamed to `backend/.env`, and `_default_env_path()` now prefers `.env` before falling back to a legacy `.env.txt`, so a leftover copy can no longer shadow the real file. `WorkerConfig.from_env` reads `SUPABASE_SECRET_KEY` first and the legacy `SUPABASE_SERVICE_ROLE_KEY` second: the stale `sb_publishable_…` line in the same file would otherwise win and reproduce the `42501` write failure. The publishable guard now runs on both variables, and the old legacy line is commented out instead of deleted. Pinned by `test_the_secret_key_variable_is_accepted_on_its_own`, `test_the_secret_key_wins_over_a_stale_legacy_variable`, `test_a_publishable_key_in_the_secret_variable_is_rejected` and `test_the_default_env_path_prefers_dot_env_over_dot_env_txt`; backend suite 214 passed. A read-only `GET /rest/v1/jobs?select=id&limit=1` with the loaded key answers `200` and `[]`, so the credential is live and the table is still empty — live writes are unblocked, and the first real collection is the remaining step.
