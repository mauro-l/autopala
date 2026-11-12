# Frontend UI — full component layer

Objective: replace the single-file offers screen with a complete, mobile-first UI layer built from presentational components, keeping the existing read-only Supabase plumbing.
Branch: none (the working tree is not a git repository yet).
Delivery: local build + visual smoke; hosting still deferred.
Test runner: none for the frontend — verification is `npx astro check`, `npm run build`, and a screenshot of a temporary preview page.

## Specs

S1. The page shell moves out of the island: `src/layouts/Layout.astro` owns `<html>`, metadata, manifest link and the single `global.css` import; `src/pages/index.astro` only renders the island through it. No page-level style duplication.

S2. `src/styles/global.css` is a token-based design system (color, spacing, radius, typography) covering every component below, mobile-first, with `prefers-reduced-motion` and visible `:focus-visible` rings. No external font, CDN or CSS framework.

S3. The offer card shows the five score dimensions from `score_breakdown.parts` (`stack` 40, `seniority` 25, `experience` 15, `location` 10, `recency` 10) as normalised meters, plus the total as a ring. `score_breakdown` is added to the existing SELECT; no new request is made.

S4. The card also surfaces `matched_skills` as tags, `seniority_level` and `required_years` when present, and every `job_sources` entry as a labelled outbound link. Missing values degrade silently — no `undefined`, no `null` text.

S5. Filters live in the UI only, over the already-fetched rows: free-text search, minimum score chips (Todo / 70+ / 85+), remote-only toggle and sort (score desc, most recent first). A live result count reflects the active combination, and clearing filters is one tap.

S6. Every shell state is explicit and styled: configuration missing, session loading, signed out, claim in progress, reading offers, error, and empty result after filtering.

S7. The UI stays read-only and $0: no insert/update/delete, no analytics, no new dependency, no service key. Only `PUBLIC_SUPABASE_URL` and `PUBLIC_SUPABASE_ANON_KEY` reach the bundle.

S8. `npx astro check` reports 0 errors and `npm run build` stays green; a temporary fixture page renders the real components for a visual check and is deleted before finishing.

## Tasks

| ID | Specs | Route | Status | Evidence |
| --- | --- | --- | --- | --- |
| T1 | S1, S2 | inline | done | `src/layouts/Layout.astro`, `src/styles/global.css` |
| T2 | S3, S4 | inline | done | `src/lib/format.ts`, `src/components/ScoreDial.tsx`, `OfferCard.tsx` |
| T3 | S5 | inline | done | `FilterBar.tsx`, `Dashboard.tsx` |
| T4 | S6 | inline | done | `LoginView.tsx`, `ConfigMissing.tsx`, `EmptyState.tsx`, `SkeletonList.tsx` |
| T5 | S7, S8 | inline | done | `npx astro check` (17 files, 0 errors/warnings/hints), `npm run build` (1 static page), temporary visual preview and cleanup |

## Log

Verification update: Visual verification completed with a temporary fixture page covering populated and degraded offer cards, dashboard states, and score dials; screenshots were captured before deleting the fixture. Astro ignores underscore-prefixed page files, so the temporary route used `preview.astro`. The fixture page/component and preview server were removed afterward. Final checks: `npx astro check` reported 0 errors, warnings, and hints across 17 files; `npm run build` completed with only `/index.html` generated. Improved empty-meter track contrast and added `mobile-web-app-capable` alongside the Apple-specific meta tag.

L1. User authorization, verbatim: "crea todo el frontend despues le agregamos funcionalidad. crea el frontend".
