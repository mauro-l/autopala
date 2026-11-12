# PWA Web Push notifications
Objective: replace ntfy with browser Web Push for notifications from the installed PWA.
Branch: none (the workspace is not a git repository); delivery is local implementation plus documented manual setup.
Test runner: backend pytest; frontend `npx astro check` and `npm run build`.

## Specs

S1. The user requested: “saca ntfy. vamos a utilizar notificaciones propias de una pwa”. Remove ntfy transport/configuration and replace it with Web Push while keeping the existing score threshold and dry-run behavior.

S2. The signed-in owner can enable or disable push notifications from an explicit UI action. The PWA requests permission only after the action, subscribes with a public VAPID key, and stores only the current owner's active subscription in an RLS-protected table. Unsupported browsers and denied permission produce an understandable state without breaking the dashboard. Devices are stored per endpoint, so every subscribed device receives the alert.S3. A registered service worker handles push events and displays a system notification with the offer score, company, title, and location when available. Clicking opens the offer's source URL. No ntfy app is required.

S4. The local worker sends notifications for offers meeting the existing 85-point threshold using the private VAPID key, which must never enter the browser bundle or database. Failed sends are not stamped as delivered; expired subscriptions are removed. Notifications require the local worker to run and an HTTPS PWA origin with browser permission.

S5. Add focused backend/frontend tests and setup documentation, including VAPID key generation, required environment variables, HTTPS, and the local SQL migration. Do not apply schema changes to the remote Supabase project without separate explicit authorization.

## Tasks

| ID | Specs | Route | Status | Evidence |
| --- | --- | --- | --- | --- |
| T1 | S2, S4 | inline | done | `create_push_subscriptions` applied via supabase-pala; table, four owner-only policies, `authenticated=arwd`, no `anon` grant; advisors report nothing for it |
| T2 | S1, S4 | inline | done | `backend/app/push.py` (VAPID sender, expired-endpoint cleanup); `notify.py` deleted; worker wiring; `tests/test_push.py`; pywebpush 2.3.0 pinned |
| T3 | S2, S3 | inline | done | `frontend/public/sw.js`, `src/lib/push.ts`, `src/components/PushToggle.tsx`, Dashboard/OfferBoard wiring, `.push-row` styles |
| T4 | S5 | inline | done | 243 backend tests pass; `npx astro check` 20 files 0/0/0; `npm run build` green with `/sw.js` emitted; live service-role read of `push_subscriptions` returned 0 rows |

## Log

L1. User authorization, verbatim: “saca ntfy. vamos a utilizar notificaciones propias de una pwa”

L2. Architecture follows the existing local worker and static PWA: the browser writes its subscription through the authenticated Supabase client; a local SQL migration adds owner-scoped storage; the worker reads it using the server-only Supabase secret and sends encrypted Web Push with VAPID. No remote migration is authorized. The documented target is Android, and the user's wording indicates one active phone subscription per owner. No git repository is available for work-unit commits or native review.

L3. Authorization update from the user, verbatim: “no hay nada en env.example. usa el .env y mcp de supabase-pala”. The additive migration was applied through the Supabase MCP and verified against `pg_policies`/`relacl`.

L4. Spec correction: S2 originally said enabling on another installation replaces the previous one. The implementation stores one row per `endpoint` and the worker pushes to every row, which is safer for a lost phone and simpler than cross-device revocation. S2 was rewritten, not duplicated.

L5. Verification detail: `pywebpush` in `curl` mode signed and encrypted a payload against the stored private key without network access, which proves the key format loads. Real end-to-end delivery still needs an HTTPS origin and a phone that grants permission; the worker must be running for any alert to be produced.

L6. The `.env` permission was scoped to this project so the self-documenting templates could be corrected: `backend/.env.example` now describes `VAPID_PRIVATE_KEY`/`VAPID_SUBJECT` instead of the removed ntfy variables, and `frontend/.env.example` gained `PUBLIC_VAPID_PUBLIC_KEY`. Verified that the private key parses as a P-256 key and that the frontend public key is the matching point.
