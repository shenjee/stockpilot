from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

from .config import ENGINE_NAME, PINNED_ENGINE_VERSION, get_default_parameters
from .describe import build_summary
from .engine import run_engine as _run_engine
from .normalize import NormalizationError, normalize_ohlcv_rows, normalize_tracker_klines
from .plotting import build_plot_primitives
from .segments import SEGMENT_MAPPING_STRATEGY, derive_segments
from .schema import (
    AnalysisResult,
    AnalysisWarning,
    MultiTimeframeAnalysisResult,
    NormalizationResult,
)
from .structure_mapping import (
    build_mapping_warnings as _build_mapping_warnings,
    build_structure_alerts as _build_structure_alerts,
    map_divergences as _map_divergences,
    map_fractals as _map_fractals,
    map_pending_stroke as _map_pending_stroke,
    map_pivot_zones as _map_pivot_zones,
    map_strokes as _map_strokes,
    safe_last_bi_extend as _safe_last_bi_extend,
)
from .signals import (
    build_signal_payloads as _build_signal_payloads,
    normalize_signals_config as _normalize_signals_config,
)
from .candidate_points import (
    build_candidate_point_events as _build_candidate_point_events,
    build_candidate_points as _build_candidate_points,
)
from .multi_timeframe import analyze_multi_timeframe as _analyze_multi_timeframe


def analyze(
    rows: Iterable[Mapping[str, object]],
    symbol: str,
    timeframe: str = "day",
    source: str = "unknown",
    parameters: Dict[str, Any] | None = None,
    signals_config: Sequence[object] | Mapping[str, object] | None = None,
    strict: bool = True,
) -> AnalysisResult:
    try:
        normalized = normalize_ohlcv_rows(
            rows=rows,
            symbol=symbol,
            timeframe=timeframe,
            source=source,
            strict=strict,
        )
    except NormalizationError as exc:
        return _frozen_result_after_normalization_failure(
            symbol=symbol,
            timeframe=timeframe,
            source=source,
            parameters=parameters,
            error=exc,
        )
    return analyze_normalized(normalized=normalized, parameters=parameters, signals_config=signals_config)


def analyze_multi_timeframe(
    rows_by_timeframe: Mapping[str, Iterable[Mapping[str, object]]],
    symbol: str,
    base_timeframe: str,
    source: str = "unknown",
    parameters: Dict[str, Any] | None = None,
    signals_config: Sequence[object] | Mapping[str, object] | None = None,
    strict: bool = True,
    timeframe_order: Sequence[str] | None = None,
) -> MultiTimeframeAnalysisResult:
    return _analyze_multi_timeframe(
        rows_by_timeframe=rows_by_timeframe,
        symbol=symbol,
        source=source,
        base_timeframe=base_timeframe,
        parameters=parameters,
        signals_config=signals_config,
        timeframe_order=timeframe_order,
        analyze_one=lambda timeframe, rows: analyze(
            rows=rows,
            symbol=symbol,
            timeframe=timeframe,
            source=source,
            parameters=parameters,
            signals_config=signals_config,
            strict=strict,
        ),
    )


def analyze_tracker_klines(
    rows: Iterable[Mapping[str, object]],
    code: str,
    market: str,
    timeframe: str = "day",
    source: str = "tencent",
    parameters: Dict[str, Any] | None = None,
    signals_config: Sequence[object] | Mapping[str, object] | None = None,
    strict: bool = True,
) -> AnalysisResult:
    try:
        normalized = normalize_tracker_klines(
            rows=rows,
            code=code,
            market=market,
            timeframe=timeframe,
            source=source,
            strict=strict,
        )
    except NormalizationError as exc:
        market_suffix = (market or "").upper()
        fallback_symbol = f"{code}.{market_suffix}" if market_suffix else code
        return _frozen_result_after_normalization_failure(
            symbol=fallback_symbol,
            timeframe=timeframe,
            source=source,
            parameters=parameters,
            error=exc,
        )
    return analyze_normalized(normalized=normalized, parameters=parameters, signals_config=signals_config)


def analyze_multi_timeframe_tracker_klines(
    rows_by_timeframe: Mapping[str, Iterable[Mapping[str, object]]],
    code: str,
    market: str,
    base_timeframe: str,
    source: str = "tencent",
    parameters: Dict[str, Any] | None = None,
    signals_config: Sequence[object] | Mapping[str, object] | None = None,
    strict: bool = True,
    timeframe_order: Sequence[str] | None = None,
) -> MultiTimeframeAnalysisResult:
    market_suffix = (market or "").upper()
    symbol = f"{code}.{market_suffix}" if market_suffix else code
    return _analyze_multi_timeframe(
        rows_by_timeframe=rows_by_timeframe,
        symbol=symbol,
        source=source,
        base_timeframe=base_timeframe,
        parameters=parameters,
        signals_config=signals_config,
        timeframe_order=timeframe_order,
        analyze_one=lambda timeframe, rows: analyze_tracker_klines(
            rows=rows,
            code=code,
            market=market,
            timeframe=timeframe,
            source=source,
            parameters=parameters,
            signals_config=signals_config,
            strict=strict,
        ),
    )


def analyze_normalized(
    normalized: NormalizationResult,
    parameters: Dict[str, Any] | None = None,
    signals_config: Sequence[object] | Mapping[str, object] | None = None,
) -> AnalysisResult:
    merged_parameters = get_default_parameters()
    if parameters:
        merged_parameters.update(parameters)

    result = AnalysisResult(
        symbol=normalized.symbol,
        timeframe=normalized.timeframe,
        source=normalized.source,
        engine=ENGINE_NAME,
        engine_version=PINNED_ENGINE_VERSION,
        parameters=merged_parameters,
        warnings=list(normalized.warnings),
        meta={
            "bar_count": len(normalized.bars),
            "input_fields": list(normalized.input_fields),
            "gaps": list(normalized.gaps),
            "engine_probe": {},
            "engine_assumptions": {
                "engine_version": PINNED_ENGINE_VERSION,
                "segment_strategy": SEGMENT_MAPPING_STRATEGY,
                "pivot_zone_strategy": "czsc.utils.sig.get_zs_seq on finished strokes",
                "divergence_strategy": "same_direction_stroke_extension_with_weaker_magnitude_around_pivot_zone",
            },
            "signals": {
                "status": "pending",
                "config": [],
            },
        },
    )

    if not normalized.bars:
        result.warnings.append(
            _warning(
                warning_id="warning_no_bars",
                code="NO_INPUT_BARS",
                message="No bars were available after normalization.",
                field="bars",
            )
        )
        result.summary = build_summary(result)
        return result

    try:
        analyzer, raw_bars = _run_engine(normalized=normalized, parameters=merged_parameters)
        index_by_timestamp = {bar.timestamp: bar.bar_index for bar in normalized.bars}
        fractals = _map_fractals(analyzer=analyzer, normalized=normalized)
        strokes = _map_strokes(analyzer=analyzer)
        pending_stroke = _map_pending_stroke(analyzer=analyzer, strokes=strokes)
        segments = derive_segments(strokes=strokes)
        pivot_zones = _map_pivot_zones(analyzer=analyzer, segments=segments)
        divergences = _map_divergences(strokes=strokes, pivot_zones=pivot_zones)
        structure_alerts = _build_structure_alerts(
            strokes=strokes,
            pivot_zones=pivot_zones,
            analyzer=analyzer,
        )
        signal_evaluations, signal_series, signal_events, signal_snapshots, signal_warnings, resolved_signals_config = (
            _build_signal_payloads(
                strokes=strokes,
                analyzer=analyzer,
                index_by_timestamp=index_by_timestamp,
                signals_config=signals_config,
            )
        )
        candidate_point_events = _build_candidate_point_events(signal_evaluations=signal_evaluations)
        buy_points, sell_points = _build_candidate_points(
            strokes=strokes,
            pivot_zones=pivot_zones,
            candidate_point_events=candidate_point_events,
        )

        result.fractals = fractals
        result.strokes = strokes
        result.segments = segments
        result.pivot_zones = pivot_zones
        result.divergences = divergences
        result.structure_alerts = structure_alerts
        result.signal_series = signal_series
        result.signal_events = signal_events
        result.signal_snapshots = signal_snapshots
        result.candidate_point_events = candidate_point_events
        result.candidate_buy_points = buy_points
        result.candidate_sell_points = sell_points
        result.meta["engine_probe"] = {
            "status": "ok",
            "raw_bar_count": len(raw_bars),
            "fractal_count": len(fractals),
            "finished_bi_count": len(strokes),
            "last_bi_extend": _safe_last_bi_extend(analyzer),
        }
        result.meta["mapping"] = {
            "fractal_count": len(fractals),
            "stroke_count": len(strokes),
            "segment_count": len(segments),
            "pivot_zone_count": len(pivot_zones),
            "divergence_count": len(divergences),
            "signal_series_count": len(signal_series),
            "signal_event_count": len(signal_events),
            "signal_snapshot_count": len(signal_snapshots),
            "candidate_point_event_count": len(candidate_point_events),
        }
        result.meta["signals"] = {
            "status": "ok",
            "config": resolved_signals_config,
            "evaluation_count": len(signal_evaluations),
            "series_count": len(signal_series),
            "event_count": len(signal_events),
            "snapshot_count": len(signal_snapshots),
            "candidate_point_event_count": len(candidate_point_events),
        }
        if pending_stroke is not None:
            result.meta["pending_stroke"] = pending_stroke
        result.warnings.extend(signal_warnings)
        result.warnings.extend(_build_mapping_warnings(result=result, analyzer=analyzer))
    except Exception as exc:
        result.meta["engine_probe"] = {"status": "failed", "error": str(exc)}
        result.warnings.append(
            _warning(
                warning_id="warning_engine_probe_failed",
                code="ENGINE_PROBE_FAILED",
                message=f"czsc probe or mapping failed during Phase 2: {exc}",
                field="engine",
            )
        )

    result.plot_primitives = build_plot_primitives(result)
    result.summary = build_summary(result)
    return result


def _frozen_result_after_normalization_failure(
    symbol: str,
    timeframe: str,
    source: str,
    parameters: Dict[str, Any] | None,
    error: Exception,
) -> AnalysisResult:
    merged_parameters = get_default_parameters()
    if parameters:
        merged_parameters.update(parameters)
    result = AnalysisResult(
        symbol=symbol,
        timeframe=timeframe,
        source=source,
        engine=ENGINE_NAME,
        engine_version=PINNED_ENGINE_VERSION,
        parameters=merged_parameters,
        warnings=[
            _warning(
                warning_id="warning_normalization_failed",
                code="NORMALIZATION_FAILED",
                message=f"Input normalization failed during Phase 2: {error}",
                field="bars",
            )
        ],
        meta={
            "bar_count": 0,
            "input_fields": [],
            "gaps": [],
            "engine_probe": {"status": "skipped", "reason": "normalization_failed"},
            "engine_assumptions": {
                "engine_version": PINNED_ENGINE_VERSION,
                "segment_strategy": SEGMENT_MAPPING_STRATEGY,
                "pivot_zone_strategy": "czsc.utils.sig.get_zs_seq on finished strokes",
                "divergence_strategy": "same_direction_stroke_extension_with_weaker_magnitude_around_pivot_zone",
            },
            "signals": {
                "status": "skipped",
                "reason": "normalization_failed",
                "config": [],
            },
        },
    )
    result.summary = build_summary(result)
    return result


def _warning(warning_id: str, code: str, message: str, field: str) -> AnalysisWarning:
    return AnalysisWarning(
        id=warning_id,
        warning_code=code,
        severity="warning",
        message=message,
        field=field,
    )



