from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Dict, List, Sequence

from marketdata.market_data import TencentStockDataProvider
from marketdata.repositories.kline_store import KLineStore, resolve_market_data_db_path
from marketdata.repositories.securities_store import SecuritiesStore
from marketdata.runtime_paths import RuntimePaths
from marketdata.services.kline_data_service import KLineDataService

_BARS_PER_DAY = {"1m": 240, "5m": 48, "30m": 8, "60m": 4, "day": 1}


@dataclass
class MultiTimeframeRowsResult:
    rows_by_timeframe: Dict[str, List[Dict[str, object]]]
    issues_by_timeframe: Dict[str, List[object]]


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


def fetch_rows_for_timeframes_result(
    symbol: str,
    market: str,
    timeframes: Sequence[str],
    start_date: date,
    end_date: date,
    security_type: str | None = None,
) -> MultiTimeframeRowsResult:
    """获取多时间级别的K线数据，并自动对齐到共同的时间范围。
    
    腾讯财经API对不同级别K线保留的历史数据长度不同（如5分钟仅保留约6个月），
    此函数会自动截取所有级别的交集范围，确保缠论多级别递归的基础数据对齐。
    """
    rows_by_timeframe: Dict[str, List[Dict[str, object]]] = {}
    seen: set[str] = set()
    issues_by_timeframe: Dict[str, List[object]] = {}

    # 第一轮：获取所有数据并记录实际数据范围
    timeframe_starts = {}
    timeframe_ends = {}
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
            # 记录该级别实际最早数据日期
            first_date = rows[0]["date"]
            last_date = rows[-1]["date"]
            # 处理分钟级别的时间戳格式
            if len(first_date) > 10:
                first_date = first_date[:10]
            if len(last_date) > 10:
                last_date = last_date[:10]
            timeframe_starts[timeframe] = date.fromisoformat(first_date)
            timeframe_ends[timeframe] = date.fromisoformat(last_date)

    # 第二轮：对齐时间范围 - 所有级别都从最晚的起始日期开始（取交集）
    common_start = None
    common_end = None
    if timeframe_starts:
        common_start = max(timeframe_starts.values())
        common_end = min(timeframe_ends.values())
        for timeframe in rows_by_timeframe:
            rows = rows_by_timeframe[timeframe]
            # 截取到共同的起始和结束日期
            filtered_rows = []
            for row in rows:
                row_date = row["date"]
                if len(row_date) > 10:
                    row_date = row_date[:10]
                row_date = date.fromisoformat(row_date)
                if common_start <= row_date <= common_end:
                    filtered_rows.append(row)
            rows_by_timeframe[timeframe] = filtered_rows

            # 如果起始日期被推后或结束日期被提前了，添加警告
            if timeframe_starts[timeframe] < common_start or timeframe_ends[timeframe] > common_end:
                if timeframe not in issues_by_timeframe:
                    issues_by_timeframe[timeframe] = []
                issues_by_timeframe[timeframe].append({
                    "level": "warning",
                    "reason_code": "timeframe_aligned",
                    "message": f"为确保多级别数据对齐，{timeframe}实际范围从 {timeframe_starts[timeframe]} ~ {timeframe_ends[timeframe]}，已截取为 {common_start} ~ {common_end}",
                })

    return MultiTimeframeRowsResult(
        rows_by_timeframe=rows_by_timeframe,
        issues_by_timeframe=issues_by_timeframe,
    )


def fetch_rows_for_timeframes(
    symbol: str,
    market: str,
    timeframes: Sequence[str],
    start_date: date,
    end_date: date,
    security_type: str | None = None,
) -> Dict[str, List[Dict[str, object]]]:
    """兼容版本，保持原有接口，返回仅返回 rows_by_timeframe"""
    return fetch_rows_for_timeframes_result(
        symbol=symbol,
        market=market,
        timeframes=timeframes,
        start_date=start_date,
        end_date=end_date,
        security_type=security_type,
    ).rows_by_timeframe


def fetch_stock_name(symbol: str, market: str) -> str:
    """Fetch the display name of a stock via the realtime quote endpoint.

    Returns an empty string when the name cannot be resolved so callers can
    fall back to a symbol-only title.
    """
    result = TencentStockDataProvider.realtime_result(symbol, markets=[market])
    if isinstance(result.data, dict):
        return str(result.data.get("name", "")).strip()
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
