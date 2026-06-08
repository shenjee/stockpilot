from __future__ import annotations

from typing import List

from .schema import AnalysisResult


def build_summary(result: AnalysisResult) -> List[str]:
    lines: List[str] = []
    bar_count = int(result.meta.get("bar_count", 0))
    engine_probe = result.meta.get("engine_probe", {})
    mapping = result.meta.get("mapping", {})

    if bar_count:
        lines.append(
            f"{result.symbol} normalized {bar_count} {result.timeframe} bars for Phase 2 analysis."
        )
    else:
        lines.append(f"{result.symbol} has no normalized bars available for Phase 2 analysis.")

    if engine_probe.get("status") == "ok":
        fractal_count = int(engine_probe.get("fractal_count", 0))
        finished_bi_count = int(engine_probe.get("finished_bi_count", 0))
        lines.append(
            f"czsc {result.engine_version} mapped {fractal_count} fractals and {finished_bi_count} finished strokes."
        )
        if result.segments or result.pivot_zones:
            lines.append(
                f"Phase 2 produced {int(mapping.get('segment_count', len(result.segments)))} segments and "
                f"{int(mapping.get('pivot_zone_count', len(result.pivot_zones)))} pivot zones."
            )
        if result.strokes:
            last_stroke = result.strokes[-1]
            lines.append(
                f"The latest confirmed stroke points {last_stroke.direction} into {last_stroke.end_timestamp}."
            )
        if result.candidate_buy_points:
            lines.append("A conservative buy-point candidate is present but not confirmed.")
        if result.candidate_sell_points:
            lines.append("A conservative sell-point candidate is present but not confirmed.")
        if any(item.warning_code == "UNSTABLE_TAIL_STROKE" for item in result.warnings):
            lines.append("The newest structure is still extending, so the tail should be treated as unstable.")
        return lines

    lines.append("czsc probe is unavailable, so the adapter returns the frozen schema with warnings.")
    if result.warnings:
        lines.append(f"{len(result.warnings)} warning(s) recorded for normalization or engine readiness.")
    return lines
