from __future__ import annotations

from typing import List

from .schema import AnalysisResult


def build_summary(result: AnalysisResult) -> List[str]:
    lines: List[str] = []
    bar_count = int(result.meta.get("bar_count", 0))
    engine_probe = result.meta.get("engine_probe", {})

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
            f"czsc {result.engine_version} probe succeeded with {fractal_count} fractals and {finished_bi_count} finished strokes."
        )
        lines.append("P1 freezes schema and engine validation only; structure mapping stays for P2.")
        return lines

    lines.append("czsc probe is unavailable, so the adapter returns the frozen schema with warnings.")
    if result.warnings:
        lines.append(f"{len(result.warnings)} warning(s) recorded for normalization or engine readiness.")
    return lines
