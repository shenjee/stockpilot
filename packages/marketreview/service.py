"""V1 orchestration helpers for daily market review."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol

from packages.marketdata.market_data import MarketDataProvider
from packages.marketdata.trading_calendar import TradingCalendar

from .errors import InvalidTradeDateError
from .repository import MarketReviewRepository
from .schema import MetricProvenance
from .sqlite_schema import utc_now_iso
from .validation import assert_writable_trade_date, resolve_trade_date

INDEX_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("sh_index_close", "sh_index_prev_close", "000001", "sh"),
    ("sz_index_close", "sz_index_prev_close", "399001", "sz"),
    ("cy_index_close", "cy_index_prev_close", "399006", "sz"),
)

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


class IndexKlineProvider(Protocol):
    def get_kline(
        self,
        code: str,
        start_date: str,
        end_date: str,
        *,
        ktype: str = "day",
        market: str | None = None,
        security_type: str | None = None,
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class IndexFetchResult:
    fields: dict[str, float]
    provenance: dict[str, MetricProvenance]
    failures: tuple[str, ...]


def _parse_finite_float(value: Any, field_name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}: close 非数值") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name}: close 非有限数值")
    return parsed


def resolve_review_trade_date(
    calendar: TradingCalendar,
    *,
    requested: str | None = None,
    now: datetime | None = None,
) -> str:
    return resolve_trade_date(calendar, requested=requested, now=now)


def fetch_index_atoms(
    provider: IndexKlineProvider | MarketDataProvider,
    calendar: TradingCalendar,
    trade_date: str,
    *,
    retrieved_at: str | None = None,
    now: datetime | None = None,
) -> IndexFetchResult:
    assert_writable_trade_date(trade_date, calendar, now=now)
    previous_day = calendar.previous_trading_day(trade_date, "sh")
    if previous_day is None:
        raise InvalidTradeDateError(f"无法确定 {trade_date} 的上一交易日。")

    fields: dict[str, float] = {}
    provenance: dict[str, MetricProvenance] = {}
    failures: list[str] = []
    retrieved = retrieved_at or utc_now_iso()

    for close_field, prev_field, code, market in INDEX_SPECS:
        try:
            current_rows = provider.get_kline(
                code,
                trade_date,
                trade_date,
                ktype="day",
                market=market,
                security_type="index",
            )
            prev_rows = provider.get_kline(
                code,
                previous_day.isoformat(),
                previous_day.isoformat(),
                ktype="day",
                market=market,
                security_type="index",
            )
        except Exception as exc:
            failures.append(f"{close_field}: {exc}")
            continue

        if not current_rows or not prev_rows:
            failures.append(close_field)
            continue

        close_value = current_rows[0].get("close")
        prev_value = prev_rows[0].get("close")
        if close_value is None or prev_value is None:
            failures.append(close_field)
            continue

        try:
            parsed_close = _parse_finite_float(close_value, close_field)
            parsed_prev = _parse_finite_float(prev_value, prev_field)
        except ValueError as exc:
            failures.append(str(exc))
            continue

        fields[close_field] = parsed_close
        fields[prev_field] = parsed_prev
        for metric_name in (close_field, prev_field):
            provenance[metric_name] = MetricProvenance(
                source="tencent",
                source_as_of=trade_date,
                retrieved_at=retrieved,
                acquisition_mode="auto",
            )

    return IndexFetchResult(
        fields=fields,
        provenance=provenance,
        failures=tuple(failures),
    )


def auto_patch_indices(
    repository: MarketReviewRepository,
    provider: IndexKlineProvider | MarketDataProvider,
    calendar: TradingCalendar,
    trade_date: str,
    *,
    now: datetime | None = None,
) -> IndexFetchResult:
    writable_date = assert_writable_trade_date(trade_date, calendar, now=now)
    result = fetch_index_atoms(provider, calendar, writable_date, now=now)
    if result.fields:
        repository.patch_review(
            writable_date,
            fields=result.fields,
            provenance=result.provenance,
            now=now,
        )
    return result


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
    if atoms is None or atoms.ladder_status != "complete":
        missing.append("ladder_snapshot")
    return missing
