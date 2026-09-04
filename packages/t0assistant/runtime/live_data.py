"""Live initial data preparation for the first authoritative snapshot."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from math import ceil
from typing import Any, Protocol

from packages.marketdata.calendar_query import (
    CalendarQueryPort,
    MarketContextCalendarAdapter,
)
from packages.marketdata.provider_request_queue import ProviderRequestPriority
from packages.marketdata.services.market_context_service import MarketContextService
from packages.marketdata.t0_schema import standardize_quote

from ._market_bars import (
    RuntimeMarketDataError,
    normalize_provider_bar_units,
    parse_market_timestamp,
)
from .coordinator import SessionSpec, SessionType
from .live_market_view import (
    LiveMarketViewError,
    ResolvedLiveMarketContext,
    assess_symbol_availability,
    resolve_live_market_context,
    resolve_security_data_trade_date,
)
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


class LiveCalendarUnavailableError(LiveDataError):
    """Calendar coverage is insufficient to authoritatively resolve Live."""


class LiveMarketDataPort(Protocol):
    """Narrow market-data surface needed by Live startup.

    ``instrument_type`` (stock|etf|index) is the authoritative identity enum
    (issue #151).  Concrete implementations like :class:`KLineDataService`
    adapt it to the provider's ``security_type``; indices load daily K-lines
    with no adjustment.
    """

    def get_klines_result(
        self,
        code: str,
        end_date: str,
        *,
        market: str | None = None,
        timeframe: str,
        start_date: str | None = None,
        limit: int = 120,
        instrument_type: str | None = None,
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
    max_security_day_lookback: int = 10
    request_priority: ProviderRequestPriority = ProviderRequestPriority.LIVE
    request_timeout: float | None = 15.0


class LiveDataPreparator(LiveInitialInputPort):
    """Prepare the first full Live input prefix without transport concerns."""

    def __init__(
        self,
        market_data: LiveMarketDataPort,
        market_context: MarketContextService,
        *,
        calendar: CalendarQueryPort | None = None,
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
        self._calendar = calendar or MarketContextCalendarAdapter(market_context)
        self._quote_reader = quote_reader
        self._clock = clock or datetime.now
        self._session_validator_factory = session_validator_factory
        self._config = config

    def prepare(
        self,
        spec: SessionSpec,
        *,
        minimum_preheat_5m: int,
        target_trade_date: date | None = None,
    ) -> PreparedLiveWarmup:
        if not isinstance(spec, SessionSpec):
            raise TypeError("spec must be a SessionSpec")
        if spec.session_type is not SessionType.LIVE:
            raise LiveDataError("LiveDataPreparator requires a live SessionSpec")
        if minimum_preheat_5m <= 0:
            raise LiveDataError("minimum_preheat_5m must be positive")

        resolved_symbol, code, market = _parse_symbol(spec.symbol)
        instrument_type = _instrument_type_from_spec(spec)
        session_validator = self._session_validator(spec)
        observed_now = self._resolve_observed_now()
        resolved = self._resolve_market_context(observed_now=observed_now, market=market)
        market_candidate_session = resolved.market_session
        market_candidate_date = market_candidate_session.trade_date

        quote_snapshots = self._load_quote_snapshots(code=code, market=market)
        if target_trade_date is not None:
            # Day-switch mode: force the session to the calendar target day.
            # Market data is loaded best-effort so a suspended security or a
            # failed provider request cannot block the day switch (#133).
            (
                effective_session,
                bars_1m,
                official_5m,
                daily_history,
                target_time,
            ) = self._prepare_forced_target_day(
                code=code,
                market=market,
                instrument_type=instrument_type,
                target_trade_date=target_trade_date,
                observed_now=observed_now,
                quote_snapshots=quote_snapshots,
                session_validator=session_validator,
            )
        else:
            (
                effective_session,
                bars_1m,
                official_5m,
                daily_history,
                target_time,
            ) = self._resolve_effective_security_day(
                code=code,
                market=market,
                instrument_type=instrument_type,
                start_date=market_candidate_date,
                start_session=market_candidate_session,
                observed_now=observed_now,
                quote_snapshots=quote_snapshots,
                session_validator=session_validator,
            )
            if target_time is None:
                raise LiveDataUnavailableError(
                    "live initial load requires a quote or intraday bars for the "
                    f"effective trade_date {market_candidate_date.isoformat()}"
                )

        if target_trade_date is not None:
            preheat_bars = self._load_preheat_5m_best_effort(
                code=code,
                market=market,
                instrument_type=instrument_type,
                session=effective_session,
                minimum_preheat_5m=minimum_preheat_5m,
                session_validator=session_validator,
            )
        else:
            preheat_bars = self._load_preheat_5m(
                code=code,
                market=market,
                instrument_type=instrument_type,
                session=effective_session,
                minimum_preheat_5m=minimum_preheat_5m,
                session_validator=session_validator,
            )
        previous_close = _derive_previous_close(daily_history, preheat_bars)

        # ---- 30m preheat and official 30m (direct provider, never from 5m)
        preheat_30m = self._load_preheat_30m_best_effort(
            code=code,
            market=market,
            instrument_type=instrument_type,
            session=effective_session,
            minimum_preheat_30m=minimum_preheat_5m,
            session_validator=session_validator,
        )
        official_30m = self._load_target_day_bars_best_effort(
            code=code,
            market=market,
            trade_date=effective_session.trade_date.isoformat(),
            timeframe="30m",
            instrument_type=instrument_type,
            session_validator=session_validator,
            observed_at=_bar_filter_observed_at(observed_now, effective_session.trade_date),
        )

        symbol_availability = assess_symbol_availability(
            market_candidate_trade_date=market_candidate_date,
            security_data_trade_date=resolve_security_data_trade_date(
                bars_1m,
                official_5m,
                quote_snapshots,
            ),
        )

        market_input_port = _InMemoryMarketInputPort(
            symbol=resolved_symbol,
            trade_date=effective_session.trade_date.isoformat(),
            session=effective_session,
            preheat_5m_bars=_freeze_rows(preheat_bars),
            bars_1m=_freeze_rows(bars_1m),
            official_5m_bars=_freeze_rows(official_5m),
            daily_bars_history=_freeze_rows(daily_history),
            quote_snapshots=_freeze_rows(quote_snapshots),
            previous_close=previous_close,
            preheat_30m_bars=_freeze_rows(preheat_30m),
            official_30m_bars=_freeze_rows(official_30m),
        )
        return PreparedLiveWarmup(
            market_session=effective_session,
            target_time=target_time,
            observed_now=observed_now,
            market_candidate_trade_date=market_candidate_date,
            market_input_port=market_input_port,
            calendar_status=resolved.calendar_status,
            market_phase=resolved.market_phase,
            symbol_availability=symbol_availability,
        )

    def load_refresh_bars(
        self,
        spec: SessionSpec,
        *,
        timeframe: str,
        trade_date: date | str,
    ) -> tuple[Mapping[str, Any], ...]:
        """Read one normalized intraday branch without coupling refreshes.

        ``trade_date`` must be the Session's prepared effective trade date.
        Day switching re-prepares through ``BranchingLiveInput`` when PR-B
        detects post-open evidence for the calendar target day.
        """

        if timeframe not in {"1m", "5m", "30m"}:
            raise LiveDataError("refresh timeframe must be '1m', '5m' or '30m'")
        _, code, market = self._refresh_identity(spec)
        instrument_type = _instrument_type_from_spec(spec)
        observed_now = self._resolve_observed_now()
        pinned_trade_date = _as_trade_date(trade_date)
        return self._load_target_day_bars(
            code=code,
            market=market,
            trade_date=pinned_trade_date.isoformat(),
            timeframe=timeframe,
            instrument_type=instrument_type,
            session_validator=self._session_validator(spec),
            observed_at=_bar_filter_observed_at(observed_now, pinned_trade_date),
        )

    def load_refresh_quotes(
        self,
        spec: SessionSpec,
        *,
        trade_date: date | str,
    ) -> tuple[Mapping[str, Any], ...]:
        """Read only the normalized quote branch for the prepared trade date."""

        _, code, market = self._refresh_identity(spec)
        pinned_trade_date = _as_trade_date(trade_date)
        snapshots = self._load_quote_snapshots(
            code=code,
            market=market,
            suppress_errors=False,
        )
        return tuple(
            row
            for row in snapshots
            if _quote_belongs_to_trade_date(row, pinned_trade_date)
        )

    def _refresh_identity(self, spec: SessionSpec) -> tuple[str, str, str]:
        if not isinstance(spec, SessionSpec):
            raise TypeError("spec must be a SessionSpec")
        if spec.session_type is not SessionType.LIVE:
            raise LiveDataError("LiveDataPreparator requires a live SessionSpec")
        return _parse_symbol(spec.symbol)

    def _resolve_observed_now(self) -> datetime:
        observed_now = self._clock()
        if not isinstance(observed_now, datetime) or observed_now.tzinfo is not None:
            raise LiveDataError(
                "clock must return a naive Asia/Shanghai datetime"
            )
        return observed_now

    def _resolve_market_context(
        self,
        *,
        observed_now: datetime,
        market: str,
    ) -> ResolvedLiveMarketContext:
        try:
            return resolve_live_market_context(
                self._calendar,
                observed_now=observed_now,
                market=market,
            )
        except LiveMarketViewError as exc:
            raise LiveCalendarUnavailableError(str(exc)) from exc

    def _session_validator(
        self,
        spec: SessionSpec,
    ) -> Callable[[], bool] | None:
        if self._session_validator_factory is None:
            return None
        return self._session_validator_factory(spec)

    def _resolve_effective_security_day(
        self,
        *,
        code: str,
        market: str,
        instrument_type: str | None,
        start_date: date,
        start_session,
        observed_now: datetime,
        quote_snapshots: tuple[Mapping[str, Any], ...],
        session_validator: Callable[[], bool] | None,
    ) -> tuple[
        Any,
        tuple[Mapping[str, Any], ...],
        tuple[Mapping[str, Any], ...],
        tuple[Mapping[str, Any], ...],
        datetime | None,
    ]:
        """Find the latest day with intraday evidence within a bounded lookback."""

        empty_bars: tuple[Mapping[str, Any], ...] = ()
        empty_daily: tuple[Mapping[str, Any], ...] = ()
        lookback = self._config.max_security_day_lookback
        if lookback <= 0:
            raise LiveDataError("max_security_day_lookback must be positive")

        earliest_date = _earliest_security_lookback_date(
            self._calendar,
            start_date=start_date,
            market=market,
            max_trading_days=lookback,
        )
        discovery_1m = self._load_intraday_discovery_range(
            code=code,
            market=market,
            instrument_type=instrument_type,
            start_date=earliest_date,
            end_date=start_date,
            timeframe="1m",
            session_validator=session_validator,
        )
        discovery_5m = self._load_intraday_discovery_range(
            code=code,
            market=market,
            instrument_type=instrument_type,
            start_date=earliest_date,
            end_date=start_date,
            timeframe="5m",
            session_validator=session_validator,
        )
        candidate_dates = _candidate_dates_with_intraday_evidence(
            self._calendar,
            discovery_1m,
            discovery_5m,
            market=market,
            observed_now=observed_now,
            quote_snapshots=quote_snapshots,
            earliest=earliest_date,
            latest=start_date,
        )
        for effective_date in candidate_dates:
            effective_session = self._calendar.require_session(effective_date, market)
            trade_date_str = effective_date.isoformat()
            bar_observed_at = _bar_filter_observed_at(observed_now, effective_date)
            bars_1m = self._load_target_day_bars(
                code=code,
                market=market,
                trade_date=trade_date_str,
                timeframe="1m",
                instrument_type=instrument_type,
                session_validator=session_validator,
                observed_at=bar_observed_at,
            )
            official_5m = self._load_target_day_bars(
                code=code,
                market=market,
                trade_date=trade_date_str,
                timeframe="5m",
                instrument_type=instrument_type,
                session_validator=session_validator,
                observed_at=bar_observed_at,
            )
            target_time = _select_target_time(
                observed_now=observed_now,
                trade_date=effective_date,
                session_end=effective_session.end,
                bars_1m=bars_1m,
                official_5m=official_5m,
                quote_snapshots=quote_snapshots,
            )
            if target_time is None:
                continue
            daily_history = self._load_daily_history(
                code=code,
                market=market,
                trade_date=trade_date_str,
                instrument_type=instrument_type,
                session_trade_date=effective_date,
                session_validator=session_validator,
            )
            return (
                effective_session,
                bars_1m,
                official_5m,
                daily_history,
                target_time,
            )
        return start_session, empty_bars, empty_bars, empty_daily, None

    def _prepare_forced_target_day(
        self,
        *,
        code: str,
        market: str,
        instrument_type: str | None,
        target_trade_date: date,
        observed_now: datetime,
        quote_snapshots: tuple[Mapping[str, Any], ...],
        session_validator: Callable[[], bool] | None,
    ) -> tuple[
        Any,
        tuple[Mapping[str, Any], ...],
        tuple[Mapping[str, Any], ...],
        tuple[Mapping[str, Any], ...],
        datetime,
    ]:
        """Load data for a forced target trade date (day-switch mode).

        Unlike :meth:`_resolve_effective_security_day`, this never falls back
        to a prior day and never raises for missing intraday data.  A suspended
        security simply yields empty bars / quotes with ``target_time`` set to
        the wall clock clamped to the session (#133).
        """

        effective_session = self._calendar.require_session(
            target_trade_date, market
        )
        trade_date_str = target_trade_date.isoformat()
        bar_observed_at = _bar_filter_observed_at(observed_now, target_trade_date)
        bars_1m = self._load_target_day_bars(
            code=code,
            market=market,
            trade_date=trade_date_str,
            timeframe="1m",
            instrument_type=instrument_type,
            session_validator=session_validator,
            observed_at=bar_observed_at,
        )
        official_5m = self._load_target_day_bars(
            code=code,
            market=market,
            trade_date=trade_date_str,
            timeframe="5m",
            instrument_type=instrument_type,
            session_validator=session_validator,
            observed_at=bar_observed_at,
        )
        target_time = _select_target_time(
            observed_now=observed_now,
            trade_date=target_trade_date,
            session_end=effective_session.end,
            bars_1m=bars_1m,
            official_5m=official_5m,
            quote_snapshots=quote_snapshots,
        )
        if target_time is None:
            # No intraday evidence (e.g. suspended stock).  Fall back to the
            # wall clock clamped to the session so the pipeline can still
            # produce a projection from preheat / daily history alone.
            target_time = min(observed_now, effective_session.end)
        daily_history = self._load_daily_history(
            code=code,
            market=market,
            trade_date=trade_date_str,
            instrument_type=instrument_type,
            session_trade_date=target_trade_date,
            session_validator=session_validator,
        )
        return effective_session, bars_1m, official_5m, daily_history, target_time

    def _load_preheat_5m_best_effort(
        self,
        *,
        code: str,
        market: str,
        instrument_type: str | None,
        session: Any,
        minimum_preheat_5m: int,
        session_validator: Callable[[], bool] | None,
    ) -> tuple[Mapping[str, Any], ...]:
        """Best-effort preheat loading for day-switch mode (#133).

        Returns whatever closed 5m preheat bars can be loaded.  Unlike
        :meth:`_load_preheat_5m`, this never raises ``LiveDataUnavailableError``
        so a provider failure cannot block the day switch.
        """

        try:
            return self._load_preheat_5m(
                code=code,
                market=market,
                instrument_type=instrument_type,
                session=session,
                minimum_preheat_5m=minimum_preheat_5m,
                session_validator=session_validator,
            )
        except LiveDataError:
            return ()

    def _load_preheat_30m_best_effort(
        self,
        *,
        code: str,
        market: str,
        instrument_type: str | None,
        session,
        minimum_preheat_30m: int,
        session_validator: Callable[[], bool] | None,
    ) -> tuple[Mapping[str, Any], ...]:
        """Best-effort 30m preheat loading (design §10, §14.3).

        30m preheat always comes from the official ``30m`` provider interface,
        never aggregated from 5m bars.  Unlike 5m preheat, 30m preheat is
        best-effort: a provider failure or insufficient bars must not block
        Live startup.  The 30m chart simply shows an empty state with a
        warning when preheat is unavailable.
        """

        try:
            return self._load_preheat_30m(
                code=code,
                market=market,
                instrument_type=instrument_type,
                session=session,
                minimum_preheat_30m=minimum_preheat_30m,
                session_validator=session_validator,
            )
        except LiveDataError:
            return ()

    def _load_preheat_30m(
        self,
        *,
        code: str,
        market: str,
        instrument_type: str | None,
        session,
        minimum_preheat_30m: int,
        session_validator: Callable[[], bool] | None,
    ) -> tuple[Mapping[str, Any], ...]:
        collected: list[Mapping[str, Any]] = []
        batch_end = self._calendar.previous_trading_day(
            session.trade_date,
            market,
        )
        normalized: tuple[dict[str, Any], ...] = ()
        while batch_end is not None and len(normalized) < minimum_preheat_30m:
            remaining = minimum_preheat_30m - len(normalized)
            batch_days = max(3, ceil(remaining / 8) + 2)
            batch_start = batch_end
            for _ in range(batch_days - 1):
                previous = self._calendar.previous_trading_day(
                    batch_start,
                    market,
                )
                if previous is None:
                    break
                batch_start = previous
            result = self._market_data.get_klines_result(
                code=code,
                end_date=batch_end.isoformat(),
                market=market,
                timeframe="30m",
                start_date=batch_start.isoformat(),
                limit=minimum_preheat_30m,
                instrument_type=instrument_type,
                request_priority=self._config.request_priority,
                session_validator=session_validator,
                request_timeout=self._config.request_timeout,
            )
            rows = normalize_provider_bar_units(
                _extract_live_rows(result),
                self._market_data,
            )
            collected.extend(rows)
            normalized = _normalize_preheat_bars(
                collected,
                session_start=session.start,
            )
            batch_end = self._calendar.previous_trading_day(
                batch_start,
                market,
            )
        if len(normalized) < minimum_preheat_30m:
            raise LiveDataUnavailableError(
                f"live initial load requires at least {minimum_preheat_30m} closed 30m preheat bars"
            )
        return normalized[-minimum_preheat_30m:]

    def _load_intraday_discovery_range(
        self,
        *,
        code: str,
        market: str,
        instrument_type: str | None,
        start_date: date,
        end_date: date,
        timeframe: str,
        session_validator: Callable[[], bool] | None,
    ) -> tuple[Mapping[str, Any], ...]:
        """Fetch one intraday range to locate the latest day with security data."""

        if timeframe not in {"1m", "5m"}:
            raise LiveDataError("discovery timeframe must be '1m' or '5m'")
        bars_per_day = 240 if timeframe == "1m" else 48
        span_days = max(1, (end_date - start_date).days + 1)
        limit = max(
            self._config.intraday_limit,
            self._config.max_security_day_lookback * bars_per_day,
            span_days * bars_per_day,
        )
        result = self._market_data.get_klines_result(
            code=code,
            end_date=end_date.isoformat(),
            market=market,
            timeframe=timeframe,
            start_date=start_date.isoformat(),
            limit=limit,
            instrument_type=instrument_type,
            request_priority=self._config.request_priority,
            session_validator=session_validator,
            request_timeout=self._config.request_timeout,
        )
        rows = normalize_provider_bar_units(
            _extract_live_rows(result),
            self._market_data,
        )
        return tuple(rows)

    def _load_preheat_5m(
        self,
        *,
        code: str,
        market: str,
        instrument_type: str | None,
        session,
        minimum_preheat_5m: int,
        session_validator: Callable[[], bool] | None,
    ) -> tuple[Mapping[str, Any], ...]:
        collected: list[Mapping[str, Any]] = []
        batch_end = self._calendar.previous_trading_day(
            session.trade_date,
            market,
        )
        normalized: tuple[dict[str, Any], ...] = ()
        while batch_end is not None and len(normalized) < minimum_preheat_5m:
            # A normal A-share session contributes 48 closed 5m bars. Fetch a
            # small trading-day range in one provider call instead of repeating
            # the same paged Tencent request once per day. The extra two days
            # cover common suspensions and incomplete cached sessions.
            remaining = minimum_preheat_5m - len(normalized)
            batch_days = max(3, ceil(remaining / 48) + 2)
            batch_start = batch_end
            for _ in range(batch_days - 1):
                previous = self._calendar.previous_trading_day(
                    batch_start,
                    market,
                )
                if previous is None:
                    break
                batch_start = previous
            result = self._market_data.get_klines_result(
                code=code,
                end_date=batch_end.isoformat(),
                market=market,
                timeframe="5m",
                start_date=batch_start.isoformat(),
                limit=minimum_preheat_5m,
                instrument_type=instrument_type,
                request_priority=self._config.request_priority,
                session_validator=session_validator,
                request_timeout=self._config.request_timeout,
            )
            rows = normalize_provider_bar_units(
                _extract_live_rows(result),
                self._market_data,
            )
            collected.extend(rows)
            normalized = _normalize_preheat_bars(
                collected,
                session_start=session.start,
            )
            batch_end = self._calendar.previous_trading_day(
                batch_start,
                market,
            )
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
        instrument_type: str | None,
        session_validator: Callable[[], bool] | None,
        observed_at: datetime,
    ) -> tuple[Mapping[str, Any], ...]:
        result = self._market_data.get_klines_result(
            code=code,
            end_date=trade_date,
            market=market,
            timeframe=timeframe,
            start_date=trade_date,
            limit=self._config.intraday_limit,
            instrument_type=instrument_type,
            request_priority=self._config.request_priority,
            session_validator=session_validator,
            request_timeout=self._config.request_timeout,
        )
        rows = [
            row
            for row in normalize_provider_bar_units(
                _extract_live_rows(result),
                self._market_data,
            )
            if _live_bar_is_closed_at(row, observed_at)
        ]
        session = self._calendar.require_session(trade_date, market)
        return _normalize_target_day_bars(rows, session=session)

    def _load_target_day_bars_best_effort(
        self,
        *,
        code: str,
        market: str,
        trade_date: str,
        timeframe: str,
        instrument_type: str | None,
        session_validator: Callable[[], bool] | None,
        observed_at: datetime,
    ) -> tuple[Mapping[str, Any], ...]:
        """Best-effort official bar load; 30m failures must not block Live."""

        try:
            return self._load_target_day_bars(
                code=code,
                market=market,
                trade_date=trade_date,
                timeframe=timeframe,
                instrument_type=instrument_type,
                session_validator=session_validator,
                observed_at=observed_at,
            )
        except LiveDataError:
            return ()

    def _load_daily_history(
        self,
        *,
        code: str,
        market: str,
        trade_date: str,
        instrument_type: str | None,
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
            instrument_type=instrument_type,
            request_priority=self._config.request_priority,
            session_validator=session_validator,
            request_timeout=self._config.request_timeout,
        )
        rows = normalize_provider_bar_units(
            _extract_live_rows(result),
            self._market_data,
        )
        return _normalize_daily_bars(rows, trade_date=session_trade_date)

    def _load_quote_snapshots(
        self,
        *,
        code: str,
        market: str,
        suppress_errors: bool = True,
    ) -> tuple[Mapping[str, Any], ...]:
        if self._quote_reader is None:
            return ()
        try:
            result = self._quote_reader.realtime_result(code, markets=[market])
        except Exception:
            if suppress_errors:
                return ()
            raise
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


def _earliest_security_lookback_date(
    calendar: CalendarQueryPort,
    *,
    start_date: date,
    market: str,
    max_trading_days: int,
) -> date:
    earliest = start_date
    for _ in range(max(0, max_trading_days - 1)):
        previous = calendar.previous_trading_day(earliest, market)
        if previous is None:
            break
        earliest = previous
    return earliest


def _candidate_dates_with_intraday_evidence(
    calendar: CalendarQueryPort,
    *row_groups: Sequence[Mapping[str, Any]],
    market: str,
    observed_now: datetime,
    quote_snapshots: Sequence[Mapping[str, Any]],
    earliest: date,
    latest: date,
) -> list[date]:
    """Return trading-day candidates with valid intraday evidence, newest first."""

    candidates: set[date] = set()
    for rows in row_groups:
        for row in rows:
            timestamp = row.get("timestamp", row.get("date"))
            if not isinstance(timestamp, str):
                continue
            try:
                parsed = parse_market_timestamp(timestamp)
            except (TypeError, ValueError):
                continue
            row_date = parsed.date()
            if row_date < earliest or row_date > latest:
                continue
            if not calendar.is_trading_day(row_date, market):
                continue
            session = calendar.require_session(row_date, market)
            if not session.is_trading_time(parsed):
                continue
            bar_observed_at = _bar_filter_observed_at(observed_now, row_date)
            if not _live_bar_is_closed_at(row, bar_observed_at):
                continue
            candidates.add(row_date)
    for row in quote_snapshots:
        timestamp = row.get("timestamp")
        if not isinstance(timestamp, str):
            continue
        try:
            row_date = parse_market_timestamp(timestamp).date()
        except (TypeError, ValueError):
            continue
        if row_date < earliest or row_date > latest:
            continue
        if not calendar.is_trading_day(row_date, market):
            continue
        candidates.add(row_date)
    return sorted(candidates, reverse=True)


def _select_target_time(
    *,
    observed_now: datetime,
    trade_date: date,
    session_end: datetime,
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
    if observed_now.date() != trade_date:
        # Closed / holiday / pre-open view of a prior session: stay on that day.
        return min(latest, session_end)
    return observed_now if observed_now >= latest else latest


def _bar_filter_observed_at(observed_now: datetime, trade_date: date) -> datetime:
    """Allow closed bars from an earlier effective day when wall clock is later."""

    if observed_now.date() == trade_date:
        return observed_now
    return datetime.combine(trade_date, time(23, 59, 59))


def _instrument_type_from_spec(spec: SessionSpec) -> str | None:
    """Extract the authoritative ``instrument_type`` from a SessionSpec.

    The App layer resolves the full :class:`InstrumentIdentity` once at entry
    and attaches it to ``spec.instrument``.  When absent (e.g. legacy callers
    or tests that have not been migrated), ``None`` lets the downstream
    provider fall back to its default security-type handling.
    """

    instrument = getattr(spec, "instrument", None)
    if instrument is None:
        return None
    # InstrumentIdentity is a frozen dataclass with an ``instrument_type``
    # field whose value is an ``InstrumentType`` StrEnum member (or plain str).
    raw = getattr(instrument, "instrument_type", None)
    if raw is None:
        return None
    return str(raw)


def _as_trade_date(value: date | str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise LiveDataError("trade_date must use YYYY-MM-DD") from exc


def _quote_belongs_to_trade_date(row: Mapping[str, Any], trade_date: date) -> bool:
    timestamp = row.get("timestamp")
    if not isinstance(timestamp, str):
        return False
    try:
        return parse_market_timestamp(timestamp).date() == trade_date
    except (TypeError, ValueError):
        return False


def _extract_live_rows(result: Any) -> list[Mapping[str, Any]]:
    """Return usable rows or fail fast on an unsuccessful provider request."""

    rows = _extract_rows(result)
    if rows:
        return rows
    success = getattr(result, "success", True)
    issues = getattr(result, "issues", ()) or ()
    error_reason = next(
        (
            getattr(issue, "reason_code", "")
            for issue in issues
            if getattr(issue, "level", "") == "error"
        ),
        "",
    )
    if success is False or error_reason:
        if error_reason == "request_timeout":
            raise LiveDataUnavailableError("live market data request timed out")
        raise LiveDataUnavailableError("live market data request failed")
    return rows


def _live_bar_is_closed_at(
    row: Mapping[str, Any],
    observed_at: datetime,
) -> bool:
    """Exclude Tencent's current, future-labelled minute bucket from Live."""

    timestamp = row.get("timestamp", row.get("date"))
    if not isinstance(timestamp, str):
        return True
    try:
        return parse_market_timestamp(timestamp) <= observed_at
    except (TypeError, ValueError):
        # Let the strict shared normalizer produce the stable validation error.
        return True


__all__ = [
    "LiveDataError",
    "LiveDataPreparator",
    "LiveDataUnavailableError",
    "LiveMarketDataPort",
    "LivePreparationConfig",
    "LiveQuotePort",
]
