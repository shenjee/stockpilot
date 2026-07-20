"""Project-level semantic comparison for stable ``AnalysisResult`` payloads."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Mapping, Sequence


REQUIRED_RESULT_FIELDS = (
    "symbol",
    "timeframe",
    "source",
    "engine",
    "engine_version",
    "parameters",
    "fractals",
    "strokes",
    "segments",
    "pivot_zones",
    "divergences",
    "structure_alerts",
    "signal_series",
    "signal_events",
    "signal_snapshots",
    "candidate_point_events",
    "candidate_buy_points",
    "candidate_sell_points",
    "plot_primitives",
    "summary",
    "warnings",
    "meta",
)


def _plain(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if is_dataclass(value):
        return asdict(value)
    return value


def semantic_payload(result: Any, normalized: Any | None = None) -> dict[str, Any]:
    raw = _plain(result)
    if not isinstance(raw, Mapping):
        raise TypeError(f"analysis payload must be a mapping, got {type(raw).__name__}")
    missing = [field for field in REQUIRED_RESULT_FIELDS if field not in raw]
    if missing:
        raise ValueError(f"analysis payload misses required semantic fields: {missing}")
    unexpected = sorted(set(raw) - set(REQUIRED_RESULT_FIELDS))
    if unexpected:
        raise ValueError(f"analysis payload has unexpected semantic fields: {unexpected}")
    # Compare the complete result mapping. The explicit field-set checks above
    # make schema growth fail closed instead of silently weakening the oracle.
    payload = dict(raw)
    if normalized is not None:
        payload["normalized"] = {
            "symbol": normalized.symbol,
            "timeframe": normalized.timeframe,
            "source": normalized.source,
            "bars": [asdict(bar) for bar in normalized.bars],
            "warnings": [asdict(item) for item in normalized.warnings],
            "input_fields": list(normalized.input_fields),
            "gaps": list(normalized.gaps),
        }
    return payload


def semantic_diff(left: Any, right: Any, path: str = "$") -> list[dict[str, Any]]:
    left, right = _plain(left), _plain(right)
    if type(left) is not type(right):
        return [{"path": path, "left": left, "right": right, "reason": "type"}]
    if isinstance(left, Mapping):
        differences: list[dict[str, Any]] = []
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}"
            if key not in left or key not in right:
                differences.append({"path": child, "left": left.get(key), "right": right.get(key), "reason": "missing"})
            else:
                differences.extend(semantic_diff(left[key], right[key], child))
        return differences
    if isinstance(left, Sequence) and not isinstance(left, (str, bytes)):
        if len(left) != len(right):
            return [{"path": path, "left": len(left), "right": len(right), "reason": "length"}]
        differences: list[dict[str, Any]] = []
        for index, (a, b) in enumerate(zip(left, right)):
            differences.extend(semantic_diff(a, b, f"{path}[{index}]"))
        return differences
    if left != right:
        return [{"path": path, "left": left, "right": right, "reason": "value"}]
    return []


def compare_results(left: Any, right: Any, left_normalized: Any | None = None, right_normalized: Any | None = None) -> list[dict[str, Any]]:
    return semantic_diff(
        semantic_payload(left, left_normalized),
        semantic_payload(right, right_normalized),
    )
