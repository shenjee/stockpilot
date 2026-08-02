"""Resolve Live effective trade date and market display dimensions.

Consumes only ``CalendarQueryPort`` for calendar facts. Does not sync calendars
or maintain holiday rules (#133 owns Calendar data sources).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from packages.marketdata.calendar_query import (
    CalendarQueryPort,
    last_trading_day_on_or_before,
)
from packages.marketdata.services.market_context_service import MarketSession

LiveMarketPhase = Literal[
    "unknown",
    "pre_open",
    "morning",
    "lunch_break",
    "afternoon",
    "closed",
    "market_closed",
]
CalendarStatus = Literal["available", "unavailable"]


class LiveMarketViewError(ValueError):
    """Raised when Live cannot resolve an effective trade date."""


@dataclass(frozen=True, slots=True)
class ResolvedLiveMarketContext:
    """Market-layer Live view before security data quality is assessed."""

    observed_now: datetime
    market: str
    effective_trade_date: date
    market_session: MarketSession
    market_phase: LiveMarketPhase
    calendar_status: CalendarStatus


def resolve_live_market_context(
    calendar: CalendarQueryPort,
    *,
    observed_now: datetime,
    market: str,
) -> ResolvedLiveMarketContext:
    """Resolve the Live effective trade date from calendar + wall clock.

    Rules (PR-A market layer):

    - Trading day before 09:30 → previous trading day, ``pre_open``.
    - Trading day morning / lunch / afternoon / closed → that day.
    - Weekend / holiday → previous trading day, ``market_closed``.
    - Observed date outside coverage, unknown day, or unknown gap between the
      candidate day and ``observed_now`` → best-effort last open day with
      ``calendar_status=unavailable``, ``market_phase=unknown``.
    """

    if not isinstance(observed_now, datetime) or observed_now.tzinfo is not None:
        raise LiveMarketViewError(
            "observed_now must be a naive Asia/Shanghai datetime"
        )
    normalized_market = str(market).strip().lower()
    observed_date = observed_now.date()

    day_status = calendar.day_status(observed_date, normalized_market)
    if day_status == "unknown":
        return _resolve_calendar_unavailable(
            calendar,
            observed_now=observed_now,
            observed_date=observed_date,
            market=normalized_market,
        )

    if day_status == "open":
        session = calendar.require_session(observed_date, normalized_market)
        phase = session.phase_at(observed_now)
        if phase == "pre_open":
            previous = calendar.previous_trading_day(observed_date, normalized_market)
            if previous is None:
                raise LiveMarketViewError(
                    "no previous trading day available before pre-open"
                )
            return _finalize_resolved(
                calendar,
                observed_now=observed_now,
                market=normalized_market,
                effective_trade_date=previous,
                market_phase="pre_open",
            )
        return _finalize_resolved(
            calendar,
            observed_now=observed_now,
            market=normalized_market,
            effective_trade_date=observed_date,
            market_phase=phase,
        )

    previous = calendar.previous_trading_day(observed_date, normalized_market)
    if previous is None:
        raise LiveMarketViewError(
            "no previous trading day available for closed market day"
        )
    return _finalize_resolved(
        calendar,
        observed_now=observed_now,
        market=normalized_market,
        effective_trade_date=previous,
        market_phase="market_closed",
    )


def _finalize_resolved(
    calendar: CalendarQueryPort,
    *,
    observed_now: datetime,
    market: str,
    effective_trade_date: date,
    market_phase: LiveMarketPhase,
) -> ResolvedLiveMarketContext:
    """Attach calendar_status after validating the full resolution interval."""

    observed_date = observed_now.date()
    if not _resolution_interval_is_known(
        calendar,
        start=effective_trade_date,
        end=observed_date,
        market=market,
    ):
        return ResolvedLiveMarketContext(
            observed_now=observed_now,
            market=market,
            effective_trade_date=effective_trade_date,
            market_session=calendar.require_session(effective_trade_date, market),
            market_phase="unknown",
            calendar_status="unavailable",
        )
    return ResolvedLiveMarketContext(
        observed_now=observed_now,
        market=market,
        effective_trade_date=effective_trade_date,
        market_session=calendar.require_session(effective_trade_date, market),
        market_phase=market_phase,
        calendar_status="available",
    )


def _resolution_interval_is_known(
    calendar: CalendarQueryPort,
    *,
    start: date,
    end: date,
    market: str,
) -> bool:
    """Return whether every day from ``start`` through ``end`` is known.

    ``calendar_status=available`` requires the full parse interval from the
    candidate effective trade date to ``observed_now``'s local date. Any
    ``unknown`` day (including gaps after last benchmark evidence) makes the
    view non-authoritative.
    """

    if end < start:
        start, end = end, start
    cursor = start
    while cursor <= end:
        if calendar.day_status(cursor, market) == "unknown":
            return False
        cursor += timedelta(days=1)
    return True


def _resolve_calendar_unavailable(
    calendar: CalendarQueryPort,
    *,
    observed_now: datetime,
    observed_date: date,
    market: str,
) -> ResolvedLiveMarketContext:
    if observed_date > calendar.coverage_end:
        anchor = calendar.coverage_end
    elif observed_date < calendar.coverage_start:
        anchor = calendar.coverage_start
    else:
        # Inside coverage but day_status is unknown (e.g. past last evidenced open).
        anchor = observed_date
    previous = last_trading_day_on_or_before(calendar, anchor, market)
    if previous is None:
        raise LiveMarketViewError(
            "calendar coverage is unavailable and contains no trading days"
        )
    return ResolvedLiveMarketContext(
        observed_now=observed_now,
        market=market,
        effective_trade_date=previous,
        market_session=calendar.require_session(previous, market),
        market_phase="unknown",
        calendar_status="unavailable",
    )
