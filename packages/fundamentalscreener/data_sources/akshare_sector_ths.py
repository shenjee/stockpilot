"""AkShare THS(同花顺) 行业板块相关实现。"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


def list_ths_sectors(
    *,
    ak: Any,
    now_isoformat: Callable[[], str],
    to_str: Callable[[Any], Optional[str]],
    name_cache: Dict[str, str],
) -> List[Dict[str, Any]]:
    df = ak.stock_board_industry_name_ths()
    if df is None or len(df) == 0:
        return []
    fetched_at = now_isoformat()
    rows: List[Dict[str, Any]] = []
    for r in df.to_dict(orient="records"):
        sector_id = to_str(r.get("code"))
        sector_name = to_str(r.get("name"))
        if not sector_id:
            continue
        if sector_name:
            name_cache[sector_id] = sector_name
        rows.append(
            {
                "sector_id": sector_id,
                "sector_name": sector_name,
                "classification_system": "ths_industry",
                "source_updated_at": fetched_at,
            }
        )
    return rows


def get_ths_sector_name(
    *,
    ak: Any,
    sector_id: str,
    to_str: Callable[[Any], Optional[str]],
    name_cache: Dict[str, str],
) -> Optional[str]:
    if sector_id in name_cache:
        return name_cache[sector_id]
    df = ak.stock_board_industry_name_ths()
    if df is None or len(df) == 0:
        return None
    for r in df.to_dict(orient="records"):
        code = to_str(r.get("code"))
        name = to_str(r.get("name"))
        if code and name:
            name_cache[code] = name
    return name_cache.get(sector_id)


def get_ths_constituents(
    *,
    ak: Any,
    sector_id: str,
    as_of_date: str,
    now_isoformat: Callable[[], str],
    to_str: Callable[[Any], Optional[str]],
    scrape_ths_constituents: Callable[[str], List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    if not sector_id:
        return []
    cons_fn = getattr(ak, "stock_board_industry_cons_ths", None)
    if cons_fn is not None:
        df = cons_fn(symbol=sector_id)
        if df is None or len(df) == 0:
            return []
        records = df.to_dict(orient="records")
    else:
        records = scrape_ths_constituents(sector_id)
        if not records:
            return []

    fetched_at = now_isoformat()
    rows: List[Dict[str, Any]] = []
    for r in records:
        code = to_str(r.get("代码"))
        if not code:
            continue
        rows.append(
            {
                "sector_id": sector_id,
                "classification_system": "ths_industry",
                "code": code,
                "as_of_date": as_of_date,
                "source_updated_at": fetched_at,
            }
        )
    return rows


def get_ths_sector_daily(
    *,
    ak: Any,
    sector_id: str,
    start_date: str,
    end_date: str,
    now_isoformat: Callable[[], str],
    compact_date: Callable[[str], str],
    normalize_date: Callable[[Any], Optional[str]],
    to_float: Callable[[Any], Optional[float]],
    get_ths_sector_name: Callable[[str], Optional[str]],
) -> List[Dict[str, Any]]:
    if not sector_id:
        return []
    sector_name = get_ths_sector_name(sector_id)
    if not sector_name:
        return []
    df = ak.stock_board_industry_index_ths(
        symbol=sector_name,
        start_date=compact_date(start_date),
        end_date=compact_date(end_date),
    )
    if df is None or len(df) == 0:
        return []
    fetched_at = now_isoformat()
    rows: List[Dict[str, Any]] = []
    for r in df.to_dict(orient="records"):
        trade_date = normalize_date(r.get("日期"))
        if not trade_date:
            continue
        rows.append(
            {
                "sector_id": sector_id,
                "classification_system": "ths_industry",
                "trade_date": trade_date,
                "open": to_float(r.get("开盘价")),
                "high": to_float(r.get("最高价")),
                "low": to_float(r.get("最低价")),
                "close": to_float(r.get("收盘价")),
                "turnover_amount": to_float(r.get("成交额")),
                "source_updated_at": fetched_at,
            }
        )
    return rows


__all__ = [
    "get_ths_constituents",
    "get_ths_sector_daily",
    "get_ths_sector_name",
    "list_ths_sectors",
]
