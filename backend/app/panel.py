"""Local control panel for the collection worker.

The panel is the only thing that decides when the worker runs. The worker is a
child process of the panel, so closing the panel (Ctrl+C) stops it too: nothing
keeps collecting behind your back, and no autostart is involved.

It binds to the loopback interface by default. That is what lets it skip
authentication entirely — only processes on this machine can reach it. Binding
``0.0.0.0`` is possible through ``--host`` but then the whole LAN can press the
buttons, so it is opt-in and off by default.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Sequence

LOG = logging.getLogger("app.panel")

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

Spawn = Callable[[Sequence[str], Path], Any]


def _spawn(command: Sequence[str], cwd: Path) -> Any:
    """Launch the worker with this interpreter so it sees the same venv."""
    return subprocess.Popen(list(command), cwd=str(cwd))


@dataclass(frozen=True)
class WorkerState:
    """What the panel shows right now."""

    running: bool
    pid: int | None
    started_at: str | None
    exit_code: int | None


class WorkerProcess:
    """Owns the single worker child process.

    One instance at a time: a second ``start`` is refused instead of stacking a
    second worker on top of the first, which would double every write.
    """

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: Path = BACKEND_DIR,
        spawn: Spawn | None = None,
        clock: Callable[[], datetime] | None = None,
        stop_timeout: float = 10.0,
    ) -> None:
        self.command = list(command)
        self.cwd = cwd
        self.spawn = spawn or _spawn
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.stop_timeout = stop_timeout
        self._process: Any | None = None
        self._started_at: str | None = None
        self._exit_code: int | None = None

    def _alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> bool:
        """Start the worker. Returns ``False`` when it is already running."""
        if self._alive():
            return False
        self._process = self.spawn(self.command, self.cwd)
        self._started_at = self.clock().isoformat()
        self._exit_code = None
        LOG.info("worker started pid=%s", getattr(self._process, "pid", "?"))
        return True

    def stop(self) -> bool:
        """Stop the worker. Returns ``False`` when it was not running."""
        if self._process is None:
            return False
        if self._process.poll() is not None:
            self._exit_code = self._process.returncode
            return False

        self._process.terminate()
        try:
            self._process.wait(timeout=self.stop_timeout)
        except subprocess.TimeoutExpired:
            LOG.warning("worker ignored SIGTERM, killing it")
            self._process.kill()
            self._process.wait()
        self._exit_code = self._process.returncode
        LOG.info("worker stopped code=%s", self._exit_code)
        return True

    def status(self) -> WorkerState:
        running = self._alive()
        if self._process is not None and not running:
            self._exit_code = self._process.returncode
        return WorkerState(
            running=running,
            pid=getattr(self._process, "pid", None) if running else None,
            started_at=self._started_at,
            exit_code=None if running else self._exit_code,
        )


def _last_run(env: dict[str, str]) -> dict[str, Any] | None:
    """The most recent collection, read with the worker's own credentials."""
    try:
        from app.worker import SupabaseWriter, WorkerConfig, _default_env_path, load_env

        config = WorkerConfig.from_env(env or load_env(_default_env_path()))
        return SupabaseWriter(config.base_url, config.service_key).last_run()
    except Exception as exc:  # the panel must render even with no database
        LOG.warning("could not read the last run: %s", exc)
        return None


PAGE = """<!doctype html>
<html lang="es-AR">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Pala Automática · panel</title>
    <style>
      :root { color-scheme: dark; }
      * { box-sizing: border-box; }
      body {
        margin: 0; min-height: 100vh; display: grid; place-items: center;
        background: #0b0e14; color: #e6ebf5; padding: 24px;
        font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
      }
      main { width: 100%; max-width: 460px; display: grid; gap: 16px; }
      h1 { font-size: 1.15rem; margin: 0; letter-spacing: 0.01em; }
      .sub { color: #8d9ab3; font-size: 0.82rem; margin: 0; }
      .card {
        background: #121722; border: 1px solid #222a3a; border-radius: 14px;
        padding: 18px; display: grid; gap: 14px;
      }
      .state { display: flex; align-items: center; gap: 10px; font-weight: 600; }
      .dot { width: 10px; height: 10px; border-radius: 999px; background: #3a465c; }
      .dot.on { background: #3ddc97; box-shadow: 0 0 0 4px rgba(61, 220, 151, 0.15); }
      .meta { color: #8d9ab3; font-size: 0.8rem; display: grid; gap: 4px; }
      .row { display: flex; gap: 10px; }
      button {
        flex: 1; padding: 13px 16px; border-radius: 10px; border: 1px solid transparent;
        font: inherit; font-weight: 600; cursor: pointer; transition: background 0.15s ease;
      }
      button:disabled { opacity: 0.45; cursor: not-allowed; }
      .start { background: #4f8cff; color: #08101f; }
      .start:hover:not(:disabled) { background: #6b9dff; }
      .stop { background: transparent; color: #ff6b6b; border-color: #4b2530; }
      .stop:hover:not(:disabled) { background: #1c1317; }
      .note { color: #8d9ab3; font-size: 0.76rem; margin: 0; }
      .warn { color: #f0b429; font-size: 0.78rem; margin: 0; min-height: 1em; }
    </style>
  </head>
  <body>
    <main>
      <div>
        <h1>Pala Automática</h1>
        <p class="sub">Panel local del worker</p>
      </div>

      <section class="card">
        <div class="state">
          <span class="dot" id="dot"></span>
          <span id="state">Consultando…</span>
        </div>
        <div class="meta" id="meta"></div>
        <div class="row">
          <button class="start" id="start" type="button">Iniciar</button>
          <button class="stop" id="stop" type="button">Detener</button>
        </div>
        <p class="warn" id="warn"></p>
      </section>

      <p class="note">
        El worker corre como hijo de este panel: si cerrás la terminal, se detiene.
        Nada queda encendido por su cuenta.
      </p>
    </main>

    <script>
      const dot = document.getElementById("dot");
      const state = document.getElementById("state");
      const meta = document.getElementById("meta");
      const warn = document.getElementById("warn");
      const startBtn = document.getElementById("start");
      const stopBtn = document.getElementById("stop");

      function render(data) {
        dot.className = data.running ? "dot on" : "dot";
        state.textContent = data.running ? "Encendido" : "Apagado";
        const lines = [];
        if (data.pid) lines.push("PID " + data.pid);
        if (data.started_at) lines.push("Desde " + new Date(data.started_at).toLocaleString());
        if (typeof data.exit_code === "number") lines.push("Última salida: código " + data.exit_code);
        if (data.last_run) {
          const run = data.last_run;
          const when = run.started_at ? new Date(run.started_at).toLocaleString() : "—";
          lines.push("Última recolección: " + (run.source || "—") + " · " + (run.status || "—"));
          lines.push(when + " · " + (run.rows_fetched ?? 0) + " filas");
        } else if (data.last_run_error) {
          lines.push(data.last_run_error);
        }
        meta.replaceChildren(
          ...lines.map((line) => {
            const span = document.createElement("span");
            span.textContent = line;
            return span;
          }),
        );
        startBtn.disabled = data.running;
        stopBtn.disabled = !data.running;
      }

      async function refresh() {
        try {
          const response = await fetch("/status", { cache: "no-store" });
          render(await response.json());
        } catch {
          state.textContent = "Panel sin respuesta";
        }
      }

      async function act(path) {
        warn.textContent = "";
        try {
          const response = await fetch(path, { method: "POST" });
          const data = await response.json();
          if (data.error) warn.textContent = data.error;
          render(data);
        } catch {
          warn.textContent = "No se pudo hablar con el panel.";
        }
      }

      startBtn.addEventListener("click", () => act("/start"));
      stopBtn.addEventListener("click", () => act("/stop"));
      refresh();
      setInterval(refresh, 3000);
    </script>
  </body>
</html>
"""


def render_page() -> bytes:
    return PAGE.encode("utf-8")


def _payload(worker: WorkerProcess, last_run: dict[str, Any] | None, error: str | None) -> dict[str, Any]:
    state = worker.status()
    return {
        "running": state.running,
        "pid": state.pid,
        "started_at": state.started_at,
        "exit_code": state.exit_code,
        "last_run": last_run,
        "last_run_error": error,
    }


def build_handler(
    worker: WorkerProcess,
    env: dict[str, str],
    *,
    ttl: float = 15.0,
    clock: Callable[[], float] | None = None,
) -> type[BaseHTTPRequestHandler]:
    """Bind the panel's routes to one worker instance and one environment.

    The last-run read hits the database, so it is cached: the page polls every
    three seconds and PostgREST does not need that much attention.
    """
    now = clock or time.monotonic
    cache: dict[str, Any] = {"at": -ttl, "value": None}

    def last_run() -> dict[str, Any] | None:
        moment = now()
        if moment - cache["at"] >= ttl:
            cache["value"] = _last_run(env)
            cache["at"] = moment
        return cache["value"]

    class Handler(BaseHTTPRequestHandler):
        server_version = "PalaPanel/1.0"

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data: dict[str, Any]) -> None:
            self._send(status, json.dumps(data).encode("utf-8"), "application/json")

        def _status_payload(self) -> dict[str, Any]:
            error = None
            latest = last_run()
            if latest is None:
                error = "No se pudo leer la última recolección."
            return _payload(worker, latest, error)

        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            if self.path in ("/", "/index.html"):
                self._send(200, render_page(), "text/html; charset=utf-8")
                return
            if self.path == "/status":
                self._json(200, self._status_payload())
                return
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib naming
            if self.path == "/start":
                started = worker.start()
                payload = self._status_payload()
                if not started:
                    payload["error"] = "Ya está encendido."
                self._json(200, payload)
                return
            if self.path == "/stop":
                stopped = worker.stop()
                payload = self._status_payload()
                if not stopped:
                    payload["error"] = "Ya estaba apagado."
                self._json(200, payload)
                return
            self._json(404, {"error": "not found"})

        def log_message(self, fmt: str, *args: Any) -> None:
            LOG.debug("%s - %s", self.client_address[0], fmt % args)

    return Handler


def serve(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    worker: WorkerProcess | None = None,
    env: dict[str, str] | None = None,
) -> None:
    """Run the panel until interrupted, always stopping the child on exit."""
    command = [sys.executable, "-m", "app.worker"]
    worker = worker or WorkerProcess(command)
    handler = build_handler(worker, env or {})

    server = ThreadingHTTPServer((host, port), handler)
    print(f"Panel listo en http://{host}:{port}  (Ctrl+C para cerrar)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando panel…", flush=True)
    finally:
        server.server_close()
        worker.stop()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local control panel for the worker.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    serve(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI guard
    raise SystemExit(main())
