#!/usr/bin/env python3
"""Downstream automated smoke evidence for #176 (Live/Replay/chan-viewer).

Runs non-UI unittest suites that cover closed-bar-only analysis, replay seek,
and chan-viewer app contracts. Records commands, exit codes, and git SHA.
Manual UI checklist is emitted for evidence that still requires a human.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import dump_json, ensure_artifacts_dir, environment_snapshot, git_sha, utc_now  # noqa: E402

SUITES = (
    {
        "id": "live_dynamic_closed_bar",
        "surface": "Live",
        "command": [
            sys.executable,
            "-m",
            "unittest",
            "packages.t0assistant.tests.test_live_dynamic_five_minute",
        ],
    },
    {
        "id": "replay_e2e",
        "surface": "Replay",
        "command": [
            sys.executable,
            "-m",
            "unittest",
            "packages.t0assistant.tests.test_replay_e2e_acceptance",
        ],
    },
    {
        "id": "live_replay_lifecycle",
        "surface": "Live+Replay",
        "command": [
            sys.executable,
            "-m",
            "unittest",
            "packages.t0assistant.tests.test_live_replay_lifecycle_acceptance",
        ],
    },
    {
        "id": "chan_viewer_app",
        "surface": "chan-viewer",
        "command": [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "apps/chan-viewer/tests",
            "-p",
            "test_*.py",
        ],
    },
    {
        "id": "chantheory_5m_spike",
        "surface": "engine/replay-equivalence",
        "command": [
            sys.executable,
            "-m",
            "unittest",
            "packages.chantheory.tests.test_czsc_5m_spike",
        ],
    },
)


MANUAL_CHECKLIST = {
    "Live": [
        "npm start in apps/t0-assistant with T0_PYTHON=~/.venvs/czsc/bin/python",
        "Select 600584.SH; observe closed-bar structure/signal refresh",
        "Confirm dynamic unclosed K is display-only (not traded, not in CZSC input)",
        "Do not enter real trades",
        "Record start/end wall time and visible bar window",
    ],
    "Replay": [
        "Begin replay on a fixed day for 600584.SH (or documented fixture day)",
        "Step forward; observe candidate point trigger/switch/invalidate",
        "Seek backward; confirm rebuild equivalence visually",
        "Exit replay to Live",
    ],
    "chan-viewer": [
        "streamlit run apps/chan-viewer/app.py",
        "Load 600584 day/5m/30m (or frozen JSON via debug path if available)",
        "Verify fractal/stroke/segment/pivot/candidate overlays against structure",
        "Save screenshot or notes; synthetic day must be labeled synthetic",
    ],
}


def main() -> int:
    results = []
    overall_ok = True
    for suite in SUITES:
        proc = subprocess.run(
            suite["command"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        ok = proc.returncode == 0
        overall_ok = overall_ok and ok
        results.append(
            {
                "id": suite["id"],
                "surface": suite["surface"],
                "command": suite["command"],
                "returncode": proc.returncode,
                "pass": ok,
                "stdout_tail": "\n".join(proc.stdout.splitlines()[-40:]),
                "stderr_tail": "\n".join(proc.stderr.splitlines()[-40:]),
            }
        )
        print(f"[{'PASS' if ok else 'FAIL'}] {suite['id']} rc={proc.returncode}")

    payload = {
        "task": "#176",
        "kind": "downstream_smoke",
        "generated_at_utc": utc_now(),
        "git_sha": git_sha(),
        "environment": environment_snapshot(),
        "automated_suites": results,
        "manual_checklist_pending": MANUAL_CHECKLIST,
        "sample_policy": {
            "symbol": "600584.SH",
            "frozen_inputs": [
                "spikes/0009-czsc-1.0.1-upgrade/baseline/inputs/5m_real_600584_548_rows.json",
                "spikes/0009-czsc-1.0.1-upgrade/baseline/inputs/30m_600584_sh_rows.json",
                "spikes/0009-czsc-1.0.1-upgrade/baseline/inputs/daily_synthetic_120_rows.json",
            ],
            "note": "T0 UI does not load spike JSON directly; automated suites use their fixtures. Manual UI uses 600584.SH live/cache.",
        },
        "summary": {"all_automated_pass": overall_ok},
        "gate_note": "Automated evidence only; #176 still requires separate Live/Replay/chan-viewer manual notes or screenshots.",
    }
    out = ensure_artifacts_dir() / "downstream_smoke.json"
    dump_json(out, payload)
    print(f"Wrote {out}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
