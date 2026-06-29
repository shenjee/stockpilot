"""AkShare 公司层（股票池 / 日度快照 / 估值 / 财务）实现。"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence


def get_stock_universe(
    *,
    ak: Any,
    as_of_date: str,
    now_isoformat: Callable[[], str],
    to_str: Callable[[Any], Optional[str]],
    derive_market: Callable[[str], Optional[str]],
) -> List[Dict[str, Any]]:
    df = ak.stock_info_a_code_name()
    if df is None or len(df) == 0:
        return []
    records = df.to_dict(orient="records")
    fetched_at = now_isoformat()
    rows: List[Dict[str, Any]] = []
    for r in records:
        code = to_str(r.get("code"))
        if not code:
            continue
        name = to_str(r.get("name"))
        rows.append(
            {
                "code": code,
                "name": name,
                "market": derive_market(code),
                "listing_status": "L",
                "delisted_at": None,
                "as_of_date": as_of_date,
                "source_updated_at": fetched_at,
            }
        )
    return rows


def all_codes(
    *, ak: Any, to_str: Callable[[Any], Optional[str]]
) -> List[str]:
    df = ak.stock_info_a_code_name()
    if df is None or len(df) == 0:
        return []
    return [to_str(r.get("code")) for r in df.to_dict(orient="records") if to_str(r.get("code"))]


def fetch_code_daily(
    *,
    ak: Any,
    code: str,
    trade_date: str,
    compact_date: Callable[[str], str],
    normalize_date: Callable[[Any], Optional[str]],
    to_float: Callable[[Any], Optional[float]],
    to_sina_symbol: Callable[[str], str],
) -> Optional[Dict[str, Any]]:
    from datetime import date as _date, timedelta as _td

    end = _date.fromisoformat(trade_date)
    start = (end - _td(days=12)).isoformat()
    symbol = to_sina_symbol(code)
    df = ak.stock_zh_a_daily(
        symbol=symbol,
        start_date=compact_date(start),
        end_date=compact_date(trade_date),
        adjust="",
    )
    if df is None or len(df) == 0:
        return None
    records = [
        r
        for r in df.to_dict(orient="records")
        if normalize_date(r.get("date")) and normalize_date(r.get("date")) <= trade_date
    ]
    if not records:
        return None
    records.sort(key=lambda r: normalize_date(r.get("date")))  # type: ignore[arg-type]
    latest = records[-1]
    close = to_float(latest.get("close"))
    outstanding = to_float(latest.get("outstanding_share"))
    market_cap = close * outstanding if (close is not None and outstanding is not None) else None
    change_pct: Optional[float] = None
    if len(records) >= 2:
        prev_close = to_float(records[-2].get("close"))
        if prev_close:
            change_pct = (close - prev_close) / prev_close if close is not None else None
    return {
        "trade_date": normalize_date(latest.get("date")),
        "close": close,
        "turnover_amount": to_float(latest.get("amount")),
        "turnover_rate": to_float(latest.get("turnover")),
        "market_cap": market_cap,
        "change_pct": change_pct,
    }


def get_company_daily_snapshot(
    *,
    ak: Any,
    trade_date: str,
    codes: Optional[Sequence[str]],
    now_isoformat: Callable[[], str],
    all_codes: Callable[[], List[str]],
    fetch_code_daily: Callable[[str, str], Optional[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    if codes is None:
        codes = all_codes()
    fetched_at = now_isoformat()
    rows: List[Dict[str, Any]] = []
    for code in codes:
        if not code:
            continue
        try:
            daily = fetch_code_daily(code, trade_date)
        except Exception:
            continue
        if not daily:
            continue
        rows.append(daily | {"code": code, "source_updated_at": fetched_at})
    return rows


def fetch_baidu_indicator(
    *,
    ak: Any,
    code: str,
    indicator: str,
    normalize_date: Callable[[Any], Optional[str]],
    to_float: Callable[[Any], Optional[float]],
) -> Dict[str, Optional[float]]:
    df = ak.stock_zh_valuation_baidu(symbol=code, indicator=indicator, period="全部")
    if df is None or len(df) == 0:
        return {}
    result: Dict[str, Optional[float]] = {}
    for r in df.to_dict(orient="records"):
        d = normalize_date(r.get("date"))
        if not d:
            continue
        result[d] = to_float(r.get("value"))
    return result


def get_company_valuation_history(
    *,
    ak: Any,
    codes: Sequence[str],
    start_date: str,
    end_date: str,
    now_isoformat: Callable[[], str],
    fetch_baidu_indicator: Callable[[str, str], Dict[str, Optional[float]]],
    derive_market: Callable[[str], Optional[str]],
) -> List[Dict[str, Any]]:
    fetched_at = now_isoformat()
    rows: List[Dict[str, Any]] = []
    for code in codes:
        if not code:
            continue
        try:
            pe_map = fetch_baidu_indicator(code, "市盈率(TTM)")
            pb_map = fetch_baidu_indicator(code, "市净率")
        except Exception:
            continue
        all_dates = sorted(set(pe_map.keys()) | set(pb_map.keys()))
        for d in all_dates:
            if d < start_date or d > end_date:
                continue
            rows.append(
                {
                    "code": code,
                    "trade_date": d,
                    "market": derive_market(code),
                    "pe": pe_map.get(d),
                    "pb": pb_map.get(d),
                    "ps": None,
                    "dividend_yield": None,
                    "source_updated_at": fetched_at,
                }
            )
    return rows


def get_financial_metrics(
    *,
    ak: Any,
    codes: Sequence[str],
    as_of_date: str,
    now_isoformat: Callable[[], str],
    normalize_date: Callable[[Any], Optional[str]],
    estimate_disclosure_date: Callable[[str], str],
    derive_report_period: Callable[[str], str],
    derive_period_type: Callable[[str], str],
    pct_to_ratio: Callable[[Any], Optional[float]],
) -> List[Dict[str, Any]]:
    start_year = str(int(as_of_date[:4]) - 5)
    fetched_at = now_isoformat()
    rows: List[Dict[str, Any]] = []
    for code in codes:
        if not code:
            continue
        try:
            df = ak.stock_financial_analysis_indicator(symbol=code, start_year=start_year)
        except Exception:
            continue
        if df is None or len(df) == 0:
            continue
        for r in df.to_dict(orient="records"):
            period_end = normalize_date(r.get("日期"))
            if not period_end:
                continue
            disclosure_date = estimate_disclosure_date(period_end)
            if disclosure_date > as_of_date:
                continue
            rows.append(
                {
                    "code": code,
                    "report_period": derive_report_period(period_end),
                    "period_end_date": period_end,
                    "disclosure_date": disclosure_date,
                    "period_type": derive_period_type(period_end),
                    "as_of_date": as_of_date,
                    "revenue_yoy": pct_to_ratio(r.get("主营业务收入增长率(%)")),
                    "net_profit_yoy": pct_to_ratio(r.get("净利润增长率(%)")),
                    "deducted_net_profit_yoy": None,
                    "gross_margin": pct_to_ratio(r.get("销售毛利率(%)")),
                    "net_margin": pct_to_ratio(r.get("销售净利率(%)")),
                    "roe": pct_to_ratio(r.get("净资产收益率(%)")),
                    "operating_cashflow_to_profit": pct_to_ratio(
                        r.get("经营现金净流量与净利润的比率(%)")
                    ),
                    "free_cashflow": None,
                    "debt_to_asset": pct_to_ratio(r.get("资产负债率(%)")),
                    "interest_bearing_debt_ratio": pct_to_ratio(r.get("长期负债比率(%)")),
                    "accounts_receivable_yoy": None,
                    "inventory_yoy": None,
                    "gross_margin_yoy_change": None,
                    "source_updated_at": fetched_at,
                }
            )
    return rows


__all__ = [
    "all_codes",
    "fetch_baidu_indicator",
    "fetch_code_daily",
    "get_company_daily_snapshot",
    "get_company_valuation_history",
    "get_financial_metrics",
    "get_stock_universe",
]
