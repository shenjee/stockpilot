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

from packages.chantheory import analyze_tracker_klines
from packages.indicators import (
    calculate_five_minute_indicators,
    calculate_one_minute_indicators,
)
from packages.marketdata.services.market_context_service import MarketSession

from ._market_bars import (
    MARKET_TIMESTAMP_FORMAT,
    RuntimeMarketDataError,
    eligible_closed_bars,
    parse_market_timestamp,
    parse_trade_date,
    validated_bar,
)
from .five_minute import DynamicFiveMinuteAggregator
from .projection import project_market_at


class WorkbenchPipelineError(RuntimeMarketDataError):
    """Raised when the pipeline cannot produce a deterministic result."""


class ClockPort(Protocol):
    """Supplies the target business moment."""

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


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Internal, strongly-typed result of one pipeline step."""

    target_time: datetime
    symbol: str
    trade_date: date
    bars_1m: tuple[dict[str, Any], ...]
    bars_5m: tuple[dict[str, Any], ...]
    closed_5m_prefix: tuple[dict[str, Any], ...]
    daily_bar: dict[str, Any] | None
    quote: dict[str, Any] | None
    indicators_1m: dict[str, Any]
    indicators_5m: dict[str, Any]
    chan_analysis: dict[str, Any]
    warnings: list[dict[str, Any]]

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
            "daily_bar": None if self.daily_bar is None else dict(self.daily_bar),
            "quote": None if self.quote is None else dict(self.quote),
            "indicators_1m": dict(self.indicators_1m),
            "indicators_5m": dict(self.indicators_5m),
            "chan_analysis": dict(self.chan_analysis),
            "warnings": list(self.warnings),
        }


def _default_analyze_5m(
    bars: Sequence[Mapping[str, Any]],
    symbol: str,
) -> dict[str, Any]:
    """Project-owned full rebuild over a closed 5m prefix."""

    market, code = _parse_canonical_symbol(symbol)
    result = analyze_tracker_klines(
        rows=bars,
        code=code,
        market=market,
        timeframe="5m",
        source="tencent",
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
        return self.compute(self._clock_port.now())

    def compute(self, target_time: datetime | str | None = None) -> PipelineResult:
        """Compute the deterministic result for ``target_time``.

        When ``target_time`` is omitted, the injected clock is used.  The
        pipeline reconstructs its internal aggregator from the supplied input
        prefix so that backward Replay seeks and forward Live advances share
        exactly the same deterministic path.
        """

        resolved_target = self._resolve_target_time(target_time)
        self._target_time = resolved_target

        try:
            result = self._compute_unlocked(resolved_target)
        except RuntimeMarketDataError as exc:
            # Preserve already-pipeline errors; surface adapter/validation
            # failures through the pipeline's stable exception type.
            if isinstance(exc, WorkbenchPipelineError):
                raise
            raise WorkbenchPipelineError(str(exc)) from exc
        self._last_result = result
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
        preheat_5m = _closed_bars(market_input.preheat_5m_bars)

        aggregator = DynamicFiveMinuteAggregator(self._session)
        for bar in bars_1m:
            aggregator.update_one_minute(bar)
        for bar in official_5m:
            aggregator.accept_official(bar)

        display_5m = list(aggregator.display_bars)
        closed_5m = list(preheat_5m) + list(aggregator.analysis_bars)
        bars_5m = _merge_sorted_bars(preheat_5m, display_5m)

        projection = project_market_at(
            bars_1m,
            trade_date=trade_date,
            target_time=resolved_target,
            previous_close=market_input.previous_close,
            quote_snapshots=market_input.quote_snapshots,
        )

        indicators_1m = (
            calculate_one_minute_indicators(bars_1m) if bars_1m else _empty_1m_indicators()
        )
        indicators_5m = (
            calculate_five_minute_indicators(closed_5m)
            if closed_5m
            else _empty_5m_indicators()
        )

        chan_analysis = self._analyzer(closed_5m, market_input.symbol)

        return PipelineResult(
            target_time=resolved_target,
            symbol=market_input.symbol,
            trade_date=trade_date,
            bars_1m=tuple(dict(bar) for bar in bars_1m),
            bars_5m=tuple(bars_5m),
            closed_5m_prefix=tuple(dict(bar) for bar in closed_5m),
            daily_bar=projection.daily_bar,
            quote=projection.quote,
            indicators_1m=indicators_1m,
            indicators_5m=indicators_5m,
            chan_analysis=chan_analysis,
            warnings=[],
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


def _closed_bars(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Validate and deduplicate a closed-bar sequence by timestamp."""

    parsed: dict[datetime, dict[str, Any]] = {}
    for row in rows:
        timestamp, bar = validated_bar(row, closed=True)
        parsed[timestamp] = bar
    return [bar for _, bar in sorted(parsed.items())]


def _merge_sorted_bars(
    left: Sequence[Mapping[str, Any]],
    right: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Merge two sorted bar sequences, dropping duplicates by timestamp."""

    merged: dict[str, dict[str, Any]] = {}
    for bar in left:
        merged[bar["timestamp"]] = dict(bar)
    for bar in right:
        merged[bar["timestamp"]] = dict(bar)
    return [merged[key] for key in sorted(merged)]


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


def _parse_canonical_symbol(symbol: str) -> tuple[str, str]:
    """Split ``sh.600000`` into ``(market, code)``."""

    if not isinstance(symbol, str) or "." not in symbol:
        raise WorkbenchPipelineError("symbol must be canonical sh.###### or sz.######")
    market, code = symbol.lower().split(".", 1)
    if market not in {"sh", "sz"} or len(code) != 6 or not code.isdigit():
        raise WorkbenchPipelineError("symbol must be canonical sh.###### or sz.######")
    return market, code
