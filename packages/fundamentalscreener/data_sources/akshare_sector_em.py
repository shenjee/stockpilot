"""AkShare 东方财富(EM) 行业板块相关实现。"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


def list_em_sectors(
    *,
    ak: Any,
    now_isoformat: Callable[[], str],
    to_str: Callable[[Any], Optional[str]],
) -> List[Dict[str, Any]]:
    df = ak.stock_board_industry_name_em()
    if df is None or len(df) == 0:
        return []
    records = df.to_dict(orient="records")
    fetched_at = now_isoformat()
    rows: List[Dict[str, Any]] = []
    for r in records:
        sector_id = to_str(r.get("板块代码"))
        sector_name = to_str(r.get("板块名称"))
        if not sector_id:
            continue
        rows.append(
            {
                "sector_id": sector_id,
                "sector_name": sector_name,
                "classification_system": "em_industry",
                "source_updated_at": fetched_at,
            }
        )
    return rows


def get_em_constituents(
    *,
    ak: Any,
    sector_id: str,
    as_of_date: str,
    now_isoformat: Callable[[], str],
    to_str: Callable[[Any], Optional[str]],
) -> List[Dict[str, Any]]:
    if not sector_id:
        return []
    df = ak.stock_board_industry_cons_em(symbol=sector_id)
    if df is None or len(df) == 0:
        return []
    records = df.to_dict(orient="records")
    fetched_at = now_isoformat()
    rows: List[Dict[str, Any]] = []
    for r in records:
        code = to_str(r.get("代码"))
        if not code:
            continue
        rows.append(
            {
                "sector_id": sector_id,
                "classification_system": "em_industry",
                "code": code,
                "as_of_date": as_of_date,
                "source_updated_at": fetched_at,
            }
        )
    return rows


def get_em_sector_daily(
    *,
    ak: Any,
    sector_id: str,
    start_date: str,
    end_date: str,
    now_isoformat: Callable[[], str],
    compact_date: Callable[[str], str],
    normalize_date: Callable[[Any], Optional[str]],
    to_float: Callable[[Any], Optional[float]],
) -> List[Dict[str, Any]]:
    if not sector_id:
        return []
    df = ak.stock_board_industry_hist_em(
        symbol=sector_id,
        start_date=compact_date(start_date),
        end_date=compact_date(end_date),
        period="日k",
        adjust="",
    )
    if df is None or len(df) == 0:
        return []
    records = df.to_dict(orient="records")
    fetched_at = now_isoformat()
    rows: List[Dict[str, Any]] = []
    for r in records:
        trade_date = normalize_date(r.get("日期"))
        if not trade_date:
            continue
        rows.append(
            {
                "sector_id": sector_id,
                "classification_system": "em_industry",
                "trade_date": trade_date,
                "open": to_float(r.get("开盘")),
                "high": to_float(r.get("最高")),
                "low": to_float(r.get("最低")),
                "close": to_float(r.get("收盘")),
                "turnover_amount": to_float(r.get("成交额")),
                "source_updated_at": fetched_at,
            }
        )
    return rows


__all__ = [
    "get_em_constituents",
    "get_em_sector_daily",
    "list_em_sectors",
]
