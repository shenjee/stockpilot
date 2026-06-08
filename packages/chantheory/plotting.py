from __future__ import annotations

from typing import List

from .schema import AnalysisResult, PlotPrimitive


LAYER_ORDER = (
    "candles",
    "fractals",
    "strokes",
    "segments",
    "pivot_zones",
    "divergences",
    "alerts",
)


def build_plot_primitives(result: AnalysisResult) -> List[PlotPrimitive]:
    if not result.meta.get("bar_count"):
        return []
    return []


def build_label(label_id: str, layer: str, x: str, y: float, text: str) -> PlotPrimitive:
    return PlotPrimitive(
        id=label_id,
        type="label",
        layer=layer,
        x=x,
        y=y,
        text=text,
    )
