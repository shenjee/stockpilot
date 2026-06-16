from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Dict, List, Sequence

from market_data import TencentStockDataProvider
from repositories.kline_store import KLineStore, resolve_market_data_db_path
from runtime_paths import RuntimePaths
from services.kline_data_service import KLineDataService


@lru_cache(maxsize=1)
def _get_runtime_paths() -> RuntimePaths:
    return RuntimePaths()


@lru_cache(maxsize=1)
def _get_kline_data_service() -> KLineDataService:
    paths = _get_runtime_paths()
    db_path = resolve_market_data_db_path(paths.db_dir)
    store = KLineStore(db_path)
    provider = TencentStockDataProvider()
    return KLineDataService(provider, store)


def fetch_rows(
    symbol: str,
    market: str,
    timeframe: str,
    start_date: date,
    end_date: date,
) -> List[Dict[str, object]]:
    service = _get_kline_data_service()
    return service.get_klines(
        code=symbol,
        end_date=end_date.strftime("%Y-%m-%d"),
        market=market,
        timeframe=timeframe,
        start_date=start_date.strftime("%Y-%m-%d"),
        limit=500,
    )


def fetch_rows_for_timeframes(
    symbol: str,
    market: str,
    timeframes: Sequence[str],
    start_date: date,
    end_date: date,
) -> Dict[str, List[Dict[str, object]]]:
    rows_by_timeframe: Dict[str, List[Dict[str, object]]] = {}
    seen: set[str] = set()
    for timeframe in timeframes:
        if timeframe in seen:
            continue
        seen.add(timeframe)
        rows = fetch_rows(
            symbol=symbol,
            market=market,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
        )
        if rows:
            rows_by_timeframe[timeframe] = rows
    return rows_by_timeframe


def probe_market_suggestions(
    symbol: str,
    selected_market: str,
    timeframe: str,
    start_date: date,
    end_date: date,
) -> List[Dict[str, object]]:
    suggestions: List[Dict[str, object]] = []
    for candidate in ["sh", "sz", "bj"]:
        if candidate == selected_market:
            continue
        rows = fetch_rows(
            symbol=symbol,
            market=candidate,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
        )
        if rows:
            suggestions.append({"market": candidate, "count": len(rows)})
    return suggestions
