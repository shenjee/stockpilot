from __future__ import annotations

from typing import Dict, Iterable, Mapping

from chantheory import analyze_multi_timeframe_tracker_klines, analyze_tracker_klines


def run_analysis(
    rows: Iterable[Mapping[str, object]],
    symbol: str,
    market: str,
    timeframe: str,
    max_bi_num: int,
    min_bars: int,
    strict_validation: bool,
):
    return analyze_tracker_klines(
        rows=rows,
        code=symbol,
        market=market,
        timeframe=timeframe,
        parameters={
            "max_bi_num": int(max_bi_num),
            "min_bars": int(min_bars),
            "strict_validation": bool(strict_validation),
        },
        strict=bool(strict_validation),
    )


def run_multi_timeframe_analysis(
    rows_by_timeframe: Mapping[str, Iterable[Mapping[str, object]]],
    symbol: str,
    market: str,
    base_timeframe: str,
    max_bi_num: int,
    min_bars: int,
    strict_validation: bool,
):
    return analyze_multi_timeframe_tracker_klines(
        rows_by_timeframe=rows_by_timeframe,
        code=symbol,
        market=market,
        base_timeframe=base_timeframe,
        parameters={
            "max_bi_num": int(max_bi_num),
            "min_bars": int(min_bars),
            "strict_validation": bool(strict_validation),
        },
        strict=bool(strict_validation),
    )
