"""Reproducible benchmark for ADR 0008 (stdlib-only harness)."""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
import tracemalloc
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from unittest.mock import patch

import czsc
import chantheory.adapters as adapters

from experiment import IncrementalExperiment, full_rebuild, rebuild_seek
from fixture import fixture_sha256, load_fixture, split_rows


def summarize(samples: list[float]) -> dict[str, float | int]:
    ordered = sorted(samples)
    p95_index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return {
        "samples": len(ordered),
        "median_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[p95_index], 3),
        "max_ms": round(max(ordered), 3),
    }


def measure(operation: Callable[[], object], samples: int) -> dict[str, float | int]:
    values: list[float] = []
    for _ in range(samples):
        started = time.perf_counter_ns()
        operation()
        values.append((time.perf_counter_ns() - started) / 1_000_000)
    return summarize(values)


def _elapsed_ms(operation: Callable[[], object]) -> float:
    started = time.perf_counter_ns()
    operation()
    return (time.perf_counter_ns() - started) / 1_000_000


def _load_average() -> list[float] | None:
    try:
        return [round(value, 3) for value in os.getloadavg()]
    except (AttributeError, OSError):
        return None


def prefix_sweeps(warm, target, sweep_count: int, prefixes: list[int], incremental: bool) -> dict[str, object]:
    rounds: list[dict[str, object]] = []
    by_prefix: dict[int, list[float]] = {prefix: [] for prefix in prefixes}
    all_values: list[float] = []

    for sweep in range(1, sweep_count + 1):
        round_started_at = datetime.now(timezone.utc).isoformat()
        round_started = time.perf_counter_ns()
        samples: list[dict[str, float | int]] = []
        incremental_analyzer = IncrementalExperiment(warm) if incremental else None
        prefix_set = set(prefixes)
        for prefix, row in enumerate(target, 1):
            if incremental:
                elapsed = _elapsed_ms(lambda current=row: incremental_analyzer.advance(current))
            elif prefix in prefix_set:
                elapsed = _elapsed_ms(lambda current=prefix: full_rebuild(warm + target[:current]))
            else:
                continue
            if prefix not in prefix_set:
                continue
            rounded = round(elapsed, 3)
            samples.append({"prefix": prefix, "bar_count": len(warm) + prefix, "elapsed_ms": rounded})
            by_prefix[prefix].append(elapsed)
            all_values.append(elapsed)
        rounds.append(
            {
                "sweep": sweep,
                "started_at_utc": round_started_at,
                "load_average": _load_average(),
                "duration_ms": round((time.perf_counter_ns() - round_started) / 1_000_000, 3),
                "summary": summarize([float(item["elapsed_ms"]) for item in samples]),
                "samples": samples,
            }
        )

    return {
        "sweeps": sweep_count,
        "prefixes_per_sweep": len(prefixes),
        "summary": summarize(all_values),
        "by_prefix": {str(prefix): summarize(values) for prefix, values in by_prefix.items()},
        "rounds": rounds,
    }


def stage_profile(rows) -> dict[str, float]:
    elapsed = {"engine_constructor": 0.0, "signal_replay": 0.0, "structure_mapping": 0.0, "plot_primitives": 0.0}

    def wrapper(name, original):
        def timed(*args, **kwargs):
            started = time.perf_counter_ns()
            try:
                return original(*args, **kwargs)
            finally:
                elapsed[name] += (time.perf_counter_ns() - started) / 1_000_000
        return timed

    structure_names = (
        "_map_fractals", "_map_strokes", "_map_pending_stroke", "derive_segments",
        "_map_pivot_zones", "_map_segment_pivot_zones", "_map_divergences",
        "_build_structure_alerts", "_build_mapping_warnings", "_build_candidate_point_events",
        "_build_candidate_points",
    )
    with ExitStack() as stack:
        stack.enter_context(patch.object(adapters, "_run_engine", wrapper("engine_constructor", adapters._run_engine)))
        stack.enter_context(patch.object(adapters, "_build_signal_payloads", wrapper("signal_replay", adapters._build_signal_payloads)))
        stack.enter_context(patch.object(adapters, "build_plot_primitives", wrapper("plot_primitives", adapters.build_plot_primitives)))
        for name in structure_names:
            stack.enter_context(patch.object(adapters, name, wrapper("structure_mapping", getattr(adapters, name))))
        started = time.perf_counter_ns()
        full_rebuild(rows)
        elapsed["analysis_result_total"] = (time.perf_counter_ns() - started) / 1_000_000
    elapsed["normalization_and_orchestration"] = max(
        0.0,
        elapsed["analysis_result_total"]
        - elapsed["engine_constructor"]
        - elapsed["signal_replay"]
        - elapsed["structure_mapping"]
        - elapsed["plot_primitives"],
    )
    return {key: round(value, 3) for key, value in elapsed.items()}


def environment() -> dict[str, object]:
    try:
        cpu = subprocess.check_output(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        cpu = platform.processor() or "unknown"
    return {
        "python": platform.python_version(),
        "python_executable": sys.executable,
        "czsc": czsc.__version__,
        "os": platform.platform(),
        "machine": platform.machine(),
        "cpu": cpu,
        "import_included": False,
        "warmup_runs": 1,
        "clock": "time.perf_counter_ns",
        "cpu_count": os.cpu_count(),
        "load_average_at_environment_capture": _load_average(),
    }


def run_benchmarks(smoke: bool = False) -> dict[str, object]:
    payload = load_fixture()
    warm, target = split_rows(payload)
    repeat = 1 if smoke else 11
    seek_repeat = 1 if smoke else 9
    sweep_count = 1 if smoke else 5
    one_bar_prefixes = [1, 24, 48] if smoke else list(range(1, 49))

    cold_500 = measure(lambda: full_rebuild(warm), 1)
    full_rebuild(warm)  # explicit warm-up
    warm_500 = measure(lambda: full_rebuild(warm), repeat)
    full_prefix_sweeps = prefix_sweeps(
        warm, target, sweep_count=sweep_count, prefixes=one_bar_prefixes, incremental=False
    )
    incremental_prefix_sweeps = prefix_sweeps(
        warm, target, sweep_count=sweep_count, prefixes=one_bar_prefixes, incremental=True
    )

    t1 = 16
    backward_seek = measure(lambda: rebuild_seek(warm, target, t1), seek_repeat)

    tracemalloc.start()
    full_rebuild(warm + target)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    return {
        "fixture": {
            "sha256": fixture_sha256(payload),
            "warm_bars": len(warm),
            "target_bars": len(target),
            "timestamp_semantics": payload["identity"]["timestamp_semantics"],
        },
        "environment": environment(),
        "measurements": {
            "cold_500_full_rebuild": cold_500,
            "warm_500_full_rebuild": warm_500,
            "one_bar_full_rebuild": full_prefix_sweeps,
            "forward_incremental_project_result": incremental_prefix_sweeps,
            "backward_seek_rebuild_t1_16": backward_seek,
            "stage_profile_548": stage_profile(warm + target),
            "peak_python_allocated_memory_bytes_548": peak,
        },
        "notes": [
            "Cold means the first analysis call after module import; interpreter and import time are excluded.",
            "Warm measurements follow one explicit unmeasured rebuild.",
            f"Full and incremental prefix results contain raw per-round samples from {sweep_count} sweep(s).",
            "The overall prefix summary mixes input lengths by design; by_prefix reports five repeated measurements at each fixed size.",
            "Incremental timing includes CZSC.update plus complete project mapping, signal replay, plot primitives, and AnalysisResult assembly.",
            "Memory is tracemalloc peak Python allocation, a reproducible proxy that excludes some native allocations.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--quiet", action="store_true", help="write output without also printing the full JSON")
    args = parser.parse_args()
    result = run_benchmarks(smoke=args.smoke)
    encoded = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    if not args.quiet:
        print(encoded, end="")


if __name__ == "__main__":
    main()
