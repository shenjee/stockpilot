"""Deterministic Replay data fixtures for T0-045.

The fixtures are generated programmatically so they stay byte-stable and cover
the required market structure: a cross-trading-day 5m preheat sequence, a full
target-day 1m sequence and a full target-day official 5m sequence spanning the
morning session, the lunch break and the afternoon session.

Two fixtures are provided:

* ``one_minute_replay``: target day has both complete 1m and official 5m bars.
* ``five_minute_fallback``: same symbol/trade date, but the 1m sequence is
  missing/unreliable so the preparation module must downgrade to 5m.

The fixtures intentionally do not touch the network or SQLite.  They expose
``PreparedFixture`` dataclasses whose fields mirror the inputs the
``ReplayDataPreparator`` consumes from ``KLineDataService``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from packages.marketdata.services.market_context_service import (
    MarketContextService,
    MarketSession,
)


TRADE_DATE = date(2026, 7, 24)
PREVIOUS_TRADE_DATE = date(2026, 7, 23)
SYMBOL = "sh.600000"
MARKET = "sh"
CODE = "600000"
SECURITY_NAME = "浦发银行"
SECURITY_TYPE = "a_share"

# Coverage window for the injected calendar.  Two trading days are enough for
# the fixtures; the calendar rejects weekends so 2026-07-23 (Thursday) and
# 2026-07-24 (Friday) are both valid.
TRADING_DAYS = (PREVIOUS_TRADE_DATE, TRADE_DATE)


def _market_context() -> MarketContextService:
    return MarketContextService(
        TRADING_DAYS,
        coverage_start=PREVIOUS_TRADE_DATE,
        coverage_end=TRADE_DATE,
    )


def market_session() -> MarketSession:
    """Return the target-day :class:`MarketSession` used by every fixture."""

    return _market_context().require_session(TRADE_DATE, MARKET)


def _session_times() -> tuple[datetime, datetime, datetime, datetime]:
    """Return (open, morning_close, afternoon_open, close) for the target day."""

    session = market_session()
    return session.start, session.morning_close, session.afternoon_open, session.end


def _bar(
    timestamp: datetime,
    *,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: int,
    amount: float,
    closed: bool = True,
) -> dict[str, Any]:
    return {
        "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": amount,
        "closed": closed,
    }


def _minute_bar(timestamp: datetime, index: int, *, base_price: float = 10.00) -> dict[str, Any]:
    """Generate a deterministic 1m bar at ``timestamp``."""

    price = round(base_price + index * 0.01, 2)
    return _bar(
        timestamp,
        open_price=price,
        high=round(price + 0.02, 2),
        low=round(price - 0.02, 2),
        close=round(price + 0.01, 2),
        volume=1000 + index * 10,
        amount=round((1000 + index * 10) * (price + 0.01), 2),
        closed=True,
    )


def _five_minute_bar(timestamp: datetime, index: int, *, base_price: float = 10.00) -> dict[str, Any]:
    """Generate a deterministic official closed 5m bar at ``timestamp``."""

    price = round(base_price + index * 0.05, 2)
    return _bar(
        timestamp,
        open_price=price,
        high=round(price + 0.10, 2),
        low=round(price - 0.10, 2),
        close=round(price + 0.05, 2),
        volume=5000 + index * 50,
        amount=round((5000 + index * 50) * (price + 0.05), 2),
        closed=True,
    )


def _daily_bar(trade_date: date, *, close: float, base_price: float = 10.00) -> dict[str, Any]:
    return {
        "timestamp": trade_date.isoformat(),
        "open": base_price,
        "high": round(close + 0.20, 2),
        "low": round(base_price - 0.10, 2),
        "close": close,
        "volume": 1_200_000,
        "amount": round(1_200_000 * close, 2),
        "closed": True,
    }


def _minute_close_times() -> list[datetime]:
    """All nominal 1m close times for the target day, excluding lunch break."""

    session = market_session()
    return list(session.bar_close_times(1))


def _five_minute_close_times() -> list[datetime]:
    """All nominal 5m close times for the target day, excluding lunch break."""

    session = market_session()
    return list(session.bar_close_times(5))


def _preheat_5m_bars() -> list[dict[str, Any]]:
    """Cross-trading-day official closed 5m bars for the previous trading day.

    These bars are strictly before the target session start so the pipeline
    treats them as preheat history.
    """

    previous_session = _market_context().require_session(PREVIOUS_TRADE_DATE, MARKET)
    bars: list[dict[str, Any]] = []
    # Use the last 6 five-minute bars of the previous trading day (14:30-15:00).
    for index, timestamp in enumerate(previous_session.bar_close_times(5)[-6:]):
        bars.append(_five_minute_bar(timestamp, index, base_price=9.50))
    return bars


def _target_day_1m_bars() -> list[dict[str, Any]]:
    """Full target-day 1m bars covering morning and afternoon sessions."""

    bars: list[dict[str, Any]] = []
    for index, timestamp in enumerate(_minute_close_times()):
        bars.append(_minute_bar(timestamp, index))
    return bars


def _target_day_5m_bars() -> list[dict[str, Any]]:
    """Full target-day official closed 5m bars."""

    bars: list[dict[str, Any]] = []
    for index, timestamp in enumerate(_five_minute_close_times()):
        bars.append(_five_minute_bar(timestamp, index))
    return bars


def _daily_bars_history() -> list[dict[str, Any]]:
    """Closed daily bars strictly before the target trade date."""

    return [
        _daily_bar(PREVIOUS_TRADE_DATE, close=9.80, base_price=9.50),
    ]


def _quote_snapshots() -> list[dict[str, Any]]:
    """A small set of quote snapshots at 1m boundaries for the target day."""

    times = _minute_close_times()
    snapshots: list[dict[str, Any]] = []
    for index, timestamp in enumerate(times[::30]):
        price = round(10.00 + index * 0.30, 2)
        snapshots.append(
            {
                "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "latest_price": price,
                "change_percent": round((price - 9.80) / 9.80 * 100, 2),
                "open": 10.00,
                "high": round(price + 0.05, 2),
                "low": round(price - 0.05, 2),
                "previous_close": 9.80,
                "volume": 100_000 + index * 1000,
                "amount": round((100_000 + index * 1000) * price, 2),
                "volume_ratio": None,
                "order_imbalance": None,
                "turnover_rate": None,
            }
        )
    return snapshots


@dataclass(frozen=True, slots=True)
class PreparedFixture:
    """A deterministic fixture consumed by the preparation tests.

    The fields mirror what ``ReplayDataPreparator`` reads from the market-data
    service: bars by timeframe and the reliability assessment.  ``bars_1m`` is
    ``None`` when the fixture simulates 1m being unavailable.
    """

    name: str
    symbol: str
    market: str
    trade_date: str
    preheat_5m_bars: tuple[dict[str, Any], ...]
    target_day_1m_bars: tuple[dict[str, Any], ...] | None
    target_day_5m_bars: tuple[dict[str, Any], ...]
    daily_bars_history: tuple[dict[str, Any], ...]
    quote_snapshots: tuple[dict[str, Any], ...]
    previous_close: float
    expected_granularity: str
    expected_1m_reliable: bool
    expected_5m_reliable: bool


def one_minute_replay() -> PreparedFixture:
    """Fixture with complete 1m and 5m data for the target day."""

    return PreparedFixture(
        name="one_minute_replay",
        symbol=SYMBOL,
        market=MARKET,
        trade_date=TRADE_DATE.isoformat(),
        preheat_5m_bars=tuple(_preheat_5m_bars()),
        target_day_1m_bars=tuple(_target_day_1m_bars()),
        target_day_5m_bars=tuple(_target_day_5m_bars()),
        daily_bars_history=tuple(_daily_bars_history()),
        quote_snapshots=tuple(_quote_snapshots()),
        previous_close=9.80,
        expected_granularity="one_minute",
        expected_1m_reliable=True,
        expected_5m_reliable=True,
    )


def five_minute_fallback() -> PreparedFixture:
    """Fixture where 1m is unavailable but official 5m is reliable."""

    return PreparedFixture(
        name="five_minute_fallback",
        symbol=SYMBOL,
        market=MARKET,
        trade_date=TRADE_DATE.isoformat(),
        preheat_5m_bars=tuple(_preheat_5m_bars()),
        target_day_1m_bars=None,
        target_day_5m_bars=tuple(_target_day_5m_bars()),
        daily_bars_history=tuple(_daily_bars_history()),
        quote_snapshots=tuple(_quote_snapshots()),
        previous_close=9.80,
        expected_granularity="five_minute",
        expected_1m_reliable=False,
        expected_5m_reliable=True,
    )


def market_context_service() -> MarketContextService:
    """Return the shared :class:`MarketContextService` used by fixtures."""

    return _market_context()


def security_identity() -> dict[str, str]:
    """Return the standard security identity for the fixture symbol."""

    return {
        "symbol": SYMBOL,
        "code": CODE,
        "market": MARKET,
        "name": SECURITY_NAME,
        "security_type": SECURITY_TYPE,
    }


__all__ = [
    "PreparedFixture",
    "CODE",
    "MARKET",
    "SECURITY_NAME",
    "SECURITY_TYPE",
    "SYMBOL",
    "TRADE_DATE",
    "PREVIOUS_TRADE_DATE",
    "five_minute_fallback",
    "market_context_service",
    "market_session",
    "one_minute_replay",
    "security_identity",
]
