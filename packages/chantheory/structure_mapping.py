"""Structure mapping helpers for converting czsc internal objects to project schema.

This module contains pure helper functions used by the structure mapping layer
(fractals, strokes, pivot zones, divergences, alerts). They are extracted from
adapters.py to improve cohesion and reduce the size of the facade module.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, List, Mapping, Sequence

from .config import ENGINE_NAME
from .schema import (
    AnalysisResult,
    AnalysisWarning,
    Divergence,
    Fractal,
    NormalizationResult,
    PivotZone,
    Segment,
    Stroke,
    StructureAlert,
)


# ---------------------------------------------------------------------------
# Generic attribute / value helpers
# ---------------------------------------------------------------------------

def safe_get(obj: object, *names: str, default: Any = None) -> Any:
    """Retrieve the first non-None value from *obj* by *names* (attr or key)."""
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


def enum_name(value: Any) -> str:
    """Return the ``name`` attribute of an enum-like value, or the value itself."""
    if value is None:
        return ""
    return str(getattr(value, "name", value))


def to_float(value: Any) -> float:
    """Convert *value* to float, returning 0.0 for None."""
    if value is None:
        return 0.0
    return float(value)


def to_timestamp(value: Any) -> str:
    """Normalise a datetime / bar / string value to a stable timestamp string."""
    if value is None:
        return ""
    if hasattr(value, "dt"):
        return to_timestamp(getattr(value, "dt"))
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


# ---------------------------------------------------------------------------
# Direction / fractal normalisation
# ---------------------------------------------------------------------------

def normalize_direction(value: Any) -> str:
    """Normalise a czsc direction enum/value to ``"up"`` / ``"down"`` / ``"unknown"``."""
    text = enum_name(value).lower()
    raw_text = str(value).strip().lower()
    value_text = str(getattr(value, "value", "")).strip().lower()
    candidates = {text, raw_text, value_text}
    joined = " ".join(item for item in candidates if item)

    if candidates & {"up"} or "up" in joined or "向上" in joined:
        return "up"
    if candidates & {"down"} or "down" in joined or "向下" in joined:
        return "down"
    return "unknown"


def normalize_fractal_type(value: Any) -> str:
    """Normalise a czsc fractal mark enum/value to ``"top"`` / ``"bottom"`` / ``"unknown"``."""
    text = enum_name(value).lower()
    raw_text = str(value).strip().lower()
    value_text = str(getattr(value, "value", "")).strip().lower()
    candidates = {text, raw_text, value_text}
    joined = " ".join(item for item in candidates if item)

    if candidates & {"g", "top", "high"} or "top" in joined or "顶" in joined or "高" in joined:
        return "top"
    if candidates & {"d", "bottom", "low"} or "bottom" in joined or "底" in joined or "低" in joined:
        return "bottom"
    return "unknown"


def direction_from_prices(start_price: float, end_price: float) -> str:
    """Infer direction from two price points."""
    return "up" if end_price >= start_price else "down"


def opposite_direction(direction: str) -> str:
    """Return the opposite direction, or empty string for non-standard values."""
    if direction == "up":
        return "down"
    if direction == "down":
        return "up"
    return ""


# ---------------------------------------------------------------------------
# Timestamp / overlap helpers
# ---------------------------------------------------------------------------

def timestamps_overlap(start_a: str, end_a: str, start_b: str, end_b: str) -> bool:
    """Check whether two timestamp intervals overlap."""
    if not start_a or not end_a or not start_b or not end_b:
        return False
    return start_a <= end_b and start_b <= end_a


# ---------------------------------------------------------------------------
# ID helpers
# ---------------------------------------------------------------------------

def fractal_id(timestamp: str, fractal_type: str) -> str:
    return f"fractal_{timestamp}_{fractal_type}"


def stroke_id(start_timestamp: str, end_timestamp: str) -> str:
    return f"stroke_{start_timestamp}_{end_timestamp}"


def stroke_endpoint_mismatch(previous: Stroke, start_timestamp: str, start_price: float) -> bool:
    """Return True if the start of the next stroke does not match the end of *previous*."""
    timestamp_matches = previous.end_timestamp == start_timestamp if (previous.end_timestamp or start_timestamp) else True
    price_matches = abs(previous.end_price - start_price) < 1e-9
    return not (timestamp_matches and price_matches)


# ---------------------------------------------------------------------------
# Internal warning helper (same pattern as signals.py, multi_timeframe.py)
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
# Structure mapping functions
# ---------------------------------------------------------------------------

def map_fractals(analyzer: object, normalized: NormalizationResult) -> List[Fractal]:
    index_by_timestamp = {bar.timestamp: bar.bar_index for bar in normalized.bars}
    items: List[Fractal] = []
    unfinished_keys = {
        (to_timestamp(item), normalize_fractal_type(safe_get(item, "mark", default="")))
        for item in list(getattr(analyzer, "ubi_fxs", []) or [])
    }

    for fx in list(getattr(analyzer, "fx_list", []) or []):
        timestamp = to_timestamp(fx)
        fractal_type = normalize_fractal_type(safe_get(fx, "mark", default=""))
        price = to_float(safe_get(fx, "fx", "high", "low", default=0.0))
        key = (timestamp, fractal_type)
        items.append(
            Fractal(
                id=fractal_id(timestamp=timestamp, fractal_type=fractal_type),
                fractal_type=fractal_type,
                bar_index=index_by_timestamp.get(timestamp, -1),
                timestamp=timestamp,
                price=price,
                confirmed=key not in unfinished_keys,
                source=ENGINE_NAME,
                meta={
                    "raw_mark": enum_name(safe_get(fx, "mark", default="")),
                },
            )
        )

    return items


def map_strokes(analyzer: object) -> List[Stroke]:
    items: List[Stroke] = []

    for bi in list(getattr(analyzer, "finished_bis", []) or []):
        fx_a = safe_get(bi, "fx_a")
        fx_b = safe_get(bi, "fx_b")
        start_ts = to_timestamp(fx_a)
        end_ts = to_timestamp(fx_b)
        start_type = normalize_fractal_type(safe_get(fx_a, "mark", default=""))
        end_type = normalize_fractal_type(safe_get(fx_b, "mark", default=""))
        start_price = to_float(safe_get(fx_a, "fx", "low", "high", default=0.0))
        end_price = to_float(safe_get(fx_b, "fx", "high", "low", default=0.0))
        direction = normalize_direction(safe_get(bi, "direction", default=""))
        start_fractal_id = fractal_id(start_ts, start_type)
        meta = {
            "high": to_float(safe_get(bi, "high", default=max(start_price, end_price))),
            "low": to_float(safe_get(bi, "low", default=min(start_price, end_price))),
            "raw_direction": enum_name(safe_get(bi, "direction", default="")),
        }

        if items:
            previous = items[-1]
            if stroke_endpoint_mismatch(previous=previous, start_timestamp=start_ts, start_price=start_price):
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
                id=stroke_id(start_timestamp=start_ts, end_timestamp=end_ts),
                direction=direction,
                start_fractal_id=start_fractal_id,
                end_fractal_id=fractal_id(end_ts, end_type),
                start_timestamp=start_ts,
                end_timestamp=end_ts,
                start_price=start_price,
                end_price=end_price,
                confirmed=True,
                meta=meta,
            )
        )

    return items


def map_pending_stroke(analyzer: object, strokes: Sequence[Stroke]) -> Stroke | None:
    ubi = getattr(analyzer, "ubi", None)
    if not ubi:
        return None

    start_fx = safe_get(ubi, "fx_a")
    raw_direction = normalize_direction(safe_get(ubi, "direction", default=""))
    direction = raw_direction
    if strokes:
        direction = opposite_direction(strokes[-1].direction)
    if start_fx is None or direction not in {"up", "down"}:
        return None

    start_ts = to_timestamp(start_fx)
    start_type = normalize_fractal_type(safe_get(start_fx, "mark", default=""))
    start_price = to_float(safe_get(start_fx, "fx", "low", "high", default=safe_get(ubi, "low", "high", default=0.0)))
    start_fractal_id = fractal_id(start_ts, start_type)
    end_bar = safe_get(ubi, "high_bar" if direction == "up" else "low_bar")
    end_ts = to_timestamp(end_bar)
    if not end_ts:
        return None
    end_price = to_float(
        safe_get(
            ubi,
            "high" if direction == "up" else "low",
            default=safe_get(end_bar, "high" if direction == "up" else "low", default=0.0),
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
        actual_direction = direction_from_prices(start_price, end_price)
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
            "raw_direction": enum_name(safe_get(ubi, "direction", default="")),
        },
    )


def map_pivot_zones(
    analyzer: object,
    segments: Sequence[Segment],
    load_czsc_utils: Callable[[], object] | None = None,
) -> List[PivotZone]:
    if load_czsc_utils is None:
        from .engine import load_czsc_utils as _load_czsc_utils
        load_czsc_utils = _load_czsc_utils

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
        start_timestamp = to_timestamp(safe_get(zs, "sdt", default=""))
        end_timestamp = to_timestamp(safe_get(zs, "edt", default=""))
        high = to_float(safe_get(zs, "zg", "gg", default=0.0))
        low = to_float(safe_get(zs, "zd", "dd", default=0.0))
        related_segment_ids = [
            segment.id
            for segment in segments
            if timestamps_overlap(
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
                    "gg": to_float(safe_get(zs, "gg", default=high)),
                    "dd": to_float(safe_get(zs, "dd", default=low)),
                    "zz": to_float(safe_get(zs, "zz", default=(high + low) / 2 if high and low else 0.0)),
                },
            )
        )

    return items


def _segment_price_range(segment: Segment) -> tuple[float, float]:
    return (
        min(segment.start_price, segment.end_price),
        max(segment.start_price, segment.end_price),
    )


def map_segment_pivot_zones(segments: Sequence[Segment]) -> List[PivotZone]:
    """根据线段三段重叠构造段级别中枢。

    输入段筛选规则（第一版语义，明确写出）：
    - 包含 status="confirmed" 段（方向与端点均已确认）
    - 包含 status="pending" 段（方向已确定，端点未最终确认）
    - 排除 status="growing" 段（仍在生长，方向可能未定）

    之所以包含 pending 段：实际行情中多数段在分析时刻仍为 pending，
    若仅取 confirmed 段，段中枢几乎无法生成。pending 段的方向已经确定，
    其价格区间可用于中枢重叠判定。

    active 语义：active=True 当且仅当没有已确认离开段。即 leave_segment 为 None
    （没有离开段），或 leave_segment 仍是 pending（离开未确认）。若中枢的成员段中
    含 pending 段，meta.contains_pending_segments=True，提示该中枢本身也是不稳定的。

    调试可见性：meta.leave_segment_status 记录离开段的 status，取值为 None（无离开段）、
    "confirmed"（已确认离开）、"pending"（未确认离开），便于 JSON 调试时直接区分
    active=True 的两种来源。
    """
    usable_segments = [
        segment for segment in segments
        if segment.meta.get("status") != "growing"
    ]
    items: List[PivotZone] = []
    if len(usable_segments) < 3:
        return items

    index = 0
    zone_index = 1
    while index + 2 < len(usable_segments):
        core_segments = usable_segments[index : index + 3]
        if not (
            core_segments[0].direction != core_segments[1].direction
            and core_segments[1].direction != core_segments[2].direction
        ):
            index += 1
            continue

        ranges = [_segment_price_range(segment) for segment in core_segments]
        zd = max(low for low, _high in ranges)
        zg = min(high for _low, high in ranges)
        if zg <= zd:
            index += 1
            continue

        member_segments = list(core_segments)
        end_index = index + 2
        gg = max(high for _low, high in ranges)
        dd = min(low for low, _high in ranges)

        scan_index = end_index + 1
        while scan_index < len(usable_segments):
            segment = usable_segments[scan_index]
            low, high = _segment_price_range(segment)
            if high < zd or low > zg:
                break
            member_segments.append(segment)
            end_index = scan_index
            gg = max(gg, high)
            dd = min(dd, low)
            scan_index += 1

        enter_segment = usable_segments[index - 1] if index > 0 else None
        leave_segment = usable_segments[end_index + 1] if end_index + 1 < len(usable_segments) else None
        core_segment_ids = [segment.id for segment in core_segments]
        extension_segment_ids = [segment.id for segment in member_segments[3:]]
        start_segment = usable_segments[index]
        end_segment = usable_segments[end_index]
        # active=True 当且仅当没有已确认离开段：
        # leave_segment 为 None，或 leave_segment 仍是 pending（离开未确认）。
        active = leave_segment is None or leave_segment.meta.get("status") == "pending"
        contains_pending_segments = any(
            segment.meta.get("status") == "pending" for segment in member_segments
        )

        items.append(
            PivotZone(
                id=f"segment_pivot_zone_{zone_index:03d}_{start_segment.start_timestamp}_{end_segment.end_timestamp}",
                start_timestamp=start_segment.start_timestamp,
                end_timestamp=end_segment.end_timestamp,
                high=zg,
                low=zd,
                segment_ids=[segment.id for segment in member_segments],
                level="segment",
                active=active,
                meta={
                    "mapping_strategy": "chantheory_segment_pivot",
                    "zg": zg,
                    "zd": zd,
                    "gg": gg,
                    "dd": dd,
                    "zz": (zg + zd) / 2,
                    "core_segment_ids": core_segment_ids,
                    "extension_segment_ids": extension_segment_ids,
                    "core_segment_count": len(core_segment_ids),
                    "extension_segment_count": len(extension_segment_ids),
                    "enter_segment_id": enter_segment.id if enter_segment else None,
                    "enter_direction": enter_segment.direction if enter_segment else None,
                    "leave_segment_id": leave_segment.id if leave_segment else None,
                    "leave_direction": leave_segment.direction if leave_segment else None,
                    "leave_segment_status": leave_segment.meta.get("status") if leave_segment else None,
                    "contains_pending_segments": contains_pending_segments,
                },
            )
        )
        zone_index += 1
        index = end_index + 1

    return items


def map_divergences(
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
        overlaps_retracement = timestamps_overlap(
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


def safe_last_bi_extend(analyzer: object) -> bool:
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


def build_structure_alerts(
    strokes: Sequence[Stroke],
    pivot_zones: Sequence[PivotZone],
    analyzer: object,
) -> List[StructureAlert]:
    alerts: List[StructureAlert] = []
    if strokes and safe_last_bi_extend(analyzer):
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


def build_mapping_warnings(result: AnalysisResult, analyzer: object) -> List[AnalysisWarning]:
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

    if result.strokes and safe_last_bi_extend(analyzer):
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
