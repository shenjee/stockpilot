"""China A-share market calendar and intraday boundary service.

The service deliberately receives an authoritative set of exchange trading
days instead of guessing from weekdays.  Consumers may build that set from an
exchange calendar or a market-wide benchmark, but must not derive it from one
security's bars: a suspended security can have no bars on an otherwise open
market day.

Shanghai and Shenzhen currently share the normal session boundaries required
by the T+0 Assistant:

* morning: 09:30 through 11:30
* lunch break: after 11:30 and before 13:00
* afternoon: 13:00 through 15:00

Missing bars never alter those boundaries.  APIs that advance through replay
therefore operate on caller-supplied *actual* bar timestamps and naturally skip
lunch, suspensions, and any other interval without a real bar.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterable, Literal, Sequence

from ..t0_schema import T0_MARKETS, T0_TIMEZONE


MarketPhase = Literal[
    "pre_open",
    "morning",
    "lunch_break",
    "afternoon",
    "closed",
]

_OPEN_TIME = time(9, 30)
_MORNING_CLOSE_TIME = time(11, 30)
_AFTERNOON_OPEN_TIME = time(13, 0)
_CLOSE_TIME = time(15, 0)
_SUPPORTED_BAR_MINUTES = frozenset({1, 5})


class MarketContextError(ValueError):
    """Raised when market context input is invalid or unavailable."""


class NonTradingDayError(MarketContextError):
    """Raised when session boundaries are requested for a closed date."""


@dataclass(frozen=True)
class MarketSession:
    """Normal Shanghai/Shenzhen session boundaries for one trading day."""

    market: str
    trade_date: date
    timezone: str = T0_TIMEZONE

    @property
    def start(self) -> datetime:
        return datetime.combine(self.trade_date, _OPEN_TIME)

    @property
    def morning_close(self) -> datetime:
        return datetime.combine(self.trade_date, _MORNING_CLOSE_TIME)

    @property
    def afternoon_open(self) -> datetime:
        return datetime.combine(self.trade_date, _AFTERNOON_OPEN_TIME)

    @property
    def end(self) -> datetime:
        return datetime.combine(self.trade_date, _CLOSE_TIME)

    def phase_at(self, value: datetime | str) -> MarketPhase:
        """Classify a local market timestamp against the fixed session."""

        moment = _parse_timestamp(value)
        if moment.date() != self.trade_date:
            return "pre_open" if moment < self.start else "closed"
        if moment < self.start:
            return "pre_open"
        if moment <= self.morning_close:
            return "morning"
        if moment < self.afternoon_open:
            return "lunch_break"
        if moment <= self.end:
            return "afternoon"
        return "closed"

    def is_trading_time(self, value: datetime | str) -> bool:
        """Return whether ``value`` lies in either continuous auction period."""

        return self.phase_at(value) in {"morning", "afternoon"}

    def bar_close_times(self, minutes: int) -> tuple[datetime, ...]:
        """Return nominal 1m or 5m close boundaries without lunch placeholders."""

        if minutes not in _SUPPORTED_BAR_MINUTES:
            raise MarketContextError("bar interval must be 1 or 5 minutes")
        step = timedelta(minutes=minutes)
        return (
            *_period_close_times(self.start, self.morning_close, step),
            *_period_close_times(self.afternoon_open, self.end, step),
        )

    def next_actual_bar_time(
        self,
        current_time: datetime | str,
        actual_bar_times: Sequence[datetime | str],
        *,
        current_time_consumed: bool = True,
    ) -> datetime | None:
        """Return the next real bar close, never a fabricated schedule slot.

        Gaps in ``actual_bar_times`` are preserved.  This is what makes a
        replay cursor cross lunch or a security suspension without generating
        fake K-lines.  The market ``end`` remains 15:00 even when the last
        available bar is earlier.

        ``current_time_consumed`` distinguishes the normal replay cursor
        (whose current timestamp is the closed upper bound of consumed input)
        from the initial 09:30 cursor, where no target-day bar has been
        consumed yet.  The latter must be ``False`` so an actual 09:30 bar is
        not skipped.
        """

        current = _parse_timestamp(current_time)
        actual = sorted({_parse_timestamp(item) for item in actual_bar_times})
        for moment in actual:
            if moment.date() != self.trade_date:
                raise MarketContextError(
                    "actual bar timestamps must belong to the session trade_date"
                )
            if not self.is_trading_time(moment):
                raise MarketContextError(
                    "actual bar timestamps must lie inside a trading period"
                )
        index = (
            bisect_right(actual, current)
            if current_time_consumed
            else bisect_left(actual, current)
        )
        return actual[index] if index < len(actual) else None

    def to_dict(self) -> dict[str, str]:
        """Return project-style local timestamp fields for service consumers."""

        return {
            "market": self.market,
            "trade_date": self.trade_date.isoformat(),
            "timezone": self.timezone,
            "start_time": self.start.strftime("%Y-%m-%d %H:%M:%S"),
            "morning_close_time": self.morning_close.strftime("%Y-%m-%d %H:%M:%S"),
            "afternoon_open_time": self.afternoon_open.strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": self.end.strftime("%Y-%m-%d %H:%M:%S"),
        }


class MarketContextService:
    """Read-only Shanghai/Shenzhen calendar with explicit coverage bounds."""

    def __init__(
        self,
        trading_days: Iterable[date | str],
        *,
        coverage_start: date | str | None = None,
        coverage_end: date | str | None = None,
    ):
        normalized = sorted({_parse_date(value) for value in trading_days})
        if not normalized:
            raise MarketContextError("at least one authoritative trading day is required")
        if any(value.weekday() >= 5 for value in normalized):
            raise MarketContextError("authoritative trading days cannot include weekends")
        resolved_start = (
            _parse_date(coverage_start) if coverage_start is not None else normalized[0]
        )
        resolved_end = (
            _parse_date(coverage_end) if coverage_end is not None else normalized[-1]
        )
        if resolved_start > resolved_end:
            raise MarketContextError("calendar coverage_start must not exceed coverage_end")
        if normalized[0] < resolved_start or normalized[-1] > resolved_end:
            raise MarketContextError(
                "authoritative trading days must lie inside the calendar coverage"
            )
        self._trading_days = tuple(normalized)
        self._trading_day_set = frozenset(normalized)
        self._coverage_start = resolved_start
        self._coverage_end = resolved_end

    def is_trading_day(self, trade_date: date | str, market: str) -> bool:
        """Return exchange-open status; weekends are never treated as open."""

        _validate_market(market)
        value = _parse_date(trade_date)
        self._require_covered(value)
        return value.weekday() < 5 and value in self._trading_day_set

    def session_on(
        self,
        trade_date: date | str,
        market: str,
    ) -> MarketSession | None:
        """Return the session for an open date, otherwise ``None``."""

        normalized_market = _validate_market(market)
        value = _parse_date(trade_date)
        if not self.is_trading_day(value, normalized_market):
            return None
        return MarketSession(market=normalized_market, trade_date=value)

    def require_session(self, trade_date: date | str, market: str) -> MarketSession:
        """Return an open session or raise a specific closed-date error."""

        session = self.session_on(trade_date, market)
        if session is None:
            raise NonTradingDayError(
                f"{_parse_date(trade_date).isoformat()} is not a {market} trading day"
            )
        return session

    def next_trading_day(
        self,
        trade_date: date | str,
        market: str,
    ) -> date | None:
        """Return the next known open date, skipping holidays and weekends."""

        _validate_market(market)
        value = _parse_date(trade_date)
        self._require_covered(value)
        index = bisect_right(self._trading_days, value)
        return self._trading_days[index] if index < len(self._trading_days) else None

    def previous_trading_day(
        self,
        trade_date: date | str,
        market: str,
    ) -> date | None:
        """Return the previous known open date."""

        _validate_market(market)
        value = _parse_date(trade_date)
        self._require_covered(value)
        index = bisect_right(self._trading_days, value) - 1
        if index >= 0 and self._trading_days[index] == value:
            index -= 1
        return self._trading_days[index] if index >= 0 else None

    def _require_covered(self, value: date) -> None:
        if value < self._coverage_start or value > self._coverage_end:
            raise MarketContextError(
                f"{value.isoformat()} is outside calendar coverage "
                f"{self._coverage_start.isoformat()}..{self._coverage_end.isoformat()}"
            )


def _period_close_times(
    period_start: datetime,
    period_end: datetime,
    step: timedelta,
) -> tuple[datetime, ...]:
    values: list[datetime] = []
    current = period_start + step
    while current <= period_end:
        values.append(current)
        current += step
    return tuple(values)


def _validate_market(market: str) -> str:
    value = str(market).strip().lower()
    if value not in T0_MARKETS:
        raise MarketContextError("market context currently supports sh and sz only")
    return value


def _parse_date(value: date | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise MarketContextError("trade_date must use YYYY-MM-DD") from exc


def _parse_timestamp(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            raise MarketContextError(
                "market timestamps must be naive local Asia/Shanghai values"
            )
        return value
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise MarketContextError(
            "market timestamp must use YYYY-MM-DD HH:MM:SS"
        ) from exc
    if parsed.tzinfo is not None:
        raise MarketContextError(
            "market timestamps must be naive local Asia/Shanghai values"
        )
    return parsed
