#!/usr/bin/env python3
"""Run the three frozen inputs through the production ``analyze()`` path.

Used for both sides of the #205 comparison:

- 1.0.1 reference: run with the formal ``~/.venvs/czsc`` (read-only).
- 1.0.2 candidate: run with the isolated ``~/.venvs/czsc102-isolated``. The
  repository pin is still 1.0.1 at that point, so ``--override-pin`` rebinds
  the already-imported ``PINNED_ENGINE_VERSION`` names in-process only. No
  file is modified.

Parameters match ``test_upgrade_176_acceptance.py``: ``strict=True``,
``signals_config=list(DEFAULT_SIGNALS_CONFIG)``, minute timeframes use
``get_default_max_bi_num()``.

Usage (from repo root):

    python spikes/0010-czsc-1.0.2-upgrade/run_frozen.py --out <dir> [--override-pin]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

INPUTS = REPO_ROOT / "spikes" / "0009-czsc-1.0.1-upgrade" / "baseline" / "inputs"

SCENARIOS = (
    {"id": "daily_synthetic_120", "file": "daily_synthetic_120_rows.json", "symbol": "000001.SZ", "timeframe": "day", "source": "synthetic"},
    {"id": "5m_real_600584_548", "file": "5m_real_600584_548_rows.json", "symbol": "600584.SH", "timeframe": "5m", "source": "tencent"},
    {"id": "30m_real_600584", "file": "30m_600584_sh_rows.json", "symbol": "600584.SH", "timeframe": "30m", "source": "tencent"},
)

# Modules that import the pin by value and therefore need rebinding under
# ``--override-pin``. ``override_pin`` scans ``sys.modules`` for these names
# (canonical and short alias); this tuple is documentation and a sanity check.
PIN_BINDING_MODULES = (
    "packages.chantheory",
    "packages.chantheory.config",
    "packages.chantheory.engine",
    "packages.chantheory.adapters",
    "packages.chantheory.multi_timeframe",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001 - diagnostic only
        return "unknown"


def pkg_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _dist_info(name: str) -> dict | None:
    try:
        dist = metadata.distribution(name)
        return {"version": dist.version, "location": str(dist.locate_file(""))}
    except metadata.PackageNotFoundError:
        return None


def override_pin(installed: str) -> list[str]:
    """Rebind ``PINNED_ENGINE_VERSION`` on every loaded module that holds it.

    Scans ``sys.modules`` rather than a fixed list so that both the canonical
    ``packages.chantheory.*`` names and any ``chantheory.*`` alias entries are
    covered. Only names under the chantheory package are touched.
    """
    import packages.chantheory  # noqa: F401 - make sure the package is loaded
    import packages.chantheory.adapters  # noqa: F401
    import packages.chantheory.multi_timeframe  # noqa: F401

    prefixes = ("packages.chantheory", "chantheory")
    rebound = []
    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith(prefixes):
            continue
        if getattr(module, "PINNED_ENGINE_VERSION", None) is not None:
            module.PINNED_ENGINE_VERSION = installed
            rebound.append(module_name)
    return sorted(rebound)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="Output directory for result JSON files")
    parser.add_argument("--override-pin", action="store_true", help="Rebind PINNED_ENGINE_VERSION in-process to the installed version")
    args = parser.parse_args()

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Import order must match engine.py: numpy.typing before czsc.
    import numpy.typing  # noqa: F401
    import czsc

    installed = getattr(czsc, "__version__", None)
    if installed is None:
        print("czsc has no __version__; aborting", file=sys.stderr)
        return 2

    from packages.chantheory import analyze
    from packages.chantheory.config import DEFAULT_SIGNALS_CONFIG, PINNED_ENGINE_VERSION
    from packages.chantheory.engine import assert_engine_version

    pin_before_override = PINNED_ENGINE_VERSION
    rebound: list[str] = []
    if args.override_pin:
        rebound = override_pin(installed)
        print(f"override-pin: rebound PINNED_ENGINE_VERSION -> {installed} on {rebound}", flush=True)
        # Sanity check: no chantheory module may still hold the old pin.
        stale = [
            name for name, module in sys.modules.items()
            if name.startswith(("packages.chantheory", "chantheory"))
            and getattr(module, "PINNED_ENGINE_VERSION", None) not in (None, installed)
        ]
        if stale:
            print(f"override-pin failed; stale modules: {stale}", file=sys.stderr)
            return 2
        missing = [m for m in PIN_BINDING_MODULES if m not in rebound]
        if missing:
            print(f"override-pin warning: expected modules not rebound: {missing}", file=sys.stderr)

    checked = assert_engine_version()
    print(f"czsc installed={installed} pin(config)={pin_before_override} assert_engine_version={checked}", flush=True)

    env = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha(),
        "git_branch": subprocess.check_output(["git", "-C", str(REPO_ROOT), "rev-parse", "--abbrev-ref", "HEAD"], text=True).strip(),
        "python": platform.python_version(),
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "czsc_version": installed,
        "czsc_file": getattr(czsc, "__file__", None),
        "czsc_dist_info": _dist_info("czsc"),
        "pin_in_config_before_override": pin_before_override,
        "pin_override": bool(args.override_pin),
        "pin_rebound_modules": rebound,
        "urllib3": pkg_version("urllib3"),
        "wbt": pkg_version("wbt"),
        "numpy": pkg_version("numpy"),
        "pandas": pkg_version("pandas"),
        "env_czsc_min_bi_len": os.environ.get("czsc_min_bi_len"),
        "env_CZSC_MIN_BI_LEN": os.environ.get("CZSC_MIN_BI_LEN"),
        "env_czsc_max_bi_num": os.environ.get("czsc_max_bi_num"),
        "env_CZSC_MAX_BI_NUM": os.environ.get("CZSC_MAX_BI_NUM"),
    }

    results = {}
    for scenario in SCENARIOS:
        rows = json.loads((INPUTS / scenario["file"]).read_text(encoding="utf-8"))
        result = analyze(
            rows=rows,
            symbol=scenario["symbol"],
            timeframe=scenario["timeframe"],
            source=scenario["source"],
            signals_config=list(DEFAULT_SIGNALS_CONFIG),
            strict=True,
        )
        payload = result.to_dict()
        out_path = out_dir / f"{scenario['id']}.json"
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        counts = {
            "bars": len(rows),
            "fractals": len(payload["fractals"]),
            "strokes": len(payload["strokes"]),
            "segments": len(payload["segments"]),
            "pivot_zones": len(payload["pivot_zones"]),
            "divergences": len(payload["divergences"]),
            "signal_series": len(payload["signal_series"]),
            "signal_events": len(payload["signal_events"]),
            "signal_snapshots": len(payload["signal_snapshots"]),
            "candidate_buy_points": len(payload["candidate_buy_points"]),
            "candidate_sell_points": len(payload["candidate_sell_points"]),
            "plot_primitives": len(payload["plot_primitives"]),
        }
        results[scenario["id"]] = {
            "input_file": scenario["file"],
            "input_sha256": sha256_file(INPUTS / scenario["file"]),
            "output_file": out_path.name,
            "output_sha256": sha256_file(out_path),
            "engine_version": payload["engine_version"],
            "counts": counts,
            "warning_codes": sorted({item["warning_code"] for item in payload["warnings"]}),
        }
        print(f"{scenario['id']}: {counts}", flush=True)

    env["scenarios"] = results
    (out_dir / "run-manifest.json").write_text(json.dumps(env, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
