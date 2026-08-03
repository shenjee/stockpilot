"""Resolve Live effective trade date and market display dimensions.

Consumes only ``CalendarQueryPort`` for calendar facts. Does not sync calendars
or maintain holiday rules (#133 owns Calendar data sources).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal, Mapping, Sequence

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
PollingProfile = Literal["active", "reduced", "idle"]
DataQuality = Literal["full", "degraded", "partial"]
SymbolAvailability = Literal["available", "no_current_data", "suspended"]
MarketClosedReason = Literal["weekend", "holiday"]

MINIMUM_PREHEAT_5M = 500
_TIMESTAMP_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$"
CloseReconcileStatus = Literal[
    "not_started",
    "in_progress",
    "retry_pending",
    "completed",
    "exhausted",
]

_MARKET_OPEN = time(9, 30)
_CLOSE_RECONCILE = time(15, 5)


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


def resolve_market_closed_reason(
    *,
    observed_now: datetime,
    market_phase: LiveMarketPhase,
    calendar_status: CalendarStatus,
) -> MarketClosedReason | None:
    """Return the natural-day reason when the market layer is closed."""

    if calendar_status != "available" or market_phase != "market_closed":
        return None
    return "weekend" if observed_now.weekday() >= 5 else "holiday"


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


def resolve_polling_profile(
    *,
    market_phase: LiveMarketPhase,
    calendar_status: CalendarStatus,
    pinned_trade_date: date,
    observed_at: datetime,
    calendar: CalendarQueryPort | None,
    market: str,
    awaiting_day_switch: bool = False,
    close_reconcile_status: CloseReconcileStatus = "not_started",
    close_reconcile_retry_due: bool = False,
) -> PollingProfile:
    """Return the refresh cadence for the current Live view (#130 PR-B)."""

    if awaiting_day_switch:
        return "reduced"
    if market_phase in {"morning", "afternoon"}:
        return "active"
    if market_phase == "closed":
        if close_reconcile_status in {"completed", "exhausted"}:
            return "idle"
        if close_reconcile_status == "retry_pending" and not close_reconcile_retry_due:
            return "reduced"
        if observed_at.time() >= _CLOSE_RECONCILE:
            return "active"
        return "idle"
    if market_phase in {"pre_open", "lunch_break", "market_closed", "unknown"}:
        return "idle"
    if calendar_status == "unavailable":
        return "idle"
    if calendar is not None and is_awaiting_day_switch(
        calendar,
        observed_at=observed_at,
        pinned_trade_date=pinned_trade_date,
        market=market,
        calendar_status=calendar_status,
    ):
        return "reduced"
    return "idle"


def resolve_initial_polling_profile(
    *,
    market_phase: LiveMarketPhase,
    calendar_status: CalendarStatus,
    pinned_trade_date: date,
    market_candidate_trade_date: date,
    observed_now: datetime,
) -> PollingProfile:
    """Resolve polling cadence for the first Live snapshot before runtime owns input."""

    awaiting_day_switch = (
        calendar_status == "available"
        and observed_now.time() >= _MARKET_OPEN
        and market_candidate_trade_date > pinned_trade_date
    )
    return resolve_polling_profile(
        market_phase=market_phase,
        calendar_status=calendar_status,
        pinned_trade_date=pinned_trade_date,
        observed_at=observed_now,
        calendar=None,
        market="",
        awaiting_day_switch=awaiting_day_switch,
    )


def is_awaiting_day_switch(
    calendar: CalendarQueryPort,
    *,
    observed_at: datetime,
    pinned_trade_date: date,
    market: str,
    calendar_status: CalendarStatus = "available",
) -> bool:
    """Return whether wall clock expects a newer day but the Session is still pinned."""

    if observed_at.time() < _MARKET_OPEN:
        return False
    if calendar_status == "unavailable":
        return False
    try:
        resolved = resolve_live_market_context(
            calendar,
            observed_now=observed_at,
            market=market,
        )
    except LiveMarketViewError:
        return False
    return resolved.effective_trade_date > pinned_trade_date


def day_switch_target_date(
    calendar: CalendarQueryPort,
    *,
    observed_at: datetime,
    pinned_trade_date: date,
    market: str,
) -> date | None:
    """Return the calendar target day when a switch is due, else ``None``."""

    if observed_at.time() < _MARKET_OPEN:
        return None
    try:
        resolved = resolve_live_market_context(
            calendar,
            observed_now=observed_at,
            market=market,
        )
    except LiveMarketViewError:
        return None
    target = resolved.effective_trade_date
    if target <= pinned_trade_date:
        return None
    return target


def row_timestamp(row: Mapping[str, object]) -> datetime | None:
    value = row.get("timestamp", row.get("date"))
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is None else None


def day_switch_evidence_date(
    rows: Sequence[Mapping[str, object]],
    *,
    target_trade_date: date,
    observed_at: datetime,
    require_closed: bool = False,
) -> bool:
    """Return whether branch rows carry post-open evidence for ``target_trade_date``."""

    if observed_at.time() < _MARKET_OPEN:
        return False
    for row in rows:
        if require_closed and row.get("closed") is not True:
            continue
        timestamp = row_timestamp(row)
        if timestamp is None:
            continue
        if timestamp.date() != target_trade_date:
            continue
        if timestamp.time() >= _MARKET_OPEN:
            return True
    return False


def should_run_close_reconciliation(
    *,
    market_phase: LiveMarketPhase,
    observed_at: datetime,
    close_reconcile_status: CloseReconcileStatus,
) -> bool:
    """Return whether the one-shot post-close reconciliation should run."""

    if close_reconcile_status in {"completed", "exhausted"}:
        return False
    if market_phase != "closed":
        return False
    return observed_at.time() >= _CLOSE_RECONCILE


def resolve_security_data_trade_date(
    bars_1m: Sequence[Mapping[str, object]],
    bars_5m: Sequence[Mapping[str, object]],
    quote_snapshots: Sequence[Mapping[str, object]] = (),
) -> date | None:
    """Return the latest quote or intraday date present in the security snapshot."""

    latest: date | None = None
    for rows in (bars_1m, bars_5m):
        for row in rows:
            timestamp = row_timestamp(row)
            if timestamp is None:
                continue
            row_date = timestamp.date()
            if latest is None or row_date > latest:
                latest = row_date
    for row in quote_snapshots:
        timestamp = row_timestamp(row)
        if timestamp is None:
            continue
        row_date = timestamp.date()
        if latest is None or row_date > latest:
            latest = row_date
    return latest


def assess_symbol_availability(
    *,
    market_candidate_trade_date: date,
    security_data_trade_date: date | None,
    authoritative_suspended: bool = False,
) -> SymbolAvailability:
    """Assess whether the symbol has data for the market candidate day."""

    if authoritative_suspended:
        return "suspended"
    if security_data_trade_date is None:
        return "no_current_data"
    if security_data_trade_date == market_candidate_trade_date:
        return "available"
    return "no_current_data"


def _intraday_quality_cutoff(
    *,
    market_phase: LiveMarketPhase,
    target_time: datetime,
    market_session: MarketSession,
) -> datetime | None:
    if market_phase in {"market_closed", "closed"}:
        return market_session.end
    if market_phase == "lunch_break":
        return market_session.morning_close
    if market_phase in {"morning", "afternoon"}:
        return target_time
    return None


def _bars_complete_to(
    rows: Sequence[Mapping[str, object]],
    *,
    trade_date: date,
    cutoff: datetime,
    market_session: MarketSession,
    minutes: int,
) -> bool:
    expected = [
        moment
        for moment in market_session.bar_close_times(minutes)
        if moment.date() == trade_date and moment <= cutoff
    ]
    if not expected:
        # Before the first bar of this timeframe should close, emptiness is normal.
        return True
    present = {
        timestamp
        for row in rows
        if row.get("closed") is True
        if (timestamp := row_timestamp(row)) is not None
        if timestamp.date() == trade_date
    }
    return all(moment in present for moment in expected)


def _has_target_day_closed_daily(
    rows: Sequence[Mapping[str, object]],
    trade_date: date,
) -> bool:
    for row in rows:
        if row.get("closed") is not True:
            continue
        timestamp = row_timestamp(row)
        if timestamp is not None and timestamp.date() == trade_date:
            return True
    return False


def assess_data_quality(
    *,
    closed_5m_prefix_count: int,
    bars_1m: Sequence[Mapping[str, object]],
    bars_5m: Sequence[Mapping[str, object]],
    daily_rows: Sequence[Mapping[str, object]],
    trade_date: date,
    market_session: MarketSession,
    target_time: datetime,
    market_phase: LiveMarketPhase,
    minimum_preheat_5m: int = MINIMUM_PREHEAT_5M,
) -> DataQuality:
    """Assess candidate-day data completeness (#130 PR-C)."""

    if closed_5m_prefix_count < minimum_preheat_5m:
        return "partial"

    cutoff = _intraday_quality_cutoff(
        market_phase=market_phase,
        target_time=target_time,
        market_session=market_session,
    )
    if cutoff is None:
        return "partial"

    has_complete_1m = _bars_complete_to(
        bars_1m,
        trade_date=trade_date,
        cutoff=cutoff,
        market_session=market_session,
        minutes=1,
    )
    has_complete_5m = _bars_complete_to(
        bars_5m,
        trade_date=trade_date,
        cutoff=cutoff,
        market_session=market_session,
        minutes=5,
    )
    has_target_day_closed_daily = _has_target_day_closed_daily(daily_rows, trade_date)
    effective_day_closed = target_time >= market_session.end
    requires_daily = (
        market_phase in {"market_closed", "closed"} or effective_day_closed
    )

    if has_complete_1m and has_complete_5m:
        if requires_daily and not has_target_day_closed_daily:
            return "degraded"
        return "full"
    if has_complete_5m and has_target_day_closed_daily:
        return "degraded"
    return "partial"


def _latest_closed_bar_timestamp(
    rows: Sequence[Mapping[str, object]],
    *,
    trade_date: date | None = None,
    closed_only: bool = True,
) -> str | None:
    latest: datetime | None = None
    for row in rows:
        if closed_only and row.get("closed") is not True:
            continue
        timestamp = row_timestamp(row)
        if timestamp is None:
            continue
        if trade_date is not None and timestamp.date() != trade_date:
            continue
        if latest is None or timestamp > latest:
            latest = timestamp
    return latest.strftime("%Y-%m-%d %H:%M:%S") if latest is not None else None


def _has_target_day_bars(
    rows: Sequence[Mapping[str, object]],
    trade_date: date,
    *,
    closed_only: bool = False,
) -> bool:
    for row in rows:
        if closed_only and row.get("closed") is not True:
            continue
        timestamp = row_timestamp(row)
        if timestamp is not None and timestamp.date() == trade_date:
            return True
    return False


def build_live_market_view(
    *,
    effective_trade_date: date | str,
    calendar_status: CalendarStatus,
    market_phase: LiveMarketPhase,
    polling_profile: PollingProfile,
    market: Mapping[str, object],
    indicators: Mapping[str, object],
    chan_analysis: Mapping[str, object],
    closed_5m_prefix: Sequence[Mapping[str, object]],
    closed_5m_prefix_count: int,
    target_time: datetime,
    market_session: MarketSession,
    market_candidate_trade_date: date | str | None = None,
    symbol_availability: SymbolAvailability | None = None,
    market_closed_reason: MarketClosedReason | None = None,
    minimum_preheat_5m: int = MINIMUM_PREHEAT_5M,
) -> dict[str, object]:
    """Build the authoritative Live Market View contract payload (#130 PR-C)."""

    trade_date = (
        effective_trade_date
        if isinstance(effective_trade_date, date)
        else date.fromisoformat(str(effective_trade_date))
    )
    trade_date_text = trade_date.isoformat()
    candidate_date = (
        trade_date
        if market_candidate_trade_date is None
        else (
            market_candidate_trade_date
            if isinstance(market_candidate_trade_date, date)
            else date.fromisoformat(str(market_candidate_trade_date))
        )
    )
    bars_1m = market.get("bars_1m")
    bars_5m = market.get("bars_5m")
    daily_bars = market.get("daily_bars")
    quote = market.get("quote")
    bars_1m_rows = bars_1m if isinstance(bars_1m, list) else ()
    bars_5m_rows = bars_5m if isinstance(bars_5m, list) else ()
    daily_rows = daily_bars if isinstance(daily_bars, list) else ()
    quote_rows = (quote,) if isinstance(quote, Mapping) else ()
    closed_5m_rows = tuple(closed_5m_prefix)

    security_data_trade_date = resolve_security_data_trade_date(
        bars_1m_rows,
        bars_5m_rows,
        quote_rows,
    )
    resolved_symbol_availability = symbol_availability or assess_symbol_availability(
        market_candidate_trade_date=candidate_date,
        security_data_trade_date=security_data_trade_date,
    )
    data_quality = assess_data_quality(
        closed_5m_prefix_count=closed_5m_prefix_count,
        bars_1m=bars_1m_rows,
        bars_5m=bars_5m_rows,
        daily_rows=daily_rows,
        trade_date=trade_date,
        market_session=market_session,
        target_time=target_time,
        market_phase=market_phase,
        minimum_preheat_5m=minimum_preheat_5m,
    )

    one_minute = indicators.get("one_minute")
    five_minute = indicators.get("five_minute")
    quote_as_of = (
        quote.get("timestamp")
        if isinstance(quote, Mapping) and isinstance(quote.get("timestamp"), str)
        else None
    )

    payload: dict[str, object] = {
        "effective_trade_date": trade_date_text,
        "calendar_status": calendar_status,
        "market_phase": market_phase,
        "symbol_availability": resolved_symbol_availability,
        "data_quality": data_quality,
        "polling_profile": polling_profile,
        "quote_as_of": quote_as_of,
        "bars_1m_as_of": _latest_closed_bar_timestamp(
            bars_1m_rows,
            trade_date=trade_date,
        ),
        "bars_5m_as_of": _latest_closed_bar_timestamp(
            bars_5m_rows,
            trade_date=trade_date,
            closed_only=True,
        ),
        "daily_as_of": _latest_closed_bar_timestamp(
            daily_rows,
            trade_date=trade_date,
            closed_only=True,
        ),
        "one_minute_indicators_as_of": _latest_closed_bar_timestamp(
            bars_1m_rows,
            trade_date=trade_date,
            closed_only=True,
        ),
        "five_minute_indicators_as_of": _latest_closed_bar_timestamp(
            closed_5m_rows,
            closed_only=True,
        ),
        "czsc_as_of": _latest_closed_bar_timestamp(
            closed_5m_rows,
            closed_only=True,
        ),
    }
    if (
        calendar_status == "available"
        and market_phase == "market_closed"
        and market_closed_reason is not None
    ):
        payload["market_closed_reason"] = market_closed_reason
    return payload
