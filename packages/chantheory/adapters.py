from __future__ import annotations

from datetime import datetime
from importlib import import_module
import re
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from .config import ENGINE_NAME, PINNED_ENGINE_VERSION, get_default_parameters
from .describe import build_summary
from .normalize import NormalizationError, normalize_ohlcv_rows, normalize_tracker_klines
from .plotting import build_plot_primitives
from .schema import (
    AnalysisResult,
    AnalysisWarning,
    CandidatePoint,
    Divergence,
    Fractal,
    NormalizationResult,
    PivotZone,
    Segment,
    Stroke,
    StructureAlert,
)


class EngineImportError(RuntimeError):
    pass


def analyze(
    rows: Iterable[Mapping[str, object]],
    symbol: str,
    timeframe: str = "day",
    source: str = "unknown",
    parameters: Dict[str, Any] | None = None,
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
    return analyze_normalized(normalized=normalized, parameters=parameters)


def analyze_tracker_klines(
    rows: Iterable[Mapping[str, object]],
    code: str,
    market: str,
    timeframe: str = "day",
    source: str = "tencent",
    parameters: Dict[str, Any] | None = None,
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
    return analyze_normalized(normalized=normalized, parameters=parameters)


def analyze_normalized(
    normalized: NormalizationResult,
    parameters: Dict[str, Any] | None = None,
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
                "segment_strategy": "conservative_three_stroke_window",
                "pivot_zone_strategy": "czsc.utils.sig.get_zs_seq on finished strokes",
                "divergence_strategy": "conservative_empty_until project-level rule is finalized",
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
        fractals = _map_fractals(analyzer=analyzer, normalized=normalized)
        strokes = _map_strokes(analyzer=analyzer)
        pending_stroke = _map_pending_stroke(analyzer=analyzer, strokes=strokes)
        segments = _map_segments(strokes=strokes)
        pivot_zones = _map_pivot_zones(analyzer=analyzer, segments=segments)
        divergences = _map_divergences()
        structure_alerts = _build_structure_alerts(
            strokes=strokes,
            pivot_zones=pivot_zones,
            analyzer=analyzer,
        )
        buy_points, sell_points = _build_candidate_points(
            strokes=strokes,
            pivot_zones=pivot_zones,
        )

        result.fractals = fractals
        result.strokes = strokes
        result.segments = segments
        result.pivot_zones = pivot_zones
        result.divergences = divergences
        result.structure_alerts = structure_alerts
        result.candidate_buy_points = buy_points
        result.candidate_sell_points = sell_points
        result.meta["engine_probe"] = {
            "status": "ok",
            "raw_bar_count": len(raw_bars),
            "fractal_count": len(fractals),
            "finished_bi_count": len(strokes),
            "last_bi_extend": bool(getattr(analyzer, "last_bi_extend", False)),
        }
        result.meta["mapping"] = {
            "fractal_count": len(fractals),
            "stroke_count": len(strokes),
            "segment_count": len(segments),
            "pivot_zone_count": len(pivot_zones),
            "divergence_count": len(divergences),
        }
        if pending_stroke is not None:
            result.meta["pending_stroke"] = pending_stroke
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
                "segment_strategy": "conservative_three_stroke_window",
                "pivot_zone_strategy": "czsc.utils.sig.get_zs_seq on finished strokes",
                "divergence_strategy": "conservative_empty_until project-level rule is finalized",
            },
        },
    )
    result.summary = build_summary(result)
    return result


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
    direction = _normalize_direction(_safe_get(ubi, "direction", default=""))
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
        if previous.end_timestamp == end_ts and abs(previous.end_price - end_price) < 1e-9:
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
            "raw_direction": _enum_name(_safe_get(ubi, "direction", default="")),
        },
    )


def _map_segments(strokes: Sequence[Stroke]) -> List[Segment]:
    items: List[Segment] = []
    if len(strokes) < 3:
        return items

    for offset, start_index in enumerate(range(0, len(strokes) - 2, 2), start=1):
        window = list(strokes[start_index : start_index + 3])
        first = window[0]
        last = window[-1]
        if not first.start_timestamp or not last.end_timestamp:
            continue
        direction = first.direction if first.direction == last.direction else _direction_from_prices(
            start_price=first.start_price,
            end_price=last.end_price,
        )
        items.append(
            Segment(
                id=f"segment_{offset:03d}_{first.start_timestamp}_{last.end_timestamp}",
                direction=direction,
                stroke_ids=[stroke.id for stroke in window],
                start_timestamp=first.start_timestamp,
                end_timestamp=last.end_timestamp,
                start_price=first.start_price,
                end_price=last.end_price,
                confirmed=True,
                meta={
                    "mapping_strategy": "conservative_three_stroke_window",
                    "window_size": len(window),
                },
            )
        )

    return items


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


def _map_divergences() -> List[Divergence]:
    return []


def _build_structure_alerts(
    strokes: Sequence[Stroke],
    pivot_zones: Sequence[PivotZone],
    analyzer: object,
) -> List[StructureAlert]:
    alerts: List[StructureAlert] = []
    if strokes and bool(getattr(analyzer, "last_bi_extend", False)):
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


def _build_candidate_points(
    strokes: Sequence[Stroke],
    pivot_zones: Sequence[PivotZone],
) -> Tuple[List[CandidatePoint], List[CandidatePoint]]:
    buy_points: List[CandidatePoint] = []
    sell_points: List[CandidatePoint] = []
    if not strokes or not pivot_zones:
        return buy_points, sell_points

    last_stroke = strokes[-1]
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

    if result.strokes and bool(getattr(analyzer, "last_bi_extend", False)):
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

    if not result.divergences:
        warnings.append(
            AnalysisWarning(
                id="warning_divergence_conservative_empty",
                warning_code="DIVERGENCE_CONSERVATIVE_EMPTY",
                severity="info",
                message="Divergences remain conservatively empty in Phase 2 until a stable project rule is finalized.",
                field="divergences",
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


def _timestamps_overlap(start_a: str, end_a: str, start_b: str, end_b: str) -> bool:
    if not start_a or not end_a or not start_b or not end_b:
        return False
    return start_a <= end_b and start_b <= end_a
