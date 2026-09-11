#!/usr/bin/env python3
"""Locate signal-replay wall time for #176 performance evidence.

Breaks ``build_signal_payloads`` on the frozen 5m sample into:
  - replay ``CZSC.update``
  - native ``call_signal`` / dispatcher
  - value extraction / string conversion
  - series / events / snapshots assembly
  - residual adapter bookkeeping

Goal: exclude obvious duplicate work or conversion waste in the adapter
before treating residual cost as upstream. No architecture change intended.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable
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
    utc_now,
)

from packages.chantheory import normalize_ohlcv_rows  # noqa: E402
from packages.chantheory.config import (  # noqa: E402
    DEFAULT_SIGNALS_CONFIG,
    get_default_max_bi_num,
    get_default_parameters,
)
from packages.chantheory.engine import run_engine  # noqa: E402
from packages.chantheory import signals as signals_mod  # noqa: E402
from packages.chantheory.structure_mapping import map_strokes  # noqa: E402


class _Bucket:
    __slots__ = ("ms", "calls")

    def __init__(self) -> None:
        self.ms = 0.0
        self.calls = 0

    def add(self, elapsed_ms: float) -> None:
        self.ms += elapsed_ms
        self.calls += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "ms": round(self.ms, 3),
            "calls": self.calls,
            "mean_us_per_call": round((self.ms * 1000.0) / self.calls, 3) if self.calls else None,
        }


def _timed(bucket: _Bucket, fn: Callable[..., Any]) -> Callable[..., Any]:
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        t0 = time.perf_counter_ns()
        try:
            return fn(*args, **kwargs)
        finally:
            bucket.add((time.perf_counter_ns() - t0) / 1_000_000)

    return wrapper


def _profile_once(rows: list, scenario: dict) -> dict[str, Any]:
    parameters = get_default_parameters()
    parameters["max_bi_num"] = get_default_max_bi_num(scenario["timeframe"])
    signals_config = list(DEFAULT_SIGNALS_CONFIG)

    normalized = normalize_ohlcv_rows(
        rows,
        symbol=scenario["symbol"],
        timeframe=scenario["timeframe"],
        source=scenario["source"],
        strict=True,
    )
    analyzer, raw_bars = run_engine(normalized, parameters)
    strokes = map_strokes(analyzer=analyzer)
    index_by_timestamp = {bar.timestamp: bar.bar_index for bar in normalized.bars}

    buckets = {
        "engine_update": _Bucket(),
        "dispatcher_call_signal": _Bucket(),
        "extract_signal_value": _Bucket(),
        "to_timestamp": _Bucket(),
        "build_series": _Bucket(),
        "build_events": _Bucket(),
        "build_snapshots": _Bucket(),
    }

    original_cls = type(analyzer)
    original_update = original_cls.update

    def timed_update(self: Any, *args: Any, **kwargs: Any) -> Any:
        t0 = time.perf_counter_ns()
        try:
            return original_update(self, *args, **kwargs)
        finally:
            buckets["engine_update"].add((time.perf_counter_ns() - t0) / 1_000_000)

    original_dispatcher_factory = signals_mod._get_default_dispatcher
    original_extract = signals_mod._extract_signal_value
    original_series = signals_mod.build_signal_series
    original_events = signals_mod.build_signal_events
    original_snapshots = signals_mod.build_signal_snapshots
    original_to_timestamp = signals_mod.to_timestamp

    def wrapping_dispatcher() -> Callable[..., Any]:
        real = original_dispatcher_factory()
        return _timed(buckets["dispatcher_call_signal"], real)

    t0 = time.perf_counter_ns()
    with patch.object(original_cls, "update", timed_update):
        with patch.object(signals_mod, "_get_default_dispatcher", wrapping_dispatcher):
            with patch.object(
                signals_mod,
                "_extract_signal_value",
                _timed(buckets["extract_signal_value"], original_extract),
            ):
                with patch.object(
                    signals_mod,
                    "to_timestamp",
                    _timed(buckets["to_timestamp"], original_to_timestamp),
                ):
                    with patch.object(
                        signals_mod,
                        "build_signal_series",
                        _timed(buckets["build_series"], original_series),
                    ):
                        with patch.object(
                            signals_mod,
                            "build_signal_events",
                            _timed(buckets["build_events"], original_events),
                        ):
                            with patch.object(
                                signals_mod,
                                "build_signal_snapshots",
                                _timed(buckets["build_snapshots"], original_snapshots),
                            ):
                                payloads = signals_mod.build_signal_payloads(
                                    strokes=strokes,
                                    analyzer=analyzer,
                                    index_by_timestamp=index_by_timestamp,
                                    signals_config=signals_config,
                                    raw_bars=raw_bars,
                                )
    total_ms = (time.perf_counter_ns() - t0) / 1_000_000

    accounted = sum(b.ms for b in buckets.values())
    residual_ms = max(0.0, total_ms - accounted)
    signal_defs = len(list(DEFAULT_SIGNALS_CONFIG))
    bar_count = len(raw_bars)
    expected_eval_calls = bar_count * signal_defs

    evaluations = payloads[0]
    return {
        "total_ms": round(total_ms, 3),
        "accounted_ms": round(accounted, 3),
        "residual_adapter_ms": round(residual_ms, 3),
        "residual_share": round(residual_ms / total_ms, 4) if total_ms else None,
        "buckets": {name: bucket.as_dict() for name, bucket in buckets.items()},
        "shares": {
            name: round(bucket.ms / total_ms, 4) if total_ms else None
            for name, bucket in buckets.items()
        },
        "bar_count": bar_count,
        "signal_definition_count": signal_defs,
        "expected_native_eval_calls": expected_eval_calls,
        "dispatcher_calls": buckets["dispatcher_call_signal"].calls,
        "engine_update_calls": buckets["engine_update"].calls,
        "evaluation_rows": len(evaluations),
        "notes": [
            "dispatcher share is upstream call_signal wall time under bar-by-bar replay.",
            "extract_signal_value is adapter conversion of list[Signal] → string.",
            "to_timestamp is adapter helper used per bar / pending bi endpoint.",
            "engine_update is CZSC.update during signal replay (same bars as full rebuild path).",
            "residual covers remaining Python loop / status mapping / bookkeeping.",
        ],
    }


def _summarize_rounds(rounds: list[dict[str, Any]]) -> dict[str, Any]:
    totals = [r["total_ms"] for r in rounds]
    dispatcher = [r["buckets"]["dispatcher_call_signal"]["ms"] for r in rounds]
    updates = [r["buckets"]["engine_update"]["ms"] for r in rounds]
    extract = [r["buckets"]["extract_signal_value"]["ms"] for r in rounds]
    to_ts = [r["buckets"]["to_timestamp"]["ms"] for r in rounds]
    residual = [r["residual_adapter_ms"] for r in rounds]
    post = [
        r["buckets"]["build_series"]["ms"]
        + r["buckets"]["build_events"]["ms"]
        + r["buckets"]["build_snapshots"]["ms"]
        for r in rounds
    ]

    def stats(values: list[float]) -> dict[str, float]:
        ordered = sorted(values)
        p95 = ordered[max(0, int(__import__("math").ceil(0.95 * len(ordered)) - 1))]
        return {
            "mean_ms": round(statistics.fmean(values), 3),
            "p50_ms": round(statistics.median(values), 3),
            "p95_ms": round(p95, 3),
            "min_ms": round(min(values), 3),
            "max_ms": round(max(values), 3),
        }

    mean_total = statistics.fmean(totals) or 1.0
    return {
        "rounds": len(rounds),
        "total": stats(totals),
        "dispatcher_call_signal": stats(dispatcher),
        "engine_update": stats(updates),
        "extract_signal_value": stats(extract),
        "to_timestamp": stats(to_ts),
        "post_process_series_events_snapshots": stats(post),
        "residual_adapter": stats(residual),
        "mean_share": {
            "dispatcher_call_signal": round(statistics.fmean(dispatcher) / mean_total, 4),
            "engine_update": round(statistics.fmean(updates) / mean_total, 4),
            "extract_signal_value": round(statistics.fmean(extract) / mean_total, 4),
            "to_timestamp": round(statistics.fmean(to_ts) / mean_total, 4),
            "post_process": round(statistics.fmean(post) / mean_total, 4),
            "residual_adapter": round(statistics.fmean(residual) / mean_total, 4),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="5m_real_600584_548")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=1)
    args = parser.parse_args()

    scenario = next(s for s in SCENARIOS if s["id"] == args.scenario)
    rows = load_json(INPUTS_DIR / scenario["input_file"])

    for _ in range(args.warmup):
        _profile_once(rows, scenario)

    rounds = [_profile_once(rows, scenario) for _ in range(args.rounds)]
    summary = _summarize_rounds(rounds)
    mean_share = summary["mean_share"]

    findings = {
        "duplicate_replay_detected": False,
        "adapter_conversion_dominant": mean_share["extract_signal_value"] >= 0.15,
        "to_timestamp_dominant": mean_share["to_timestamp"] >= 0.30,
        "dispatcher_dominant": mean_share["dispatcher_call_signal"] >= 0.45,
        "post_process_dominant": mean_share["post_process"] >= 0.20,
        "conclusion": "",
    }
    first = rounds[0]
    findings["duplicate_replay_detected"] = (
        first["dispatcher_calls"] != first["expected_native_eval_calls"]
    )
    if findings["duplicate_replay_detected"]:
        findings["conclusion"] = (
            "Dispatcher call count != bars * signal definitions; investigate "
            "duplicate evaluation before accepting upstream cost."
        )
    elif findings["to_timestamp_dominant"]:
        findings["conclusion"] = (
            "Signal-replay wall time is dominated by adapter to_timestamp helpers "
            "in the bar loop / pending-bi path (also confirmed by cProfile). "
            "Native call_signal and list[Signal]→string conversion are small. "
            "No duplicate bar×signal evaluation. Per acceptance guidance, do not "
            "refactor architecture to reclaim tens of ms; keep full rebuild."
        )
    elif findings["dispatcher_dominant"] and not findings["adapter_conversion_dominant"]:
        findings["conclusion"] = (
            "Signal-replay cost is dominated by native call_signal under bar-by-bar "
            "replay; adapter conversion and series/events assembly are secondary. "
            "No evidence of duplicate full analyze or heavy conversion waste that "
            "would justify adapter surgery for the observed tens of ms."
        )
    elif findings["adapter_conversion_dominant"]:
        findings["conclusion"] = (
            "Adapter value extraction/conversion is a material share; inspect "
            "_extract_signal_value before attributing all cost to upstream."
        )
    else:
        findings["conclusion"] = (
            "Cost is mixed across update + dispatcher + residual loops; no single "
            "adapter hot path exceeds the confirmation threshold for a local fix."
        )

    report = {
        "task": "#176",
        "kind": "signal_replay_profile",
        "generated_at_utc": utc_now(),
        "git_sha": git_sha(),
        "environment": environment_snapshot(),
        "scenario_id": scenario["id"],
        "warmup": args.warmup,
        "rounds": args.rounds,
        "summary": summary,
        "round_details": rounds,
        "findings": findings,
        "cprofile_artifact": "signal_replay_cprofile.txt",
        "gate_note": (
            "Evidence only; absolute 5m cost already accepted. Do not close #176 "
            "until UI smoke + #174/#175 gates clear. No architecture refactor for "
            "tens of ms."
        ),
    }
    out = ensure_artifacts_dir() / "signal_replay_profile.json"
    dump_json(out, report)
    print(f"Wrote {out}")
    print(f"total p95={summary['total']['p95_ms']} ms over {args.rounds} rounds")
    for key in (
        "dispatcher_call_signal",
        "engine_update",
        "extract_signal_value",
        "to_timestamp",
        "post_process_series_events_snapshots",
        "residual_adapter",
    ):
        item = summary[key]
        share_key = {
            "dispatcher_call_signal": "dispatcher_call_signal",
            "engine_update": "engine_update",
            "extract_signal_value": "extract_signal_value",
            "to_timestamp": "to_timestamp",
            "post_process_series_events_snapshots": "post_process",
            "residual_adapter": "residual_adapter",
        }[key]
        print(
            f"  {key}: mean={item['mean_ms']} p95={item['p95_ms']} "
            f"share={mean_share[share_key]}"
        )
    print(f"finding: {findings['conclusion']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
