#!/usr/bin/env python3
"""Fake Python local service for the Electron/Python spike (ADR 0006 / 0007).

This is NOT the T+0 backend. It is a controllable fake used to exercise the
Electron-managed process lifecycle (Phase A) and the local transport contract
(Phase B).

stdout / stderr discipline (ADR 0006):
  - The ONLY structured lines written to stdout are bootstrap/lifecycle
    handshake lines: SPIKE_LISTENING, SPIKE_READY, SPIKE_FAILED, SPIKE_STOPPING.
    They carry process metadata (port / pid / generation) and are read by
    Electron only for process bootstrap, never for business data.
  - All other diagnostics go to stderr as SPIKE_LOG <json> lines.
  - Business request/event traffic flows only over HTTP / WebSocket / SSE
    (added in Phase B). Nothing business-shaped is ever written to stdout/stderr.

Behavior is controlled by environment variables so the Node test harness can
inject failures deterministically:

  SPIKE_GENERATION       int     generation number assigned by Electron main
  SPIKE_CREDENTIAL       str     per-launch bearer credential
  SPIKE_MODE             str     normal | startup-fail | slow-ready | crash-after-ready
  SPIKE_INIT_DELAY_MS    int     delay before readiness (slow-ready)
  SPIKE_CRASH_DELAY_MS   int     delay after ready before hard crash (crash-after-ready)
  SPIKE_PORT             int     bind port (0 = ephemeral, default)
  SPIKE_READY_FAIL_MS    int     time /healthz stays 503 before turning 200
  SPIKE_IGNORE_SHUTDOWN  1       ignore POST /shutdown (to test forced kill path)
  SPIKE_SLOW_WORK_MS     int     delay for POST /api/slow-work (active-work helper)
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys

from aiohttp import web


# ---------------------------------------------------------------------------
# Diagnostic emission
# ---------------------------------------------------------------------------

_LIFECYCLE_KINDS = {
    "SPIKE_LISTENING",
    "SPIKE_READY",
    "SPIKE_FAILED",
    "SPIKE_STOPPING",
}


def _emit(kind: str, payload: dict) -> None:
    """Emit one structured diagnostic line.

    Lifecycle handshake lines go to stdout (read by Electron for bootstrap only).
    Everything else is a log line on stderr.
    """
    line = json.dumps({"kind": kind, **payload}, separators=(",", ":"))
    stream = sys.stdout if kind in _LIFECYCLE_KINDS else sys.stderr
    stream.write(line + "\n")
    stream.flush()


def _log(event: str, **fields) -> None:
    _emit("SPIKE_LOG", {"event": event, **fields})


# ---------------------------------------------------------------------------
# Fake service
# ---------------------------------------------------------------------------


class FakeService:
    def __init__(self) -> None:
        self.generation = int(os.environ.get("SPIKE_GENERATION", "0"))
        self.credential = os.environ.get("SPIKE_CREDENTIAL", "")
        self.mode = os.environ.get("SPIKE_MODE", "normal")
        self.init_delay_ms = int(os.environ.get("SPIKE_INIT_DELAY_MS", "0"))
        self.ready_fail_ms = int(os.environ.get("SPIKE_READY_FAIL_MS", "0"))
        self.crash_delay_ms = int(os.environ.get("SPIKE_CRASH_DELAY_MS", "0"))
        self.ignore_shutdown = os.environ.get("SPIKE_IGNORE_SHUTDOWN", "") == "1"
        self.slow_work_ms = int(os.environ.get("SPIKE_SLOW_WORK_MS", "0"))
        port = int(os.environ.get("SPIKE_PORT", "0"))
        self.requested_port = port
        self.port: int | None = None
        self.pid = os.getpid()
        self.ready = False
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._crash_task: asyncio.Task | None = None
        self._stop_task: asyncio.Task | None = None
        self._app = self._build_app()

    # -- auth ---------------------------------------------------------------

    def _auth_middleware(self):
        svc = self

        @web.middleware
        async def middleware(request, handler):
            # Every endpoint requires the per-launch credential. Electron main
            # always holds it; the Renderer never does.
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return web.json_response(
                    {"error": "unauthenticated", "generation": svc.generation},
                    status=401,
                )
            token = auth[len("Bearer ") :]
            if token != svc.credential:
                return web.json_response(
                    {"error": "invalid_credential", "generation": svc.generation},
                    status=403,
                )
            return await handler(request)

        return middleware

    # -- routes (Phase A: lifecycle only) -----------------------------------

    def _build_app(self) -> web.Application:
        app = web.Application(middlewares=[self._auth_middleware()])
        app.router.add_get("/healthz", self._healthz)
        app.router.add_post("/shutdown", self._shutdown)
        app.router.add_post("/api/crash", self._crash_on_demand)
        app.router.add_post("/api/slow-work", self._slow_work)
        return app

    async def _slow_work(self, request: web.Request) -> web.Response:
        # Occupies the service for a configurable duration. Used to test
        # graceful/forced shutdown while work is in flight. Not business logic.
        body = {}
        if request.can_read_body:
            try:
                body = await request.json()
            except Exception:
                body = {}
        delay = self.slow_work_ms / 1000.0
        if delay > 0:
            await asyncio.sleep(delay)
        return web.json_response(
            {"ok": True, "generation": self.generation, "work": body}
        )

    async def _healthz(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "ready" if self.ready else "starting",
                "generation": self.generation,
                "pid": self.pid,
                "port": self.port,
            },
            status=200 if self.ready else 503,
        )

    async def _shutdown(self, request: web.Request) -> web.Response:
        if self.ignore_shutdown:
            _log("shutdown_ignored", generation=self.generation)
            return web.json_response(
                {"ok": False, "ignored": True, "generation": self.generation},
                status=202,
            )
        _log("shutdown_requested", generation=self.generation)
        # Respond first, then stop the event loop after a short grace period.
        if self._stop_task is None:
            self._stop_task = asyncio.create_task(self._graceful_stop(delay_ms=50))
        return web.json_response({"ok": True, "generation": self.generation})

    async def _crash_on_demand(self, request: web.Request) -> web.Response:
        _log("crash_on_demand", generation=self.generation)
        # Hardest crash: skip shutdown handlers entirely (simulates SIGKILL).
        # We cannot return a response because the process dies immediately.
        os._exit(137)

    # -- lifecycle ----------------------------------------------------------

    async def _bind(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", self.requested_port)
        await self._site.start()
        # Resolve the actual (ephemeral) port.
        sockets = self._site._server.sockets  # type: ignore[attr-defined]
        self.port = sockets[0].getsockname()[1]
        _emit("SPIKE_LISTENING", {"port": self.port, "pid": self.pid, "generation": self.generation})

    async def _init(self) -> None:
        """Run service initialization. May fail or be slow per SPIKE_MODE."""
        if self.mode == "startup-fail":
            _emit("SPIKE_FAILED", {"reason": "startup_fail_mode", "generation": self.generation})
            raise SystemExit(1)

        # slow-ready: /healthz stays 503 for the configured window.
        delay = max(self.init_delay_ms, self.ready_fail_ms) / 1000.0
        if delay > 0:
            _log("init_waiting", generation=self.generation, delay_ms=int(delay * 1000))
            await asyncio.sleep(delay)

        self.ready = True
        _emit("SPIKE_READY", {"port": self.port, "pid": self.pid, "generation": self.generation})

        if self.mode == "crash-after-ready" and self.crash_delay_ms > 0:
            self._crash_task = asyncio.create_task(self._crash_after(self.crash_delay_ms))

    async def _crash_after(self, delay_ms: int) -> None:
        await asyncio.sleep(delay_ms / 1000.0)
        _log("crashing", generation=self.generation, reason="crash_after_ready")
        os._exit(137)

    async def _graceful_stop(self, delay_ms: int) -> None:
        await asyncio.sleep(delay_ms / 1000.0)
        _emit("SPIKE_STOPPING", {"generation": self.generation, "pid": self.pid})
        if self._runner is not None:
            await self._runner.cleanup()
        os._exit(0)

    async def run(self) -> None:
        try:
            await self._bind()
            await self._init()
            # Block forever; shutdown via /shutdown, SIGTERM, or crash.
            await asyncio.Event().wait()
        except SystemExit:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            _emit("SPIKE_FAILED", {"reason": "exception", "error": str(exc), "generation": self.generation})
            raise


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    def handler(signum, _frame):
        _log("signal", signum=signum)
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, handler)


def main() -> None:
    svc = FakeService()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)

    async def _main():
        await svc.run()
        # svc.run blocks; if it returns (e.g. signal set stop_event is not wired
        # into run), exit 0. In practice we exit via _graceful_stop or signals.

    try:
        loop.run_until_complete(_main())
    except SystemExit as exc:
        _log("exit", code=exc.code)
        raise
    finally:
        loop.close()


if __name__ == "__main__":
    main()
