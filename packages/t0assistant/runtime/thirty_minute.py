"""Dynamic thirty-minute aggregation and official-bar replacement.

Mirrors :class:`DynamicFiveMinuteAggregator` with one critical difference
mandated by the 30m design (§8.3): when an official 30m bar arrives it replaces
*only* the temporary bar with the same end-time.  It must **never** delete the
next bucket's still-forming temporary bar.  This differs from the 5m
aggregator, whose ``accept_official`` clears the dynamic bucket and its 1m
constituents because the 5m "late official drops unclosed" rule does not apply
to 30m.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from packages.marketdata.services.market_context_service import MarketSession

from ._market_bars import (
    MARKET_TIMESTAMP_FORMAT,
    RuntimeMarketDataError,
    aggregate_ohlcva,
    validated_bar,
)


class DynamicThirtyMinuteAggregator:
    """Maintain one trading day's display and analysis thirty-minute sequences."""

    def __init__(self, session: MarketSession):
        if not isinstance(session, MarketSession):
            raise TypeError("session must be a MarketSession")
        self._session = session
        self._one_minute: dict[datetime, dict[str, Any]] = {}
        self._dynamic: dict[datetime, dict[str, Any]] = {}
        self._official: dict[datetime, dict[str, Any]] = {}
        self._latest_boundary: datetime | None = None
        self._official_boundaries = frozenset(session.bar_close_times(30))

    @property
    def session(self) -> MarketSession:
        return self._session

    @property
    def display_bars(self) -> tuple[dict[str, Any], ...]:
        """Return official bars plus current dynamic bars in timestamp order."""

        combined = {**self._dynamic, **self._official}
        return tuple(dict(combined[key]) for key in sorted(combined))

    @property
    def dynamic_bars(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(self._dynamic[key]) for key in sorted(self._dynamic))

    @property
    def analysis_bars(self) -> tuple[dict[str, Any], ...]:
        """Return only official closed bars, safe for indicators and CZSC."""

        return tuple(dict(self._official[key]) for key in sorted(self._official))

    def update_one_minute(self, row: Mapping[str, Any]) -> dict[str, Any]:
        """Apply a closed 1m bar and return the affected display 30m bar."""

        timestamp, bar = validated_bar(
            row,
            closed=True,
            trade_date=self._session.trade_date,
        )
        if timestamp.second or timestamp.microsecond:
            raise RuntimeMarketDataError("1m bar timestamp must be minute-aligned")
        if not self._session.is_trading_time(timestamp):
            raise RuntimeMarketDataError("1m bar timestamp is outside trading periods")

        boundary = self._thirty_minute_boundary(timestamp)
        if boundary in self._official:
            # A late or revised 1m input cannot overwrite the authoritative 30m.
            return dict(self._official[boundary])
        if self._latest_boundary is not None and boundary < self._latest_boundary:
            raise RuntimeMarketDataError("1m bar belongs to an expired 30m bucket")
        if self._latest_boundary is None or boundary > self._latest_boundary:
            self._dynamic.clear()
            self._latest_boundary = boundary

        self._one_minute[timestamp] = bar
        bucket_rows = [
            value
            for minute, value in sorted(self._one_minute.items())
            if self._thirty_minute_boundary(minute) == boundary
        ]
        dynamic = aggregate_ohlcva(
            boundary.strftime(MARKET_TIMESTAMP_FORMAT),
            bucket_rows,
            closed=False,
        )
        self._dynamic[boundary] = dynamic
        return dict(dynamic)

    def accept_official(self, row: Mapping[str, Any]) -> dict[str, Any]:
        """Replace the matching dynamic bar with an official closed 30m bar.

        Per §8.3, this replaces *only* the temporary bar whose end-time matches
        the official bar.  It must **not** delete the next bucket's still-
        forming temporary bar (the key difference from the 5m aggregator).
        """

        timestamp, bar = validated_bar(
            row,
            closed=True,
            trade_date=self._session.trade_date,
        )
        if timestamp not in self._official_boundaries:
            raise RuntimeMarketDataError(
                "official 30m timestamp must be a market thirty-minute close boundary"
            )
        self._official[timestamp] = bar
        self._dynamic.pop(timestamp, None)
        # Deliberately do NOT clear _dynamic or _one_minute for the next bucket.
        # The 30m merge rule only replaces the same-end-time temp bar.
        # Remove only the 1m rows that belong to the now-official bucket.
        self._one_minute = {
            minute: one_minute_bar
            for minute, one_minute_bar in self._one_minute.items()
            if self._thirty_minute_boundary(minute) != timestamp
        }
        return dict(bar)

    def _thirty_minute_boundary(self, timestamp: datetime) -> datetime:
        if timestamp <= self._session.morning_close:
            period_start = self._session.start
            period_end = self._session.morning_close
        else:
            period_start = self._session.afternoon_open
            period_end = self._session.end

        elapsed_minutes = int((timestamp - period_start).total_seconds() // 60)
        bucket_number = max(1, (elapsed_minutes + 29) // 30)
        boundary = period_start + timedelta(minutes=bucket_number * 30)
        if boundary > period_end:
            raise RuntimeMarketDataError("1m bar cannot be assigned to a 30m session bucket")
        return boundary
