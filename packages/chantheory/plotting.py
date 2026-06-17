from __future__ import annotations

from typing import List
from collections import defaultdict

from .schema import AnalysisResult, PlotPrimitive


LAYER_ORDER = (
    "candles",
    "fractals",
    "strokes",
    "segments",
    "pivot_zones",
    "candidate_points",
    "divergences",
    "alerts",
)


def build_plot_primitives(result: AnalysisResult) -> List[PlotPrimitive]:
    if not result.meta.get("bar_count"):
        return []

    primitives: List[PlotPrimitive] = []

    for fractal in result.fractals:
        if fractal.fractal_type == "top":
            color = "#DC2626"
            style = "triangle_down"
            text = ""
            textposition = "top center"
        elif fractal.fractal_type == "bottom":
            color = "#16A34A"
            style = "triangle_up"
            text = ""
            textposition = "bottom center"
        else:
            color = "#6B7280"
            style = "circle"
            text = ""
            textposition = "top center"
        primitives.append(
            PlotPrimitive(
                id=f"primitive_{fractal.id}",
                type="marker",
                layer="fractals",
                x=fractal.timestamp,
                y=fractal.price,
                style=style,
                color=color,
                text=text,
                meta={
                    "reference_type": "fractal",
                    "reference_id": fractal.id,
                    "confirmed": fractal.confirmed,
                    "textposition": textposition,
                },
            )
        )

    for stroke in result.strokes:
        primitives.append(
            PlotPrimitive(
                id=f"primitive_{stroke.id}",
                type="line",
                layer="strokes",
                x1=stroke.start_timestamp,
                y1=stroke.start_price,
                x2=stroke.end_timestamp,
                y2=stroke.end_price,
                style="solid",
                color="#2563EB" if stroke.direction == "up" else "#F97316",
                meta={
                    "reference_type": "stroke",
                    "reference_id": stroke.id,
                    "confirmed": stroke.confirmed,
                },
            )
        )

    pending_stroke = result.meta.get("pending_stroke")
    if pending_stroke:
        primitives.append(
            PlotPrimitive(
                id=f"primitive_{pending_stroke.id}",
                type="line",
                layer="strokes",
                x1=pending_stroke.start_timestamp,
                y1=pending_stroke.start_price,
                x2=pending_stroke.end_timestamp,
                y2=pending_stroke.end_price,
                style="dashed",
                color="#2563EB" if pending_stroke.direction == "up" else "#F97316",
                meta={
                    "reference_type": "stroke",
                    "reference_id": pending_stroke.id,
                    "confirmed": False,
                    "pending": True,
                },
            )
        )

    for segment in result.segments:
        is_growing = segment.meta.get("status") == "growing"
        primitives.append(
            PlotPrimitive(
                id=f"primitive_{segment.id}",
                type="line",
                layer="segments",
                x1=segment.start_timestamp,
                y1=segment.start_price,
                x2=segment.end_timestamp,
                y2=segment.end_price,
                style="dashed" if is_growing else "solid",
                color="rgba(124, 58, 237, 0.25)" if segment.direction == "up" else "rgba(147, 51, 234, 0.25)",
                meta={
                    "reference_type": "segment",
                    "reference_id": segment.id,
                    "confirmed": segment.confirmed,
                    "width_multiplier": 3,
                },
            )
        )

    for zone in result.pivot_zones:
        is_segment_pivot = zone.level == "segment"
        primitives.append(
            PlotPrimitive(
                id=f"primitive_{zone.id}",
                type="box",
                layer="pivot_zones",
                x1=zone.start_timestamp,
                y1=zone.high,
                x2=zone.end_timestamp,
                y2=zone.low,
                style="fill" if zone.active else "outline",
                color=("#8B5CF6" if zone.active else "#C4B5FD") if is_segment_pivot else ("#F59E0B" if zone.active else "#FCD34D"),
                text=("Segment Pivot Zone" if is_segment_pivot else "Pivot Zone") if zone.active else "",
                meta={
                    "reference_type": "pivot_zone",
                    "reference_id": zone.id,
                    "active": zone.active,
                    "level": zone.level,
                },
            )
        )

    for divergence in result.divergences:
        is_bullish = divergence.divergence_type == "bullish"
        text = "Bull Div" if is_bullish else "Bear Div"
        textposition = "bottom center" if is_bullish else "top center"
        primitives.append(
            PlotPrimitive(
                id=f"primitive_{divergence.id}",
                type="marker",
                layer="divergences",
                x=divergence.timestamp,
                y=float(divergence.meta.get("price", 0.0)),
                style="text",
                color="#059669" if is_bullish else "#B91C1C",
                text=text,
                meta={
                    "reference_type": "divergence",
                    "reference_id": divergence.id,
                    "divergence_type": divergence.divergence_type,
                    "strength": divergence.strength,
                    "confirmed": divergence.confirmed,
                    "source_reference_id": divergence.reference_id,
                    "textposition": textposition,
                },
            )
        )

    buy_points_by_ts = defaultdict(list)
    for point in result.candidate_buy_points:
        buy_points_by_ts[point.timestamp].append(point)

    for ts, points in buy_points_by_ts.items():
        labels = []
        for point in points:
            if point.point_type == "first_buy":
                labels.append("1B")
            elif point.point_type == "second_buy":
                labels.append("2B")
            elif point.point_type == "third_buy":
                labels.append("3B")
            else:
                labels.append("Buy?")
                
        unique_labels = list(dict.fromkeys(labels))
        if len(unique_labels) > 1 and "Buy?" in unique_labels:
            unique_labels.remove("Buy?")
            
        text = "<br>↑<br>" + ", ".join(unique_labels)
        
        base_point = points[0]
        direction = base_point.meta.get("direction", "")
        textposition = "bottom center" if direction == "down" else "top center"
        
        primitives.append(
            PlotPrimitive(
                id=f"primitive_{base_point.id}",
                type="marker",
                layer="candidate_points",
                x=base_point.timestamp,
                y=base_point.price,
                style="text",
                color="#059669",
                text=text,
                meta={
                    "reference_type": "candidate_buy_point",
                    "reference_id": base_point.id,
                    "point_type": base_point.point_type,
                    "source_reference_id": base_point.reference_id,
                    "confirmed": base_point.confirmed,
                    "signal_scope": base_point.meta.get("signal_scope", "structure_candidate_only"),
                    "textposition": textposition,
                },
            )
        )

    sell_points_by_ts = defaultdict(list)
    for point in result.candidate_sell_points:
        sell_points_by_ts[point.timestamp].append(point)

    for ts, points in sell_points_by_ts.items():
        labels = []
        for point in points:
            if point.point_type == "first_sell":
                labels.append("1S")
            elif point.point_type == "second_sell":
                labels.append("2S")
            elif point.point_type == "third_sell":
                labels.append("3S")
            else:
                labels.append("Sell?")
                
        unique_labels = list(dict.fromkeys(labels))
        if len(unique_labels) > 1 and "Sell?" in unique_labels:
            unique_labels.remove("Sell?")
            
        text = ", ".join(unique_labels) + "<br>↓<br>"
        
        base_point = points[0]
        direction = base_point.meta.get("direction", "")
        textposition = "top center" if direction == "up" else "bottom center"
        
        primitives.append(
            PlotPrimitive(
                id=f"primitive_{base_point.id}",
                type="marker",
                layer="candidate_points",
                x=base_point.timestamp,
                y=base_point.price,
                style="text",
                color="#B91C1C",
                text=text,
                meta={
                    "reference_type": "candidate_sell_point",
                    "reference_id": base_point.id,
                    "point_type": base_point.point_type,
                    "source_reference_id": base_point.reference_id,
                    "confirmed": base_point.confirmed,
                    "signal_scope": base_point.meta.get("signal_scope", "structure_candidate_only"),
                    "textposition": textposition,
                },
            )
        )

    for alert in result.structure_alerts:
        primitives.append(
            build_label(
                label_id=f"primitive_{alert.id}",
                layer="alerts",
                x=alert.timestamp,
                y=_alert_anchor(result),
                text=alert.message,
            )
        )
        primitives[-1].color = "#DC2626" if alert.severity == "warning" else "#0F766E"
        primitives[-1].meta = {
            "reference_type": "structure_alert",
            "reference_id": alert.id,
            "related_ids": list(alert.related_ids),
            "alert_type": alert.alert_type,
            "severity": alert.severity,
            "alert_meta": dict(alert.meta),
        }

    return primitives


def build_label(label_id: str, layer: str, x: str, y: float, text: str) -> PlotPrimitive:
    return PlotPrimitive(
        id=label_id,
        type="label",
        layer=layer,
        x=x,
        y=y,
        text=text,
    )


def _alert_anchor(result: AnalysisResult) -> float:
    values = [item.price for item in result.fractals]
    values.extend(item.end_price for item in result.strokes)
    values.extend(item.high for item in result.pivot_zones)
    if not values:
        return 0.0
    return max(values)
