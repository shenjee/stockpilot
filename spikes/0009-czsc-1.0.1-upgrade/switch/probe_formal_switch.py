#!/usr/bin/env python3
"""Post-merge #177 probe: formal venv + merged main + 1.0.1 provenance.

Must run with ``~/.venvs/czsc`` on the merged main tree. Refuses the
rollback worktree and any interpreter that is not the formal venv.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
EXPECTED_VERSION = "1.0.1"
FORMAL_VENV = Path.home() / ".venvs" / "czsc"
ROLLBACK_WORKTREE = Path.home() / "development" / "stockpilot-wt-01012"
OLD_SHA = "2883f34e956e49372f69c379b0ef45f8e63c929c"


def _fail(message: str, code: int = 2) -> int:
    print(message, file=sys.stderr)
    return code


def git_sha() -> str:
    return subprocess.check_output(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True
    ).strip()


def origin_main_sha() -> str:
    return subprocess.check_output(
        ["git", "-C", str(REPO), "rev-parse", "origin/main"], text=True
    ).strip()


def is_formal_venv() -> bool:
    formal = FORMAL_VENV.resolve()
    prefixes = {Path(sys.prefix).resolve(), Path(sys.exec_prefix).resolve()}
    if formal in prefixes:
        return True
    exe = Path(os.path.abspath(sys.executable))
    return formal in exe.parents


def pkg_ver(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def main() -> int:
    if not is_formal_venv():
        return _fail(
            f"refusing non-formal interpreter {sys.executable}; "
            f"activate {FORMAL_VENV}/bin/python"
        )
    sha = git_sha()
    main_sha = origin_main_sha()
    if sha != main_sha:
        return _fail(f"HEAD {sha} is not origin/main {main_sha}")
    if (ROLLBACK_WORKTREE / ".git").exists() or (ROLLBACK_WORKTREE / ".git").is_file():
        rollback_head = subprocess.check_output(
            ["git", "-C", str(ROLLBACK_WORKTREE), "rev-parse", "HEAD"], text=True
        ).strip()
        if rollback_head != OLD_SHA:
            print(
                f"warning: rollback worktree HEAD={rollback_head} (expected {OLD_SHA})",
                file=sys.stderr,
            )

    sys.path.insert(0, str(REPO))
    sys.path.insert(0, str(REPO / "packages"))

    import numpy.typing  # noqa: F401  # import-order shim
    import czsc
    from chantheory import analyze, analyze_multi_timeframe
    from chantheory.config import PINNED_ENGINE_VERSION
    from t0assistant.runtime.pipeline import _default_analyze_5m, _default_analyze_30m

    errors: list[str] = []
    if czsc.__version__ != EXPECTED_VERSION:
        errors.append(f"czsc.__version__={czsc.__version__!r}")
    if PINNED_ENGINE_VERSION != EXPECTED_VERSION:
        errors.append(f"PINNED_ENGINE_VERSION={PINNED_ENGINE_VERSION!r}")
    if pkg_ver("rs_czsc") is not None:
        errors.append("rs_czsc still installed")

    inputs = REPO / "spikes" / "0009-czsc-1.0.1-upgrade" / "baseline" / "inputs"
    rows_5m = json.loads((inputs / "5m_real_600584_548_rows.json").read_text())
    rows_30m = json.loads((inputs / "30m_600584_sh_rows.json").read_text())
    rows_day = json.loads((inputs / "daily_synthetic_120_rows.json").read_text())

    single_5m = analyze(rows=rows_5m, symbol="600584.SH", timeframe="5m", source="tencent")
    single_30m = analyze(rows=rows_30m, symbol="600584.SH", timeframe="30m", source="tencent")
    single_day = analyze(rows=rows_day, symbol="SYNTH.DAY", timeframe="day", source="synthetic")
    multi = analyze_multi_timeframe(
        rows_by_timeframe={"5m": rows_5m, "30m": rows_30m},
        symbol="600584.SH",
        base_timeframe="5m",
        source="tencent",
    )
    live_5m = _default_analyze_5m(rows_5m, "600584.SH")
    live_30m = _default_analyze_30m(rows_30m, "600584.SH")
    level_versions = {level.timeframe: level.analysis.engine_version for level in multi.levels}

    checks = {
        "analyze_5m": single_5m.engine_version,
        "analyze_30m": single_30m.engine_version,
        "analyze_day": single_day.engine_version,
        "analyze_multi_timeframe": multi.engine_version,
        "t0_pipeline_5m": live_5m.get("engine_version"),
        "t0_pipeline_30m": live_30m.get("engine_version"),
        **{f"multi_level_{tf}": ver for tf, ver in level_versions.items()},
    }
    for label, version in checks.items():
        if version != EXPECTED_VERSION:
            errors.append(f"{label} engine_version={version!r}")

    payload = {
        "ok": not errors,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "#177 post-merge formal switch probe",
        "git_sha": sha,
        "origin_main": main_sha,
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "prefix": sys.prefix,
        "platform": platform.platform(),
        "declared_pin": "czsc==1.0.1",
        "PINNED_ENGINE_VERSION": PINNED_ENGINE_VERSION,
        "czsc": czsc.__version__,
        "wbt": pkg_ver("wbt"),
        "rs_czsc": pkg_ver("rs_czsc"),
        "czsc__file__": czsc.__file__,
        "czsc__native": bool(getattr(czsc, "_native", None)),
        "chantheory__file__": sys.modules["chantheory"].__file__,
        "structure_counts": {
            "5m": {
                "fractals": len(single_5m.fractals),
                "strokes": len(single_5m.strokes),
            },
            "30m": {
                "fractals": len(single_30m.fractals),
                "strokes": len(single_30m.strokes),
            },
        },
        "engine_versions": checks,
        "errors": errors,
    }
    out = Path(__file__).resolve().parent / "formal-switch-probe.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
