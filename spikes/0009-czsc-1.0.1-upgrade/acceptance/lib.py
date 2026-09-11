"""Shared helpers for #176 acceptance runs."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

SPIKE_DIR = Path(__file__).resolve().parents[1]
BASELINE_DIR = SPIKE_DIR / "baseline"
INPUTS_DIR = BASELINE_DIR / "inputs"
OUTPUTS_DIR = BASELINE_DIR / "outputs"
ARTIFACTS_DIR = SPIKE_DIR / "acceptance" / "artifacts"
REPO_ROOT = SPIKE_DIR.parents[1]

SCENARIOS = (
    {
        "id": "daily_synthetic_120",
        "timeframe": "day",
        "symbol": "000001.SZ",
        "source": "synthetic",
        "input_file": "daily_synthetic_120_rows.json",
        "baseline_output": "daily_synthetic_120_result.json",
        "synthetic": True,
    },
    {
        "id": "5m_real_600584_548",
        "timeframe": "5m",
        "symbol": "600584.SH",
        "source": "tencent",
        "input_file": "5m_real_600584_548_rows.json",
        "baseline_output": "5m_real_600584_548_result.json",
        "synthetic": False,
    },
    {
        "id": "30m_real_600584",
        "timeframe": "30m",
        "symbol": "600584.SH",
        "source": "tencent",
        "input_file": "30m_600584_sh_rows.json",
        "baseline_output": "30m_real_600584_result.json",
        "synthetic": False,
    },
)

# Expected metadata-only / documented-migration paths (not silent behavior drift).
EXPECTED_META_PATH_PREFIXES = (
    "$.engine_version",
    "$.meta.engine_probe",
    "$.meta.engine_version",
)

STRUCTURE_FIELDS = (
    "fractals",
    "strokes",
    "segments",
    "pivot_zones",
    "divergences",
)


def ensure_artifacts_dir() -> Path:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    return ARTIFACTS_DIR


def git_sha(repo_root: Path = REPO_ROOT) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def environment_snapshot() -> dict[str, Any]:
    try:
        cpu = subprocess.check_output(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        cpu = platform.processor() or "unknown"
    try:
        import czsc  # noqa: WPS433

        czsc_version = getattr(czsc, "__version__", "unknown")
    except Exception as exc:  # pragma: no cover - diagnostic only
        czsc_version = f"import_failed:{exc}"
    load_avg = None
    try:
        load_avg = [round(value, 3) for value in os.getloadavg()]
    except (AttributeError, OSError):
        load_avg = None
    return {
        "captured_at_utc": utc_now(),
        "git_sha": git_sha(),
        "python": platform.python_version(),
        "python_executable": sys.executable,
        "czsc": czsc_version,
        "os": platform.platform(),
        "machine": platform.machine(),
        "cpu": cpu,
        "cpu_count": os.cpu_count(),
        "load_average": load_avg,
        "formal_env_note": (
            "Formal ~/.venvs/czsc is already on czsc 1.0.1; this is an early "
            "environment deviation vs #172 switch order and must be closed by #177."
        ),
    }


def summarize_ms(samples: Sequence[float]) -> dict[str, float | int]:
    ordered = sorted(float(sample) for sample in samples)
    if not ordered:
        return {"samples": 0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0, "mean_ms": 0.0}
    p95_index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return {
        "samples": len(ordered),
        "p50_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[p95_index], 3),
        "max_ms": round(max(ordered), 3),
        "mean_ms": round(statistics.fmean(ordered), 3),
    }


def measure_ms(operation: Callable[[], object], samples: int, warmup: int = 1) -> dict[str, Any]:
    for _ in range(max(0, warmup)):
        operation()
    values: list[float] = []
    for _ in range(samples):
        started = time.perf_counter_ns()
        operation()
        values.append((time.perf_counter_ns() - started) / 1_000_000)
    return {
        "warmup": warmup,
        "summary": summarize_ms(values),
        "samples_ms": [round(value, 3) for value in values],
    }


def semantic_diff(left: Any, right: Any, path: str = "$") -> list[dict[str, Any]]:
    if type(left) is not type(right):
        return [{"path": path, "left": left, "right": right, "reason": "type"}]
    if isinstance(left, Mapping):
        differences: list[dict[str, Any]] = []
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}"
            if key not in left or key not in right:
                differences.append(
                    {"path": child, "left": left.get(key), "right": right.get(key), "reason": "missing"}
                )
            else:
                differences.extend(semantic_diff(left[key], right[key], child))
        return differences
    if isinstance(left, Sequence) and not isinstance(left, (str, bytes)):
        if len(left) != len(right):
            return [{"path": path, "left": len(left), "right": len(right), "reason": "length"}]
        differences = []
        for index, (item_left, item_right) in enumerate(zip(left, right)):
            differences.extend(semantic_diff(item_left, item_right, f"{path}[{index}]"))
        return differences
    if left != right:
        return [{"path": path, "left": left, "right": right, "reason": "value"}]
    return []


def classify_diff(path: str) -> str:
    """Classify a semantic diff path.

    ``migration_provenance`` covers expected #174/#175 metadata changes that do
    not alter structure geometry or signal active/event semantics.
    """
    if path.startswith("$.engine_version"):
        return "migration_provenance"
    if path.startswith("$.parameters.min_bi_len"):
        # Old baseline omitted explicit min_bi_len; 1.0.1 records the effective 6.
        return "migration_provenance"
    if path.startswith("$.meta.engine_assumptions") or path.startswith("$.meta.engine_probe"):
        return "migration_provenance"
    if "mapping_strategy" in path:
        return "migration_provenance"
    if path.endswith(".module") or ".module" in path:
        return "migration_provenance"
    if path.startswith("$.summary"):
        # Summary text embeds engine_version; treat as provenance unless counts diverge.
        return "migration_provenance"
    if path.startswith("$.warnings"):
        return "warnings"
    if any(path.startswith(f"$.{field}") for field in STRUCTURE_FIELDS):
        return "structure"
    if path.startswith("$.signal_"):
        return "signal"
    if path.startswith("$.candidate_"):
        return "candidate"
    if path.startswith("$.plot_primitives"):
        return "plot_primitives"
    return "other"


def strip_expected_migration_noise(diffs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for item in diffs:
        path = str(item.get("path", ""))
        category = classify_diff(path)
        enriched = dict(item)
        enriched["category"] = category
        kept.append(enriched)
    return kept


def count_by_category(diffs: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in diffs:
        category = str(item.get("category") or classify_diff(str(item.get("path", ""))))
        counts[category] = counts.get(category, 0) + 1
    return dict(sorted(counts.items()))


def structure_counts(payload: Mapping[str, Any]) -> dict[str, int]:
    return {
        "fractals": len(payload.get("fractals") or []),
        "strokes": len(payload.get("strokes") or []),
        "segments": len(payload.get("segments") or []),
        "pivot_zones": len(payload.get("pivot_zones") or []),
        "divergences": len(payload.get("divergences") or []),
        "signal_events": len(payload.get("signal_events") or []),
        "candidate_point_events": len(payload.get("candidate_point_events") or []),
        "plot_primitives": len(payload.get("plot_primitives") or []),
        "warnings": len(payload.get("warnings") or []),
    }


def signal_active_counts(payload: Mapping[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for series in payload.get("signal_series") or []:
        key = str(series.get("signal_key") or series.get("key") or "?")
        points = series.get("points") or []
        counts[key] = sum(1 for point in points if point.get("active"))
    return counts
