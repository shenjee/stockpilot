#!/usr/bin/env python3
"""#177 Live/Replay service smoke: real backend, no Electron GUI.

Starts ``apps/t0-assistant/backend/service.py`` the same way Electron's
PythonServiceHost does, then select_security / get_live_snapshot /
begin_replay / get_replay_snapshot. Asserts 5m and 30m engine_version.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SERVICE = REPO / "apps" / "t0-assistant" / "backend" / "service.py"
OUT = Path(__file__).resolve().parent / "ui-smoke"
EXPECTED = "1.0.1"
SYMBOL = "sh.600584"
REPLAY_DATE = "2026-07-14"
PYTHON = Path.home() / ".venvs" / "czsc" / "bin" / "python"
RUNTIME_DIR = (
    Path.home()
    / "Library"
    / "Application Support"
    / "stockpilot-t0-assistant"
    / "stockpilot"
)


def git_sha() -> str:
    return subprocess.check_output(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True
    ).strip()


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def post(url: str, token: str, payload: dict, timeout: int = 180) -> dict:
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.load(exc)
        except Exception:
            detail = {"raw": exc.read().decode("utf-8", errors="replace")}
        detail["_http_status"] = exc.code
        return detail


def wait_health(port: int, token: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/health",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(request, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(0.2)
    raise RuntimeError(f"service health timeout: {last_error}")


def snapshot_versions(snapshot: dict) -> dict:
    chan_5m = snapshot.get("chan_analysis") or {}
    chan_30m = snapshot.get("chan_analysis_30m") or {}
    return {
        "5m": chan_5m.get("engine_version"),
        "30m": chan_30m.get("engine_version"),
        "symbol": snapshot.get("session", {}).get("symbol")
        or snapshot.get("symbol"),
        "session_id": (snapshot.get("session") or {}).get("session_id"),
    }


def main() -> int:
    if Path(sys.executable).resolve() != PYTHON.resolve():
        print(f"refusing {sys.executable}; use {PYTHON}", file=sys.stderr)
        return 2

    port = free_port()
    token = secrets.token_hex(32)
    env = os.environ.copy()
    env["T0_SERVICE_TOKEN"] = token
    env["T0_RUNTIME_DIR"] = str(RUNTIME_DIR)
    env["T0_PYTHON"] = str(PYTHON)

    proc = subprocess.Popen(
        [
            str(PYTHON),
            str(SERVICE),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--service-generation",
            "1",
        ],
        cwd=str(SERVICE.parent),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    report: dict = {
        "kind": "t0_service_smoke",
        "task": "#177",
        "git_sha": git_sha(),
        "python": str(PYTHON),
        "port": port,
        "runtime_dir": str(RUNTIME_DIR),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "no_real_trades": True,
        "errors": [],
    }
    try:
        wait_health(port, token)
        report["python_service_ready"] = True

        selected = post(
            f"http://127.0.0.1:{port}/api/commands/select_security",
            token,
            {
                "schema_version": "t0_app_v2",
                "request_id": "177-select",
                "command": "select_security",
                "session_id": None,
                "payload": {"symbol": SYMBOL},
            },
        )
        report["select_security"] = {
            "accepted": selected.get("accepted"),
            "error": selected.get("error"),
        }
        data = selected.get("data") or {}
        session_id = data.get("session_id") or (data.get("session") or {}).get("session_id")
        report["select_security"] = {
            "accepted": selected.get("accepted"),
            "error": selected.get("error"),
            "http_status": selected.get("_http_status"),
            "session_id": session_id,
            "data_keys": sorted(data.keys()) if isinstance(data, dict) else None,
        }
        if not session_id:
            report["errors"].append(f"select_security missing session_id: {selected}")
            return 1

        live_versions = None
        deadline = time.time() + 120
        last_live = None
        while time.time() < deadline:
            live = post(
                f"http://127.0.0.1:{port}/api/commands/get_live_snapshot",
                token,
                {
                    "schema_version": "t0_app_v2",
                    "request_id": "177-live",
                    "command": "get_live_snapshot",
                    "session_id": session_id,
                    "payload": {},
                },
            )
            last_live = live
            live_snap = live.get("data") or {}
            live_versions = snapshot_versions(live_snap)
            if live_versions["5m"] and live_versions["30m"]:
                break
            time.sleep(1.0)
        report["live"] = {
            "versions": live_versions,
            "accepted": (last_live or {}).get("accepted"),
            "http_status": (last_live or {}).get("_http_status"),
            "error": (last_live or {}).get("error"),
        }

        replay = post(
            f"http://127.0.0.1:{port}/api/commands/begin_replay",
            token,
            {
                "schema_version": "t0_replay_v2",
                "request_id": "177-begin-replay",
                "symbol": SYMBOL,
                "trade_date": REPLAY_DATE,
            },
            timeout=180,
        )
        replay_session = replay.get("session_id") or (replay.get("payload") or {}).get(
            "session_id"
        )
        report["begin_replay"] = {
            "keys": sorted(replay.keys()),
            "session_id": replay_session,
            "error_code": replay.get("error_code"),
        }
        if not replay_session:
            report["errors"].append(f"begin_replay missing session_id: {replay}")
        else:
            replay_snap_resp = post(
                f"http://127.0.0.1:{port}/api/commands/get_replay_snapshot",
                token,
                {
                    "schema_version": "t0_replay_v2",
                    "request_id": "177-replay-snap",
                    "session_id": replay_session,
                },
                timeout=60,
            )
            snapshot = (
                replay_snap_resp.get("snapshot")
                or (replay_snap_resp.get("payload") or {}).get("snapshot")
                or replay_snap_resp.get("data")
                or {}
            )
            replay_versions = snapshot_versions(snapshot)
            report["replay"] = {"versions": replay_versions}

        errors = []
        for surface, payload in (
            ("live", report.get("live") or {}),
            ("replay", report.get("replay") or {}),
        ):
            versions = payload.get("versions") or {}
            for tf in ("5m", "30m"):
                if versions.get(tf) != EXPECTED:
                    errors.append(f"{surface} {tf}={versions.get(tf)!r}")
        report["errors"] = errors
        report["ok"] = not errors
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        return 0 if report["ok"] else 1
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(repr(exc))
        report["ok"] = False
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        return 1
    finally:
        proc.terminate()
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
        report["service_stdout_tail"] = (stdout or "")[-2000:]
        report["service_stderr_tail"] = (stderr or "")[-4000:]
        OUT.mkdir(parents=True, exist_ok=True)
        path = OUT / "t0_service_smoke.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
