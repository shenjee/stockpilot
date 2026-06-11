from __future__ import annotations

from typing import List

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
            text = "T"
            textposition = "top center"
        elif fractal.fractal_type == "bottom":
            color = "#16A34A"
            style = "triangle_up"
            text = "B"
            textposition = "bottom center"
        else:
            color = "#6B7280"
            style = "circle"
            text = "?"
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

    for segment in result.segments:
        primitives.append(
            PlotPrimitive(
                id=f"primitive_{segment.id}",
                type="line",
                layer="segments",
                x1=segment.start_timestamp,
                y1=segment.start_price,
                x2=segment.end_timestamp,
                y2=segment.end_price,
                style="dashed",
                color="#7C3AED" if segment.direction == "up" else "#9333EA",
                meta={
                    "reference_type": "segment",
                    "reference_id": segment.id,
                    "confirmed": segment.confirmed,
                },
            )
        )

    for zone in result.pivot_zones:
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
                color="#F59E0B" if zone.active else "#FCD34D",
                text="Pivot Zone" if zone.active else "",
                meta={
                    "reference_type": "pivot_zone",
                    "reference_id": zone.id,
                    "active": zone.active,
                    "level": zone.level,
                },
            )
        )

    for point in result.candidate_buy_points:
        primitives.append(
            PlotPrimitive(
                id=f"primitive_{point.id}",
                type="marker",
                layer="candidate_points",
                x=point.timestamp,
                y=point.price,
                style="diamond",
                color="#059669",
                text="Buy?",
                meta={
                    "reference_type": "candidate_buy_point",
                    "reference_id": point.id,
                    "point_type": point.point_type,
                    "source_reference_id": point.reference_id,
                    "confirmed": point.confirmed,
                    "signal_scope": point.meta.get("signal_scope", "structure_candidate_only"),
                },
            )
        )

    for point in result.candidate_sell_points:
        primitives.append(
            PlotPrimitive(
                id=f"primitive_{point.id}",
                type="marker",
                layer="candidate_points",
                x=point.timestamp,
                y=point.price,
                style="diamond",
                color="#B91C1C",
                text="Sell?",
                meta={
                    "reference_type": "candidate_sell_point",
                    "reference_id": point.id,
                    "point_type": point.point_type,
                    "source_reference_id": point.reference_id,
                    "confirmed": point.confirmed,
                    "signal_scope": point.meta.get("signal_scope", "structure_candidate_only"),
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
