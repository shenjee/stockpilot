"""Dynamic daily bar and target-time quote projection."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from packages.marketdata.t0_schema import standardize_quote

from ._market_bars import (
    RuntimeMarketDataError,
    aggregate_ohlcva,
    eligible_closed_bars,
    parse_market_timestamp,
    parse_trade_date,
)


@dataclass(frozen=True, slots=True)
class TargetTimeMarketProjection:
    """Market facts available at one Live or Replay target time."""

    daily_bar: dict[str, Any] | None
    quote: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "daily_bar": None if self.daily_bar is None else dict(self.daily_bar),
            "quote": None if self.quote is None else dict(self.quote),
        }


def build_dynamic_daily_bar(
    bars_1m: Sequence[Mapping[str, Any]],
    *,
    trade_date: date | str,
    target_time: datetime | str,
) -> dict[str, Any] | None:
    """Aggregate only target-day 1m bars at or before ``target_time``."""

    resolved_date, resolved_target = _projection_time(trade_date, target_time)
    eligible = eligible_closed_bars(
        bars_1m,
        trade_date=resolved_date,
        target_time=resolved_target,
    )
    if not eligible:
        return None
    return aggregate_ohlcva(
        resolved_date.isoformat(),
        [bar for _, bar in eligible],
        closed=False,
    )


def project_quote_at(
    bars_1m: Sequence[Mapping[str, Any]],
    *,
    trade_date: date | str,
    target_time: datetime | str,
    previous_close: float | int | None,
    quote_snapshots: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any] | None:
    """Build the freshest quote without inspecting future snapshot values.

    Core OHLCVA fields advance from eligible 1m bars.  A quote snapshot at or
    before the target may be fresher and then remains authoritative.  When the
    minute-derived core is fresher, only the snapshot's optional fields are
    carried forward.  Missing Replay-only optional fields remain ``None``.
    """

    resolved_previous_close = _previous_close(previous_close)
    resolved_date, resolved_target = _projection_time(trade_date, target_time)
    eligible_bars = eligible_closed_bars(
        bars_1m,
        trade_date=resolved_date,
        target_time=resolved_target,
    )
    latest_snapshot = _latest_eligible_quote(
        quote_snapshots,
        trade_date=resolved_date,
        target_time=resolved_target,
    )

    if latest_snapshot is not None:
        snapshot_time, snapshot = latest_snapshot
        if not eligible_bars or snapshot_time >= eligible_bars[-1][0]:
            return dict(snapshot)

    if not eligible_bars:
        return None
    snapshot_previous_close = (
        latest_snapshot[1]["previous_close"] if latest_snapshot is not None else None
    )
    if resolved_previous_close is None:
        resolved_previous_close = _previous_close(snapshot_previous_close)
    if resolved_previous_close is None:
        return None

    bars = [bar for _, bar in eligible_bars]
    latest_time = eligible_bars[-1][0]
    latest_price = bars[-1]["close"]
    optional_source = latest_snapshot[1] if latest_snapshot is not None else {}
    change_percent = (
        0.0
        if resolved_previous_close == 0
        else (latest_price - resolved_previous_close) / resolved_previous_close * 100
    )
    return {
        "timestamp": latest_time.strftime("%Y-%m-%d %H:%M:%S"),
        "latest_price": latest_price,
        "change_percent": change_percent,
        "open": bars[0]["open"],
        "high": max(bar["high"] for bar in bars),
        "low": min(bar["low"] for bar in bars),
        "previous_close": resolved_previous_close,
        "volume": sum(bar["volume"] for bar in bars),
        "amount": sum(bar["amount"] for bar in bars),
        "volume_ratio": optional_source.get("volume_ratio"),
        "order_imbalance": optional_source.get("order_imbalance"),
        "turnover_rate": optional_source.get("turnover_rate"),
    }


def project_market_at(
    bars_1m: Sequence[Mapping[str, Any]],
    *,
    trade_date: date | str,
    target_time: datetime | str,
    previous_close: float | int | None,
    quote_snapshots: Sequence[Mapping[str, Any]] = (),
) -> TargetTimeMarketProjection:
    """Project dynamic daily and quote facts from the same input prefix."""

    return TargetTimeMarketProjection(
        daily_bar=build_dynamic_daily_bar(
            bars_1m,
            trade_date=trade_date,
            target_time=target_time,
        ),
        quote=project_quote_at(
            bars_1m,
            trade_date=trade_date,
            target_time=target_time,
            previous_close=previous_close,
            quote_snapshots=quote_snapshots,
        ),
    )


def _projection_time(
    trade_date: date | str,
    target_time: datetime | str,
) -> tuple[date, datetime]:
    resolved_date = parse_trade_date(trade_date)
    if isinstance(target_time, datetime):
        if target_time.tzinfo is not None:
            raise RuntimeMarketDataError(
                "target_time must be a naive Asia/Shanghai market timestamp"
            )
        resolved_target = target_time
    else:
        resolved_target = parse_market_timestamp(target_time, field="target_time")
    if resolved_target.date() != resolved_date:
        raise RuntimeMarketDataError("target_time must belong to trade_date")
    return resolved_date, resolved_target


def _latest_eligible_quote(
    rows: Sequence[Mapping[str, Any]],
    *,
    trade_date: date,
    target_time: datetime,
) -> tuple[datetime, dict[str, Any]] | None:
    latest: tuple[datetime, dict[str, Any]] | None = None
    for row in rows:
        # Timestamp is the only future-snapshot field inspected.
        timestamp = parse_market_timestamp(row.get("timestamp"))
        if timestamp.date() != trade_date or timestamp > target_time:
            continue
        try:
            quote = standardize_quote(row)
        except (TypeError, ValueError) as exc:
            raise RuntimeMarketDataError(str(exc)) from exc
        if latest is None or timestamp >= latest[0]:
            latest = timestamp, quote
    return latest


def _previous_close(value: float | int | None) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeMarketDataError("previous_close must be numeric or null")
    if not math.isfinite(value) or value < 0:
        raise RuntimeMarketDataError("previous_close must be finite and non-negative")
    return value
