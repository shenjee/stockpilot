#!/usr/bin/env python3
"""Reproducible old-engine (czsc 0.10.12) baseline generator for #173.

This script freezes fixed inputs and complete analysis outputs from the
**production code path** (``packages.chantheory.analyze``), which uses the
pure-Python ``czsc.py.objects.RawBar`` / ``czsc.py.analyze.CZSC`` entry —
the same path ``engine.py`` selects at runtime.

Outputs (written next to this script):
  - ``inputs/*.json``          — fixed experiment inputs (OHLCV rows)
  - ``outputs/*.json``         — complete AnalysisResult JSON per scenario
  - ``checksums.sha256``       — SHA-256 of every input and output file
  - ``repro-log.txt``          — generation log with engine version, env, timestamps

Usage (from repo root, prod env ``~/.venvs/czsc``):

    source ~/.venvs/czsc/bin/activate
    python spikes/0009-czsc-1.0.1-upgrade/baseline/generate_baseline.py

Re-run to verify reproducibility: outputs must be byte-identical and
checksums must match.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

# Ensure repo root is on sys.path so packages.chantheory is importable.
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from packages.chantheory import analyze  # noqa: E402
from packages.chantheory.config import (  # noqa: E402
    DEFAULT_PARAMETERS,
    DEFAULT_SIGNALS_CONFIG,
    PINNED_ENGINE_VERSION,
    get_default_max_bi_num,
)
from packages.chantheory.engine import load_czsc  # noqa: E402

BASELINE_DIR = Path(__file__).resolve().parent
INPUTS_DIR = BASELINE_DIR / "inputs"
OUTPUTS_DIR = BASELINE_DIR / "outputs"


# ---------------------------------------------------------------------------
# Fixed input data generators
# ---------------------------------------------------------------------------

def _make_daily_input() -> list[dict]:
    """Fixed synthetic daily bars (seed=42, 120 bars, 000001.SZ).

    Deterministic: uses ``random.seed(42)`` with a fixed starting price and
    date. No network dependency. Produces enough fractals/strokes for a
    meaningful structure baseline.
    """
    import random

    random.seed(42)
    bars: list[dict] = []
    price = 10.0
    dt = datetime(2024, 1, 1)
    for i in range(120):
        o = price
        c = o + random.uniform(-0.5, 0.5)
        h = max(o, c) + random.uniform(0, 0.3)
        lo = min(o, c) - random.uniform(0, 0.3)
        bars.append(
            {
                "date": dt.strftime("%Y-%m-%d"),
                "open": round(o, 4),
                "close": round(c, 4),
                "high": round(h, 4),
                "low": round(lo, 4),
                "volume": 1000 + i * 10,
            }
        )
        price = c
        from datetime import timedelta

        dt += timedelta(days=1)
        while dt.weekday() >= 5:
            dt += timedelta(days=1)
    return bars


def _make_5m_input() -> list[dict]:
    """Fixed real 5-minute bars (600584.SH, 548 bars).

    Sourced from ``spikes/0008-.../fixtures/a_share_5m_548.json`` — a
    frozen real-data sample already in the repository. We copy the rows
    into the baseline inputs directory so the baseline is self-contained.
    """
    src = REPO_ROOT / "spikes/0008-czsc-update-and-rebuild-strategy/fixtures/a_share_5m_548.json"
    with open(src, encoding="utf-8") as f:
        payload = json.load(f)
    bars = payload["bars"] if isinstance(payload, dict) and "bars" in payload else payload
    # Normalise to the OHLCV row schema expected by analyze().
    normalised: list[dict] = []
    for b in bars:
        normalised.append(
            {
                "date": b["timestamp"],
                "open": b["open"],
                "close": b["close"],
                "high": b["high"],
                "low": b["low"],
                "volume": b["volume"],
                "amount": b.get("amount"),
            }
        )
    return normalised


def _make_30m_input() -> list[dict]:
    """Fixed real 30-minute bars (600584.SH, ~1336 bars).

    Fetched once from the Tencent mkline API and frozen. The fetch is
    deterministic given the same market state snapshot; the frozen JSON is
    the authoritative input. If the file already exists we reuse it rather
    than re-fetching, ensuring reproducibility.

    Uses the full available history (2026-01-01..2026-09-09) so all four
    default signals trigger at least once, satisfying the #173 DoD
    requirement for 'four default signals each triggered/untriggered'.
    """
    frozen = INPUTS_DIR / "30m_600584_sh_rows.json"
    if frozen.exists():
        with open(frozen, encoding="utf-8") as f:
            return json.load(f)

    # Fetch once and freeze. This requires network access on first run only.
    from packages.marketdata import TencentStockDataProvider

    rows = TencentStockDataProvider.get_minute_kline(
        "600584", "2026-01-01", "2026-09-09", ktype="30m", market="sh"
    )
    normalised: list[dict] = []
    for r in rows:
        normalised.append(
            {
                "date": r["timestamp"],
                "open": r["open"],
                "close": r["close"],
                "high": r["high"],
                "low": r["low"],
                "volume": r["volume"],
                "amount": r.get("amount"),
            }
        )
    with open(frozen, "w", encoding="utf-8") as f:
        json.dump(normalised, f, ensure_ascii=False, indent=2)
    return normalised


# ---------------------------------------------------------------------------
# Scenario definitions
# ---------------------------------------------------------------------------

SCENARIOS: list[dict] = [
    {
        "id": "daily_synthetic_120",
        "timeframe": "day",
        "symbol": "000001.SZ",
        "source": "synthetic",
        "input_file": "daily_synthetic_120_rows.json",
        "make_rows": _make_daily_input,
        "description": "Synthetic daily bars, seed=42, 120 bars. Structure + timestamp baseline.",
    },
    {
        "id": "5m_real_600584_548",
        "timeframe": "5m",
        "symbol": "600584.SH",
        "source": "tencent",
        "input_file": "5m_real_600584_548_rows.json",
        "make_rows": _make_5m_input,
        "description": "Real 5-minute bars, 600584.SH, 548 bars. Structure + timestamp baseline.",
    },
    {
        "id": "30m_real_600584",
        "timeframe": "30m",
        "symbol": "600584.SH",
        "source": "tencent",
        "input_file": "30m_600584_sh_rows.json",
        "make_rows": _make_30m_input,
        "description": "Real 30-minute bars, 600584.SH. Structure + timestamp baseline.",
    },
]


# ---------------------------------------------------------------------------
# Engine probe (records actual types and env)
# ---------------------------------------------------------------------------

def _probe_engine() -> dict:
    """Record the actual RawBar/CZSC types loaded by engine.py."""
    import importlib

    czsc = importlib.import_module("czsc")
    RawBar, Freq, CZSC = load_czsc()
    return {
        "czsc_version": getattr(czsc, "__version__", "unknown"),
        "RawBar_module": getattr(RawBar, "__module__", str(RawBar)),
        "CZSC_module": getattr(CZSC, "__module__", str(CZSC)),
        "Freq_module": getattr(Freq, "__module__", str(Freq)),
        "czsc_min_bi_len_env": os.environ.get("czsc_min_bi_len", "<unset>"),
        "python_version": sys.version,
        "PINNED_ENGINE_VERSION": PINNED_ENGINE_VERSION,
    }


# ---------------------------------------------------------------------------
# Signal-triggered scenario helpers
# ---------------------------------------------------------------------------

def _run_signal_probe(rows: list[dict], symbol: str, timeframe: str) -> dict:
    """Run the full analyze() pipeline with default signals and record
    per-signal triggered/untriggered status at the final bar.

    This produces the 'four default signals each triggered/untriggered'
    evidence required by #173 DoD.
    """
    result = analyze(
        rows=rows,
        symbol=symbol,
        timeframe=timeframe,
        source="tencent",
        parameters=None,
        signals_config=list(DEFAULT_SIGNALS_CONFIG),
        strict=True,
    )
    signal_status: dict[str, dict] = {}
    for series in result.signal_series:
        signal_status[series.signal_key] = {
            "signal_name": series.signal_name,
            "latest_value": series.latest_value,
            "latest_timestamp": series.latest_timestamp,
            "point_count": len(series.points),
            "active_points": sum(1 for p in series.points if p.active),
        }
    return {
        "signal_status": signal_status,
        "warnings": [w.message for w in result.warnings],
        "bar_count": result.meta.get("bar_count", 0),
        "fractal_count": len(result.fractals),
        "stroke_count": len(result.strokes),
    }


# ---------------------------------------------------------------------------
# Main generation logic
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    log_lines: list[str] = []
    log_lines.append(f"Baseline generation started: {datetime.now().isoformat()}")
    probe = _probe_engine()
    log_lines.append(f"Engine probe: {json.dumps(probe, indent=2)}")

    checksums: list[str] = []

    for scenario in SCENARIOS:
        sid = scenario["id"]
        log_lines.append(f"\n--- Scenario: {sid} ---")
        log_lines.append(f"Description: {scenario['description']}")

        # Generate / load fixed input
        rows = scenario["make_rows"]()
        input_path = INPUTS_DIR / scenario["input_file"]
        with open(input_path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        log_lines.append(f"Input written: {input_path.relative_to(BASELINE_DIR)} ({len(rows)} bars)")

        # Run full analyze() pipeline (production code path)
        max_bi_num = get_default_max_bi_num(scenario["timeframe"])
        parameters = dict(DEFAULT_PARAMETERS)
        parameters["max_bi_num"] = max_bi_num

        try:
            result = analyze(
                rows=rows,
                symbol=scenario["symbol"],
                timeframe=scenario["timeframe"],
                source=scenario["source"],
                parameters=parameters,
                signals_config=list(DEFAULT_SIGNALS_CONFIG),
                strict=True,
            )
        except Exception:
            log_lines.append(f"ERROR: analyze() failed for {sid}:\n{traceback.format_exc()}")
            continue

        # Serialise complete AnalysisResult
        output_path = OUTPUTS_DIR / f"{sid}_result.json"
        result_json = json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(result_json)
        log_lines.append(
            f"Output written: {output_path.relative_to(BASELINE_DIR)} "
            f"({len(result.fractals)} fractals, {len(result.strokes)} strokes, "
            f"{len(result.segments)} segments, {len(result.pivot_zones)} pivots)"
        )

        # Signal probe
        sig_probe = _run_signal_probe(rows, scenario["symbol"], scenario["timeframe"])
        log_lines.append(f"Signal probe: {json.dumps(sig_probe, indent=2)}")

        # Checksums
        for p in (input_path, output_path):
            digest = _sha256_file(p)
            rel = p.relative_to(BASELINE_DIR)
            checksums.append(f"{digest}  {rel}")
            log_lines.append(f"SHA-256 {rel}: {digest}")

    # Write checksums
    checksum_path = BASELINE_DIR / "checksums.sha256"
    checksums.sort()
    with open(checksum_path, "w", encoding="utf-8") as f:
        f.write("\n".join(checksums) + "\n")
    log_lines.append(f"\nChecksums written: {checksum_path.relative_to(BASELINE_DIR)} ({len(checksums)} entries)")

    # Write log
    log_path = BASELINE_DIR / "repro-log.txt"
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines) + "\n")

    print(f"Baseline generation complete. {len(checksums)} files checksummed.")
    print(f"Log: {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
