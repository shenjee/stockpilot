from __future__ import annotations

from typing import Iterable, Mapping

from chantheory import analyze_tracker_klines


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
