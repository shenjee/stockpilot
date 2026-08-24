"""Runtime validation for market review writes."""

from __future__ import annotations

import math
import re
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from packages.marketdata.trading_calendar import TradingCalendar

from .errors import InvalidFieldValueError, InvalidTradeDateError
from .schema import LadderStockInput

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CHINA_TZ = ZoneInfo("Asia/Shanghai")
_MARKET_CLOSE = time(15, 0)
_INT_FIELDS = frozenset(
    {
        "effective_limit_up",
        "limit_up_20pct",
        "opened_limit_down",
        "closed_limit_down",
        "limit_up_failed",
        "pullback_count",
        "advancing_count",
        "declining_count",
    }
)
_FLOAT_FIELDS = frozenset(
    {
        "median_change_pct",
        "margin_balance_sh",
        "margin_balance_sz",
        "margin_balance_bj",
        "sh_index_close",
        "sh_index_prev_close",
        "sz_index_close",
        "sz_index_prev_close",
        "cy_index_close",
        "cy_index_prev_close",
        "turnover_amount_sh",
        "turnover_amount_sz",
        "turnover_amount_cy",
        "turnover_amount_bj",
        "total_market_cap",
        "float_market_cap",
        "pe_sh",
        "pe_sz",
        "pe_cy",
        "pe_all",
        "avg_stock_price",
    }
)
_MONEY_FIELDS = frozenset(
    {
        "margin_balance_sh",
        "margin_balance_sz",
        "margin_balance_bj",
        "turnover_amount_sh",
        "turnover_amount_sz",
        "turnover_amount_cy",
        "turnover_amount_bj",
        "total_market_cap",
        "float_market_cap",
        "avg_stock_price",
    }
)


def parse_trade_date(value: str) -> date:
    if not _DATE_PATTERN.fullmatch(value):
        raise InvalidTradeDateError(f"日期格式必须为 YYYY-MM-DD：{value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidTradeDateError(f"日期无效：{value!r}") from exc


def is_market_closed(trade_date: date, *, now: datetime | None = None) -> bool:
    current = now or datetime.now(_CHINA_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_CHINA_TZ)
    else:
        current = current.astimezone(_CHINA_TZ)
    if trade_date < current.date():
        return True
    if trade_date > current.date():
        return False
    return current.time() >= _MARKET_CLOSE


def latest_closed_trading_day(
    calendar: TradingCalendar,
    *,
    now: datetime | None = None,
    market: str = "sh",
) -> date:
    current = now or datetime.now(_CHINA_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_CHINA_TZ)
    else:
        current = current.astimezone(_CHINA_TZ)
    candidate = current.date()
    if calendar.is_trading_day(candidate, market) and is_market_closed(candidate, now=current):
        return candidate
    previous = calendar.previous_trading_day(candidate, market)
    if previous is None:
        raise InvalidTradeDateError("无法确定最近一个已收盘交易日。")
    return previous


def resolve_trade_date(
    calendar: TradingCalendar,
    *,
    requested: str | None = None,
    now: datetime | None = None,
    market: str = "sh",
) -> str:
    if requested is None:
        return latest_closed_trading_day(calendar, now=now, market=market).isoformat()
    trade_date = parse_trade_date(requested)
    current = now or datetime.now(_CHINA_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_CHINA_TZ)
    else:
        current = current.astimezone(_CHINA_TZ)
    if trade_date > current.date():
        raise InvalidTradeDateError(f"{requested} 是未来日期，不能生成正式记录。")
    if not calendar.is_trading_day(trade_date, market):
        nearest = latest_closed_trading_day(calendar, now=now, market=market).isoformat()
        raise InvalidTradeDateError(
            f"{requested} 不是交易日，不能落库。最近已收盘交易日：{nearest}",
            suggested_date=nearest,
        )
    if trade_date == current.date() and current.time() < _MARKET_CLOSE:
        raise InvalidTradeDateError(f"{requested} 尚未收盘，不能生成正式记录。")
    return trade_date.isoformat()


def assert_writable_trade_date(
    trade_date: str,
    calendar: TradingCalendar,
    *,
    now: datetime | None = None,
    market: str = "sh",
) -> str:
    return resolve_trade_date(calendar, requested=trade_date, now=now, market=market)


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _round_money(value: float) -> float:
    from decimal import Decimal, ROUND_HALF_UP

    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def validate_atomic_field(key: str, value: Any) -> Any:
    if value is None:
        return None
    if key in _INT_FIELDS:
        if type(value) is not int:
            raise InvalidFieldValueError(f"{key} 必须为非负整数")
        if value < 0:
            raise InvalidFieldValueError(f"{key} 必须为非负整数")
        return value
    if key in _FLOAT_FIELDS:
        if not _is_finite_number(value):
            raise InvalidFieldValueError(f"{key} 必须为有限数值")
        normalized = float(value)
        if key in _MONEY_FIELDS:
            return _round_money(normalized)
        return normalized
    raise InvalidFieldValueError(f"未知字段：{key}")


def validate_ladder_stock_input(stock: LadderStockInput) -> None:
    if type(stock.is_st) is not bool:
        raise InvalidFieldValueError(
            f"连板股票 {stock.market}.{stock.code} 必须显式提交 is_st=false"
        )
    if stock.is_st:
        return
    if type(stock.streak_height) is not int or isinstance(stock.streak_height, bool):
        raise InvalidFieldValueError(
            f"连板高度必须为整数：{stock.market}.{stock.code} streak_height={stock.streak_height!r}"
        )
    if stock.streak_height < 2:
        raise InvalidFieldValueError(
            f"连板高度必须 >= 2：{stock.market}.{stock.code} streak_height={stock.streak_height}"
        )
    if type(stock.name) is not str:
        raise InvalidFieldValueError(
            f"股票名称必须为字符串：{stock.market}.{stock.code}"
        )
    if not stock.name or not stock.name.strip():
        raise InvalidFieldValueError(f"股票名称不能为空：{stock.market}.{stock.code}")
