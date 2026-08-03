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
