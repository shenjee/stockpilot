"""Shared validation helpers for T+0 runtime market-bar processing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from packages.marketdata.t0_schema import standardize_bar


MARKET_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


class RuntimeMarketDataError(ValueError):
    """Raised when standardized market input violates runtime invariants."""


def parse_market_timestamp(value: Any, *, field: str = "timestamp") -> datetime:
    if not isinstance(value, str):
        raise RuntimeMarketDataError(f"{field} must be a market timestamp string")
    try:
        parsed = datetime.strptime(value, MARKET_TIMESTAMP_FORMAT)
    except ValueError as exc:
        raise RuntimeMarketDataError(
            f"{field} must use YYYY-MM-DD HH:MM:SS"
        ) from exc
    return parsed


def parse_trade_date(value: date | str) -> date:
    if isinstance(value, datetime):
        raise RuntimeMarketDataError("trade_date must not include a time")
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise RuntimeMarketDataError("trade_date must use YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise RuntimeMarketDataError("trade_date must use YYYY-MM-DD") from exc


def validated_bar(
    row: Mapping[str, Any],
    *,
    closed: bool,
    trade_date: date | None = None,
) -> tuple[datetime, dict[str, Any]]:
    bar = _standardized_bar(row, closed=closed)
    timestamp = parse_market_timestamp(bar["timestamp"])
    if trade_date is not None and timestamp.date() != trade_date:
        raise RuntimeMarketDataError("bar timestamp is outside the runtime trade_date")
    return timestamp, bar


def _standardized_bar(
    row: Mapping[str, Any],
    *,
    closed: bool,
) -> dict[str, Any]:
    try:
        bar = standardize_bar(row)
    except (TypeError, ValueError) as exc:
        raise RuntimeMarketDataError(str(exc)) from exc
    if bar["closed"] is not closed:
        expected = "closed" if closed else "dynamic"
        raise RuntimeMarketDataError(f"expected a {expected} bar")
    return bar


def eligible_closed_bars(
    rows: Sequence[Mapping[str, Any]],
    *,
    trade_date: date,
    target_time: datetime,
) -> list[tuple[datetime, dict[str, Any]]]:
    """Validate only the input prefix at or before ``target_time``.

    Future rows are inspected only for their timestamp so Replay projection
    cannot accidentally read their prices, volume, or other market values.
    Duplicate timestamps are deterministic revisions: the last eligible row
    wins.
    """

    eligible: dict[datetime, dict[str, Any]] = {}
    for row in rows:
        timestamp_value = row.get("timestamp")
        if timestamp_value is None:
            timestamp_value = row.get("date")
        timestamp = parse_market_timestamp(timestamp_value)
        if timestamp.date() != trade_date or timestamp > target_time:
            continue
        eligible[timestamp] = _standardized_bar(row, closed=True)
    return sorted(eligible.items())


def aggregate_ohlcva(
    timestamp: str,
    bars: Sequence[Mapping[str, Any]],
    *,
    closed: bool,
) -> dict[str, Any]:
    if not bars:
        raise RuntimeMarketDataError("cannot aggregate an empty bar sequence")
    return {
        "timestamp": timestamp,
        "open": bars[0]["open"],
        "high": max(bar["high"] for bar in bars),
        "low": min(bar["low"] for bar in bars),
        "close": bars[-1]["close"],
        "volume": sum(bar["volume"] for bar in bars),
        "amount": sum(bar["amount"] for bar in bars),
        "closed": closed,
    }
