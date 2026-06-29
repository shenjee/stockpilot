"""AkShare 基准指数日线（新浪指数源）。"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


def get_benchmark_daily(
    *,
    ak: Any,
    benchmark: str,
    start_date: str,
    end_date: str,
    benchmark_symbols: Dict[str, str],
    now_isoformat: Callable[[], str],
    normalize_date: Callable[[Any], Optional[str]],
    to_float: Callable[[Any], Optional[float]],
    to_sina_index_symbol: Callable[[str], str],
) -> List[Dict[str, Any]]:
    symbol = benchmark_symbols.get(benchmark)
    if symbol is None:
        raise ValueError(
            f"unsupported benchmark: {benchmark!r}. Supported: {sorted(benchmark_symbols)}"
        )

    sina_symbol = to_sina_index_symbol(symbol)
    df = ak.stock_zh_index_daily(symbol=sina_symbol)
    if df is None or len(df) == 0:
        return []
    records = df.to_dict(orient="records")
    fetched_at = now_isoformat()
    rows: List[Dict[str, Any]] = []
    for r in records:
        trade_date = normalize_date(r.get("date"))
        if not trade_date:
            continue
        if trade_date < start_date or trade_date > end_date:
            continue
        rows.append(
            {
                "benchmark": benchmark,
                "trade_date": trade_date,
                "open": to_float(r.get("open")),
                "high": to_float(r.get("high")),
                "low": to_float(r.get("low")),
                "close": to_float(r.get("close")),
                "turnover_amount": None,
                "source_updated_at": fetched_at,
            }
        )
    return rows


__all__ = ["get_benchmark_daily"]
