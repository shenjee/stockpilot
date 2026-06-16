from __future__ import annotations

from typing import List

from .schema import AnalysisResult, MultiTimeframeAnalysisResult


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
        if result.divergences:
            lines.append(
                f"Divergence mapping identified {len(result.divergences)} confirmed "
                f"{'divergence' if len(result.divergences) == 1 else 'divergences'}."
            )
        if result.signal_series:
            lines.append(
                f"Signal replay tracks {len(result.signal_series)} series, {len(result.signal_snapshots)} snapshots, "
                f"and {len(result.signal_events)} transition events."
            )
        if result.candidate_point_events:
            lines.append(
                f"Candidate replay records {len(result.candidate_point_events)} buy/sell lifecycle events."
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


def build_multi_timeframe_summary(result: MultiTimeframeAnalysisResult) -> List[str]:
    lines: List[str] = []
    level_count = len(result.levels)
    if level_count:
        lines.append(
            f"{result.symbol} aggregated {level_count} timeframe analyses around base timeframe {result.base_timeframe}."
        )
    else:
        lines.append(
            f"{result.symbol} has no multi-timeframe analyses available for base timeframe {result.base_timeframe}."
        )

    if result.levels:
        higher_timeframes = [level.timeframe for level in result.levels if level.role == "higher"]
        lower_timeframes = [level.timeframe for level in result.levels if level.role == "lower"]
        base_level = next((level for level in result.levels if level.role == "base"), None)
        if base_level is not None:
            lines.append(
                f"Base timeframe {base_level.timeframe} carries {base_level.bar_count} bars, "
                f"{len(base_level.analysis.signal_events)} signal events, and "
                f"{len(base_level.analysis.candidate_point_events)} candidate replay events."
            )
        if higher_timeframes:
            lines.append(f"Higher timeframe context is available for: {', '.join(higher_timeframes)}.")
        if lower_timeframes:
            lines.append(f"Lower timeframe context is available for: {', '.join(lower_timeframes)}.")

    if result.warnings:
        lines.append(f"{len(result.warnings)} warning(s) were aggregated across timeframe levels.")
    return lines
