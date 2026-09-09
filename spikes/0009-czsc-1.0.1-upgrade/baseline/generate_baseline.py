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

Generate (writes inputs/outputs/checksums/repro-log next to this script):

    python spikes/0009-czsc-1.0.1-upgrade/baseline/generate_baseline.py

Verify reproducibility (read-only: generates to a temp dir and compares
against the committed ``checksums.sha256``; returns non-zero on mismatch):

    python spikes/0009-czsc-1.0.1-upgrade/baseline/generate_baseline.py --verify

The generator refuses to run unless the installed czsc version equals the
hardcoded ``BASELINE_ENGINE_VERSION`` (0.10.12), independent of the project
pin. This prevents accidental baseline corruption from a new-version
environment, even after the project pin is updated to 1.0.1 (#177).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
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
COMMITTED_CHECKSUMS = BASELINE_DIR / "checksums.sha256"

# Hardcoded old-engine version this baseline was frozen against. This is
# intentionally NOT sourced from config.PINNED_ENGINE_VERSION, so that when
# the project pin is updated to 1.0.1 (#177), this generator still refuses
# to run in a 1.0.1 environment — protecting the old-engine baseline.
BASELINE_ENGINE_VERSION = "0.10.12"


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


def _make_30m_input(inputs_dir: Path | None = None) -> list[dict]:
    """Fixed real 30-minute bars (600584.SH, ~1336 bars).

    Fetched once from the Tencent mkline API and frozen. The frozen JSON in
    the committed ``inputs/`` directory is the authoritative input. This
    function **never re-fetches** — it only reads the frozen file. If the
    file is missing, it raises ``FileNotFoundError`` so the caller knows
    the committed baseline is incomplete.

    Uses the full available history (2026-01-01..2026-09-09) so all four
    default signals trigger at least once, satisfying the #173 DoD
    requirement for 'four default signals each triggered/untriggered'.
    """
    # Always read from the committed inputs directory, regardless of where
    # outputs are being generated (temp dir or committed dir). This ensures
    # --verify mode works offline and never re-fetches.
    frozen = INPUTS_DIR / "30m_600584_sh_rows.json"
    if not frozen.exists():
        raise FileNotFoundError(
            f"Frozen 30m input not found: {frozen}. The committed baseline "
            f"is incomplete. Do not re-fetch in verify mode."
        )
    with open(frozen, encoding="utf-8") as f:
        return json.load(f)


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


def _assert_engine_version(probe: dict) -> None:
    """Refuse to generate baselines if the installed czsc version does not
    match the hardcoded ``BASELINE_ENGINE_VERSION`` (0.10.12).

    This guard is intentionally independent of
    ``config.PINNED_ENGINE_VERSION``: when the project pin is updated to
    1.0.1 (#177), this generator must still refuse to run in a 1.0.1
    environment, protecting the old-engine baseline from being silently
    overwritten by new-version output.
    """
    installed = probe["czsc_version"]
    if installed != BASELINE_ENGINE_VERSION:
        raise RuntimeError(
            f"Engine version mismatch: installed czsc=={installed} but "
            f"this baseline requires czsc=={BASELINE_ENGINE_VERSION}. "
            f"Refusing to generate old-engine baselines from a "
            f"mismatched environment. Activate the prod env (~/.venvs/czsc) "
            f"and re-run."
        )


def _run_scenarios(
    outputs_dir: Path,
    log_lines: list[str],
    probe: dict,
) -> tuple[list[str], list[str]]:
    """Run all scenarios, writing outputs to ``outputs_dir``.

    Fixed inputs are **always** read from the committed ``INPUTS_DIR`` —
    never re-fetched. This ensures offline reproducibility.

    Returns ``(checksums, failures)``. On any scenario failure the
    function records the error and continues, but the caller must check
    ``failures`` and return non-zero if non-empty.
    """
    outputs_dir.mkdir(parents=True, exist_ok=True)

    checksums: list[str] = []
    failures: list[str] = []

    for scenario in SCENARIOS:
        sid = scenario["id"]
        log_lines.append(f"\n--- Scenario: {sid} ---")
        log_lines.append(f"Description: {scenario['description']}")

        # Load fixed input from committed INPUTS_DIR (never re-fetch).
        make_rows = scenario["make_rows"]
        import inspect

        sig = inspect.signature(make_rows)
        if len(sig.parameters) > 0:
            rows = make_rows(INPUTS_DIR)
        else:
            rows = make_rows()
        input_path = INPUTS_DIR / scenario["input_file"]
        if not input_path.exists():
            # Write the generated rows to the committed inputs dir (generate
            # mode only; verify mode will fail here if input is missing).
            with open(input_path, "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False, indent=2)
        log_lines.append(f"Input loaded: {scenario['input_file']} ({len(rows)} bars)")

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
            tb = traceback.format_exc()
            log_lines.append(f"ERROR: analyze() failed for {sid}:\n{tb}")
            failures.append(sid)
            continue

        # Serialise complete AnalysisResult to outputs_dir
        output_path = outputs_dir / f"{sid}_result.json"
        result_json = json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(result_json)
        log_lines.append(
            f"Output written: {sid}_result.json "
            f"({len(result.fractals)} fractals, {len(result.strokes)} strokes, "
            f"{len(result.segments)} segments, {len(result.pivot_zones)} pivots)"
        )

        # Signal probe
        try:
            sig_probe = _run_signal_probe(rows, scenario["symbol"], scenario["timeframe"])
            log_lines.append(f"Signal probe: {json.dumps(sig_probe, indent=2)}")
        except Exception:
            tb = traceback.format_exc()
            log_lines.append(f"ERROR: signal probe failed for {sid}:\n{tb}")
            failures.append(f"{sid} (signal_probe)")

        # Checksums (relative paths match committed layout: inputs/... outputs/...)
        # Input checksum is computed from the committed INPUTS_DIR path.
        # Output checksum is computed from the temp outputs path, but the
        # relative path uses the committed layout (outputs/<sid>_result.json).
        input_digest = _sha256_file(input_path)
        input_rel = input_path.relative_to(BASELINE_DIR)
        checksums.append(f"{input_digest}  {input_rel}")
        log_lines.append(f"SHA-256 {input_rel}: {input_digest}")

        output_digest = _sha256_file(output_path)
        output_rel = f"outputs/{output_path.name}"
        checksums.append(f"{output_digest}  {output_rel}")
        log_lines.append(f"SHA-256 {output_rel}: {output_digest}")

    checksums.sort()
    return checksums, failures


def _load_committed_checksums() -> dict[str, str]:
    """Load the committed ``checksums.sha256`` into a ``{rel_path: digest}``
    dict. Returns ``{}`` if the file does not exist.
    """
    if not COMMITTED_CHECKSUMS.exists():
        return {}
    result: dict[str, str] = {}
    with open(COMMITTED_CHECKSUMS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            digest, rel = parts
            result[rel] = digest
    return result


def _verify(temp_dir: Path, log_lines: list[str], probe: dict) -> int:
    """Read-only reproducibility verification.

    Generates baselines into ``temp_dir/outputs`` (inputs are always read
    from the committed ``INPUTS_DIR`` — never re-fetched), computes
    checksums, and compares against the committed ``checksums.sha256``.
    Returns ``0`` on exact match, non-zero on any mismatch. Does not
    modify any committed file.
    """
    temp_outputs = temp_dir / "outputs"

    checksums, failures = _run_scenarios(
        temp_outputs, log_lines, probe
    )

    if failures:
        log_lines.append(
            f"\nVERIFICATION FAILED: {len(failures)} scenario(s) failed: {failures}"
        )
        return 1

    committed = _load_committed_checksums()
    if not committed:
        log_lines.append("\nVERIFICATION FAILED: no committed checksums.sha256 found")
        return 1

    generated: dict[str, str] = {}
    for line in checksums:
        parts = line.split(None, 1)
        if len(parts) == 2:
            generated[parts[1]] = parts[0]

    log_lines.append(
        f"\n--- Verify: comparing {len(generated)} generated files against committed checksums ---"
    )

    mismatches: list[str] = []
    for rel, digest in sorted(generated.items()):
        committed_digest = committed.get(rel)
        if committed_digest is None:
            mismatches.append(f"  {rel}: NOT IN committed checksums")
        elif digest != committed_digest:
            mismatches.append(
                f"  {rel}: MISMATCH (generated={digest[:16]}..., committed={committed_digest[:16]}...)"
            )
        else:
            log_lines.append(f"  {rel}: OK")

    # Check for committed entries missing from generation
    for rel in sorted(committed):
        if rel not in generated:
            mismatches.append(f"  {rel}: in committed but NOT generated")

    if mismatches:
        log_lines.append(f"\nVERIFICATION FAILED: {len(mismatches)} mismatch(es):")
        log_lines.extend(mismatches)
        return 1

    log_lines.append(f"\nVERIFICATION PASSED: all {len(generated)} files match committed checksums")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reproducible old-engine (czsc 0.10.12) baseline generator for #173"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Read-only mode: generate to a temp dir and compare against committed "
        "checksums.sha256. Returns non-zero on mismatch. Does not modify committed files.",
    )
    args = parser.parse_args()

    log_lines: list[str] = []
    log_lines.append(f"Baseline generation started: {datetime.now().isoformat()}")
    probe = _probe_engine()
    log_lines.append(f"Engine probe: {json.dumps(probe, indent=2)}")

    # Engine version guard — refuse to run in a mismatched environment.
    try:
        _assert_engine_version(probe)
    except RuntimeError as exc:
        log_lines.append(f"FATAL: {exc}")
        print(f"FATAL: {exc}", file=sys.stderr)
        return 2

    if args.verify:
        with tempfile.TemporaryDirectory(prefix="czsc-baseline-verify-") as tmp:
            log_lines.append(f"Verify mode: generating outputs to temp dir {tmp}")
            log_lines.append(f"Inputs read from committed: {INPUTS_DIR.relative_to(BASELINE_DIR)}")
            exit_code = _verify(Path(tmp), log_lines, probe)
            print("\n".join(log_lines))
            return exit_code

    # --- Generate mode ---
    # Generate to a temp dir first; only copy to OUTPUTS_DIR and write
    # checksums if ALL scenarios succeed. This ensures partial failures
    # never overwrite committed gold-standard outputs.
    with tempfile.TemporaryDirectory(prefix="czsc-baseline-gen-") as tmp:
        temp_outputs = Path(tmp) / "outputs"
        log_lines.append(f"Generate mode: generating outputs to temp dir {tmp}")
        log_lines.append(f"Inputs read from committed: {INPUTS_DIR.relative_to(BASELINE_DIR)}")

        checksums, failures = _run_scenarios(temp_outputs, log_lines, probe)

        if failures:
            log_lines.append(
                f"\nGENERATION FAILED: {len(failures)} scenario(s) failed: {failures}. "
                f"Committed outputs NOT overwritten (all generation was in temp dir)."
            )
            log_path = BASELINE_DIR / "repro-log.txt"
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("\n".join(log_lines) + "\n")
            print(
                f"GENERATION FAILED: {len(failures)} scenario(s) failed: {failures}. "
                f"Committed files not overwritten. See {log_path}.",
                file=sys.stderr,
            )
            return 1

        # All scenarios succeeded — copy temp outputs to committed OUTPUTS_DIR.
        import shutil

        OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
        for scenario in SCENARIOS:
            sid = scenario["id"]
            src = temp_outputs / f"{sid}_result.json"
            dst = OUTPUTS_DIR / f"{sid}_result.json"
            shutil.copy2(src, dst)
            log_lines.append(f"Output committed: {dst.relative_to(BASELINE_DIR)}")

        # Write checksums (computed from committed paths in _run_scenarios).
        checksum_path = BASELINE_DIR / "checksums.sha256"
        with open(checksum_path, "w", encoding="utf-8") as f:
            f.write("\n".join(checksums) + "\n")
        log_lines.append(f"\nChecksums written: checksums.sha256 ({len(checksums)} entries)")

    log_path = BASELINE_DIR / "repro-log.txt"
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines) + "\n")

    print(f"Baseline generation complete. {len(checksums)} files checksummed.")
    print(f"Log: {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
