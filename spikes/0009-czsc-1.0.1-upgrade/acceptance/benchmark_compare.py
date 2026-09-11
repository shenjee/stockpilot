#!/usr/bin/env python3
"""Performance compare: isolated czsc 0.10.12 (old code) vs current 1.0.1.

Measures on the same machine / frozen inputs:
  - full analyze()
  - signal-replay stage (patched)
  - engine constructor / one-bar CZSC.update incremental

Old side is launched via subprocess into an isolated venv + worktree so the
current formal ~/.venvs/czsc (1.0.1) is never downgraded.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import (  # noqa: E402
    INPUTS_DIR,
    SCENARIOS,
    dump_json,
    ensure_artifacts_dir,
    environment_snapshot,
    git_sha,
    load_json,
    measure_ms,
    utc_now,
)

DEFAULT_OLD_PYTHON = Path.home() / ".venvs" / "czsc01012-bench" / "bin" / "python"
DEFAULT_OLD_ROOT = Path.home().parent.joinpath("jishen/development/stockpilot-wt-01012")
# Prefer sibling worktree next to repo.
_CANDIDATE_OLD_ROOTS = [
    REPO_ROOT.parent / "stockpilot-wt-01012",
    Path("/Users/jishen/development/stockpilot-wt-01012"),
]


WORKER = r'''
import json, sys, time
from pathlib import Path
from unittest.mock import patch

repo = Path(sys.argv[1])
sys.path.insert(0, str(repo))
payload = json.loads(sys.stdin.read())
rows = payload["rows"]
symbol = payload["symbol"]
timeframe = payload["timeframe"]
source = payload["source"]
kind = payload["kind"]
samples = int(payload["samples"])
warmup = int(payload["warmup"])

from packages.chantheory import analyze, analyze_normalized, normalize_ohlcv_rows
from packages.chantheory import adapters
from packages.chantheory.config import DEFAULT_SIGNALS_CONFIG, get_default_max_bi_num, get_default_parameters
from packages.chantheory.engine import load_czsc, parse_dt, run_engine
from packages.chantheory.config import get_freq_name
import czsc

def summarize(values):
    values = sorted(values)
    import math, statistics
    p95 = values[max(0, math.ceil(0.95 * len(values)) - 1)]
    return {
        "samples": len(values),
        "p50_ms": round(statistics.median(values), 3),
        "p95_ms": round(p95, 3),
        "max_ms": round(max(values), 3),
        "mean_ms": round(statistics.fmean(values), 3),
    }

def measure(op):
    for _ in range(warmup):
        op()
    vals = []
    for _ in range(samples):
        t0 = time.perf_counter_ns()
        op()
        vals.append((time.perf_counter_ns() - t0) / 1_000_000)
    return {"warmup": warmup, "summary": summarize(vals), "samples_ms": [round(v, 3) for v in vals]}

parameters = get_default_parameters()
parameters["max_bi_num"] = get_default_max_bi_num(timeframe)
signals = list(DEFAULT_SIGNALS_CONFIG)

def full():
    return analyze(rows=rows, symbol=symbol, timeframe=timeframe, source=source,
                   parameters=parameters, signals_config=signals, strict=True)

out = {"czsc": czsc.__version__, "kind": kind}

if kind == "full_analyze":
    out["timing"] = measure(full)
elif kind == "signal_replay":
    # Time only _build_signal_payloads inside a single analyze-normalized path.
    normalized = normalize_ohlcv_rows(rows, symbol=symbol, timeframe=timeframe, source=source, strict=True)
    analyzer, raw_bars = run_engine(normalized, parameters)
    elapsed = {"signal_replay": 0.0}
    def wrapper(original):
        def timed(*a, **k):
            t0 = time.perf_counter_ns()
            try:
                return original(*a, **k)
            finally:
                elapsed["signal_replay"] += (time.perf_counter_ns() - t0) / 1_000_000
        return timed
    def one():
        elapsed["signal_replay"] = 0.0
        with patch.object(adapters, "_build_signal_payloads", wrapper(adapters._build_signal_payloads)):
            with patch.object(adapters, "_run_engine", return_value=(analyzer, list(raw_bars))):
                analyze_normalized(normalized, parameters=parameters, signals_config=signals)
        return elapsed["signal_replay"]
    for _ in range(warmup):
        one()
    vals = []
    for _ in range(samples):
        vals.append(one())
    out["timing"] = {"warmup": warmup, "summary": summarize(vals), "samples_ms": [round(v, 3) for v in vals]}
elif kind == "engine_update":
    RawBar, Freq, _ = load_czsc()
    warm_n = max(30, len(rows) - 1)
    warm = rows[:warm_n]
    last = rows[warm_n]
    def one():
        normalized = normalize_ohlcv_rows(warm, symbol=symbol, timeframe=timeframe, source=source, strict=True)
        analyzer, _ = run_engine(normalized, parameters)
        norm2 = normalize_ohlcv_rows(warm + [last], symbol=symbol, timeframe=timeframe, source=source, strict=True)
        bar = norm2.bars[-1]
        raw = RawBar(symbol=bar.symbol, id=bar.bar_index, dt=parse_dt(bar.timestamp),
                     freq=getattr(Freq, get_freq_name(bar.timeframe)),
                     open=bar.open, close=bar.close, high=bar.high, low=bar.low,
                     vol=bar.volume, amount=bar.amount)
        t0 = time.perf_counter_ns()
        analyzer.update(raw)
        return (time.perf_counter_ns() - t0) / 1_000_000
    for _ in range(warmup):
        one()
    vals = [one() for _ in range(samples)]
    out["timing"] = {"warmup": warmup, "summary": summarize(vals), "samples_ms": [round(v, 3) for v in vals]}
else:
    raise SystemExit(f"unknown kind {kind}")

print(json.dumps(out))
'''


def _old_root() -> Path:
    for candidate in _CANDIDATE_OLD_ROOTS:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "Old worktree not found. Create with: "
        "git worktree add ../stockpilot-wt-01012 main"
    )


def _run_worker(python: Path, repo: Path, payload: dict) -> dict:
    proc = subprocess.run(
        [str(python), "-c", WORKER, str(repo)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"worker failed ({python}):\n{proc.stderr}\n{proc.stdout}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _local_new_measure(kind: str, scenario: dict, rows: list, samples: int, warmup: int) -> dict:
    # Reuse worker logic in-process for current env.
    payload = {
        "rows": rows,
        "symbol": scenario["symbol"],
        "timeframe": scenario["timeframe"],
        "source": scenario["source"],
        "kind": kind,
        "samples": samples,
        "warmup": warmup,
    }
    return _run_worker(Path(sys.executable), REPO_ROOT, payload)


def _summarize_values(values: list[float]) -> dict:
    import math
    import statistics

    ordered = sorted(values)
    p95 = ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]
    return {
        "samples": len(values),
        "p50_ms": round(statistics.median(values), 3),
        "p95_ms": round(p95, 3),
        "max_ms": round(max(values), 3),
        "mean_ms": round(statistics.fmean(values), 3),
        "min_ms": round(min(values), 3),
        "stdev_ms": round(statistics.pstdev(values), 3) if len(values) > 1 else 0.0,
    }


def _aggregate_rounds(round_items: list[dict], side_key: str) -> dict:
    """Pool sample timings across rounds and summarize round-level p95 stability."""
    pooled: list[float] = []
    round_p95: list[float] = []
    round_mean: list[float] = []
    for item in round_items:
        timing = item[side_key]["timing"]
        pooled.extend(timing["samples_ms"])
        round_p95.append(timing["summary"]["p95_ms"])
        round_mean.append(timing["summary"]["mean_ms"])
    return {
        "pooled": _summarize_values(pooled),
        "round_p95_ms": [round(v, 3) for v in round_p95],
        "round_p95_summary": _summarize_values(round_p95),
        "round_mean_summary": _summarize_values(round_mean),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-python", type=Path, default=DEFAULT_OLD_PYTHON)
    parser.add_argument("--old-root", type=Path, default=None)
    parser.add_argument("--samples", type=int, default=9)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument(
        "--rounds",
        type=int,
        default=1,
        help="Independent measurement rounds (each with its own warmup+samples).",
    )
    parser.add_argument(
        "--multi-round",
        action="store_true",
        help="Convenience: rounds=5, warmup=2, samples=20 for stable p95 evidence.",
    )
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--scenario", action="append")
    parser.add_argument(
        "--output-name",
        default="performance_compare.json",
        help="Artifact filename under acceptance/artifacts/.",
    )
    args = parser.parse_args()

    if args.smoke:
        args.samples = 3
        args.warmup = 1
        args.rounds = 1
    elif args.multi_round:
        args.rounds = max(args.rounds, 5)
        args.warmup = max(args.warmup, 2)
        args.samples = max(args.samples, 20)
        if args.output_name == "performance_compare.json":
            args.output_name = "performance_compare_multi_round.json"

    old_root = args.old_root or _old_root()
    if not args.old_python.exists():
        raise FileNotFoundError(f"Old python not found: {args.old_python}")

    selected = [s for s in SCENARIOS if not args.scenario or s["id"] in args.scenario]
    # Prefer 5m for primary timing; still include day; 30m optional unless selected.
    if not args.scenario and args.smoke:
        selected = [s for s in SCENARIOS if s["id"] in {"daily_synthetic_120", "5m_real_600584_548"}]

    kinds = ("full_analyze", "signal_replay", "engine_update")
    results = []
    for scenario in selected:
        rows = load_json(INPUTS_DIR / scenario["input_file"])
        scenario_result = {
            "scenario_id": scenario["id"],
            "symbol": scenario["symbol"],
            "timeframe": scenario["timeframe"],
            "bar_count": len(rows),
            "measurements": {},
        }
        for kind in kinds:
            round_items = []
            for round_idx in range(args.rounds):
                payload = {
                    "rows": rows,
                    "symbol": scenario["symbol"],
                    "timeframe": scenario["timeframe"],
                    "source": scenario["source"],
                    "kind": kind,
                    "samples": args.samples,
                    "warmup": args.warmup,
                }
                old = _run_worker(args.old_python, old_root, payload)
                new = _run_worker(Path(sys.executable), REPO_ROOT, payload)
                old_p95 = old["timing"]["summary"]["p95_ms"]
                new_p95 = new["timing"]["summary"]["p95_ms"]
                rel = None if old_p95 == 0 else round((new_p95 - old_p95) / old_p95, 4)
                round_items.append(
                    {
                        "round": round_idx + 1,
                        "old_0_10_12": old,
                        "new_1_0_1": new,
                        "p95_delta_ms": round(new_p95 - old_p95, 3),
                        "p95_relative_change": rel,
                    }
                )

            # Primary table uses first round for backward-compatible single-round shape,
            # plus aggregate when rounds > 1.
            first = round_items[0]
            entry = {
                "old_0_10_12": first["old_0_10_12"],
                "new_1_0_1": first["new_1_0_1"],
                "p95_delta_ms": first["p95_delta_ms"],
                "p95_relative_change": first["p95_relative_change"],
            }
            if args.rounds > 1:
                old_agg = _aggregate_rounds(round_items, "old_0_10_12")
                new_agg = _aggregate_rounds(round_items, "new_1_0_1")
                pooled_old_p95 = old_agg["pooled"]["p95_ms"]
                pooled_new_p95 = new_agg["pooled"]["p95_ms"]
                pooled_rel = (
                    None
                    if pooled_old_p95 == 0
                    else round((pooled_new_p95 - pooled_old_p95) / pooled_old_p95, 4)
                )
                entry["rounds"] = round_items
                entry["aggregate"] = {
                    "old_0_10_12": old_agg,
                    "new_1_0_1": new_agg,
                    "pooled_p95_delta_ms": round(pooled_new_p95 - pooled_old_p95, 3),
                    "pooled_p95_relative_change": pooled_rel,
                    "round_p95_delta_ms": [
                        round(n - o, 3)
                        for o, n in zip(
                            old_agg["round_p95_ms"], new_agg["round_p95_ms"], strict=True
                        )
                    ],
                }
                # Prefer pooled multi-round stats as the headline numbers.
                entry["p95_delta_ms"] = entry["aggregate"]["pooled_p95_delta_ms"]
                entry["p95_relative_change"] = entry["aggregate"]["pooled_p95_relative_change"]
                entry["headline"] = {
                    "old_pooled_p95_ms": pooled_old_p95,
                    "new_pooled_p95_ms": pooled_new_p95,
                    "old_round_p95_range_ms": [
                        old_agg["round_p95_summary"]["min_ms"],
                        old_agg["round_p95_summary"]["max_ms"],
                    ],
                    "new_round_p95_range_ms": [
                        new_agg["round_p95_summary"]["min_ms"],
                        new_agg["round_p95_summary"]["max_ms"],
                    ],
                }
            scenario_result["measurements"][kind] = entry
        results.append(scenario_result)

    report = {
        "task": "#176",
        "kind": "performance_compare",
        "generated_at_utc": utc_now(),
        "git_sha": git_sha(),
        "environment_new": environment_snapshot(),
        "old_python": str(args.old_python),
        "old_root": str(old_root),
        "samples": args.samples,
        "warmup": args.warmup,
        "rounds": args.rounds,
        "historical_reference_only": "spike 0008 / #173 p95 ~188-195ms (not used as gate)",
        "scenarios": results,
        "gate_note": (
            "Absolute 5m cost accepted by user (2026-09-11) while multi-round + "
            "signal-replay profile evidence is required before closing #176. "
            "#174/#175 and manual UI smoke remain open gates."
        ),
    }
    out = ensure_artifacts_dir() / args.output_name
    dump_json(out, report)
    print(f"Wrote {out}")
    for scenario in results:
        print(f"== {scenario['scenario_id']} ==")
        for kind, item in scenario["measurements"].items():
            if "headline" in item:
                h = item["headline"]
                print(
                    f"  {kind}: pooled_old_p95={h['old_pooled_p95_ms']} "
                    f"pooled_new_p95={h['new_pooled_p95_ms']} "
                    f"delta={item['p95_delta_ms']} rel={item['p95_relative_change']} "
                    f"new_round_p95_range={h['new_round_p95_range_ms']}"
                )
            else:
                print(
                    f"  {kind}: old_p95={item['old_0_10_12']['timing']['summary']['p95_ms']} "
                    f"new_p95={item['new_1_0_1']['timing']['summary']['p95_ms']} "
                    f"delta={item['p95_delta_ms']} rel={item['p95_relative_change']}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
