"""Replay data preparation, pre-market warmup and granularity degradation.

The :class:`ReplayDataPreparator` owns the ``loading -> ready`` transition of a
Replay Session.  It reads bars through an injected :class:`ReplayMarketDataPort`
(a narrow projection of :class:`KLineDataService`), assesses the reliability of
the 1m and official-5m sequences, backfills gaps through the shared Provider
request queue, and returns an immutable :class:`PreparedReplayData` plus a
pure-memory :class:`_InMemoryMarketInputPort` that T0-046 can drive without any
network or SQLite access.

Design rules enforced here:

* The preparator never imports Electron, React, HTTP, SQLite or the Tencent
  provider.  It only depends on :mod:`packages.marketdata` ports and the
  standard library.
* Reliability is not a hard bar count.  The preparator reuses the market
  calendar and the standard-bar validator so suspensions and real gaps are
  distinguished from lunch-break placeholders.
* Degradation to 5m follows the contract: ``bars_1m`` becomes empty, the
  warning uses the frozen ``one_minute_data_unavailable`` structure, and
  ``end_time`` always comes from :attr:`MarketSession.end`.
* Provider raw payloads, file paths and wall-clock time never leak into the
  prepared result.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Any, Protocol

from packages.marketdata.provider_request_queue import ProviderRequestPriority
from packages.marketdata.services.market_context_service import (
    MarketContextService,
    MarketSession,
)
from packages.marketdata.t0_schema import standardize_bar

from .computation_contract import (
    PreparedReplayData,
    ReplayMarketInputPort,
    ReplayReliabilityAssessment,
)
from .live_market_view import DEFAULT_CHART_PREHEAT_COUNT
from ._market_bars import (
    MARKET_TIMESTAMP_FORMAT,
    RuntimeMarketDataError,
    normalize_provider_bar_units,
    parse_market_timestamp,
)


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


_ALLOWED_GRANULARITIES = frozenset({"one_minute", "five_minute"})
_ONE_MINUTE_DATA_UNAVAILABLE_WARNING: Mapping[str, Any] = {
    "warning_code": "one_minute_data_unavailable",
    "severity": "warning",
    "message": "目标日没有 1 分钟数据，已使用 5 分钟回放",
    "affected_capability": "intraday_chart",
    "affected_field": "market.bars_1m",
    "details": {},
}


class ReplayDataError(RuntimeMarketDataError):
    """Base class for stable Replay data preparation failures."""


class ReplayDataUnavailableError(ReplayDataError):
    """Raised when neither 1m nor official-5m can form a reliable Replay.

    Maps onto ``ReplayApiError("replay_price_data_unavailable")`` at the API
    boundary. The historical class name is retained for call-site stability;
    the public error code is the v1.1 name.
    """


class ReplayDataInvalidError(ReplayDataError):
    """Raised when returned price bars contain illegal OHLC or field values.

    Maps onto ``ReplayApiError("replay_data_invalid")`` at the API boundary.
    ``details`` always includes ``timeframe``, ``affected_field`` and
    ``invalid_count`` so callers need not parse the exception message.
    """

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.details = dict(details or {})


class ReplayDataTimeoutError(ReplayDataError):
    """Raised when preparation exceeds its absolute monotonic deadline."""


class ReplayMarketDataPort(Protocol):
    """Narrow projection of :class:`KLineDataService` consumed by the preparator.

    The port keeps the preparator independent of SQLite and the Tencent
    provider.  Implementations may wrap a real ``KLineDataService`` or a
    deterministic fake.

    ``instrument_type`` (stock|etf|index) is the authoritative identity enum
    (issue #151).  Concrete implementations adapt it to the provider's
    ``security_type``; indices load daily K-lines with no adjustment.
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

    def identify_missing_ranges(
        self,
        *,
        code: str,
        start_date: str,
        end_date: str,
        market: str | None,
        timeframe: str,
    ) -> list[tuple[str, str]]: ...

    def replay_reliability_evidence(
        self,
        *,
        code: str,
        trade_date: str,
        market: str | None,
        timeframe: str,
    ) -> bool | None: ...


@dataclass(frozen=True, slots=True)
class ReplayPreparationConfig:
    """Injected configuration for one preparation run.

    All timeouts and counts are explicit so no magic numbers live in business
    code.  Tests inject short values; production injects longer ones.
    """

    preheat_5m_count: int = DEFAULT_CHART_PREHEAT_COUNT
    preheat_30m_count: int = DEFAULT_CHART_PREHEAT_COUNT
    daily_history_days: int = 120
    deadline_monotonic: float | None = None
    request_priority: ProviderRequestPriority = ProviderRequestPriority.REPLAY_PREFETCH


class ReplayDataPreparator:
    """Prepare the complete in-memory Replay input before ``ready``.

    Args:
        market_data: port over :class:`KLineDataService` (never the provider).
        market_context: authoritative trading calendar.
        clock: monotonic clock callable used only for deadline checks.
            Defaults to :func:`time.monotonic` so deadlines are always
            enforced even when the caller does not inject a clock.
    """

    def __init__(
        self,
        market_data: ReplayMarketDataPort,
        market_context: MarketContextService,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not isinstance(market_context, MarketContextService):
            raise TypeError("market_context must be a MarketContextService")
        self._market_data = market_data
        self._market_context = market_context
        self._clock = clock or time.monotonic

    def prepare(
        self,
        symbol: str,
        trade_date: date | str,
        *,
        config: ReplayPreparationConfig,
        session_validator: Callable[[], bool] | None = None,
        instrument_type: str | None = None,
    ) -> PreparedReplayData:
        """Prepare and validate the complete Replay input.

        Args:
            instrument_type: authoritative identity enum (stock|etf|index)
                resolved once at the App/API entry from the securities master.
                When ``None``, the provider falls back to its default
                security-type handling.

        Raises:
            ReplayDataUnavailableError: when neither 1m nor official 5m can
                form a reliable Replay.
            ReplayDataTimeoutError: when the absolute deadline elapses before
                preparation completes.
        """

        resolved_symbol, code, market = _parse_symbol(symbol)
        resolved_trade_date = _parse_date(trade_date)
        session = self._market_context.require_session(resolved_trade_date, market)
        trade_date_str = resolved_trade_date.isoformat()

        # ---- preheat 5m (cross-trading-day, before session start) ----------
        preheat_bars = self._load_preheat_5m(
            code=code,
            market=market,
            instrument_type=instrument_type,
            session=session,
            config=config,
            session_validator=session_validator,
        )

        # ---- target-day 1m and official 5m --------------------------------
        # Illegal OHLC invalidates only that granularity. Prefer 1m when
        # reliable; otherwise degrade to legal official 5m. Raise
        # ReplayDataInvalidError only when no legal input remains and at
        # least one attempted granularity already returned illegal bars.
        probe_1m = self._assess_granularity(
            code=code,
            market=market,
            instrument_type=instrument_type,
            session=session,
            timeframe="1m",
            config=config,
            session_validator=session_validator,
        )
        probe_5m = self._assess_granularity(
            code=code,
            market=market,
            instrument_type=instrument_type,
            session=session,
            timeframe="5m",
            config=config,
            session_validator=session_validator,
        )

        bars_1m: tuple[Mapping[str, Any], ...]
        if probe_1m.assessment.is_reliable:
            bars_1m = self._load_target_day_bars(
                code=code,
                market=market,
                instrument_type=instrument_type,
                session=session,
                timeframe="1m",
                config=config,
                session_validator=session_validator,
            )
        else:
            bars_1m = ()

        if probe_5m.assessment.is_reliable:
            official_5m = self._load_target_day_bars(
                code=code,
                market=market,
                instrument_type=instrument_type,
                session=session,
                timeframe="5m",
                config=config,
                session_validator=session_validator,
            )
        else:
            official_5m = ()
        assessment_5m = ReplayReliabilityAssessment(
            granularity="five_minute",
            is_reliable=probe_5m.assessment.is_reliable and len(official_5m) > 0,
            bar_count=len(official_5m),
            covered_missing_ranges=probe_5m.assessment.covered_missing_ranges,
            uncovered_missing_ranges=probe_5m.assessment.uncovered_missing_ranges,
        )

        if probe_1m.assessment.is_reliable:
            granularity = "one_minute"
            warnings: tuple[Mapping[str, Any], ...] = ()
        elif assessment_5m.is_reliable:
            granularity = "five_minute"
            warnings = (_ONE_MINUTE_DATA_UNAVAILABLE_WARNING,)
        else:
            invalid = probe_1m.invalid_error or probe_5m.invalid_error
            if invalid is not None:
                raise invalid
            raise ReplayDataUnavailableError(
                "neither 1m nor official 5m data can form a reliable Replay"
            )

        # ---- history: daily K and quote snapshots -------------------------
        daily_history = self._load_daily_history(
            code=code,
            market=market,
            instrument_type=instrument_type,
            session=session,
            config=config,
            session_validator=session_validator,
        )
        quote_snapshots = self._load_quote_snapshots(
            code=code,
            market=market,
            session=session,
            config=config,
            session_validator=session_validator,
        )
        previous_close = _derive_previous_close(daily_history, preheat_bars)

        # ---- preheat 30m (cross-trading-day, before session start) ----------
        # 30m preheat always comes from the official 30m provider interface,
        # never aggregated from 5m bars (design §10, §14.3).
        preheat_30m: tuple[Mapping[str, Any], ...] = ()
        official_30m: tuple[Mapping[str, Any], ...] = ()
        try:
            preheat_30m = self._load_preheat_30m(
                code=code,
                market=market,
                instrument_type=instrument_type,
                session=session,
                config=config,
                session_validator=session_validator,
            )
        except ReplayDataError:
            preheat_30m = ()
        try:
            official_30m = self._load_target_day_bars(
                code=code,
                market=market,
                instrument_type=instrument_type,
                session=session,
                timeframe="30m",
                config=config,
                session_validator=session_validator,
            )
        except ReplayDataError:
            official_30m = ()

        actual_bar_times = _build_actual_bar_times(
            granularity=granularity,
            bars_1m=bars_1m,
            official_5m=official_5m,
            session=session,
        )

        # Final deadline check before returning the prepared result.  A slow
        # provider that returned after the deadline must not let a stale
        # PreparedReplayData reach the caller.
        self._check_deadline(config)

        frozen_preheat_bars = _freeze_rows(preheat_bars)
        frozen_bars_1m = _freeze_rows(bars_1m)
        frozen_official_5m = _freeze_rows(official_5m)
        frozen_daily_history = _freeze_rows(daily_history)
        frozen_quote_snapshots = _freeze_rows(quote_snapshots)
        frozen_warnings = _freeze_rows(warnings)
        frozen_preheat_30m = _freeze_rows(preheat_30m)
        frozen_official_30m = _freeze_rows(official_30m)

        market_input_port = _InMemoryMarketInputPort(
            symbol=resolved_symbol,
            trade_date=trade_date_str,
            session=session,
            preheat_5m_bars=frozen_preheat_bars,
            bars_1m=frozen_bars_1m,
            official_5m_bars=frozen_official_5m,
            daily_bars_history=frozen_daily_history,
            quote_snapshots=frozen_quote_snapshots,
            previous_close=previous_close,
            preheat_30m_bars=frozen_preheat_30m,
            official_30m_bars=frozen_official_30m,
        )

        return PreparedReplayData(
            symbol=resolved_symbol,
            market_session=session,
            trade_date=trade_date_str,
            granularity=granularity,
            preheat_5m_bars=frozen_preheat_bars,
            bars_1m=frozen_bars_1m,
            official_5m_bars=frozen_official_5m,
            daily_bars_history=frozen_daily_history,
            quote_snapshots=frozen_quote_snapshots,
            actual_bar_times=actual_bar_times,
            start_time=session.start,
            end_time=session.end,
            previous_close=previous_close,
            warnings=frozen_warnings,
            market_input_port=market_input_port,
            assessment_1m=probe_1m.assessment,
            assessment_5m=assessment_5m,
            preheat_30m_bars=frozen_preheat_30m,
            official_30m_bars=frozen_official_30m,
        )

    # ------------------------------------------------------------------
    # Loading helpers
    # ------------------------------------------------------------------

    def _check_deadline(self, config: ReplayPreparationConfig) -> None:
        if config.deadline_monotonic is None:
            return
        if self._clock() >= config.deadline_monotonic:
            raise ReplayDataTimeoutError(
                "replay data preparation exceeded its deadline"
            )

    def _remaining_timeout(self, config: ReplayPreparationConfig) -> float | None:
        self._check_deadline(config)
        if config.deadline_monotonic is None:
            return None
        remaining = config.deadline_monotonic - self._clock()
        if remaining <= 0:
            raise ReplayDataTimeoutError(
                "replay data preparation exceeded its deadline"
            )
        return remaining

    def _get_klines_result(
        self,
        *,
        code: str,
        end_date: str,
        market: str | None,
        timeframe: str,
        start_date: str | None,
        limit: int,
        instrument_type: str | None,
        config: ReplayPreparationConfig,
        session_validator: Callable[[], bool] | None,
    ) -> Any:
        try:
            result = self._market_data.get_klines_result(
                code=code,
                end_date=end_date,
                market=market,
                timeframe=timeframe,
                start_date=start_date,
                limit=limit,
                instrument_type=instrument_type,
                request_priority=config.request_priority,
                session_validator=session_validator,
                request_timeout=self._remaining_timeout(config),
            )
        except FutureTimeoutError as exc:
            raise ReplayDataTimeoutError(
                "replay data preparation exceeded its deadline"
            ) from exc
        if _has_issue_reason(result, "request_timeout"):
            raise ReplayDataTimeoutError(
                "replay data preparation exceeded its deadline"
            )
        return result

    def _load_preheat_5m(
        self,
        *,
        code: str,
        market: str,
        instrument_type: str | None,
        session: MarketSession,
        config: ReplayPreparationConfig,
        session_validator: Callable[[], bool] | None,
    ) -> tuple[Mapping[str, Any], ...]:
        """Load cross-trading-day official closed 5m bars before session start."""

        self._check_deadline(config)
        collected: list[Mapping[str, Any]] = []
        normalized: tuple[dict[str, Any], ...] = ()
        batch_end = self._market_context.previous_trading_day(
            session.trade_date,
            market,
        )
        while batch_end is not None and len(normalized) < config.preheat_5m_count:
            batch_start = batch_end
            for _ in range(14):
                previous = self._market_context.previous_trading_day(
                    batch_start,
                    market,
                )
                if previous is None:
                    break
                batch_start = previous
            result = self._get_klines_result(
                code=code,
                end_date=batch_end.isoformat(),
                market=market,
                timeframe="5m",
                start_date=batch_start.isoformat(),
                # Leave room for legacy/incomplete rows that normalization
                # rejects; the final result is still trimmed to the requested
                # number of valid bars.
                limit=config.preheat_5m_count + 15 * 48,
                instrument_type=instrument_type,
                config=config,
                session_validator=session_validator,
            )
            self._check_deadline(config)
            collected.extend(
                normalize_provider_bar_units(
                    _extract_rows(result),
                    self._market_data,
                )
            )
            # Count only valid, closed OHLCV bars; rejected cache rows must not
            # make the loop stop before 500 usable bars have been collected.
            normalized = _normalize_preheat_bars(
                collected,
                session_start=session.start,
            )
            batch_end = self._market_context.previous_trading_day(
                batch_start,
                market,
            )

        if len(normalized) <= config.preheat_5m_count:
            return normalized
        return normalized[-config.preheat_5m_count :]

    def _load_preheat_30m(
        self,
        *,
        code: str,
        market: str,
        instrument_type: str | None,
        session: MarketSession,
        config: ReplayPreparationConfig,
        session_validator: Callable[[], bool] | None,
    ) -> tuple[Mapping[str, Any], ...]:
        """Load cross-trading-day official closed 30m bars before session start.

        30m preheat always comes from the official ``30m`` provider interface,
        never aggregated from 5m bars (design §10, §14.3, §17).
        """

        self._check_deadline(config)
        collected: list[Mapping[str, Any]] = []
        normalized: tuple[dict[str, Any], ...] = ()
        batch_end = self._market_context.previous_trading_day(
            session.trade_date,
            market,
        )
        while batch_end is not None and len(normalized) < config.preheat_30m_count:
            batch_start = batch_end
            for _ in range(14):
                previous = self._market_context.previous_trading_day(
                    batch_start,
                    market,
                )
                if previous is None:
                    break
                batch_start = previous
            result = self._get_klines_result(
                code=code,
                end_date=batch_end.isoformat(),
                market=market,
                timeframe="30m",
                start_date=batch_start.isoformat(),
                limit=config.preheat_30m_count + 15 * 8,
                instrument_type=instrument_type,
                config=config,
                session_validator=session_validator,
            )
            self._check_deadline(config)
            collected.extend(
                normalize_provider_bar_units(
                    _extract_rows(result),
                    self._market_data,
                )
            )
            normalized = _normalize_preheat_bars(
                collected,
                session_start=session.start,
            )
            batch_end = self._market_context.previous_trading_day(
                batch_start,
                market,
            )

        if len(normalized) <= config.preheat_30m_count:
            return normalized
        return normalized[-config.preheat_30m_count :]

    def _assess_granularity(
        self,
        *,
        code: str,
        market: str,
        instrument_type: str | None,
        session: MarketSession,
        timeframe: str,
        config: ReplayPreparationConfig,
        session_validator: Callable[[], bool] | None,
    ) -> _GranularityProbe:
        """Assess whether one granularity can form a reliable Replay.

        Illegal price bars invalidate this granularity only; the caller may
        still degrade to another legal granularity.
        """

        self._check_deadline(config)
        trade_date_str = session.trade_date.isoformat()
        missing = self._market_data.identify_missing_ranges(
            code=code,
            start_date=trade_date_str,
            end_date=trade_date_str,
            market=market,
            timeframe=timeframe,
        )
        self._check_deadline(config)
        if not missing:
            # No missing ranges: load bars and verify they are valid and
            # within the trading session.
            result = self._get_klines_result(
                code=code,
                end_date=trade_date_str,
                market=market,
                timeframe=timeframe,
                start_date=trade_date_str,
                limit=300,
                instrument_type=instrument_type,
                config=config,
                session_validator=session_validator,
            )
            self._check_deadline(config)
            rows = normalize_provider_bar_units(
                _extract_rows(result),
                self._market_data,
            )
            try:
                normalized = _normalize_target_day_bars(
                    rows, session=session, timeframe=timeframe
                )
            except ReplayDataInvalidError as exc:
                return _GranularityProbe(
                    assessment=ReplayReliabilityAssessment(
                        granularity=_timeframe_to_granularity(timeframe),
                        is_reliable=False,
                        bar_count=0,
                    ),
                    invalid_error=exc,
                )
            is_reliable = _assess_local_intraday_reliability(
                result=result,
                market_data=self._market_data,
                code=code,
                trade_date=trade_date_str,
                market=market,
                timeframe=timeframe,
                bars=normalized,
                session=session,
            )
            return _GranularityProbe(
                assessment=ReplayReliabilityAssessment(
                    granularity=_timeframe_to_granularity(timeframe),
                    is_reliable=is_reliable,
                    bar_count=len(normalized),
                    covered_missing_ranges=(),
                    uncovered_missing_ranges=(),
                )
            )
        # There are missing ranges.  Try to backfill through the shared queue.
        # The get_klines_result call already triggers backfill internally via
        # KLineDataService; we re-assess missing ranges afterwards.
        self._get_klines_result(
            code=code,
            end_date=trade_date_str,
            market=market,
            timeframe=timeframe,
            start_date=trade_date_str,
            limit=300,
            instrument_type=instrument_type,
            config=config,
            session_validator=session_validator,
        )
        self._check_deadline(config)
        missing_after = self._market_data.identify_missing_ranges(
            code=code,
            start_date=trade_date_str,
            end_date=trade_date_str,
            market=market,
            timeframe=timeframe,
        )
        self._check_deadline(config)
        result = self._get_klines_result(
            code=code,
            end_date=trade_date_str,
            market=market,
            timeframe=timeframe,
            start_date=trade_date_str,
            limit=300,
            instrument_type=instrument_type,
            config=config,
            session_validator=session_validator,
        )
        self._check_deadline(config)
        rows = normalize_provider_bar_units(
            _extract_rows(result),
            self._market_data,
        )
        try:
            normalized = _normalize_target_day_bars(
                rows, session=session, timeframe=timeframe
            )
        except ReplayDataInvalidError as exc:
            return _GranularityProbe(
                assessment=ReplayReliabilityAssessment(
                    granularity=_timeframe_to_granularity(timeframe),
                    is_reliable=False,
                    bar_count=0,
                    covered_missing_ranges=tuple(
                        _covered_ranges(missing, missing_after)
                    ),
                    uncovered_missing_ranges=tuple(missing_after),
                ),
                invalid_error=exc,
            )
        is_reliable = not missing_after and _assess_local_intraday_reliability(
            result=result,
            market_data=self._market_data,
            code=code,
            trade_date=trade_date_str,
            market=market,
            timeframe=timeframe,
            bars=normalized,
            session=session,
        )
        return _GranularityProbe(
            assessment=ReplayReliabilityAssessment(
                granularity=_timeframe_to_granularity(timeframe),
                is_reliable=is_reliable,
                bar_count=len(normalized),
                covered_missing_ranges=tuple(_covered_ranges(missing, missing_after)),
                uncovered_missing_ranges=tuple(missing_after),
            )
        )

    def _load_target_day_bars(
        self,
        *,
        code: str,
        market: str,
        instrument_type: str | None,
        session: MarketSession,
        timeframe: str,
        config: ReplayPreparationConfig,
        session_validator: Callable[[], bool] | None,
    ) -> tuple[Mapping[str, Any], ...]:
        self._check_deadline(config)
        trade_date_str = session.trade_date.isoformat()
        result = self._get_klines_result(
            code=code,
            end_date=trade_date_str,
            market=market,
            timeframe=timeframe,
            start_date=trade_date_str,
            limit=300,
            instrument_type=instrument_type,
            config=config,
            session_validator=session_validator,
        )
        self._check_deadline(config)
        rows = normalize_provider_bar_units(
            _extract_rows(result),
            self._market_data,
        )
        return _normalize_target_day_bars(
            rows, session=session, timeframe=timeframe
        )

    def _load_daily_history(
        self,
        *,
        code: str,
        market: str,
        instrument_type: str | None,
        session: MarketSession,
        config: ReplayPreparationConfig,
        session_validator: Callable[[], bool] | None,
    ) -> tuple[Mapping[str, Any], ...]:
        self._check_deadline(config)
        trade_date_str = session.trade_date.isoformat()
        result = self._get_klines_result(
            code=code,
            end_date=trade_date_str,
            market=market,
            timeframe="day",
            start_date=None,
            limit=config.daily_history_days,
            instrument_type=instrument_type,
            config=config,
            session_validator=session_validator,
        )
        self._check_deadline(config)
        rows = normalize_provider_bar_units(
            _extract_rows(result),
            self._market_data,
        )
        return _normalize_daily_bars(rows, trade_date=session.trade_date)

    def _load_quote_snapshots(
        self,
        *,
        code: str,
        market: str,
        session: MarketSession,
        config: ReplayPreparationConfig,
        session_validator: Callable[[], bool] | None,
    ) -> tuple[Mapping[str, Any], ...]:
        # Quote snapshots are optional.  The base KLineDataService does not
        # expose a quote endpoint, so we return an empty tuple.  Real
        # implementations override the port to supply snapshots.
        return ()


# ----------------------------------------------------------------------
# Normalisation helpers
# ----------------------------------------------------------------------


def _parse_symbol(symbol: str) -> tuple[str, str, str]:
    if not isinstance(symbol, str) or len(symbol) != 9:
        raise ReplayDataError("symbol must use canonical sh.###### or sz.######")
    market = symbol[:2]
    code = symbol[3:]
    if symbol[2] != "." or market not in {"sh", "sz"} or not code.isdigit():
        raise ReplayDataError("symbol must use canonical sh.###### or sz.######")
    return symbol, code, market


def _parse_date(value: date | str) -> date:
    if isinstance(value, datetime):
        raise ReplayDataError("trade_date must not include a time")
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ReplayDataError("trade_date must use YYYY-MM-DD") from exc


def _extract_rows(result: Any) -> list[Mapping[str, Any]]:
    """Extract bar rows from a MarketDataResult or plain list."""

    if result is None:
        return []
    data = getattr(result, "data", result)
    if data is None:
        return []
    return list(data)


def _has_issue_reason(result: Any, reason_code: str) -> bool:
    issues = getattr(result, "issues", ())
    for issue in issues or ():
        if getattr(issue, "reason_code", None) == reason_code:
            return True
    return False


def _normalize_preheat_bars(
    rows: Sequence[Mapping[str, Any]],
    *,
    session_start: datetime,
) -> tuple[dict[str, Any], ...]:
    """Sort, deduplicate and validate preheat 5m bars before session start."""

    parsed: dict[datetime, dict[str, Any]] = {}
    for row in rows:
        timestamp = _row_timestamp(row)
        if timestamp >= session_start:
            # Drop any bar at or after the target session start; preheat is
            # strictly historical.
            continue
        try:
            # Missing/null amount is a valid unknown observation under the
            # OHLC-only readiness contract; do not fabricate a zero sentinel.
            bar = standardize_bar(row, closed=True)
        except (TypeError, ValueError):
            # Invalid bars are skipped, not fatal, for preheat history.
            logger.debug("skipping invalid preheat bar at %s", timestamp)
            continue
        if bar.get("closed") is not True:
            logger.debug("skipping non-closed preheat bar at %s", timestamp)
            continue
        parsed[timestamp] = bar
    return tuple(parsed[key] for key in sorted(parsed))


@dataclass(frozen=True, slots=True)
class _GranularityProbe:
    """Per-granularity reliability probe, including any illegal-bar fact."""

    assessment: ReplayReliabilityAssessment
    invalid_error: ReplayDataInvalidError | None = None


def _normalize_target_day_bars(
    rows: Sequence[Mapping[str, Any]],
    *,
    session: MarketSession,
    timeframe: str = "target_day",
) -> tuple[dict[str, Any], ...]:
    """Sort, deduplicate and validate target-day bars inside the session.

    Bars outside the trading session are dropped.  Invalid bars raise so this
    granularity is marked unusable rather than silently discarding illegal
    price facts; the preparator may still degrade to another legal granularity.
    """

    parsed: dict[datetime, dict[str, Any]] = {}
    invalid: list[tuple[str, str, str]] = []
    for row in rows:
        timestamp, timestamp_error = _try_parse_row_timestamp(row)
        if timestamp_error is not None:
            raw = row.get("timestamp", row.get("date"))
            stamp = raw if isinstance(raw, str) else ""
            invalid.append((stamp, "timestamp", str(timestamp_error)))
            continue
        if timestamp.date() != session.trade_date:
            continue
        if not session.is_trading_time(timestamp):
            continue
        try:
            bar = standardize_bar(row, closed=True)
        except (TypeError, ValueError) as exc:
            invalid.append(
                (
                    timestamp.strftime(MARKET_TIMESTAMP_FORMAT),
                    _invalid_price_field(exc),
                    str(exc),
                )
            )
            continue
        if bar.get("closed") is not True:
            invalid.append(
                (
                    timestamp.strftime(MARKET_TIMESTAMP_FORMAT),
                    "closed",
                    "expected a closed bar",
                )
            )
            continue
        parsed[timestamp] = bar
    if invalid:
        stamp, affected_field, reason = invalid[0]
        location = stamp or "unknown"
        raise ReplayDataInvalidError(
            f"invalid bar at {location}: {reason}",
            details={
                "timeframe": timeframe,
                "affected_field": affected_field,
                "invalid_count": len(invalid),
                "timestamp": stamp,
            },
        )
    return tuple(parsed[key] for key in sorted(parsed))


def _invalid_price_field(exc: BaseException) -> str:
    """Return the first machine-readable price field named by a schema error."""

    message = str(exc)
    if message.startswith("bar high"):
        return "high"
    if message.startswith("bar low"):
        return "low"
    for field in ("open", "high", "low", "close", "closed", "timestamp"):
        if message == f"{field} is required" or message.startswith(f"{field} "):
            return field
    if "closed" in message:
        return "closed"
    if "timestamp" in message:
        return "timestamp"
    return "ohlc"


def _normalize_daily_bars(
    rows: Sequence[Mapping[str, Any]],
    *,
    trade_date: date,
) -> tuple[dict[str, Any], ...]:
    """Sort, deduplicate and validate closed daily bars before trade_date."""

    parsed: dict[str, dict[str, Any]] = {}
    for row in rows:
        timestamp_value = row.get("timestamp", row.get("date"))
        if not timestamp_value:
            continue
        try:
            bar_date = date.fromisoformat(str(timestamp_value)[:10])
        except ValueError:
            continue
        if bar_date >= trade_date:
            continue
        try:
            bar = standardize_bar(row, closed=True)
        except (TypeError, ValueError):
            continue
        if bar.get("closed") is not True:
            continue
        parsed[bar_date.isoformat()] = bar
    return tuple(parsed[key] for key in sorted(parsed))


def _try_parse_row_timestamp(
    row: Mapping[str, Any],
) -> tuple[datetime | None, Exception | None]:
    """Parse a bar timestamp without aborting the rest of the granularity."""

    try:
        return _row_timestamp(row), None
    except (ReplayDataError, TypeError, ValueError) as exc:
        return None, exc


def _row_timestamp(row: Mapping[str, Any]) -> datetime:
    value = row.get("timestamp", row.get("date"))
    if not isinstance(value, str):
        raise ReplayDataError("bar row missing timestamp/date string")
    return datetime.strptime(value, MARKET_TIMESTAMP_FORMAT)


def _all_within_session(
    bars: Sequence[Mapping[str, Any]], session: MarketSession
) -> bool:
    for bar in bars:
        timestamp = datetime.strptime(bar["timestamp"], MARKET_TIMESTAMP_FORMAT)
        if timestamp.date() != session.trade_date:
            return False
        if not session.is_trading_time(timestamp):
            return False
    return True


def _assess_local_intraday_reliability(
    *,
    result: Any,
    market_data: ReplayMarketDataPort,
    code: str,
    trade_date: str,
    market: str,
    timeframe: str,
    bars: Sequence[Mapping[str, Any]],
    session: MarketSession,
) -> bool:
    """Return whether intraday bars are reliable enough for Replay.

    Runtime should prefer explicit completeness evidence from ``marketdata``
    when available, because legal intraday halts can yield fewer than the
    nominal 240/48 bars without being data corruption.  Until the provider
    boundary exposes richer evidence everywhere, fall back to basic structural
    validation only.
    """

    if not bars or not _all_within_session(bars, session):
        return False
    if _issues_imply_unreliable_intraday(result):
        return False
    evidence_getter = getattr(market_data, "replay_reliability_evidence", None)
    if callable(evidence_getter):
        evidence = evidence_getter(
            code=code,
            trade_date=trade_date,
            market=market,
            timeframe=timeframe,
        )
        return bool(evidence)
    return False


def _issues_imply_unreliable_intraday(result: Any) -> bool:
    issues = getattr(result, "issues", ())
    if not issues:
        return False
    unreliable_warning_codes = {
        "parse_failed",
        "request_timeout",
        "session_retired",
    }
    for issue in issues:
        if getattr(issue, "level", None) == "error":
            return True
        if getattr(issue, "reason_code", None) in unreliable_warning_codes:
            return True
    return False


def _covered_ranges(
    before: Sequence[tuple[str, str]],
    after: Sequence[tuple[str, str]],
) -> list[tuple[str, str]]:
    after_set = set(after)
    return [r for r in before if r not in after_set]


def _freeze_rows(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    return tuple(_deep_freeze(row) for row in rows)


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _deep_freeze(inner) for key, inner in value.items()}
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _timeframe_to_granularity(timeframe: str) -> str:
    if timeframe == "1m":
        return "one_minute"
    if timeframe == "5m":
        return "five_minute"
    raise ReplayDataError(f"unsupported timeframe: {timeframe}")


def _build_actual_bar_times(
    *,
    granularity: str,
    bars_1m: Sequence[Mapping[str, Any]],
    official_5m: Sequence[Mapping[str, Any]],
    session: MarketSession,
) -> tuple[datetime, ...]:
    """Return the strictly-increasing actual bar close times for the cursor."""

    if granularity == "one_minute":
        source = bars_1m
    elif granularity == "five_minute":
        source = official_5m
    else:
        raise ReplayDataError(f"unsupported granularity: {granularity}")
    seen: set[datetime] = set()
    times: list[datetime] = []
    for bar in source:
        timestamp = datetime.strptime(bar["timestamp"], MARKET_TIMESTAMP_FORMAT)
        if timestamp in seen:
            continue
        if timestamp.date() != session.trade_date:
            continue
        if not session.is_trading_time(timestamp):
            continue
        seen.add(timestamp)
        times.append(timestamp)
    times.sort()
    return tuple(times)


def _derive_previous_close(
    daily_history: Sequence[Mapping[str, Any]],
    preheat_bars: Sequence[Mapping[str, Any]],
) -> float | int | None:
    """Derive the previous close from daily history or preheat bars."""

    if daily_history:
        last = daily_history[-1]
        close = last.get("close")
        if isinstance(close, (int, float)) and not isinstance(close, bool):
            return close
    if preheat_bars:
        close = preheat_bars[-1].get("close")
        if isinstance(close, (int, float)) and not isinstance(close, bool):
            return close
    return None


# ----------------------------------------------------------------------
# In-memory MarketInputPort
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _InMemoryMarketInputPort:
    """Pure-memory :class:`MarketInputPort` backed by prepared data.

    ``read(target_time)`` returns a :class:`PipelineMarketInput` whose
    sequences are the prefix of the prepared data at or before ``target_time``.
    It never performs I/O and never exposes future bars, quotes, indicators or
    CZSC output.  Repeated calls with the same ``target_time`` return equal
    prefixes because the prefix is recomputed deterministically from the same
    frozen source tuples.
    """

    symbol: str
    trade_date: str
    session: MarketSession
    preheat_5m_bars: tuple[Mapping[str, Any], ...]
    bars_1m: tuple[Mapping[str, Any], ...]
    official_5m_bars: tuple[Mapping[str, Any], ...]
    daily_bars_history: tuple[Mapping[str, Any], ...]
    quote_snapshots: tuple[Mapping[str, Any], ...]
    previous_close: float | int | None
    preheat_30m_bars: tuple[Mapping[str, Any], ...] = ()
    official_30m_bars: tuple[Mapping[str, Any], ...] = ()

    def read(self, target_time: datetime) -> Any:
        """Return the :class:`PipelineMarketInput` prefix at ``target_time``.

        The import is local so the contract module stays import-light.
        """

        from .pipeline import PipelineMarketInput

        if not isinstance(target_time, datetime):
            raise TypeError("target_time must be a naive datetime")
        if target_time.tzinfo is not None:
            raise RuntimeMarketDataError(
                "target_time must be a naive Asia/Shanghai market timestamp"
            )
        if target_time.date() != self.session.trade_date:
            raise RuntimeMarketDataError(
                "target_time must belong to the session trade_date"
            )

        return PipelineMarketInput(
            symbol=self.symbol,
            trade_date=self.trade_date,
            previous_close=self.previous_close,
            preheat_5m_bars=tuple(self.preheat_5m_bars),
            bars_1m=_prefix_at(self.bars_1m, target_time),
            official_5m_bars=_prefix_at(self.official_5m_bars, target_time),
            quote_snapshots=_prefix_at(self.quote_snapshots, target_time),
            daily_bars_history=tuple(self.daily_bars_history),
            preheat_30m_bars=tuple(self.preheat_30m_bars),
            official_30m_bars=_prefix_at(self.official_30m_bars, target_time),
        )


def _prefix_at(
    bars: Sequence[Mapping[str, Any]],
    target_time: datetime,
) -> tuple[Mapping[str, Any], ...]:
    """Return the prefix of ``bars`` whose timestamp is at or before ``target_time``.

    Only the timestamp field is inspected for rows that fall after the
    target, so future OHLCVA values are never read into memory.
    """

    prefix: list[Mapping[str, Any]] = []
    for bar in bars:
        timestamp_value = bar.get("timestamp", bar.get("date"))
        if not isinstance(timestamp_value, str):
            continue
        try:
            timestamp = datetime.strptime(timestamp_value, MARKET_TIMESTAMP_FORMAT)
        except ValueError:
            continue
        if timestamp <= target_time:
            prefix.append(bar)
    return tuple(prefix)


__all__ = [
    "ReplayDataError",
    "ReplayDataInvalidError",
    "ReplayDataPreparator",
    "ReplayDataTimeoutError",
    "ReplayDataUnavailableError",
    "ReplayMarketDataPort",
    "ReplayPreparationConfig",
]
