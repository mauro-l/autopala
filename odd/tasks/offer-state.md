# Offer triage — seen, saved, day grouping and filters

Objective: make the board actionable — mark an offer as seen, bookmark it, split the list by the day it arrived, and filter by that state. First writable owner state in the app; `jobs` stays worker-owned and read-only for the browser.
Branch: `main` (working tree is a git repository; no feature branch, local only).
Delivery: local build + `npx astro check`; Netlify redeploy is the user's decision.
Test runner: none for the frontend — pure logic is checked with `node` (type stripping) against temporary scripts outside the worktree, plus `npx astro check` and `npm run build`.

## Specs

S1. **Seen.** Every offer card carries a seen toggle. Marking it writes a timestamp for that offer and the owner; unmarking clears it. A seen card is visibly dimmed but stays listed and readable, and its outbound link is untouched.

S2. **Saved.** Every offer card carries a save toggle, independent of seen. A saved card is visually marked. Saved offers are reachable through a filter, not through a separate screen.

S3. **Grouped by day.** The list is split into day sections by the offer's own date — `posted_at` when present, otherwise `first_seen_at` (when it entered the app), never `last_seen_at` (which advances on every scrape and would collapse everything into "Hoy"). Section labels are `Hoy`, `Ayer`, and `dd/mm` (with the year when it is not the current year); a missing date falls into `sin fecha`. Sections are ordered newest first and, inside a section, the chosen sort is preserved. A `Por día` toggle (default on) restores the flat ranking when turned off.

S4. **Filters.** The existing filters (free text, minimum score, remote only, sort) stay. One new dimension is added: `Todas` / `Sin ver` / `Guardadas`, with live counts per option. `Sin ver` shows offers without a seen timestamp; `Guardadas` shows saved offers. `Limpiar` resets every dimension.

S5. **Persistence belongs in the database, not the device.** State lives in a new table `public.job_state`, keyed by `unique (user_id, job_id)`, holding `seen_at` and `saved_at` (NULL means unset). It must survive a reinstall and be visible from the phone while the PC is off. `localStorage` is rejected: it would not sync across devices and would be lost with the browser profile.

S6. **Security is least privilege.** `job_state` has RLS enabled; every policy requires `is_owner() and user_id = auth.uid()`. `authenticated` receives `SELECT`, `INSERT`, `UPDATE` only — no `DELETE`, because the app clears flags by setting them to NULL. `anon` receives nothing. `jobs` keeps `SELECT`-only for `authenticated`: the browser must not be able to mutate scraped rows.

S7. **Writes are optimistic and reversible.** A toggle updates the UI immediately, then writes through PostgREST upsert on `(user_id, job_id)`. If the write fails, the optimistic change is rolled back and the failure is shown; a stale UI must never claim a flag was stored.

S8. `npx astro check` reports 0 errors and `npm run build` stays green. No new runtime dependency, no service key, no secret in the bundle.

## Tasks

| ID | Specs | Route | Status | Evidence |
| --- | --- | --- | --- | --- |
| T1 | S5, S6 | inline | done | migration `job_state_owner_flags` (20261116162204): RLS on, 1 trigger, `authenticated` = SELECT/INSERT/UPDATE, `anon` = nothing; probe results in the log |
| T2 | S3 | inline | done | `src/lib/dates.ts`; `check-dates.mjs` GREEN in ART, UTC and UTC+13 |
| T3 | S1, S2, S7 | inline | done | `src/lib/triage.ts`, `OfferCard.tsx`, `OfferBoard.tsx`; `check-triage.mjs` GREEN; partial upsert verified against the live DB |
| T4 | S4 | inline | done | `src/lib/filters.ts`, `FilterBar.tsx`; `check-filters.mjs` GREEN (real module, bundled by the project's esbuild) |
| T5 | S3 | inline | done | `src/components/OfferList.tsx`, `Dashboard.tsx` |
| T6 | S1–S4 | inline | done | `src/styles/global.css` |
| T7 | S8 | inline | done | `npx astro check` 30 files / 0 errors; `npm run build` 1 page |
| T8 | S3, S7 | inline | done | correction batch after review: `revertFlag` + in-flight guard + `last_seen_at` fallback removed + calendar-day card label; regression tests in `frontend/scripts/checks/` |

## Log

L1. User request, verbatim: "ok, necesito marcar si esta visto, tener la opcion de guardar, de separar por dias, agregar filtros"

Baseline measured before any edit: `npx astro check` → 24 files, 0 errors, 0 warnings, 0 hints. DB inspected: `public` holds `jobs` (796 rows), `job_sources`, `scrape_runs`, `push_subscriptions`; `jobs` has no seen/saved column and `authenticated` holds `SELECT` only on it, so owner state needs its own table. `push_subscriptions` was used as the policy/trigger template; `touch_updated_at()` already exists.

Design decisions taken without asking, each with its reason:
- **Storage in `job_state`, not `localStorage`.** The flags must survive a reinstall and be readable from the phone while the PC is off; `localStorage` gives neither. The table is owner-scoped like `push_subscriptions` instead of adding writable columns to `jobs`, which is worker-owned.
- **Grouping date is `posted_at ?? first_seen_at`.** `last_seen_at` is advanced by the hourly worker for every row it re-scrapes, so it would put everything in "Hoy". `posted_at` is a `date` column and must be pinned to local midnight, otherwise a Buenos Aires user sees today's offers under "Ayer" (UTC-3).
- **Grouping is a view mode, not a filter.** `Por día` defaults to on and lives outside `Filters`, so `Limpiar` resets filters without silently flattening the list.
- **`Sort: Más recientes` now uses the grouping date.** It used to sort by `last_seen_at`, which is the scrape clock and made the order meaningless.

Verification evidence:
- `check-dates.mjs` — RED first (`ERR_MODULE_NOT_FOUND`), then GREEN in `America/Argentina/Buenos_Aires`, `UTC` and `Pacific/Auckland`. It pins the date-only trap, midnight crossings, DST-safe day counting, group order (undated last) and input immutability.
- `check-triage.mjs` — RED first, then GREEN. Covers `mergeState` dropping rows with no flag, `flagsOf` totality, immutable `setFlag` (including dropping an entry that carries nothing) and `countFlags`.
- `check-filters.mjs` — runs the REAL `src/lib/filters.ts` bundled by the repo's own `esbuild` into `/tmp`, so no dependency was added. Covers the new `status` dimension, counts restricted by the other filters, and the pre-existing query/score/remote behaviour as a regression guard on the shared code.
- DB write path, executed inside one rolled-back `DO` block: a both-flags row, then PostgREST's single-column upsert shape, left `saved_at` intact, cleared `seen_at`, and let `touch_updated_at` move `updated_at` (backdated first — `now()` is transaction-constant, which is why the first probe's assertion was wrong). `public.job_state` was left with 0 rows.
- Security probes: `anon` → `401` / `42501 permission denied` on both `POST` and `GET` against `/rest/v1/job_state`; owner JWT claims → insert and read succeed (`owner_visible_rows = 1`); a different `authenticated` sub → `42501 new row violates row-level security policy for table "job_state"`.
- `npx astro check` → 27 files, 0 errors / 0 warnings / 0 hints (24 at baseline, +3 new files). `npm run build` → 1 static page.

Risk: item 2 (security — new RLS policies and table grants). The independent verifier was run for that reason.

### Correction batch (after the independent verifier)

The verifier confirmed S1, S2, S3 (probed rules), S4, S8 and found **one blocker** plus six advisories. It could not confirm S5/S6 because it has no database access; those rest on the recorded probes. One bounded correction batch:

- **B1 (blocker, fixed).** Two fast taps on the same flag where both writes fail left the UI claiming `seen=true` with no row stored: the rollback restored the *pre-tap* value, which for the second tap was only ever optimistic. Fixed twice over: `revertFlag` rolls back to the last value known to be persisted (`persistedRef`), and a `${jobId}:${field}` in-flight set blocks a second concurrent write for the same flag, which also removes the out-of-order-commit divergence.
- **Advisory 1 (spec violation, fixed).** `offerDate` still fell back to `last_seen_at`, which S3 forbids. Removed — a row with neither date is `sin fecha` rather than being filed under "Hoy".
- **Advisory 3 (fixed).** `loadState` no longer assigns over an in-flight write.
- **Advisory 4 (fixed).** The card age now uses the same calendar-day unit as the section header (`formatDayDistance`), so a card can never read "hoy" inside the "Ayer" section. `formatAge` was removed from `format.ts`; `OfferCard` was its only caller.
- **Advisory 5 (fixed).** Flat mode renders a visually hidden `h2` so the heading order never skips a level.
- **Advisory 6 (accepted).** A rollback message is cleared by the next toggle attempt or success; it is transient UI feedback, not state.
- **Recheck residual (accepted).** The `loadState` guard is evaluated when the response arrives, not when it was issued, so a reload started before a successful write can still land afterwards and overwrite `persisted`. It needs a manual Reload during a write to reach, and the next read corrects it; not a false flag without a successful write. Left documented instead of chained into a third correction round.

The recheck confirmed B1 fixed with no new blocker, across four adversarial interleavings: same-tick double tap with both writes failing (second write dropped, UI and DB both read unset), tap during an in-flight upsert, a failed write followed by a successful one, and a rollback that must preserve the other flag. It also noted the accepted trade-off: a deliberate on→off double tap within one frame ends at the first tap's value, because the second is dropped rather than queued.

Regression proof: `frontend/scripts/checks/` (run with `npm run check:logic`, green under `TZ=America/Argentina/Buenos_Aires` and `TZ=UTC`) — no framework, no new dependency, Node type stripping, and the filters check bundles the real module with the repo's own esbuild into the OS temp dir. `toggle.mjs` models the component's toggle/loadState state machine over the real `triage.ts` functions and pins the exact B1 interleaving plus the reload guard; `triage.mjs` pins the persisted-rollback rule, and against the old algorithm the same scenario returns `seen:true`, which is the bug.

Not verified here: a real browser session. There is no owner password available to the agent, so the two-tap flow was checked through the pure modules, the PostgREST payload shape against the live database, and the type checker rather than a click-through.
