"""Calendar query port for Live and other market-date consumers.

`LiveMarketView` depends only on this port. Production wraps
`MarketContextService`; tests inject a fixture-backed adapter. Issue #133 may
replace the production adapter's data source without changing this contract.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Protocol

from .services.market_context_service import (
    MarketContextError,
    MarketContextService,
    MarketSession,
    NonTradingDayError,
)
from .t0_schema import T0_MARKETS

CalendarDayStatus = Literal["open", "closed", "unknown"]


class CalendarQueryPort(Protocol):
    """Stable calendar surface consumed by Live Market View."""

    @property
    def coverage_start(self) -> date: ...

    @property
    def coverage_end(self) -> date: ...

    def covers(self, trade_date: date | str) -> bool: ...

    def day_status(self, trade_date: date | str, market: str) -> CalendarDayStatus: ...

    def is_trading_day(self, trade_date: date | str, market: str) -> bool: ...

    def session_on(
        self,
        trade_date: date | str,
        market: str,
    ) -> MarketSession | None: ...

    def require_session(self, trade_date: date | str, market: str) -> MarketSession: ...

    def previous_trading_day(
        self,
        trade_date: date | str,
        market: str,
    ) -> date | None: ...

    def next_trading_day(
        self,
        trade_date: date | str,
        market: str,
    ) -> date | None: ...


class MarketContextCalendarAdapter:
    """Adapt an existing ``MarketContextService`` to ``CalendarQueryPort``.

    ``authoritative_through`` marks the last date for which absence from the
    trading-day set is treated as a confirmed closed day (holiday / weekend
    already handled). Weekdays after that bound return ``unknown`` so Live can
    degrade with ``calendar_status=unavailable`` instead of inventing opens.
    """

    def __init__(
        self,
        market_context: MarketContextService,
        *,
        authoritative_through: date | str | None = None,
    ) -> None:
        if not isinstance(market_context, MarketContextService):
            raise TypeError("market_context must be a MarketContextService")
        self._context = market_context
        self._authoritative_through = (
            market_context.coverage_end
            if authoritative_through is None
            else _as_date(authoritative_through)
        )

    @property
    def coverage_start(self) -> date:
        return self._context.coverage_start

    @property
    def coverage_end(self) -> date:
        return self._context.coverage_end

    @property
    def authoritative_through(self) -> date:
        return self._authoritative_through

    def covers(self, trade_date: date | str) -> bool:
        value = _as_date(trade_date)
        return self._context.coverage_start <= value <= self._context.coverage_end

    def day_status(self, trade_date: date | str, market: str) -> CalendarDayStatus:
        _require_supported_market(market)
        value = _as_date(trade_date)
        if not self.covers(value):
            return "unknown"
        if self._context.is_trading_day(value, market):
            return "open"
        if value.weekday() >= 5:
            return "closed"
        # Weekday missing from the authoritative trading-day set.
        if value <= self._authoritative_through:
            return "closed"
        return "unknown"

    def is_trading_day(self, trade_date: date | str, market: str) -> bool:
        return self._context.is_trading_day(trade_date, market)

    def session_on(
        self,
        trade_date: date | str,
        market: str,
    ) -> MarketSession | None:
        return self._context.session_on(trade_date, market)

    def require_session(self, trade_date: date | str, market: str) -> MarketSession:
        return self._context.require_session(trade_date, market)

    def previous_trading_day(
        self,
        trade_date: date | str,
        market: str,
    ) -> date | None:
        return self._context.previous_trading_day(trade_date, market)

    def next_trading_day(
        self,
        trade_date: date | str,
        market: str,
    ) -> date | None:
        return self._context.next_trading_day(trade_date, market)


class FixtureCalendarQuery(MarketContextCalendarAdapter):
    """Fixture-backed calendar adapter for deterministic Live tests."""

    def __init__(
        self,
        trading_days: list[date | str] | tuple[date | str, ...],
        *,
        coverage_start: date | str | None = None,
        coverage_end: date | str | None = None,
    ) -> None:
        super().__init__(
            MarketContextService(
                trading_days,
                coverage_start=coverage_start,
                coverage_end=coverage_end,
            )
        )


def last_trading_day_on_or_before(
    calendar: CalendarQueryPort,
    trade_date: date | str,
    market: str,
) -> date | None:
    """Return the latest open day at or before ``trade_date`` within coverage."""

    value = _as_date(trade_date)
    if not calendar.covers(value):
        if value > calendar.coverage_end:
            value = calendar.coverage_end
        elif value < calendar.coverage_start:
            return None
    if calendar.day_status(value, market) == "open":
        return value
    return calendar.previous_trading_day(value, market)


def _as_date(value: date | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise MarketContextError("trade_date must use YYYY-MM-DD") from exc


def _require_supported_market(market: str) -> str:
    value = str(market).strip().lower()
    if value not in T0_MARKETS:
        raise MarketContextError("market context currently supports sh and sz only")
    return value


__all__ = [
    "CalendarDayStatus",
    "CalendarQueryPort",
    "FixtureCalendarQuery",
    "MarketContextCalendarAdapter",
    "MarketContextError",
    "NonTradingDayError",
    "last_trading_day_on_or_before",
]
