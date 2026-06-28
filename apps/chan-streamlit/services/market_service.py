from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Dict, List, Sequence

from marketdata.market_data import TencentStockDataProvider
from marketdata.repositories.kline_store import KLineStore, resolve_market_data_db_path
from marketdata.repositories.securities_store import SecuritiesStore
from marketdata.runtime_paths import RuntimePaths
from marketdata.services.kline_data_service import KLineDataService

_BARS_PER_DAY = {"1m": 240, "5m": 48, "30m": 8, "60m": 4, "day": 1}


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


@lru_cache(maxsize=1)
def _get_securities_store() -> SecuritiesStore:
    """证券主数据仓储单例，与 K 线库共用同一个 SQLite 文件。"""

    paths = _get_runtime_paths()
    db_path = resolve_market_data_db_path(paths.db_dir)
    return SecuritiesStore(db_path)


def search_securities(query: str, limit: int = 50) -> List[Dict[str, object]]:
    """按 code / 名称 / 拼音首字母搜索证券主数据，供前端下拉选择。"""

    return _get_securities_store().search(query, limit=limit)


def _estimate_limit(start_date: date, end_date: date, timeframe: str) -> int:
    bars_per_day = _BARS_PER_DAY.get(timeframe, 1)
    day_count = max((end_date - start_date).days + 1, 1)
    return max(day_count * bars_per_day, 500)


def fetch_rows(
    symbol: str,
    market: str,
    timeframe: str,
    start_date: date,
    end_date: date,
    security_type: str | None = None,
) -> List[Dict[str, object]]:
    service = _get_kline_data_service()
    return service.get_klines(
        code=symbol,
        end_date=end_date.strftime("%Y-%m-%d"),
        market=market,
        timeframe=timeframe,
        start_date=start_date.strftime("%Y-%m-%d"),
        limit=_estimate_limit(start_date, end_date, timeframe),
        security_type=security_type,
    )


def fetch_rows_for_timeframes(
    symbol: str,
    market: str,
    timeframes: Sequence[str],
    start_date: date,
    end_date: date,
    security_type: str | None = None,
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
            security_type=security_type,
        )
        if rows:
            rows_by_timeframe[timeframe] = rows
    return rows_by_timeframe


def fetch_stock_name(symbol: str, market: str) -> str:
    """Fetch the display name of a stock via the realtime quote endpoint.

    Returns an empty string when the name cannot be resolved so callers can
    fall back to a symbol-only title.
    """
    try:
        result = TencentStockDataProvider.realtime(symbol, markets=[market])
        if isinstance(result, dict):
            return str(result.get("name", "")).strip()
    except Exception:
        pass
    return ""


def probe_market_suggestions(
    symbol: str,
    selected_market: str,
    timeframe: str,
    start_date: date,
    end_date: date,
    security_type: str | None = None,
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
            security_type=security_type,
        )
        if rows:
            suggestions.append({"market": candidate, "count": len(rows)})
    return suggestions
