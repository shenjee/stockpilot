"""Read-only trading calendar backed by per-market/year holiday JSON.

This module is a small, dependency-free calendar: it loads annual holiday JSON
files plus a single intraday session template, then answers trading-day and
session questions from those files alone.  It deliberately does **not**:

- own a SQLite store or repository,
- sync holidays from AkShare or any network source at runtime,
- run benchmark probes against market data,
- infer holidays from a single security's bars,
- treat an unknown year as silently closed.

Data layout (shipped with the package)::

    calendars/
    +-- market_sessions.json      # intraday session templates per market
    +-- sh/2026.json               # SSE 2026 closed + half-day dates
    +-- sz/2026.json               # SZSE 2026 closed + half-day dates
    +-- hk/2026.json               # HKEX 2026 closed + half-day dates
    +-- sh/2027.json               # added at year end, etc.

``market_sessions.json`` schema (per market)::

    {
      "sh": {
        "timezone": "Asia/Shanghai",
        "regular_sessions": [["09:30", "11:30"], ["13:00", "15:00"]],
        "half_day_sessions": [["09:30", "12:00"]]   # HK only
      }
    }

``<market>/<year>.json`` schema::

    {
      "market": "sh",
      "year": 2026,
      "closed_dates": ["2026-01-01", ...],
      "half_day_dates": ["2026-02-16", ...]   # HK only; sh/sz omit it
    }

Yearly maintenance is additive: drop in the next year's JSON files and rerun
the unit tests.  No code change is required when an exchange keeps the same
session boundaries.

Issue #133.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from threading import RLock

__all__ = [
    "CalendarUnavailableError",
    "TradingCalendar",
]

#: Markets with shipped JSON data.  Adding a market only requires a new
#: ``calendars/<market>/`` tree and an entry in ``market_sessions.json``.
SUPPORTED_MARKETS = frozenset({"sh", "sz", "hk"})

#: Maximum number of days :meth:`TradingCalendar.previous_trading_day` and
#: :meth:`TradingCalendar.next_trading_day` will walk before giving up.  Well
#: over a year, so the only realistic way to hit it is missing-year data.
_WALK_LIMIT_DAYS = 800

#: Default location of the bundled calendar JSON, next to this module.
_DEFAULT_CALENDARS_DIR = Path(__file__).resolve().parent / "calendars"


class CalendarUnavailableError(ValueError):
    """Raised when a market/year JSON file is missing or malformed.

    A missing year file is an *error*, never a silent ``False``: without
    authoritative holiday data the calendar must not guess that a weekday is
    closed (it might be a holiday) or open (it might be a holiday too).
    """


@dataclass(frozen=True)
class _YearData:
    """Parsed contents of one ``<market>/<year>.json`` file."""

    closed_dates: frozenset[date]
    half_day_dates: frozenset[date]


@dataclass(frozen=True)
class _MarketSessions:
    """Parsed intraday session template for one market."""

    timezone: str
    sessions: tuple[tuple[time, time], ...]
    half_day_sessions: tuple[tuple[time, time], ...]


class TradingCalendar:
    """Read-only trading calendar over bundled holiday JSON.

    The calendar lazily loads and caches JSON in memory on first access per
    market/year.  All public methods accept :class:`~datetime.date` or
    ``YYYY-MM-DD`` strings and normalise market codes case-insensitively.

    A missing market or missing year file raises
    :class:`CalendarUnavailableError` rather than returning a default, so that
    consumers (e.g. Live) can surface ``calendar_status=unavailable`` without
    the calendar silently misreporting a holiday as a trading day or vice
    versa.
    """

    def __init__(self, calendars_dir: Path | str | None = None) -> None:
        self._calendars_dir = Path(calendars_dir).resolve() if calendars_dir else _DEFAULT_CALENDARS_DIR
        self._lock = RLock()
        # (market, year) -> parsed year data.  Lazily populated.
        self._year_cache: dict[tuple[str, int], _YearData] = {}
        # market -> parsed session template.  Lazily populated.
        self._sessions_cache: dict[str, _MarketSessions] = {}

    # -- public API --------------------------------------------------------

    def available_years(self, market: str) -> tuple[int, ...]:
        """Return the sorted years for which holiday JSON exists for *market*.

        Scans the ``<market>/`` sub-directory for ``<year>.json`` files.
        Non-numeric filenames are silently skipped.
        """
        normalized_market = self._require_market(market)
        market_dir = self._calendars_dir / normalized_market
        if not market_dir.is_dir():
            return ()
        years: list[int] = []
        for path in market_dir.glob("*.json"):
            try:
                years.append(int(path.stem))
            except ValueError:
                continue
        return tuple(sorted(years))

    def is_trading_day(self, trade_date: date | str, market: str) -> bool:
        """Return whether ``trade_date`` is an exchange-open day.

        Rules:

        * Saturday or Sunday -> ``False``.
        * Date in the year's ``closed_dates`` -> ``False``.
        * Any other weekday -> ``True`` (half-day dates are trading days).
        * Missing market or year JSON -> :class:`CalendarUnavailableError`.
        """
        normalized_market = self._require_market(market)
        value = _as_date(trade_date)
        year_data = self._load_year(value.year, normalized_market)
        if value.weekday() >= 5:
            return False
        return value not in year_data.closed_dates

    def sessions_on(
        self,
        trade_date: date | str,
        market: str,
    ) -> tuple[tuple[time, time], ...]:
        """Return the intraday sessions actually in effect on ``trade_date``.

        * Weekend or ``closed_dates`` entry -> empty tuple (market shut).
        * HK ``half_day_dates`` entry -> the market's ``half_day_sessions``.
        * Any other trading day -> the market's full ``sessions``.
        * Missing market or year JSON -> :class:`CalendarUnavailableError`.
        """
        normalized_market = self._require_market(market)
        value = _as_date(trade_date)
        year_data = self._load_year(value.year, normalized_market)
        if value.weekday() >= 5 or value in year_data.closed_dates:
            return ()
        sessions = self._load_sessions(normalized_market)
        if value in year_data.half_day_dates:
            return sessions.half_day_sessions
        return sessions.sessions

    def previous_trading_day(
        self,
        trade_date: date | str,
        market: str,
    ) -> date | None:
        """Return the latest trading day strictly before ``trade_date``.

        Walks backwards day by day, skipping weekends, holidays, and any
        half-day-adjacent closures.  A required year file that is missing
        raises :class:`CalendarUnavailableError` (the calendar will not invent
        a day from an unknown year).  Returns ``None`` only if the bounded
        walk exhausts without finding a trading day, which cannot happen with
        valid year files.
        """
        normalized_market = self._require_market(market)
        cursor = _as_date(trade_date) - timedelta(days=1)
        for _ in range(_WALK_LIMIT_DAYS):
            # ``_load_year`` raises CalendarUnavailableError for a missing
            # year file, which propagates to the caller.
            year_data = self._load_year(cursor.year, normalized_market)
            if cursor.weekday() < 5 and cursor not in year_data.closed_dates:
                return cursor
            cursor -= timedelta(days=1)
        return None

    def next_trading_day(
        self,
        trade_date: date | str,
        market: str,
    ) -> date | None:
        """Return the earliest trading day strictly after ``trade_date``.

        Symmetric counterpart to :meth:`previous_trading_day`.  A missing
        required year file raises :class:`CalendarUnavailableError`.
        """
        normalized_market = self._require_market(market)
        cursor = _as_date(trade_date) + timedelta(days=1)
        for _ in range(_WALK_LIMIT_DAYS):
            year_data = self._load_year(cursor.year, normalized_market)
            if cursor.weekday() < 5 and cursor not in year_data.closed_dates:
                return cursor
            cursor += timedelta(days=1)
        return None

    def trading_days_between(
        self,
        start_date: date | str,
        end_date: date | str,
        market: str,
    ) -> tuple[date, ...]:
        """Return every trading day in the inclusive ``[start, end]`` range.

        Iterates calendar days from ``start_date`` through ``end_date`` and
        keeps the open ones.  Every year touched by the range must have a
        shipped JSON file, otherwise :class:`CalendarUnavailableError` is
        raised for the first missing year.
        """
        normalized_market = self._require_market(market)
        start = _as_date(start_date)
        end = _as_date(end_date)
        if start > end:
            raise CalendarUnavailableError("start_date must not exceed end_date")
        results: list[date] = []
        cursor = start
        while cursor <= end:
            year_data = self._load_year(cursor.year, normalized_market)
            if cursor.weekday() < 5 and cursor not in year_data.closed_dates:
                results.append(cursor)
            cursor += timedelta(days=1)
        return tuple(results)

    # -- internals ---------------------------------------------------------

    def _require_market(self, market: str) -> str:
        normalized = str(market).strip().lower()
        if normalized not in SUPPORTED_MARKETS:
            raise CalendarUnavailableError(
                f"unsupported market {market!r}; supported: {sorted(SUPPORTED_MARKETS)}"
            )
        return normalized

    def _load_year(self, year: int, market: str) -> _YearData:
        key = (market, year)
        with self._lock:
            cached = self._year_cache.get(key)
            if cached is not None:
                return cached
        path = self._calendars_dir / market / f"{year}.json"
        if not path.is_file():
            raise CalendarUnavailableError(
                f"no calendar file for market {market!r} year {year}: {path}"
            )
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CalendarUnavailableError(
                f"failed to read calendar file {path}: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise CalendarUnavailableError(f"calendar file {path} must be a JSON object")
        year_data = _YearData(
            closed_dates=_parse_date_set(raw.get("closed_dates"), "closed_dates", path),
            half_day_dates=_parse_date_set(raw.get("half_day_dates"), "half_day_dates", path),
        )
        with self._lock:
            self._year_cache[key] = year_data
        return year_data

    def _load_sessions(self, market: str) -> _MarketSessions:
        with self._lock:
            cached = self._sessions_cache.get(market)
            if cached is not None:
                return cached
        path = self._calendars_dir / "market_sessions.json"
        if not path.is_file():
            raise CalendarUnavailableError(
                f"missing session template file: {path}"
            )
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CalendarUnavailableError(
                f"failed to read session template {path}: {exc}"
            ) from exc
        if not isinstance(raw, dict) or market not in raw:
            raise CalendarUnavailableError(
                f"session template {path} has no entry for market {market!r}"
            )
        market_block = raw[market]
        if not isinstance(market_block, dict):
            raise CalendarUnavailableError(
                f"session template entry for {market!r} must be an object"
            )
        timezone = str(market_block.get("timezone", "Asia/Shanghai"))
        sessions = _parse_sessions(
            market_block.get("regular_sessions"),
            "regular_sessions",
            market,
            path,
        )
        half_day = _parse_sessions(
            market_block.get("half_day_sessions"),
            "half_day_sessions",
            market,
            path,
        )
        if not sessions:
            raise CalendarUnavailableError(
                f"market {market!r} must define at least one regular session in {path}"
            )
        result = _MarketSessions(
            timezone=timezone,
            sessions=sessions,
            half_day_sessions=half_day,
        )
        with self._lock:
            self._sessions_cache[market] = result
        return result


def _parse_date_set(
    raw: object,
    field: str,
    path: Path,
) -> frozenset[date]:
    """Parse a JSON list of ``YYYY-MM-DD`` strings into a date frozenset."""
    if raw is None:
        return frozenset()
    if not isinstance(raw, list):
        raise CalendarUnavailableError(
            f"{field!r} in {path} must be a list of YYYY-MM-DD strings"
        )
    values: list[date] = []
    for item in raw:
        if not isinstance(item, str):
            raise CalendarUnavailableError(
                f"{field!r} entries in {path} must be YYYY-MM-DD strings"
            )
        try:
            values.append(date.fromisoformat(item))
        except ValueError as exc:
            raise CalendarUnavailableError(
                f"{field!r} entry {item!r} in {path} is not YYYY-MM-DD: {exc}"
            ) from exc
    return frozenset(values)


def _parse_sessions(
    raw: object,
    field: str,
    market: str,
    path: Path,
) -> tuple[tuple[time, time], ...]:
    """Parse a JSON session list into ``((start, end), ...)`` tuples.

    Each session is a two-element ``["HH:MM", "HH:MM"]`` array.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise CalendarUnavailableError(
            f"{field!r} for market {market!r} in {path} must be a list"
        )
    sessions: list[tuple[time, time]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, list) or len(item) != 2:
            raise CalendarUnavailableError(
                f"{field}[{index}] for market {market!r} in {path} "
                f"must be a [start, end] pair"
            )
        start = _parse_time(item[0], f"{field}[{index}][0]", market, path)
        end = _parse_time(item[1], f"{field}[{index}][1]", market, path)
        if start >= end:
            raise CalendarUnavailableError(
                f"{field}[{index}] for market {market!r} in {path}: "
                f"start {start:%H:%M} must precede end {end:%H:%M}"
            )
        sessions.append((start, end))
    return tuple(sessions)


def _parse_time(
    raw: object,
    label: str,
    market: str,
    path: Path,
) -> time:
    if not isinstance(raw, str):
        raise CalendarUnavailableError(
            f"{label} for market {market!r} in {path} must be HH:MM"
        )
    try:
        hours, minutes = raw.split(":", 1)
        return time(int(hours), int(minutes))
    except (ValueError, TypeError) as exc:
        raise CalendarUnavailableError(
            f"{label} {raw!r} for market {market!r} in {path} is not HH:MM: {exc}"
        ) from exc


def _as_date(value: date | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise CalendarUnavailableError(
                f"trade_date must use YYYY-MM-DD, got {value!r}"
            ) from exc
    raise CalendarUnavailableError(
        f"trade_date must be a date or YYYY-MM-DD string, got {type(value).__name__}"
    )
