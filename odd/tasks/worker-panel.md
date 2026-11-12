# Worker control panel

Objective: give the user explicit, visible control over when the collection worker runs, without an autostart daemon.
Branch: none (not a git repository yet).
Delivery: local `python -m app.panel`; no deployment.
Test runner: `cd backend && .venv/bin/python -m pytest`.

## Specs

S1. User decision, verbatim: “yo quiero tener control sobre cuando esta encendido y apagado. pensemos en una solucion.” and “vamos con el A”. The worker must never start by itself.

S2. A local page at `127.0.0.1:8765` shows whether the worker is running and offers **Iniciar** / **Detener**.

S3. The worker is a child process of the panel. Closing the panel stops it; nothing survives the panel.

S4. The panel also shows the most recent collection (source, status, time, rows read) using the worker's own credentials, cached so polling does not hammer PostgREST.

S5. The panel binds loopback only by default, needs no authentication, and adds no dependency: stdlib `http.server` plus the existing worker.

## Tasks

| ID | Specs | Route | Status | Evidence |
| --- | --- | --- | --- | --- |
| T1 | S2, S3 | inline | done | `backend/app/panel.py` (`WorkerProcess`, routes `/`, `/status`, `/start`, `/stop`) |
| T2 | S4 | inline | done | `SupabaseWriter.last_run()` + 15 s cache in the handler |
| T3 | S1 | inline | done | systemd unit and `linger` removed; no autostart remains |
| T4 | S5 | inline | done | `backend/tests/test_panel.py` (12 tests); 255 total pass; live smoke on port 8799 read a real `linkedin/ok/20 rows` run and started/stopped a real child |

## Log

L1. User authorization, verbatim: “vamos con el A”, after rejecting both autostart and a permanent always-on bridge.

L2. Rejected alternatives: a button inside the hosted PWA cannot start a local process (static site, no server), and the bridge that would make it possible has to stay running, which contradicts the request. A systemd user service was installed first, then removed together with `linger` once the user asked for manual control.

L3. Convenience launcher: `backend/panel` (executable) so the whole command is `./panel`. It uses the project venv and passes through `--host`/`--port`.
