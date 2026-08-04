"""Calendar query port for Live and other market-date consumers.

`LiveMarketView` depends only on this port. Production wraps
`MarketContextService`; tests inject a fixture-backed adapter. Issue #133 may
replace the production adapter's data source without changing this contract.
"""

from __future__ import annotations

from datetime import date, datetime
from threading import RLock
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

    def confirm_open_day(self, trade_date: date | str) -> None:
        """Runtime-confirm a day as open via benchmark probe evidence (#140).

        Implementations that do not support runtime confirmation may make this
        a no-op.  Production ``MarketContextCalendarAdapter`` records the day
        so ``day_status`` returns ``open`` instead of ``unknown``.
        """
        ...

    def reset_confirmed_open_days(self) -> None:
        """Clear all runtime-confirmed open days (tests / re-init)."""
        ...


class MarketContextCalendarAdapter:
    """Adapt an existing ``MarketContextService`` to ``CalendarQueryPort``.

    ``authoritative_through`` marks the last date for which absence from the
    trading-day set is treated as a confirmed closed day (holiday / weekend
    already handled). Weekdays after that bound return ``unknown`` so Live can
    degrade with ``calendar_status=unavailable`` instead of inventing opens.

    When ``evidence_authoritative`` is false (non-benchmark / cold-start
    scaffold), every in-coverage day is ``unknown`` so Live never claims
    ``calendar_status=available`` from synthetic weekdays or sparse securities.
    """

    def __init__(
        self,
        market_context: MarketContextService,
        *,
        authoritative_through: date | str | None = None,
        evidence_authoritative: bool = True,
    ) -> None:
        if not isinstance(market_context, MarketContextService):
            raise TypeError("market_context must be a MarketContextService")
        self._context = market_context
        self._evidence_authoritative = bool(evidence_authoritative)
        self._authoritative_through = (
            market_context.coverage_end
            if authoritative_through is None
            else _as_date(authoritative_through)
        )
        # Runtime-confirmed open days from benchmark probe (#140).  These
        # overlay the immutable base context so Live can break the
        # Calendar-unknown self-lock without minting fake authority from
        # weekdays or single-stock data.
        self._confirmed_open_days: set[date] = set()
        self._lock = RLock()

    @property
    def coverage_start(self) -> date:
        return self._context.coverage_start

    @property
    def coverage_end(self) -> date:
        with self._lock:
            if not self._confirmed_open_days:
                return self._context.coverage_end
            return max(self._context.coverage_end, max(self._confirmed_open_days))

    @property
    def authoritative_through(self) -> date:
        return self._authoritative_through

    @property
    def evidence_authoritative(self) -> bool:
        return self._evidence_authoritative

    @property
    def confirmed_open_days(self) -> frozenset[date]:
        """Return the runtime-confirmed open days (benchmark probe evidence)."""

        with self._lock:
            return frozenset(self._confirmed_open_days)

    def confirm_open_day(self, trade_date: date | str) -> None:
        """Runtime-confirm a day as open via benchmark probe evidence (#140).

        This does **not** mint Calendar authority from weekdays or single-stock
        data.  It records that ``sh.000001`` (or an equivalent benchmark) has
        produced valid intraday evidence for ``trade_date``, so the adapter
        reports ``open`` instead of ``unknown`` for that day.  All consumers
        sharing this adapter instance (LiveDataPreparator, BranchingLiveInput,
        KLineDataService) see the update atomically.
        """

        value = _as_date(trade_date)
        if value.weekday() >= 5:
            raise MarketContextError(
                "cannot confirm a weekend as a trading day"
            )
        with self._lock:
            self._confirmed_open_days.add(value)

    def reset_confirmed_open_days(self) -> None:
        """Clear all runtime-confirmed open days (used by tests / re-init)."""

        with self._lock:
            self._confirmed_open_days.clear()

    def covers(self, trade_date: date | str) -> bool:
        value = _as_date(trade_date)
        with self._lock:
            if value in self._confirmed_open_days:
                return True
        return self._context.coverage_start <= value <= self._context.coverage_end

    def day_status(self, trade_date: date | str, market: str) -> CalendarDayStatus:
        _require_supported_market(market)
        value = _as_date(trade_date)
        with self._lock:
            if value in self._confirmed_open_days:
                return "open"
        if not self.covers(value):
            return "unknown"
        if not self._evidence_authoritative:
            # Scaffold / sparse fallback must not mint open/closed authority.
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
        value = _as_date(trade_date)
        with self._lock:
            if value in self._confirmed_open_days:
                return True
        return self._context.is_trading_day(trade_date, market)

    def session_on(
        self,
        trade_date: date | str,
        market: str,
    ) -> MarketSession | None:
        value = _as_date(trade_date)
        normalized_market = _require_supported_market(market)
        with self._lock:
            if value in self._confirmed_open_days:
                return MarketSession(market=normalized_market, trade_date=value)
        return self._context.session_on(trade_date, market)

    def require_session(self, trade_date: date | str, market: str) -> MarketSession:
        value = _as_date(trade_date)
        normalized_market = _require_supported_market(market)
        with self._lock:
            if value in self._confirmed_open_days:
                return MarketSession(market=normalized_market, trade_date=value)
        return self._context.require_session(trade_date, market)

    def previous_trading_day(
        self,
        trade_date: date | str,
        market: str,
    ) -> date | None:
        value = _as_date(trade_date)
        with self._lock:
            confirmed = sorted(self._confirmed_open_days)
        base_previous = self._context.previous_trading_day(value, market)
        # Merge confirmed days with the context's answer so runtime-confirmed
        # open days participate in the backward walk.
        candidates = [d for d in confirmed if d < value]
        if base_previous is not None:
            candidates.append(base_previous)
        return max(candidates) if candidates else None

    def next_trading_day(
        self,
        trade_date: date | str,
        market: str,
    ) -> date | None:
        value = _as_date(trade_date)
        with self._lock:
            confirmed = sorted(self._confirmed_open_days)
        base_next = None
        try:
            base_next = self._context.next_trading_day(value, market)
        except MarketContextError:
            pass
        candidates = [d for d in confirmed if d > value]
        if base_next is not None:
            candidates.append(base_next)
        return min(candidates) if candidates else None


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
