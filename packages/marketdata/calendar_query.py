"""Calendar query port for Live and other market-date consumers.

``LiveMarketView`` depends only on this port.  Production wraps a
``MarketContextService`` built from :class:`~packages.marketdata.trading_calendar.TradingCalendar`
authoritative holiday JSON; tests inject a fixture-backed adapter.

Issue #133 removed the benchmark probe and ``day_status="unknown"``:
the calendar is authoritative within its coverage range, so every day is
either ``open`` or ``closed``.  Dates outside coverage (missing year JSON)
raise :class:`MarketContextError`; callers catch it and set
``calendar_status="unavailable"``.
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
from .trading_calendar import CalendarUnavailableError, TradingCalendar

CalendarDayStatus = Literal["open", "closed"]


class CalendarQueryPort(Protocol):
    """Stable calendar surface consumed by Live Market View."""

    @property
    def coverage_start(self) -> date: ...

    @property
    def coverage_end(self) -> date: ...

    def covers(self, trade_date: date | str) -> bool: ...

    def day_status(self, trade_date: date | str, market: str) -> CalendarDayStatus:
        """Return ``"open"`` or ``"closed"``.

        Raises :class:`MarketContextError` if *trade_date* is outside the
        calendar's coverage range (missing year JSON).
        """
        ...

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

    The underlying ``MarketContextService`` is the single source of truth for
    trading days and sessions.  Every in-coverage day is authoritatively
    ``open`` or ``closed``; dates outside coverage raise
    :class:`MarketContextError` so Live can degrade to
    ``calendar_status="unavailable"``.
    """

    def __init__(
        self,
        market_context: MarketContextService,
    ) -> None:
        if not isinstance(market_context, MarketContextService):
            raise TypeError("market_context must be a MarketContextService")
        self._context = market_context

    @property
    def coverage_start(self) -> date:
        return self._context.coverage_start

    @property
    def coverage_end(self) -> date:
        return self._context.coverage_end

    def covers(self, trade_date: date | str) -> bool:
        value = _as_date(trade_date)
        return self._context.coverage_start <= value <= self._context.coverage_end

    def day_status(self, trade_date: date | str, market: str) -> CalendarDayStatus:
        _require_supported_market(market)
        value = _as_date(trade_date)
        if not self.covers(value):
            raise MarketContextError(
                f"date {value.isoformat()} is outside calendar coverage "
                f"({self._context.coverage_start.isoformat()}"
                f"..{self._context.coverage_end.isoformat()})"
            )
        if self._context.is_trading_day(value, market):
            return "open"
        return "closed"

    def is_trading_day(self, trade_date: date | str, market: str) -> bool:
        return self._context.is_trading_day(trade_date, market)

    def session_on(
        self,
        trade_date: date | str,
        market: str,
    ) -> MarketSession | None:
        _require_supported_market(market)
        return self._context.session_on(trade_date, market)

    def require_session(self, trade_date: date | str, market: str) -> MarketSession:
        _require_supported_market(market)
        return self._context.require_session(trade_date, market)

    def previous_trading_day(
        self,
        trade_date: date | str,
        market: str,
    ) -> date | None:
        value = _as_date(trade_date)
        context = self._context
        if value < context.coverage_start:
            return None
        clamped = value if value <= context.coverage_end else context.coverage_end
        if clamped < value and context.is_trading_day(clamped, market):
            return clamped
        try:
            return context.previous_trading_day(clamped, market)
        except MarketContextError:
            return None

    def next_trading_day(
        self,
        trade_date: date | str,
        market: str,
    ) -> date | None:
        value = _as_date(trade_date)
        if value > self._context.coverage_end:
            return None
        try:
            return self._context.next_trading_day(value, market)
        except MarketContextError:
            return None


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


def build_market_context_from_trading_calendar(
    calendar: TradingCalendar,
    market: str,
) -> MarketContextService:
    """Build a ``MarketContextService`` from :class:`TradingCalendar` data.

    Scans the calendar's bundled JSON for available years, then materialises
    every trading day in that range.  The resulting service is authoritative:
    every in-coverage weekday is either a trading day (``open``) or a
    holiday/weekend (``closed``) -- there is no ``unknown``.

    Raises :class:`CalendarUnavailableError` if *market* has no year JSON at
    all.
    """
    years = calendar.available_years(market)
    if not years:
        raise CalendarUnavailableError(
            f"no calendar year JSON for market {market!r}"
        )
    coverage_start = date(years[0], 1, 1)
    coverage_end = date(years[-1], 12, 31)
    trading_days = calendar.trading_days_between(coverage_start, coverage_end, market)
    return MarketContextService(
        trading_days=[value.isoformat() for value in trading_days],
        coverage_start=coverage_start.isoformat(),
        coverage_end=coverage_end.isoformat(),
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
    try:
        if calendar.day_status(value, market) == "open":
            return value
    except MarketContextError:
        pass
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
    "build_market_context_from_trading_calendar",
    "last_trading_day_on_or_before",
]
