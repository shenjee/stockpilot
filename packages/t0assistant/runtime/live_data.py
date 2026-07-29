"""Live initial data preparation for the first authoritative snapshot."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from packages.marketdata.provider_request_queue import ProviderRequestPriority
from packages.marketdata.services.market_context_service import MarketContextService
from packages.marketdata.t0_schema import standardize_quote

from ._market_bars import RuntimeMarketDataError, parse_market_timestamp
from .coordinator import SessionSpec, SessionType
from .live_session import LiveInitialInputPort, PreparedLiveWarmup
from .replay_data import (
    _InMemoryMarketInputPort,
    _derive_previous_close,
    _extract_rows,
    _freeze_rows,
    _normalize_daily_bars,
    _normalize_preheat_bars,
    _normalize_target_day_bars,
    _parse_symbol,
)


class LiveDataError(RuntimeMarketDataError):
    """Base class for stable Live warmup failures."""


class LiveDataUnavailableError(LiveDataError):
    """The first Live snapshot cannot be prepared from current market data."""


class LiveMarketDataPort(Protocol):
    """Narrow market-data surface needed by Live startup."""

    def get_klines_result(
        self,
        code: str,
        end_date: str,
        *,
        market: str | None = None,
        timeframe: str,
        start_date: str | None = None,
        limit: int = 120,
        request_priority: ProviderRequestPriority = ...,
        session_validator: Callable[[], bool] | None = None,
        request_timeout: float | None = None,
    ) -> Any: ...


class LiveQuotePort(Protocol):
    """Optional quote source for a fresher Live target moment."""

    def realtime_result(self, codes, markets=None) -> Any: ...


@dataclass(frozen=True, slots=True)
class LivePreparationConfig:
    """Explicit knobs for one Live initial-load preparation run."""

    daily_history_days: int = 120
    intraday_limit: int = 300
    request_priority: ProviderRequestPriority = ProviderRequestPriority.LIVE
    request_timeout: float | None = None


class LiveDataPreparator(LiveInitialInputPort):
    """Prepare the first full Live input prefix without transport concerns."""

    def __init__(
        self,
        market_data: LiveMarketDataPort,
        market_context: MarketContextService,
        *,
        quote_reader: LiveQuotePort | None = None,
        clock: Callable[[], datetime] | None = None,
        session_validator_factory: Callable[[SessionSpec], Callable[[], bool] | None]
        | None = None,
        config: LivePreparationConfig = LivePreparationConfig(),
    ) -> None:
        if not isinstance(market_context, MarketContextService):
            raise TypeError("market_context must be a MarketContextService")
        self._market_data = market_data
        self._market_context = market_context
        self._quote_reader = quote_reader
        self._clock = clock or datetime.now
        self._session_validator_factory = session_validator_factory
        self._config = config

    def prepare(
        self,
        spec: SessionSpec,
        *,
        minimum_preheat_5m: int,
    ) -> PreparedLiveWarmup:
        if not isinstance(spec, SessionSpec):
            raise TypeError("spec must be a SessionSpec")
        if spec.session_type is not SessionType.LIVE:
            raise LiveDataError("LiveDataPreparator requires a live SessionSpec")
        if minimum_preheat_5m <= 0:
            raise LiveDataError("minimum_preheat_5m must be positive")

        resolved_symbol, code, market = _parse_symbol(spec.symbol)
        session_validator = (
            None
            if self._session_validator_factory is None
            else self._session_validator_factory(spec)
        )
        observed_now = self._clock()
        if not isinstance(observed_now, datetime) or observed_now.tzinfo is not None:
            raise LiveDataError(
                "clock must return a naive Asia/Shanghai datetime"
            )

        session = self._market_context.require_session(observed_now.date(), market)
        trade_date_str = session.trade_date.isoformat()

        preheat_bars = self._load_preheat_5m(
            code=code,
            market=market,
            session=session,
            minimum_preheat_5m=minimum_preheat_5m,
            session_validator=session_validator,
        )
        bars_1m = self._load_target_day_bars(
            code=code,
            market=market,
            trade_date=trade_date_str,
            timeframe="1m",
            session_validator=session_validator,
        )
        official_5m = self._load_target_day_bars(
            code=code,
            market=market,
            trade_date=trade_date_str,
            timeframe="5m",
            session_validator=session_validator,
        )
        daily_history = self._load_daily_history(
            code=code,
            market=market,
            trade_date=trade_date_str,
            session_trade_date=session.trade_date,
            session_validator=session_validator,
        )
        quote_snapshots = self._load_quote_snapshots(code=code, market=market)
        previous_close = _derive_previous_close(daily_history, preheat_bars)
        target_time = _select_target_time(
            observed_now=observed_now,
            trade_date=session.trade_date,
            bars_1m=bars_1m,
            official_5m=official_5m,
            quote_snapshots=quote_snapshots,
        )
        if target_time is None:
            raise LiveDataUnavailableError(
                "live initial load requires a quote or intraday bars for the current trade_date"
            )

        market_input_port = _InMemoryMarketInputPort(
            symbol=resolved_symbol,
            trade_date=trade_date_str,
            session=session,
            preheat_5m_bars=_freeze_rows(preheat_bars),
            bars_1m=_freeze_rows(bars_1m),
            official_5m_bars=_freeze_rows(official_5m),
            daily_bars_history=_freeze_rows(daily_history),
            quote_snapshots=_freeze_rows(quote_snapshots),
            previous_close=previous_close,
        )
        return PreparedLiveWarmup(
            market_session=session,
            target_time=target_time,
            market_input_port=market_input_port,
        )

    def _load_preheat_5m(
        self,
        *,
        code: str,
        market: str,
        session,
        minimum_preheat_5m: int,
        session_validator: Callable[[], bool] | None,
    ) -> tuple[Mapping[str, Any], ...]:
        collected: list[Mapping[str, Any]] = []
        day = self._market_context.previous_trading_day(session.trade_date, market)
        normalized: tuple[dict[str, Any], ...] = ()
        while day is not None and len(normalized) < minimum_preheat_5m:
            day_str = day.isoformat()
            result = self._market_data.get_klines_result(
                code=code,
                end_date=day_str,
                market=market,
                timeframe="5m",
                start_date=day_str,
                limit=minimum_preheat_5m,
                request_priority=self._config.request_priority,
                session_validator=session_validator,
                request_timeout=self._config.request_timeout,
            )
            collected.extend(_extract_rows(result))
            normalized = _normalize_preheat_bars(
                collected,
                session_start=session.start,
            )
            day = self._market_context.previous_trading_day(day, market)
        if len(normalized) < minimum_preheat_5m:
            raise LiveDataUnavailableError(
                f"live initial load requires at least {minimum_preheat_5m} closed 5m preheat bars"
            )
        return normalized[-minimum_preheat_5m:]

    def _load_target_day_bars(
        self,
        *,
        code: str,
        market: str,
        trade_date: str,
        timeframe: str,
        session_validator: Callable[[], bool] | None,
    ) -> tuple[Mapping[str, Any], ...]:
        result = self._market_data.get_klines_result(
            code=code,
            end_date=trade_date,
            market=market,
            timeframe=timeframe,
            start_date=trade_date,
            limit=self._config.intraday_limit,
            request_priority=self._config.request_priority,
            session_validator=session_validator,
            request_timeout=self._config.request_timeout,
        )
        rows = _extract_rows(result)
        session = self._market_context.require_session(trade_date, market)
        return _normalize_target_day_bars(rows, session=session)

    def _load_daily_history(
        self,
        *,
        code: str,
        market: str,
        trade_date: str,
        session_trade_date,
        session_validator: Callable[[], bool] | None,
    ) -> tuple[Mapping[str, Any], ...]:
        result = self._market_data.get_klines_result(
            code=code,
            end_date=trade_date,
            market=market,
            timeframe="day",
            start_date=None,
            limit=self._config.daily_history_days,
            request_priority=self._config.request_priority,
            session_validator=session_validator,
            request_timeout=self._config.request_timeout,
        )
        rows = _extract_rows(result)
        return _normalize_daily_bars(rows, trade_date=session_trade_date)

    def _load_quote_snapshots(
        self,
        *,
        code: str,
        market: str,
    ) -> tuple[Mapping[str, Any], ...]:
        if self._quote_reader is None:
            return ()
        try:
            result = self._quote_reader.realtime_result(code, markets=[market])
        except Exception:
            return ()
        payload = getattr(result, "data", result)
        if isinstance(payload, Mapping):
            rows = [payload]
        elif payload is None:
            rows = []
        else:
            rows = list(payload)
        snapshots: list[dict[str, Any]] = []
        for row in rows:
            try:
                snapshots.append(standardize_quote(row))
            except (TypeError, ValueError):
                continue
        return tuple(snapshots)


def _select_target_time(
    *,
    observed_now: datetime,
    trade_date: date,
    bars_1m: Sequence[Mapping[str, Any]],
    official_5m: Sequence[Mapping[str, Any]],
    quote_snapshots: Sequence[Mapping[str, Any]],
) -> datetime | None:
    candidates: list[datetime] = []
    for row in bars_1m:
        timestamp = row.get("timestamp", row.get("date"))
        if isinstance(timestamp, str):
            candidates.append(parse_market_timestamp(timestamp))
    for row in official_5m:
        timestamp = row.get("timestamp", row.get("date"))
        if isinstance(timestamp, str):
            candidates.append(parse_market_timestamp(timestamp))
    for row in quote_snapshots:
        timestamp = row.get("timestamp")
        if isinstance(timestamp, str):
            parsed = parse_market_timestamp(timestamp)
            if parsed.date() == trade_date:
                candidates.append(parsed)
    if not candidates:
        return None
    latest = max(candidates)
    return observed_now if observed_now >= latest else latest


__all__ = [
    "LiveDataError",
    "LiveDataPreparator",
    "LiveDataUnavailableError",
    "LiveMarketDataPort",
    "LivePreparationConfig",
    "LiveQuotePort",
]
