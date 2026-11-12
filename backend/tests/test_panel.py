"""Tests for the local control panel.

The child process is faked, so nothing here starts a real worker. One test does
bind an HTTP server, but only on loopback with port 0 (the OS picks a free one),
and it shuts down before the test returns.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from typing import Any, Sequence

from app.panel import WorkerProcess, WorkerState, build_handler, render_page

NOW = datetime(2026, 11, 12, 19, 0, tzinfo=timezone.utc)


class FakeProcess:
    """Just enough of ``subprocess.Popen`` for the panel."""

    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int | None:
        return self.returncode


class FakeSpawn:
    def __init__(self) -> None:
        self.calls: list[tuple[Sequence[str], Any]] = []
        self.process = FakeProcess()

    def __call__(self, command: Sequence[str], cwd: Any) -> FakeProcess:
        self.calls.append((command, cwd))
        return self.process


def make_worker(spawn: FakeSpawn | None = None) -> tuple[WorkerProcess, FakeSpawn]:
    spawn = spawn or FakeSpawn()
    worker = WorkerProcess(
        ["python", "-m", "app.worker"], spawn=spawn, clock=lambda: NOW, stop_timeout=1.0
    )
    return worker, spawn


class TestWorkerProcess:
    def test_start_spawns_the_worker_once(self) -> None:
        worker, spawn = make_worker()

        assert worker.start() is True
        assert len(spawn.calls) == 1
        assert spawn.calls[0][0] == ["python", "-m", "app.worker"]

    def test_a_second_start_is_refused_while_running(self) -> None:
        worker, spawn = make_worker()

        worker.start()
        assert worker.start() is False
        assert len(spawn.calls) == 1

    def test_status_reports_the_running_child(self) -> None:
        worker, _ = make_worker()
        worker.start()

        state = worker.status()

        assert state == WorkerState(
            running=True, pid=4242, started_at=NOW.isoformat(), exit_code=None
        )

    def test_stop_terminates_the_child_and_records_the_exit(self) -> None:
        worker, spawn = make_worker()
        worker.start()

        assert worker.stop() is True
        assert spawn.process.terminated is True
        assert spawn.process.killed is False

        state = worker.status()
        assert state.running is False
        assert state.exit_code == -15
        assert state.pid is None

    def test_stop_without_a_child_is_a_noop(self) -> None:
        worker, _ = make_worker()
        assert worker.stop() is False

    def test_stop_after_the_child_already_exited_reports_not_running(self) -> None:
        worker, spawn = make_worker()
        worker.start()
        spawn.process.returncode = 0

        assert worker.stop() is False

    def test_a_finished_child_is_reported_as_stopped(self) -> None:
        worker, spawn = make_worker()
        worker.start()
        spawn.process.returncode = 0

        state = worker.status()

        assert state.running is False
        assert state.exit_code == 0
        assert state.pid is None

    def test_restart_after_exit_is_allowed(self) -> None:
        worker, spawn = make_worker()
        worker.start()
        spawn.process.returncode = 0
        worker.status()

        assert worker.start() is True
        assert len(spawn.calls) == 2


class TestRenderPage:
    def test_the_page_carries_both_buttons_and_the_loopback_note(self) -> None:
        page = render_page().decode("utf-8")

        assert "Iniciar" in page
        assert "Detener" in page
        assert "si cerrás la terminal, se detiene" in page


class TestPanelRoutes:
    def _serve(self, worker: WorkerProcess) -> tuple[ThreadingHTTPServer, str]:
        # An empty URL fails config loading fast, so /status never hits the network.
        env = {"SUPABASE_URL": ""}
        handler = build_handler(worker, env, ttl=0.0)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        host, port = server.server_address[:2]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, f"http://{host}:{port}"

    def test_the_page_and_the_status_respond(self) -> None:
        worker, _ = make_worker()
        server, base = self._serve(worker)
        try:
            with urllib.request.urlopen(f"{base}/", timeout=5) as response:
                assert response.status == 200
                assert "Pala Automática" in response.read().decode("utf-8")

            with urllib.request.urlopen(f"{base}/status", timeout=5) as response:
                body = json.loads(response.read())
                assert body["running"] is False
                assert body["last_run"] is None
        finally:
            server.shutdown()
            server.server_close()

    def test_start_and_stop_change_the_reported_state(self) -> None:
        worker, _ = make_worker()
        server, base = self._serve(worker)
        try:
            start = urllib.request.Request(f"{base}/start", method="POST")
            with urllib.request.urlopen(start, timeout=5) as response:
                body = json.loads(response.read())
                assert body["running"] is True
                assert body["pid"] == 4242

            stop = urllib.request.Request(f"{base}/stop", method="POST")
            with urllib.request.urlopen(stop, timeout=5) as response:
                body = json.loads(response.read())
                assert body["running"] is False
                assert body["exit_code"] == -15
        finally:
            server.shutdown()
            server.server_close()

    def test_an_unknown_path_is_404(self) -> None:
        worker, _ = make_worker()
        server, base = self._serve(worker)
        try:
            try:
                urllib.request.urlopen(f"{base}/nope", timeout=5)
            except urllib.error.HTTPError as exc:
                assert exc.code == 404
            else:  # pragma: no cover - the route must not exist
                raise AssertionError("expected a 404")
        finally:
            server.shutdown()
            server.server_close()
