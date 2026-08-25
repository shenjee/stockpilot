"""V1 helpers for daily market review."""

from __future__ import annotations

from datetime import datetime
from typing import Mapping

from packages.marketdata.trading_calendar import TradingCalendar

from .repository import MarketReviewRepository
from .validation import resolve_trade_date

TRACKED_MISSING_FIELDS: tuple[str, ...] = (
    "effective_limit_up",
    "limit_up_20pct",
    "opened_limit_down",
    "closed_limit_down",
    "limit_up_failed",
    "pullback_count",
    "median_change_pct",
    "advancing_count",
    "declining_count",
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
)


def resolve_review_trade_date(
    calendar: TradingCalendar,
    *,
    requested: str | None = None,
    now: datetime | None = None,
) -> str:
    return resolve_trade_date(calendar, requested=requested, now=now)


def missing_atomic_fields(
    repository: MarketReviewRepository,
    trade_date: str,
    *,
    field_names: Mapping[str, str] | None = None,
) -> list[str]:
    view = repository.get_review(trade_date)
    atoms = view.atoms if view is not None else None
    names = field_names or {name: name for name in TRACKED_MISSING_FIELDS}
    missing: list[str] = []
    for field_name in names:
        if atoms is None or getattr(atoms, field_name) is None:
            missing.append(field_name)
    return missing
