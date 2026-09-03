"""Shared Live/Replay Workbench Pipeline.

The pipeline is driven by two injected ports:

* :class:`ClockPort` provides the current target moment.
* :class:`MarketInputPort` provides the standard market prefix at that moment.

Live and Replay use the same :class:`WorkbenchPipeline` implementation and the
same computation order.  The only differences are the concrete port instances
and the pipeline instance identity.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

from packages.chantheory import (
    ENGINE_NAME,
    PINNED_ENGINE_VERSION,
    AnalysisResult,
    analyze,
    get_default_parameters,
)
from packages.indicators import (
    calculate_five_minute_indicators,
    calculate_one_minute_indicators,
    calculate_thirty_minute_indicators,
)
from packages.marketdata.services.market_context_service import MarketSession

from ._market_bars import (
    MARKET_TIMESTAMP_FORMAT,
    RuntimeMarketDataError,
    eligible_closed_bars,
    parse_market_timestamp,
    parse_trade_date,
    standardize_bar,
    validated_bar,
)
from .five_minute import DynamicFiveMinuteAggregator
from .projection import project_market_at
from .thirty_minute import DynamicThirtyMinuteAggregator


class WorkbenchPipelineError(RuntimeMarketDataError):
    """Raised when the pipeline cannot produce a deterministic result."""


class ClockPort(Protocol):
    """Supplies the current target business moment."""

    def now(self) -> datetime:
        """Return the current target time in naive Asia/Shanghai local time."""


class MarketInputPort(Protocol):
    """Supplies the standard market input prefix for one target time."""

    def read(self, target_time: datetime) -> PipelineMarketInput:
        """Return the market input available at ``target_time``."""


class CzscAnalyzerPort(Protocol):
    """Injectable seam for closed 5m CZSC analysis."""

    def __call__(
        self,
        bars: Sequence[Mapping[str, Any]],
        symbol: str,
    ) -> dict[str, Any]:
        """Analyze the closed 5m prefix and return a serializable result."""


@dataclass(frozen=True, slots=True)
class PipelineMarketInput:
    """Deterministic market input boundary for one pipeline step."""

    symbol: str
    trade_date: date | str
    previous_close: float | int | None = None
    preheat_5m_bars: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    bars_1m: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    official_5m_bars: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    quote_snapshots: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    daily_bars_history: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    preheat_30m_bars: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    official_30m_bars: Sequence[Mapping[str, Any]] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Internal, strongly-typed result of one pipeline step."""

    target_time: datetime
    symbol: str
    trade_date: date
    bars_1m: tuple[dict[str, Any], ...]
    bars_5m: tuple[dict[str, Any], ...]
    closed_5m_prefix: tuple[dict[str, Any], ...]
    daily_bars: tuple[dict[str, Any], ...]
    daily_bar: dict[str, Any] | None
    quote: dict[str, Any] | None
    indicators_1m: dict[str, Any]
    indicators_5m: dict[str, Any]
    chan_analysis: dict[str, Any]
    warnings: list[dict[str, Any]]
    bars_30m: tuple[dict[str, Any], ...] = ()
    closed_30m_prefix: tuple[dict[str, Any], ...] = ()
    indicators_30m: dict[str, Any] = field(default_factory=dict)
    chan_analysis_30m: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_time": self.target_time.strftime(MARKET_TIMESTAMP_FORMAT),
            "symbol": self.symbol,
            "trade_date": (
                self.trade_date.isoformat()
                if isinstance(self.trade_date, date)
                else self.trade_date
            ),
            "bars_1m": [dict(bar) for bar in self.bars_1m],
            "bars_5m": [dict(bar) for bar in self.bars_5m],
            "closed_5m_prefix": [dict(bar) for bar in self.closed_5m_prefix],
            "daily_bars": [dict(bar) for bar in self.daily_bars],
            "daily_bar": None if self.daily_bar is None else dict(self.daily_bar),
            "quote": None if self.quote is None else dict(self.quote),
            "indicators_1m": dict(self.indicators_1m),
            "indicators_5m": dict(self.indicators_5m),
            "chan_analysis": dict(self.chan_analysis),
            "warnings": list(self.warnings),
            "bars_30m": [dict(bar) for bar in self.bars_30m],
            "closed_30m_prefix": [dict(bar) for bar in self.closed_30m_prefix],
            "indicators_30m": dict(self.indicators_30m),
            "chan_analysis_30m": dict(self.chan_analysis_30m),
        }

    @classmethod
    def degraded(
        cls,
        *,
        session: MarketSession,
        symbol: str,
        target_time: datetime,
    ) -> PipelineResult:
        """Create a degraded result with empty data and a warning.

        Used when the pipeline projection fails after a calendar-driven day
        switch (#133).  The trade date and symbol are set correctly so the
        workbench can display the new trading day; all market data and
        indicators are empty.  Chan analysis is produced from an empty bar
        prefix so the payload still satisfies the frozen schema contract.  A
        structured warning explains the degradation so the renderer and
        downstream consumers can surface it.
        """

        try:
            chan_analysis = _default_analyze_5m((), symbol)
        except Exception:
            chan_analysis = _empty_chan_analysis(symbol)
        try:
            chan_analysis_30m = _default_analyze_30m((), symbol)
        except Exception:
            chan_analysis_30m = _empty_chan_analysis_30m(symbol)
        return cls(
            target_time=target_time,
            symbol=symbol,
            trade_date=session.trade_date,
            bars_1m=(),
            bars_5m=(),
            closed_5m_prefix=(),
            daily_bars=(),
            daily_bar=None,
            quote=None,
            indicators_1m=_empty_1m_indicators(),
            indicators_5m=_empty_5m_indicators(),
            chan_analysis=chan_analysis,
            warnings=[
                {
                    "warning_code": "degraded_projection",
                    "severity": "warning",
                    "message": (
                        "Pipeline projection failed after day switch; "
                        "showing empty market data for the new trading day."
                    ),
                    "affected_capability": "intraday_chart",
                    "affected_field": "market",
                    "details": {},
                }
            ],
            bars_30m=(),
            closed_30m_prefix=(),
            indicators_30m=_empty_30m_indicators(),
            chan_analysis_30m=chan_analysis_30m,
        )


def _default_analyze_5m(
    bars: Sequence[Mapping[str, Any]],
    symbol: str,
) -> dict[str, Any]:
    """Project-owned full rebuild over a closed 5m prefix."""

    result = analyze(
        rows=bars,
        symbol=symbol,
        timeframe="5m",
        source="tencent",
    )
    return result.to_dict()


def _default_analyze_30m(
    bars: Sequence[Mapping[str, Any]],
    symbol: str,
) -> dict[str, Any]:
    """Project-owned full rebuild over a closed 30m prefix."""

    result = analyze(
        rows=bars,
        symbol=symbol,
        timeframe="30m",
        source="tencent",
    )
    return result.to_dict()


def _empty_chan_analysis(symbol: str) -> dict[str, Any]:
    """Schema-valid empty chan analysis when the analyzer cannot run.

    All array fields default to empty lists and ``meta`` to an empty dict,
    satisfying the frozen ``chan_analysis`` contract without relying on the
    CZSC engine.
    """

    result = AnalysisResult(
        symbol=symbol,
        timeframe="5m",
        source="tencent",
        engine=ENGINE_NAME,
        engine_version=PINNED_ENGINE_VERSION,
        parameters=get_default_parameters(),
    )
    return result.to_dict()


def _empty_chan_analysis_30m(symbol: str) -> dict[str, Any]:
    """Schema-valid empty 30m chan analysis when the analyzer cannot run."""

    result = AnalysisResult(
        symbol=symbol,
        timeframe="30m",
        source="tencent",
        engine=ENGINE_NAME,
        engine_version=PINNED_ENGINE_VERSION,
        parameters=get_default_parameters(),
    )
    return result.to_dict()


class WorkbenchPipeline:
    """Live/Replay shared computation pipeline.

    The pipeline owns no provider, storage, or wall-clock references.  It
    receives target time from a :class:`ClockPort`, reads the matching market
    prefix through a :class:`MarketInputPort`, and produces a deterministic
    :class:`PipelineResult` for that exact prefix.

    Mutable state (dynamic 5m aggregation, current result) belongs to one
    instance only.  Two instances with the same configuration, target time, and
    input prefix produce the same result.
    """

    def __init__(
        self,
        session: MarketSession,
        market_input_port: MarketInputPort,
        clock_port: ClockPort | None = None,
        analyzer: CzscAnalyzerPort | None = None,
    ) -> None:
        if not isinstance(session, MarketSession):
            raise TypeError("session must be a MarketSession")
        self._session = session
        self._market_input_port = market_input_port
        self._clock_port = clock_port
        self._analyzer: CzscAnalyzerPort = analyzer or _default_analyze_5m
        self._target_time: datetime | None = None
        self._last_result: PipelineResult | None = None

    @property
    def session(self) -> MarketSession:
        return self._session

    @property
    def target_time(self) -> datetime | None:
        return self._target_time

    @property
    def last_result(self) -> PipelineResult | None:
        return self._last_result

    def reset(self) -> None:
        """Drop all mutable derived state without releasing the ports."""

        self._target_time = None
        self._last_result = None

    def step(self) -> PipelineResult:
        """Advance using the injected clock."""

        if self._clock_port is None:
            raise WorkbenchPipelineError("step requires a ClockPort")
        # Pass None so the clock read happens inside compute, where all runtime
        # failures are wrapped into WorkbenchPipelineError.
        return self.compute()

    def preview(self, target_time: datetime | str | None = None) -> PipelineResult:
        """Compute the deterministic result for ``target_time`` without mutating state.

        When ``target_time`` is omitted, the injected clock is used.  The
        pipeline reconstructs its internal aggregator from the supplied input
        prefix so that backward Replay seeks and forward Live advances share
        exactly the same deterministic path.

        The returned :class:`PipelineResult` is an isolated preview.  Call
        :meth:`commit_preview` to publish it into ``_target_time`` and
        ``_last_result``.  This is what lets the executor reject a late or
        superseded result without first polluting pipeline state.
        """

        try:
            resolved_target = self._resolve_target_time(target_time)
            result = self._compute_unlocked(resolved_target)
        except WorkbenchPipelineError:
            raise
        except Exception as exc:
            raise WorkbenchPipelineError(
                f"pipeline computation failed: {exc}"
            ) from exc

        return result

    def commit_preview(self, result: PipelineResult) -> None:
        """Publish a previously computed preview into pipeline state."""

        if not isinstance(result, PipelineResult):
            raise TypeError("result must be a PipelineResult")
        self._target_time = result.target_time
        self._last_result = result

    def compute(self, target_time: datetime | str | None = None) -> PipelineResult:
        """Compute the deterministic result for ``target_time`` and commit it."""

        result = self.preview(target_time)
        self.commit_preview(result)
        return result

    def _compute_unlocked(self, resolved_target: datetime) -> PipelineResult:
        market_input = self._market_input_port.read(resolved_target)
        trade_date = parse_trade_date(market_input.trade_date)
        if trade_date != self._session.trade_date:
            raise WorkbenchPipelineError(
                "market input trade_date does not match pipeline session"
            )

        bars_1m = _target_day_closed_bars(
            market_input.bars_1m,
            trade_date=trade_date,
            target_time=resolved_target,
        )
        official_5m = _target_day_closed_bars(
            market_input.official_5m_bars,
            trade_date=trade_date,
            target_time=resolved_target,
        )
        preheat_5m = _preheat_closed_bars(
            market_input.preheat_5m_bars,
            session_start=self._session.start,
        )
        official_30m = _target_day_closed_bars(
            market_input.official_30m_bars,
            trade_date=trade_date,
            target_time=resolved_target,
        )
        preheat_30m = _preheat_closed_bars(
            market_input.preheat_30m_bars,
            session_start=self._session.start,
        )

        aggregator = DynamicFiveMinuteAggregator(self._session)
        for bar in bars_1m:
            aggregator.update_one_minute(bar)
        for bar in official_5m:
            aggregator.accept_official(bar)

        display_5m = list(aggregator.display_bars)
        closed_5m = _merge_sorted_bars(preheat_5m, aggregator.analysis_bars)
        bars_5m = _merge_sorted_bars(preheat_5m, display_5m)

        aggregator_30m = DynamicThirtyMinuteAggregator(self._session)
        for bar in bars_1m:
            aggregator_30m.update_one_minute(bar)
        for bar in official_30m:
            aggregator_30m.accept_official(bar)

        display_30m = list(aggregator_30m.display_bars)
        closed_30m = _merge_sorted_bars(preheat_30m, aggregator_30m.analysis_bars)
        bars_30m = _merge_sorted_bars(preheat_30m, display_30m)

        projection = project_market_at(
            bars_1m,
            trade_date=trade_date,
            target_time=resolved_target,
            previous_close=market_input.previous_close,
            quote_snapshots=market_input.quote_snapshots,
            official_5m_bars=official_5m,
        )

        daily_bars = _build_daily_bars(
            market_input.daily_bars_history,
            projection.daily_bar,
            trade_date=trade_date,
        )

        indicators_1m = (
            calculate_one_minute_indicators(bars_1m) if bars_1m else _empty_1m_indicators()
        )
        indicators_5m = (
            calculate_five_minute_indicators(closed_5m)
            if closed_5m
            else _empty_5m_indicators()
        )
        indicators_30m = (
            calculate_thirty_minute_indicators(closed_30m)
            if closed_30m
            else _empty_30m_indicators()
        )

        chan_analysis = self._analyzer(closed_5m, market_input.symbol)
        if not isinstance(chan_analysis, Mapping):
            raise WorkbenchPipelineError(
                "analyzer must satisfy the CzscAnalyzerPort contract and return "
                "a dict chan_analysis payload (e.g. AnalysisResult.to_dict()), "
                f"got {type(chan_analysis).__name__}"
            )

        chan_analysis_30m = _default_analyze_30m(closed_30m, market_input.symbol)

        return PipelineResult(
            target_time=resolved_target,
            symbol=market_input.symbol,
            trade_date=trade_date,
            bars_1m=tuple(dict(bar) for bar in bars_1m),
            bars_5m=tuple(bars_5m),
            closed_5m_prefix=tuple(closed_5m),
            daily_bars=tuple(daily_bars),
            daily_bar=projection.daily_bar,
            quote=projection.quote,
            indicators_1m=indicators_1m,
            indicators_5m=indicators_5m,
            chan_analysis=chan_analysis,
            warnings=[],
            bars_30m=tuple(bars_30m),
            closed_30m_prefix=tuple(closed_30m),
            indicators_30m=indicators_30m,
            chan_analysis_30m=chan_analysis_30m,
        )

    def _resolve_target_time(
        self,
        target_time: datetime | str | None,
    ) -> datetime:
        if target_time is None:
            if self._clock_port is None:
                raise WorkbenchPipelineError(
                    "target_time requires a ClockPort when omitted"
                )
            target_time = self._clock_port.now()
        if isinstance(target_time, datetime):
            if target_time.tzinfo is not None:
                raise WorkbenchPipelineError(
                    "target_time must be a naive Asia/Shanghai market timestamp"
                )
            resolved = target_time
        else:
            resolved = parse_market_timestamp(target_time, field="target_time")
        if resolved.date() != self._session.trade_date:
            raise WorkbenchPipelineError("target_time must belong to the session trade_date")
        return resolved


def _target_day_closed_bars(
    rows: Sequence[Mapping[str, Any]],
    *,
    trade_date: date,
    target_time: datetime,
) -> list[dict[str, Any]]:
    """Return validated, sorted, deduplicated target-day bars at or before target_time."""

    return [bar for _, bar in eligible_closed_bars(rows, trade_date=trade_date, target_time=target_time)]


def _preheat_closed_bars(
    rows: Sequence[Mapping[str, Any]],
    *,
    session_start: datetime,
) -> list[dict[str, Any]]:
    """Validate preheat 5m bars: closed and strictly before the session start.

    The timestamp is checked before any other field is read, so a future
    preheat row with poison OHLCVA fields cannot leak future market data.
    """

    parsed: dict[datetime, dict[str, Any]] = {}
    for row in rows:
        timestamp = _peek_timestamp(row)
        if timestamp >= session_start:
            raise WorkbenchPipelineError(
                "preheat 5m bar timestamp must be before the target session start"
            )
        _, bar = validated_bar(row, closed=True)
        parsed[timestamp] = bar
    return [bar for _, bar in sorted(parsed.items())]


def _peek_timestamp(row: Mapping[str, Any]) -> datetime:
    """Read only the timestamp/date field from a bar row."""

    timestamp = row.get("timestamp", row.get("date"))
    if not timestamp:
        raise WorkbenchPipelineError("bar row missing timestamp/date")
    return parse_market_timestamp(timestamp, field="timestamp")


def _merge_sorted_bars(
    left: Sequence[Mapping[str, Any]],
    right: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Merge two sorted bar sequences, with ``right`` overriding ``left`` on timestamp."""

    merged: dict[str, dict[str, Any]] = {}
    for bar in left:
        merged[bar["timestamp"]] = dict(bar)
    for bar in right:
        merged[bar["timestamp"]] = dict(bar)
    return [merged[key] for key in sorted(merged)]


def _build_daily_bars(
    history: Sequence[Mapping[str, Any]],
    dynamic_daily_bar: Mapping[str, Any] | None,
    *,
    trade_date: date,
) -> list[dict[str, Any]]:
    """Merge historical daily bars with the current dynamic daily bar."""

    merged = _daily_bars_history(history, trade_date=trade_date)
    if dynamic_daily_bar is not None:
        merged[dynamic_daily_bar["timestamp"]] = dict(dynamic_daily_bar)
    return [merged[key] for key in sorted(merged)]


def _daily_bars_history(
    rows: Sequence[Mapping[str, Any]],
    *,
    trade_date: date,
) -> dict[str, dict[str, Any]]:
    """Validate and deduplicate closed historical daily bars by date.

    Historical daily bars must be strictly before the current trade date so
    that the current (dynamic) daily bar is the only source for the target day.
    """

    parsed: dict[str, dict[str, Any]] = {}
    for row in rows:
        timestamp = row.get("timestamp", row.get("date"))
        if not timestamp:
            raise WorkbenchPipelineError("daily bar missing timestamp/date")
        bar_date = parse_trade_date(timestamp)
        if bar_date >= trade_date:
            raise WorkbenchPipelineError(
                "daily_bars_history must contain only dates before the trade_date"
            )
        bar = standardize_bar(row)
        if bar["closed"] is not True:
            raise WorkbenchPipelineError("expected a closed daily bar")
        parsed[bar_date.isoformat()] = bar
    return parsed


def _empty_1m_indicators() -> dict[str, Any]:
    return {
        "vwap": [],
        "volume": {"values": []},
        "macd": {
            "fast_period": 12,
            "slow_period": 26,
            "signal_period": 9,
            "dif": [],
            "dea": [],
            "histogram": [],
        },
    }


def _empty_5m_indicators() -> dict[str, Any]:
    return {
        "ma": {f"ma{period}": [] for period in (5, 10, 20, 30, 60)},
        "boll": {
            "period": 20,
            "stddev": 2.0,
            "upper": [],
            "middle": [],
            "lower": [],
        },
        "volume": {"values": [], "ma5": [], "ma10": []},
        "macd": {
            "fast_period": 12,
            "slow_period": 26,
            "signal_period": 9,
            "dif": [],
            "dea": [],
            "histogram": [],
        },
    }


def _empty_30m_indicators() -> dict[str, Any]:
    return {
        "ma": {f"ma{period}": [] for period in (5, 10, 20, 30, 60)},
        "boll": {
            "period": 20,
            "stddev": 2.0,
            "upper": [],
            "middle": [],
            "lower": [],
        },
        "volume": {"values": [], "ma5": [], "ma10": []},
        "macd": {
            "fast_period": 12,
            "slow_period": 26,
            "signal_period": 9,
            "dif": [],
            "dea": [],
            "histogram": [],
        },
    }
