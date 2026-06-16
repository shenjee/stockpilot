from __future__ import annotations

from datetime import datetime
from importlib import import_module
import re
from typing import Any, Callable, Dict, Iterable, List, Mapping, Sequence, Tuple

from .config import ENGINE_NAME, PINNED_ENGINE_VERSION, get_default_parameters, get_default_signals_config
from .describe import build_multi_timeframe_summary, build_summary
from .normalize import NormalizationError, normalize_ohlcv_rows, normalize_tracker_klines
from .plotting import build_plot_primitives
from .segments import SEGMENT_MAPPING_STRATEGY, derive_segments
from .schema import (
    AnalysisResult,
    AnalysisWarning,
    CandidatePoint,
    CandidatePointEvent,
    Divergence,
    Fractal,
    MultiTimeframeAnalysisResult,
    MultiTimeframeLevel,
    NormalizationResult,
    PivotZone,
    Segment,
    SignalEvent,
    SignalSeries,
    SignalSeriesPoint,
    SignalSnapshot,
    Stroke,
    StructureAlert,
)


class EngineImportError(RuntimeError):
    pass


_TIMEFRAME_ORDER = {
    "1m": 0,
    "5m": 1,
    "15m": 2,
    "30m": 3,
    "60m": 4,
    "day": 5,
    "week": 6,
    "month": 7,
}


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


def _analyze_multi_timeframe(
    rows_by_timeframe: Mapping[str, Iterable[Mapping[str, object]]],
    symbol: str,
    source: str,
    base_timeframe: str,
    parameters: Dict[str, Any] | None,
    signals_config: Sequence[object] | Mapping[str, object] | None,
    timeframe_order: Sequence[str] | None,
    analyze_one: Callable[[str, Iterable[Mapping[str, object]]], AnalysisResult],
) -> MultiTimeframeAnalysisResult:
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
    ordered_timeframes = _order_multi_timeframes(
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
        role = _multi_timeframe_role(timeframe=timeframe, base_timeframe=base_timeframe)
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
        aggregated_warnings.extend(_lift_multi_timeframe_warnings(timeframe=timeframe, role=role, warnings=analysis.warnings))

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


def _summarize_multi_timeframe_signals_config(
    signals_config: Sequence[object] | Mapping[str, object] | None,
) -> Dict[str, Any]:
    try:
        normalized = _normalize_signals_config(signals_config)
    except (TypeError, ValueError):
        return {"status": "invalid", "config": []}
    return {"status": "ok", "config": normalized}


def _order_multi_timeframes(
    available_timeframes: Sequence[str],
    base_timeframe: str,
    timeframe_order: Sequence[str] | None,
) -> List[str]:
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

    for timeframe in sorted(available_timeframes, key=lambda item: (_TIMEFRAME_ORDER.get(item, 999), item)):
        _push(str(timeframe))

    return ordered


def _multi_timeframe_role(timeframe: str, base_timeframe: str) -> str:
    if timeframe == base_timeframe:
        return "base"
    timeframe_rank = _TIMEFRAME_ORDER.get(timeframe, 999)
    base_rank = _TIMEFRAME_ORDER.get(base_timeframe, 999)
    if timeframe_rank > base_rank:
        return "higher"
    if timeframe_rank < base_rank:
        return "lower"
    return "peer"


def _lift_multi_timeframe_warnings(
    timeframe: str,
    role: str,
    warnings: Sequence[AnalysisWarning],
) -> List[AnalysisWarning]:
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


def _run_engine(
    normalized: NormalizationResult,
    parameters: Dict[str, Any],
) -> Tuple[object, list]:
    RawBar, Freq, CZSC = load_czsc()
    freq = getattr(Freq, _get_freq_name(normalized.timeframe))
    raw_bars = []

    for bar in normalized.bars:
        raw_bars.append(
            RawBar(
                symbol=bar.symbol,
                id=bar.bar_index,
                dt=_parse_dt(bar.timestamp),
                freq=freq,
                open=bar.open,
                close=bar.close,
                high=bar.high,
                low=bar.low,
                vol=bar.volume,
                amount=bar.amount,
            )
        )

    analyzer = CZSC(raw_bars, max_bi_num=int(parameters["max_bi_num"]))
    return analyzer, raw_bars


def load_czsc() -> Tuple[object, object, object]:
    # Load numpy.typing first so rs_czsc-backed imports initialize consistently.
    import_module("numpy.typing")
    try:
        czsc = import_module("czsc")
    except ImportError as exc:
        raise EngineImportError(str(exc)) from exc

    def _import_attr(module_name: str, attr_name: str) -> object | None:
        try:
            module = import_module(module_name)
        except ImportError:
            return None
        return getattr(module, attr_name, None)

    # Prefer the pure-Python CZSC path. The top-level rs_czsc-backed RawBar
    # converts date-only daily bars to pandas timestamps such as the previous
    # day 16:00, which makes Chan structures fall off the K-line trading dates.
    py_raw_bar = _import_attr("czsc.py.objects", "RawBar")
    py_freq = _import_attr("czsc.py.objects", "Freq")
    py_czsc = _import_attr("czsc.py.analyze", "CZSC")
    if py_raw_bar is not None and py_freq is not None and py_czsc is not None:
        return py_raw_bar, py_freq, py_czsc

    # czsc 0.10.x exports these symbols from the package root / core module,
    # while older releases exposed RawBar from czsc.objects.
    raw_bar_candidates = (
        getattr(czsc, "RawBar", None),
        _import_attr("czsc.core", "RawBar"),
        _import_attr("czsc.py.objects", "RawBar"),
    )
    RawBar = next((candidate for candidate in raw_bar_candidates if candidate is not None), None)
    if RawBar is None:
        try:
            RawBar = getattr(import_module("czsc.objects"), "RawBar")
        except ImportError as exc:
            raise EngineImportError("Unable to resolve czsc.RawBar from the installed czsc package") from exc

    Freq = getattr(czsc, "Freq", None) or _import_attr("czsc.core", "Freq")
    CZSC = getattr(czsc, "CZSC", None) or _import_attr("czsc.core", "CZSC")
    return RawBar, Freq, CZSC


def load_czsc_utils() -> object:
    return import_module("czsc.utils.sig")


def _parse_dt(value: str) -> datetime:
    if len(value) == 10:
        return datetime.strptime(value, "%Y-%m-%d")
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def _get_freq_name(timeframe: str) -> str:
    mapping = {
        "1m": "F1",
        "5m": "F5",
        "15m": "F15",
        "30m": "F30",
        "60m": "F60",
        "day": "D",
        "week": "W",
        "month": "M",
    }
    return mapping[timeframe]


def _warning(warning_id: str, code: str, message: str, field: str) -> AnalysisWarning:
    return AnalysisWarning(
        id=warning_id,
        warning_code=code,
        severity="warning",
        message=message,
        field=field,
    )


def _map_fractals(analyzer: object, normalized: NormalizationResult) -> List[Fractal]:
    index_by_timestamp = {bar.timestamp: bar.bar_index for bar in normalized.bars}
    items: List[Fractal] = []
    unfinished_keys = {
        (_to_timestamp(item), _normalize_fractal_type(_safe_get(item, "mark", default="")))
        for item in list(getattr(analyzer, "ubi_fxs", []) or [])
    }

    for fx in list(getattr(analyzer, "fx_list", []) or []):
        timestamp = _to_timestamp(fx)
        fractal_type = _normalize_fractal_type(_safe_get(fx, "mark", default=""))
        price = _to_float(_safe_get(fx, "fx", "high", "low", default=0.0))
        key = (timestamp, fractal_type)
        items.append(
            Fractal(
                id=_fractal_id(timestamp=timestamp, fractal_type=fractal_type),
                fractal_type=fractal_type,
                bar_index=index_by_timestamp.get(timestamp, -1),
                timestamp=timestamp,
                price=price,
                confirmed=key not in unfinished_keys,
                source=ENGINE_NAME,
                meta={
                    "raw_mark": _enum_name(_safe_get(fx, "mark", default="")),
                },
            )
        )

    return items


def _map_strokes(analyzer: object) -> List[Stroke]:
    items: List[Stroke] = []

    for bi in list(getattr(analyzer, "finished_bis", []) or []):
        fx_a = _safe_get(bi, "fx_a")
        fx_b = _safe_get(bi, "fx_b")
        start_ts = _to_timestamp(fx_a)
        end_ts = _to_timestamp(fx_b)
        start_type = _normalize_fractal_type(_safe_get(fx_a, "mark", default=""))
        end_type = _normalize_fractal_type(_safe_get(fx_b, "mark", default=""))
        start_price = _to_float(_safe_get(fx_a, "fx", "low", "high", default=0.0))
        end_price = _to_float(_safe_get(fx_b, "fx", "high", "low", default=0.0))
        direction = _normalize_direction(_safe_get(bi, "direction", default=""))
        start_fractal_id = _fractal_id(start_ts, start_type)
        meta = {
            "high": _to_float(_safe_get(bi, "high", default=max(start_price, end_price))),
            "low": _to_float(_safe_get(bi, "low", default=min(start_price, end_price))),
            "raw_direction": _enum_name(_safe_get(bi, "direction", default="")),
        }

        if items:
            previous = items[-1]
            if _stroke_endpoint_mismatch(previous=previous, start_timestamp=start_ts, start_price=start_price):
                meta["continuity_adjusted"] = True
                meta["continuity_reference_stroke_id"] = previous.id
                meta["original_start_timestamp"] = start_ts
                meta["original_start_price"] = start_price
                meta["original_start_fractal_id"] = start_fractal_id
                start_ts = previous.end_timestamp or start_ts
                start_price = previous.end_price
                start_fractal_id = previous.end_fractal_id or start_fractal_id

        items.append(
            Stroke(
                id=_stroke_id(start_timestamp=start_ts, end_timestamp=end_ts),
                direction=direction,
                start_fractal_id=start_fractal_id,
                end_fractal_id=_fractal_id(end_ts, end_type),
                start_timestamp=start_ts,
                end_timestamp=end_ts,
                start_price=start_price,
                end_price=end_price,
                confirmed=True,
                meta=meta,
            )
        )

    return items


def _map_pending_stroke(analyzer: object, strokes: Sequence[Stroke]) -> Stroke | None:
    ubi = getattr(analyzer, "ubi", None)
    if not ubi:
        return None

    start_fx = _safe_get(ubi, "fx_a")
    raw_direction = _normalize_direction(_safe_get(ubi, "direction", default=""))
    direction = raw_direction
    if strokes:
        direction = _opposite_direction(strokes[-1].direction)
    if start_fx is None or direction not in {"up", "down"}:
        return None

    start_ts = _to_timestamp(start_fx)
    start_type = _normalize_fractal_type(_safe_get(start_fx, "mark", default=""))
    start_price = _to_float(_safe_get(start_fx, "fx", "low", "high", default=_safe_get(ubi, "low", "high", default=0.0)))
    start_fractal_id = _fractal_id(start_ts, start_type)
    end_bar = _safe_get(ubi, "high_bar" if direction == "up" else "low_bar")
    end_ts = _to_timestamp(end_bar)
    if not end_ts:
        return None
    end_price = _to_float(
        _safe_get(
            ubi,
            "high" if direction == "up" else "low",
            default=_safe_get(end_bar, "high" if direction == "up" else "low", default=0.0),
        )
    )

    if strokes:
        previous = strokes[-1]
        start_ts = previous.end_timestamp or start_ts
        start_price = previous.end_price
        start_fractal_id = previous.end_fractal_id or start_fractal_id
        if end_ts <= start_ts:
            return None
        if previous.end_timestamp == end_ts and abs(previous.end_price - end_price) < 1e-9:
            return None
        actual_direction = _direction_from_prices(start_price, end_price)
        if actual_direction != direction:
            return None

    return Stroke(
        id=f"stroke_pending_{start_ts}_{end_ts}",
        direction=direction,
        start_fractal_id=start_fractal_id,
        end_fractal_id=f"fractal_pending_{end_ts}_{direction}",
        start_timestamp=start_ts,
        end_timestamp=end_ts,
        start_price=start_price,
        end_price=end_price,
        confirmed=False,
        meta={
            "pending": True,
            "source": "czsc_ubi",
            "mapped_direction": direction,
            "raw_direction": _enum_name(_safe_get(ubi, "direction", default="")),
        },
    )


def _map_pivot_zones(analyzer: object, segments: Sequence[Segment]) -> List[PivotZone]:
    try:
        sig_module = load_czsc_utils()
        get_zs_seq = getattr(sig_module, "get_zs_seq")
    except Exception:
        return []

    items: List[PivotZone] = []
    bis = list(getattr(analyzer, "finished_bis", []) or [])
    if not bis:
        return items

    for index, zs in enumerate(list(get_zs_seq(bis) or []), start=1):
        start_timestamp = _to_timestamp(_safe_get(zs, "sdt", default=""))
        end_timestamp = _to_timestamp(_safe_get(zs, "edt", default=""))
        high = _to_float(_safe_get(zs, "zg", "gg", default=0.0))
        low = _to_float(_safe_get(zs, "zd", "dd", default=0.0))
        related_segment_ids = [
            segment.id
            for segment in segments
            if _timestamps_overlap(
                start_a=segment.start_timestamp,
                end_a=segment.end_timestamp,
                start_b=start_timestamp,
                end_b=end_timestamp,
            )
        ]
        items.append(
            PivotZone(
                id=f"pivot_zone_{index:03d}_{start_timestamp}_{end_timestamp}",
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
                high=high,
                low=low,
                segment_ids=related_segment_ids,
                level="stroke",
                active=index == len(list(get_zs_seq(bis) or [])),
                meta={
                    "mapping_strategy": "czsc_get_zs_seq",
                    "gg": _to_float(_safe_get(zs, "gg", default=high)),
                    "dd": _to_float(_safe_get(zs, "dd", default=low)),
                    "zz": _to_float(_safe_get(zs, "zz", default=(high + low) / 2 if high and low else 0.0)),
                },
            )
        )

    return items


def _map_divergences(
    strokes: Sequence[Stroke],
    pivot_zones: Sequence[PivotZone],
) -> List[Divergence]:
    items: List[Divergence] = []
    if len(strokes) < 3 or not pivot_zones:
        return items

    for index in range(2, len(strokes)):
        previous = strokes[index - 2]
        retracement = strokes[index - 1]
        current = strokes[index]
        if not previous.confirmed or not retracement.confirmed or not current.confirmed:
            continue
        if previous.direction not in {"up", "down"} or current.direction != previous.direction:
            continue
        if retracement.direction == previous.direction:
            continue

        zone = _resolve_divergence_zone(
            previous=previous,
            retracement=retracement,
            current=current,
            pivot_zones=pivot_zones,
        )
        if zone is None:
            continue

        previous_magnitude = _stroke_magnitude(previous)
        current_magnitude = _stroke_magnitude(current)
        if previous_magnitude <= 0 or current_magnitude <= 0 or current_magnitude >= previous_magnitude:
            continue

        if current.direction == "up":
            if current.end_price <= previous.end_price or current.end_price <= zone.high:
                continue
            divergence_type = "bearish"
        else:
            if current.end_price >= previous.end_price or current.end_price >= zone.low:
                continue
            divergence_type = "bullish"

        magnitude_ratio = current_magnitude / previous_magnitude
        strength = "strong" if magnitude_ratio <= 0.72 else "normal"
        items.append(
            Divergence(
                id=f"divergence_{divergence_type}_{current.end_timestamp}_{current.id}",
                divergence_type=divergence_type,
                reference_type="stroke",
                reference_id=current.id,
                timestamp=current.end_timestamp,
                strength=strength,
                confirmed=True,
                description=_build_divergence_description(
                    divergence_type=divergence_type,
                    current=current,
                    previous=previous,
                    zone=zone,
                    magnitude_ratio=magnitude_ratio,
                ),
                meta={
                    "direction": current.direction,
                    "price": current.end_price,
                    "current_stroke_id": current.id,
                    "comparison_stroke_id": previous.id,
                    "retracement_stroke_id": retracement.id,
                    "pivot_zone_id": zone.id,
                    "pivot_zone_high": zone.high,
                    "pivot_zone_low": zone.low,
                    "current_magnitude": current_magnitude,
                    "comparison_magnitude": previous_magnitude,
                    "magnitude_ratio": round(magnitude_ratio, 4),
                    "mapping_strategy": "same_direction_stroke_extension_with_weaker_magnitude_around_pivot_zone",
                },
            )
        )

    return items


def _resolve_divergence_zone(
    previous: Stroke,
    retracement: Stroke,
    current: Stroke,
    pivot_zones: Sequence[PivotZone],
) -> PivotZone | None:
    for zone in reversed(list(pivot_zones)):
        overlaps_retracement = _timestamps_overlap(
            start_a=zone.start_timestamp,
            end_a=zone.end_timestamp,
            start_b=retracement.start_timestamp,
            end_b=retracement.end_timestamp,
        )
        retracement_inside_zone = zone.low <= retracement.end_price <= zone.high
        if not overlaps_retracement or not retracement_inside_zone:
            continue
        if current.direction == "up" and previous.end_price >= zone.high and current.end_price >= zone.high:
            return zone
        if current.direction == "down" and previous.end_price <= zone.low and current.end_price <= zone.low:
            return zone
    return None


def _stroke_magnitude(stroke: Stroke) -> float:
    return abs(float(stroke.end_price) - float(stroke.start_price))


def _build_divergence_description(
    divergence_type: str,
    current: Stroke,
    previous: Stroke,
    zone: PivotZone,
    magnitude_ratio: float,
) -> str:
    side = "above" if divergence_type == "bearish" else "below"
    zone_anchor = zone.high if divergence_type == "bearish" else zone.low
    label = "Bearish divergence" if divergence_type == "bearish" else "Bullish divergence"
    return (
        f"{label}: {current.id} extends price {side} pivot zone {zone.id} "
        f"to {current.end_price:.2f} versus {previous.end_price:.2f}, while stroke magnitude "
        f"contracts from {abs(previous.end_price - previous.start_price):.2f} "
        f"to {abs(current.end_price - current.start_price):.2f} "
        f"(ratio {magnitude_ratio:.2f}) around zone anchor {zone_anchor:.2f}."
    )


def _safe_last_bi_extend(analyzer: object) -> bool:
    bi_list = getattr(analyzer, "bi_list", None)
    if bi_list is not None:
        if not list(bi_list or []):
            return False
    elif not list(getattr(analyzer, "finished_bis", []) or []):
        return False

    try:
        return bool(getattr(analyzer, "last_bi_extend", False))
    except (IndexError, AttributeError):
        return False


def _build_structure_alerts(
    strokes: Sequence[Stroke],
    pivot_zones: Sequence[PivotZone],
    analyzer: object,
) -> List[StructureAlert]:
    alerts: List[StructureAlert] = []
    if strokes and _safe_last_bi_extend(analyzer):
        last_stroke = strokes[-1]
        alerts.append(
            StructureAlert(
                id=f"alert_unstable_tail_{last_stroke.id}",
                alert_type="unstable_tail_stroke",
                severity="info",
                timestamp=last_stroke.end_timestamp,
                related_ids=[last_stroke.id],
                message="The latest stroke is still extending and should be treated as unstable.",
                meta={"direction": last_stroke.direction},
            )
        )

    if pivot_zones:
        last_zone = pivot_zones[-1]
        latest_stroke = strokes[-1] if strokes else None
        related_ids = [last_zone.id]
        if latest_stroke:
            related_ids.append(latest_stroke.id)
            if latest_stroke.end_price < last_zone.low:
                stroke_position = "below"
            elif latest_stroke.end_price > last_zone.high:
                stroke_position = "above"
            else:
                stroke_position = "inside"
            message = (
                f"Latest active pivot zone spans {last_zone.low:.2f}-{last_zone.high:.2f}; "
                f"the latest confirmed stroke ends {stroke_position} the zone at {latest_stroke.end_price:.2f}."
            )
        else:
            stroke_position = "unknown"
            message = f"Latest active pivot zone spans {last_zone.low:.2f}-{last_zone.high:.2f}."
        alerts.append(
            StructureAlert(
                id=f"alert_active_pivot_zone_{last_zone.id}",
                alert_type="active_pivot_zone",
                severity="info",
                timestamp=last_zone.end_timestamp,
                related_ids=related_ids,
                message=message,
                meta={
                    "level": last_zone.level,
                    "zone_low": last_zone.low,
                    "zone_high": last_zone.high,
                    "latest_stroke_position": stroke_position,
                    "latest_stroke_end_price": latest_stroke.end_price if latest_stroke else None,
                },
            )
        )

    return alerts


def _build_signal_payloads(
    strokes: Sequence[Stroke],
    analyzer: object,
    index_by_timestamp: Mapping[str, int],
    signals_config: Sequence[object] | Mapping[str, object] | None,
) -> Tuple[List[Dict[str, Any]], List[SignalSeries], List[SignalEvent], List[SignalSnapshot], List[AnalysisWarning], List[Dict[str, Any]]]:
    warnings: List[AnalysisWarning] = []
    try:
        signal_definitions = _normalize_signals_config(signals_config)
    except (TypeError, ValueError) as exc:
        warnings.append(
            _warning(
                warning_id="warning_invalid_signals_config",
                code="INVALID_SIGNALS_CONFIG",
                message=f"signals_config is invalid and has been ignored: {exc}",
                field="signals_config",
            )
        )
        return [], [], [], [], warnings, []

    if not signal_definitions:
        return [], [], [], [], warnings, []

    bars_raw = list(getattr(analyzer, "bars_raw", []) or [])
    if not bars_raw:
        return [], [], [], [], warnings, signal_definitions

    strokes_by_end_ts = {stroke.end_timestamp: stroke for stroke in strokes}
    module_cache: Dict[str, object] = {}
    missing_modules: set[str] = set()
    missing_functions: set[tuple[str, str]] = set()
    failed_functions: set[tuple[str, str]] = set()
    evaluations: List[Dict[str, Any]] = []

    try:
        CZSC_cls = type(analyzer)
        replay_analyzer = CZSC_cls(bars_raw[:1], max_bi_num=getattr(analyzer, "max_bi_num", 50))
    except Exception:
        replay_analyzer = analyzer

    for i, bar in enumerate(bars_raw):
        if i > 0:
            replay_analyzer.update(bar)
        
        end_ts = _to_timestamp(bar)
        stroke = strokes_by_end_ts.get(end_ts)
        
        if stroke:
            direction = stroke.direction
            reference_id = stroke.id
            price = stroke.end_price
        else:
            bi_list = list(getattr(replay_analyzer, "bi_list", []) or [])
            bi = bi_list[-1] if bi_list else None
            if bi:
                direction = _normalize_direction(_safe_get(bi, "direction", default=""))
                fx_a = _safe_get(bi, "fx_a")
                start_ts = _to_timestamp(fx_a) if fx_a else end_ts
            else:
                direction = ""
                start_ts = end_ts
                
            price = _to_float(getattr(bar, "close", 0.0))
            reference_id = f"stroke_pending_{start_ts}_{end_ts}"

        bar_index = int(index_by_timestamp.get(end_ts, -1))
        for definition in signal_definitions:
            module_name = str(definition["module"])
            signal_name = str(definition["name"])
            signal_key = str(definition["key"])
            kwargs = dict(definition.get("kwargs", {}))
            di = int(definition.get("di", 1))

            if module_name in missing_modules:
                continue
            module = module_cache.get(module_name)
            if module is None:
                try:
                    module = import_module(module_name)
                    module_cache[module_name] = module
                except Exception as exc:
                    if module_name not in missing_modules:
                        missing_modules.add(module_name)
                        warnings.append(
                            _warning(
                                warning_id=f"warning_signal_module_missing_{module_name}",
                                code="SIGNAL_MODULE_UNAVAILABLE",
                                message=f"Signal module `{module_name}` could not be imported: {exc}",
                                field="signals",
                            )
                        )
                    continue

            func = getattr(module, signal_name, None)
            if func is None:
                missing_key = (module_name, signal_name)
                if missing_key not in missing_functions:
                    missing_functions.add(missing_key)
                    warnings.append(
                        _warning(
                            warning_id=f"warning_signal_function_missing_{signal_name}",
                            code="SIGNAL_FUNCTION_UNAVAILABLE",
                            message=f"Signal function `{module_name}.{signal_name}` is not available in the installed czsc package.",
                            field="signals",
                        )
                    )
                continue

            try:
                signal_value = _evaluate_signal_function(func=func, analyzer=replay_analyzer, di=di, kwargs=kwargs)
            except Exception as exc:
                failed_key = (module_name, signal_name)
                if failed_key not in failed_functions:
                    failed_functions.add(failed_key)
                    warnings.append(
                        _warning(
                            warning_id=f"warning_signal_eval_failed_{signal_name}",
                            code="SIGNAL_EVALUATION_FAILED",
                            message=f"Signal function `{module_name}.{signal_name}` failed: {exc}",
                            field="signals",
                        )
                    )
                continue

            evaluations.append(
                {
                    "signal_key": signal_key,
                    "signal_name": signal_name,
                    "module": module_name,
                    "timestamp": end_ts,
                    "bar_index": bar_index,
                    "reference_id": reference_id,
                    "price": price,
                    "direction": direction,
                    "value": signal_value,
                    "active": _signal_value_is_active(signal_value),
                    "di": di,
                    "meta": {
                        "kwargs": kwargs,
                    },
                }
            )

    return (
        evaluations,
        _build_signal_series(evaluations=evaluations, signal_definitions=signal_definitions),
        _build_signal_events(evaluations=evaluations),
        _build_signal_snapshots(evaluations=evaluations),
        warnings,
        signal_definitions,
    )


def _normalize_signals_config(
    signals_config: Sequence[object] | Mapping[str, object] | None,
) -> List[Dict[str, Any]]:
    raw_items: Sequence[object] | None
    if signals_config is None:
        raw_items = get_default_signals_config()
    elif isinstance(signals_config, Mapping):
        raw_value = signals_config.get("signals", [])
        if not isinstance(raw_value, Sequence) or isinstance(raw_value, (str, bytes)):
            raise ValueError("`signals` must be a list when signals_config is a mapping.")
        raw_items = list(raw_value)
    elif isinstance(signals_config, Sequence) and not isinstance(signals_config, (str, bytes)):
        raw_items = list(signals_config)
    else:
        raise TypeError("signals_config must be a list or a mapping with a `signals` list.")

    normalized: List[Dict[str, Any]] = []
    for index, item in enumerate(raw_items):
        if isinstance(item, str):
            name = item.strip()
            if not name:
                raise ValueError(f"signals_config[{index}] is empty.")
            normalized.append(
                {
                    "module": "czsc.signals",
                    "name": name,
                    "key": name,
                    "di": 1,
                    "kwargs": {},
                }
            )
            continue

        if not isinstance(item, Mapping):
            raise TypeError(f"signals_config[{index}] must be a string or mapping.")
        if item.get("enabled", True) is False:
            continue

        name = str(item.get("name", "")).strip()
        if not name:
            raise ValueError(f"signals_config[{index}] is missing `name`.")
        kwargs = dict(item.get("kwargs", {}))
        if not isinstance(kwargs, Mapping):
            raise ValueError(f"signals_config[{index}].kwargs must be a mapping.")
            
        for k, v in item.items():
            if k not in ("module", "name", "key", "alias", "enabled", "kwargs"):
                kwargs[k] = v
        di = int(kwargs.pop("di", 1))
                
        normalized.append(
            {
                "module": str(item.get("module") or "czsc.signals"),
                "name": name,
                "key": str(item.get("key") or item.get("alias") or _default_signal_key(name=name, kwargs=kwargs)),
                "di": di,
                "kwargs": kwargs,
            }
        )

    return normalized


def _default_signal_key(name: str, kwargs: Mapping[str, Any]) -> str:
    key_parts = [name]
    freq = kwargs.get("freq")
    if freq not in (None, ""):
        key_parts.insert(0, str(freq))
    for key in sorted(k for k in kwargs.keys() if k != "freq"):
        value = kwargs[key]
        if value in (None, ""):
            continue
        key_parts.append(f"{key}={value}")
    return "_".join(key_parts)


def _evaluate_signal_function(func: Any, analyzer: object, di: int, kwargs: Mapping[str, Any]) -> str:
    result = func(analyzer, di=di, **dict(kwargs))
    return _extract_signal_value(result)


def _extract_signal_value(result: Any) -> str:
    if not isinstance(result, Mapping) or not result:
        return ""
    return str(next(iter(result.values()), "")).strip()


def _signal_value_is_active(value: str) -> bool:
    return bool(value) and not value.startswith("其他")


def _build_signal_series(
    evaluations: Sequence[Mapping[str, Any]],
    signal_definitions: Sequence[Mapping[str, Any]],
) -> List[SignalSeries]:
    by_key: Dict[str, List[Mapping[str, Any]]] = {}
    for evaluation in evaluations:
        by_key.setdefault(str(evaluation["signal_key"]), []).append(evaluation)

    items: List[SignalSeries] = []
    for definition in signal_definitions:
        signal_key = str(definition["key"])
        points = [
            SignalSeriesPoint(
                timestamp=str(evaluation["timestamp"]),
                bar_index=int(evaluation["bar_index"]),
                value=str(evaluation["value"]),
                active=bool(evaluation["active"]),
                price=float(evaluation["price"]) if evaluation.get("price") is not None else None,
                reference_id=str(evaluation["reference_id"]),
                meta={
                    "direction": evaluation.get("direction", ""),
                },
            )
            for evaluation in by_key.get(signal_key, [])
        ]
        latest = points[-1] if points else None
        items.append(
            SignalSeries(
                signal_key=signal_key,
                signal_name=str(definition["name"]),
                module=str(definition["module"]),
                latest_value=latest.value if latest else "",
                latest_timestamp=latest.timestamp if latest else "",
                points=points,
                meta={
                    "active_point_count": sum(1 for point in points if point.active),
                },
            )
        )
    return items


def _build_signal_events(evaluations: Sequence[Mapping[str, Any]]) -> List[SignalEvent]:
    by_key: Dict[str, List[Mapping[str, Any]]] = {}
    for evaluation in evaluations:
        by_key.setdefault(str(evaluation["signal_key"]), []).append(evaluation)

    events: List[SignalEvent] = []
    for signal_key, items in by_key.items():
        previous: Mapping[str, Any] | None = None
        for item in items:
            current_active = bool(item["active"])
            previous_active = bool(previous["active"]) if previous is not None else False
            current_value = str(item["value"])
            previous_value = str(previous["value"]) if previous is not None else ""
            event_type = ""
            if previous is None and current_active:
                event_type = "triggered"
            elif not previous_active and current_active:
                event_type = "triggered"
            elif previous_active and current_active and current_value != previous_value:
                event_type = "switched"
            elif previous_active and not current_active:
                event_type = "invalidated"

            if event_type:
                events.append(
                    SignalEvent(
                        id=f"signal_event_{signal_key}_{item['bar_index']}_{event_type}",
                        signal_key=signal_key,
                        signal_name=str(item["signal_name"]),
                        module=str(item["module"]),
                        event_type=event_type,
                        timestamp=str(item["timestamp"]),
                        bar_index=int(item["bar_index"]),
                        value=current_value,
                        active=current_active,
                        reference_id=str(item["reference_id"]),
                        price=float(item["price"]) if item.get("price") is not None else None,
                        meta={
                            "previous_value": previous_value,
                            "direction": item.get("direction", ""),
                        },
                    )
                )
            previous = item

    events.sort(key=lambda item: (item.bar_index, item.signal_key, item.event_type))
    return events


def _build_signal_snapshots(evaluations: Sequence[Mapping[str, Any]]) -> List[SignalSnapshot]:
    snapshots_by_key: Dict[Tuple[str, int, str], Dict[str, Any]] = {}
    for evaluation in evaluations:
        snapshot_key = (
            str(evaluation["timestamp"]),
            int(evaluation["bar_index"]),
            str(evaluation["reference_id"]),
        )
        snapshot = snapshots_by_key.setdefault(
            snapshot_key,
            {
                "timestamp": str(evaluation["timestamp"]),
                "bar_index": int(evaluation["bar_index"]),
                "reference_id": str(evaluation["reference_id"]),
                "price": float(evaluation["price"]) if evaluation.get("price") is not None else None,
                "values": {},
                "active_signals": {},
                "signal_names": {},
            },
        )
        signal_key = str(evaluation["signal_key"])
        signal_value = str(evaluation["value"])
        snapshot["values"][signal_key] = signal_value
        snapshot["signal_names"][signal_key] = str(evaluation["signal_name"])
        if bool(evaluation["active"]):
            snapshot["active_signals"][signal_key] = signal_value

    snapshots = [
        SignalSnapshot(
            id=f"signal_snapshot_{item['bar_index']}_{item['reference_id']}",
            timestamp=str(item["timestamp"]),
            bar_index=int(item["bar_index"]),
            values=dict(item["values"]),
            active_signals=dict(item["active_signals"]),
            reference_id=str(item["reference_id"]),
            price=float(item["price"]) if item.get("price") is not None else None,
            meta={
                "signal_names": dict(item["signal_names"]),
            },
        )
        for item in sorted(snapshots_by_key.values(), key=lambda current: (current["bar_index"], current["reference_id"]))
    ]
    return snapshots


def _build_candidate_point_events(signal_evaluations: Sequence[Mapping[str, Any]]) -> List[CandidatePointEvent]:
    by_signal: Dict[str, List[Mapping[str, Any]]] = {}
    for evaluation in signal_evaluations:
        by_signal.setdefault(str(evaluation["signal_key"]), []).append(evaluation)

    events: List[CandidatePointEvent] = []
    for signal_key, items in by_signal.items():
        previous_mapping: Tuple[str, str] | None = None
        for item in items:
            current_mapping = _map_signal_evaluation_to_candidate_point(item)
            event_type = ""
            point_type = ""

            if previous_mapping is None and current_mapping is not None:
                event_type = "triggered"
                _, point_type = current_mapping
            elif previous_mapping is not None and current_mapping is None:
                event_type = "invalidated"
                point_type = previous_mapping[1]
            elif previous_mapping is not None and current_mapping is not None:
                _, previous_point_type = previous_mapping
                _, current_point_type = current_mapping
                if previous_point_type != current_point_type:
                    event_type = "switched"
                    point_type = current_point_type

            if event_type:
                previous_point_type = previous_mapping[1] if previous_mapping is not None else ""
                events.append(
                    CandidatePointEvent(
                        id=f"candidate_point_event_{signal_key}_{item['bar_index']}_{event_type}",
                        point_type=point_type,
                        event_type=event_type,
                        timestamp=str(item["timestamp"]),
                        bar_index=int(item["bar_index"]),
                        active=current_mapping is not None,
                        reference_id=str(item["reference_id"]),
                        price=float(item["price"]) if item.get("price") is not None else None,
                        reason=str(item["value"]),
                        meta={
                            "signal_key": signal_key,
                            "signal_name": str(item["signal_name"]),
                            "previous_point_type": previous_point_type,
                            "previous_value": str(previous_mapping[0]) if previous_mapping is not None else "",
                            "direction": item.get("direction", ""),
                        },
                    )
                )

            previous_mapping = (str(item["value"]), current_mapping[1]) if current_mapping is not None else None

    events.sort(key=lambda item: (item.bar_index, item.point_type, item.event_type))
    return events


def _build_candidate_points(
    strokes: Sequence[Stroke],
    pivot_zones: Sequence[PivotZone],
    candidate_point_events: Sequence[CandidatePointEvent],
) -> Tuple[List[CandidatePoint], List[CandidatePoint]]:
    buy_points: List[CandidatePoint] = []
    sell_points: List[CandidatePoint] = []
    if not strokes:
        return buy_points, sell_points

    last_stroke = strokes[-1]
    for event in candidate_point_events:
        if event.event_type not in ("triggered", "switched"):
            continue
            
        point = CandidatePoint(
            id=f"{event.point_type}_{event.timestamp}",
            point_type=event.point_type,
            timestamp=event.timestamp,
            price=event.price,
            reference_id=event.reference_id,
            confirmed=False,
            reason=event.reason,
            meta={
                "direction": event.meta.get("direction", ""),
                "signal_scope": "cxt_signal",
                "signal_name": event.meta.get("signal_name", ""),
                "signal_key": event.meta.get("signal_key", ""),
                "signal_value": event.reason,
                "signal_version": str(event.meta.get("signal_name", "")).rsplit("_", 1)[-1]
                if "_" in str(event.meta.get("signal_name", ""))
                else "",
            },
        )
        if "buy" in event.point_type:
            buy_points.append(point)
        else:
            sell_points.append(point)

    if not pivot_zones:
        return buy_points, sell_points

    last_zone = pivot_zones[-1]
    if last_zone.low <= last_stroke.end_price <= last_zone.high:
        if last_stroke.direction not in {"up", "down"}:
            return buy_points, sell_points
        collection = buy_points if last_stroke.direction == "down" else sell_points
        point_type = "structure_buy_candidate" if last_stroke.direction == "down" else "structure_sell_candidate"
        collection.append(
            CandidatePoint(
                id=f"{point_type}_{last_stroke.end_timestamp}",
                point_type=point_type,
                timestamp=last_stroke.end_timestamp,
                price=last_stroke.end_price,
                reference_id=last_zone.id,
                confirmed=False,
                reason="The latest confirmed stroke ends inside the active pivot zone range.",
                meta={
                    "direction": last_stroke.direction,
                    "signal_scope": "structure_candidate_only",
                },
            )
        )

    return buy_points, sell_points


def _map_signal_evaluation_to_candidate_point(evaluation: Mapping[str, Any]) -> Tuple[str, str] | None:
    if not bool(evaluation.get("active")):
        return None

    signal_name = str(evaluation.get("signal_name", ""))
    value = str(evaluation.get("value", ""))
    if signal_name.startswith("cxt_first_buy_") and value.startswith("一买"):
        return ("buy", "first_buy")
    if signal_name.startswith("cxt_first_sell_") and value.startswith("一卖"):
        return ("sell", "first_sell")
    if signal_name.startswith("cxt_second_bs_") and "二买" in value:
        return ("buy", "second_buy")
    if signal_name.startswith("cxt_second_bs_") and "二卖" in value:
        return ("sell", "second_sell")
    if signal_name.startswith("cxt_third_bs_") and "三买" in value:
        return ("buy", "third_buy")
    if signal_name.startswith("cxt_third_bs_") and "三卖" in value:
        return ("sell", "third_sell")
    return None


def _build_mapping_warnings(result: AnalysisResult, analyzer: object) -> List[AnalysisWarning]:
    warnings: List[AnalysisWarning] = []
    min_bars = int(result.parameters.get("min_bars", 0))
    bar_count = int(result.meta.get("bar_count", 0))
    if min_bars and bar_count < min_bars:
        warnings.append(
            _warning(
                warning_id="warning_insufficient_bars",
                code="INSUFFICIENT_BARS",
                message=(
                    f"Only {bar_count} bars are available; at least {min_bars} bars are "
                    "recommended for more stable Chan structure mapping."
                ),
                field="bars",
            )
        )

    if result.strokes and _safe_last_bi_extend(analyzer):
        warnings.append(
            _warning(
                warning_id="warning_unstable_tail_stroke",
                code="UNSTABLE_TAIL_STROKE",
                message="The latest stroke is extending, so the most recent structure remains unstable.",
                field="strokes",
            )
        )

    if result.strokes and not result.segments:
        warnings.append(
            _warning(
                warning_id="warning_segments_unavailable",
                code="SEGMENTS_UNAVAILABLE",
                message=(
                    "czsc 0.10.12 does not expose a first-class segment list, and the current "
                    "input does not yet support the conservative project segment mapping."
                ),
                field="segments",
            )
        )

    if any(fractal.fractal_type == "unknown" for fractal in result.fractals):
        warnings.append(
            _warning(
                warning_id="warning_unknown_fractal_type",
                code="UNKNOWN_FRACTAL_TYPE",
                message="At least one czsc fractal mark could not be mapped to top or bottom.",
                field="fractals",
            )
        )

    if any(stroke.direction == "unknown" for stroke in result.strokes):
        warnings.append(
            _warning(
                warning_id="warning_unknown_stroke_direction",
                code="UNKNOWN_STROKE_DIRECTION",
                message="At least one czsc stroke direction could not be mapped to up or down.",
                field="strokes",
            )
        )

    return warnings


def _fractal_id(timestamp: str, fractal_type: str) -> str:
    return f"fractal_{timestamp}_{fractal_type}"


def _stroke_id(start_timestamp: str, end_timestamp: str) -> str:
    return f"stroke_{start_timestamp}_{end_timestamp}"


def _stroke_endpoint_mismatch(previous: Stroke, start_timestamp: str, start_price: float) -> bool:
    timestamp_matches = previous.end_timestamp == start_timestamp if (previous.end_timestamp or start_timestamp) else True
    price_matches = abs(previous.end_price - start_price) < 1e-9
    return not (timestamp_matches and price_matches)


def _safe_get(obj: object, *names: str, default: Any = None) -> Any:
    for name in names:
        if obj is None:
            return default
        if isinstance(obj, Mapping) and name in obj:
            value = obj[name]
            if value is not None:
                return value
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return default


def _enum_name(value: Any) -> str:
    if value is None:
        return ""
    return str(getattr(value, "name", value))


def _normalize_direction(value: Any) -> str:
    text = _enum_name(value).lower()
    raw_text = str(value).strip().lower()
    value_text = str(getattr(value, "value", "")).strip().lower()
    candidates = {text, raw_text, value_text}
    joined = " ".join(item for item in candidates if item)

    if candidates & {"up"} or "up" in joined or "向上" in joined:
        return "up"
    if candidates & {"down"} or "down" in joined or "向下" in joined:
        return "down"
    return "unknown"


def _normalize_fractal_type(value: Any) -> str:
    text = _enum_name(value).lower()
    raw_text = str(value).strip().lower()
    value_text = str(getattr(value, "value", "")).strip().lower()
    candidates = {text, raw_text, value_text}
    joined = " ".join(item for item in candidates if item)

    if candidates & {"g", "top", "high"} or "top" in joined or "顶" in joined or "高" in joined:
        return "top"
    if candidates & {"d", "bottom", "low"} or "bottom" in joined or "底" in joined or "低" in joined:
        return "bottom"
    return "unknown"


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


def _to_timestamp(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "dt"):
        return _to_timestamp(getattr(value, "dt"))
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S") if value.time() != datetime.min.time() else value.strftime("%Y-%m-%d")
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    text = str(value).strip().replace("T", " ")
    text = " ".join(text.split())
    text = re.sub(r"\s*:\s*", ":", text)

    for fmt, width in (
        ("%Y-%m-%d %H:%M:%S", 19),
        ("%Y-%m-%d %H:%M", 16),
        ("%Y-%m-%d", 10),
    ):
        try:
            dt = datetime.strptime(text[:width], fmt)
            if fmt == "%Y-%m-%d":
                return dt.strftime("%Y-%m-%d")
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue

    if len(text) >= 19:
        return text[:19]
    return text[:10]


def _direction_from_prices(start_price: float, end_price: float) -> str:
    return "up" if end_price >= start_price else "down"


def _opposite_direction(direction: str) -> str:
    if direction == "up":
        return "down"
    if direction == "down":
        return "up"
    return ""


def _timestamps_overlap(start_a: str, end_a: str, start_b: str, end_b: str) -> bool:
    if not start_a or not end_a or not start_b or not end_b:
        return False
    return start_a <= end_b and start_b <= end_a
