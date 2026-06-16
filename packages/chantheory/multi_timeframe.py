"""Multi-timeframe aggregation for chantheory.

This module handles ordering timeframes, determining roles (base/higher/lower),
running per-timeframe analysis via an injected callable, and aggregating results
into a single ``MultiTimeframeAnalysisResult``.

It does **not** import from ``adapters.py`` — the single-timeframe analysis
capability is injected as a callable to avoid circular dependencies.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Mapping, Sequence

from .config import ENGINE_NAME, PINNED_ENGINE_VERSION, TIMEFRAME_ORDER
from .describe import build_multi_timeframe_summary
from .schema import (
    AnalysisResult,
    AnalysisWarning,
    MultiTimeframeAnalysisResult,
    MultiTimeframeLevel,
)
from .signals import normalize_signals_config


# ---------------------------------------------------------------------------
# Warning helper (local copy; will be consolidated later)
# ---------------------------------------------------------------------------

def _warning(warning_id: str, code: str, message: str, field: str) -> AnalysisWarning:
    return AnalysisWarning(
        id=warning_id,
        warning_code=code,
        severity="warning",
        message=message,
        field=field,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def analyze_multi_timeframe(
    rows_by_timeframe: Mapping[str, Iterable[Mapping[str, object]]],
    symbol: str,
    source: str,
    base_timeframe: str,
    parameters: Dict[str, Any] | None,
    signals_config: Sequence[object] | Mapping[str, object] | None,
    timeframe_order: Sequence[str] | None,
    analyze_one: Callable[[str, Iterable[Mapping[str, object]]], AnalysisResult],
) -> MultiTimeframeAnalysisResult:
    """Run multi-timeframe analysis by delegating single-timeframe work to *analyze_one*."""
    result = MultiTimeframeAnalysisResult(
        symbol=symbol,
        source=source,
        engine=ENGINE_NAME,
        engine_version=PINNED_ENGINE_VERSION,
        base_timeframe=base_timeframe,
        meta={
            "requested_base_timeframe": base_timeframe,
            "requested_timeframes": [],
            "available_timeframes": [],
            "higher_timeframes": [],
            "lower_timeframes": [],
            "level_count": 0,
            "roles": {},
            "signal_event_count": 0,
            "candidate_point_event_count": 0,
            "bar_count_by_timeframe": {},
            "signals_config": _summarize_multi_timeframe_signals_config(signals_config),
        },
    )

    if not isinstance(rows_by_timeframe, Mapping) or not rows_by_timeframe:
        result.warnings.append(
            _warning(
                warning_id="warning_multi_timeframe_input_empty",
                code="MULTI_TIMEFRAME_INPUT_EMPTY",
                message="No multi-timeframe row groups were provided.",
                field="rows_by_timeframe",
            )
        )
        result.summary = build_multi_timeframe_summary(result)
        return result

    available_timeframes = [str(timeframe) for timeframe in rows_by_timeframe.keys()]
    ordered_timeframes = order_multi_timeframes(
        available_timeframes=available_timeframes,
        base_timeframe=base_timeframe,
        timeframe_order=timeframe_order,
    )
    result.meta["requested_timeframes"] = list(ordered_timeframes)
    result.meta["available_timeframes"] = list(ordered_timeframes)

    if base_timeframe not in rows_by_timeframe:
        result.warnings.append(
            _warning(
                warning_id=f"warning_multi_timeframe_missing_base_{base_timeframe}",
                code="MULTI_TIMEFRAME_BASE_MISSING",
                message=f"Base timeframe `{base_timeframe}` is missing from the provided multi-timeframe inputs.",
                field="rows_by_timeframe",
            )
        )

    levels: List[MultiTimeframeLevel] = []
    aggregated_warnings = list(result.warnings)
    roles: Dict[str, str] = {}
    higher_timeframes: List[str] = []
    lower_timeframes: List[str] = []
    signal_event_count = 0
    candidate_point_event_count = 0
    bar_count_by_timeframe: Dict[str, int] = {}

    for timeframe in ordered_timeframes:
        role = multi_timeframe_role(timeframe=timeframe, base_timeframe=base_timeframe)
        roles[timeframe] = role
        if role == "higher":
            higher_timeframes.append(timeframe)
        elif role == "lower":
            lower_timeframes.append(timeframe)

        analysis = analyze_one(timeframe, rows_by_timeframe.get(timeframe, []))
        bar_count = int(analysis.meta.get("bar_count", 0))
        signal_event_count += len(analysis.signal_events)
        candidate_point_event_count += len(analysis.candidate_point_events)
        bar_count_by_timeframe[timeframe] = bar_count
        levels.append(
            MultiTimeframeLevel(
                timeframe=timeframe,
                role=role,
                bar_count=bar_count,
                analysis=analysis,
                meta={
                    "warning_count": len(analysis.warnings),
                    "signal_series_count": len(analysis.signal_series),
                    "signal_event_count": len(analysis.signal_events),
                    "candidate_point_event_count": len(analysis.candidate_point_events),
                },
            )
        )
        aggregated_warnings.extend(lift_multi_timeframe_warnings(timeframe=timeframe, role=role, warnings=analysis.warnings))

    result.timeframes = [level.timeframe for level in levels]
    result.levels = levels
    result.warnings = aggregated_warnings
    result.meta["higher_timeframes"] = higher_timeframes
    result.meta["lower_timeframes"] = lower_timeframes
    result.meta["level_count"] = len(levels)
    result.meta["roles"] = roles
    result.meta["signal_event_count"] = signal_event_count
    result.meta["candidate_point_event_count"] = candidate_point_event_count
    result.meta["bar_count_by_timeframe"] = bar_count_by_timeframe
    result.summary = build_multi_timeframe_summary(result)
    return result


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def order_multi_timeframes(
    available_timeframes: Sequence[str],
    base_timeframe: str,
    timeframe_order: Sequence[str] | None,
) -> List[str]:
    """Order timeframes with base first, then by rank."""
    ordered: List[str] = []
    seen: set[str] = set()

    def _push(timeframe: str) -> None:
        if timeframe not in seen and timeframe in available_timeframes:
            ordered.append(timeframe)
            seen.add(timeframe)

    _push(base_timeframe)
    if timeframe_order:
        for timeframe in timeframe_order:
            _push(str(timeframe))

    for timeframe in sorted(available_timeframes, key=lambda item: (TIMEFRAME_ORDER.get(item, 999), item)):
        _push(str(timeframe))

    return ordered


def multi_timeframe_role(timeframe: str, base_timeframe: str) -> str:
    """Determine the role of *timeframe* relative to *base_timeframe*."""
    if timeframe == base_timeframe:
        return "base"
    timeframe_rank = TIMEFRAME_ORDER.get(timeframe, 999)
    base_rank = TIMEFRAME_ORDER.get(base_timeframe, 999)
    if timeframe_rank > base_rank:
        return "higher"
    if timeframe_rank < base_rank:
        return "lower"
    return "peer"


def lift_multi_timeframe_warnings(
    timeframe: str,
    role: str,
    warnings: Sequence[AnalysisWarning],
) -> List[AnalysisWarning]:
    """Prefix warning IDs with multi-timeframe context."""
    lifted: List[AnalysisWarning] = []
    for warning in warnings:
        lifted.append(
            AnalysisWarning(
                id=f"multi_timeframe_{timeframe}_{warning.id}",
                warning_code=warning.warning_code,
                severity=warning.severity,
                message=warning.message,
                field=warning.field,
                meta={
                    **dict(warning.meta),
                    "timeframe": timeframe,
                    "role": role,
                },
            )
        )
    return lifted


def _summarize_multi_timeframe_signals_config(
    signals_config: Sequence[object] | Mapping[str, object] | None,
) -> Dict[str, Any]:
    try:
        normalized = normalize_signals_config(signals_config)
    except (TypeError, ValueError):
        return {"status": "invalid", "config": []}
    return {"status": "ok", "config": normalized}
