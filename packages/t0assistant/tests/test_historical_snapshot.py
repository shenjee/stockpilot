"""Regression tests for the historical snapshot analyzer wiring.

The historical path must route ``analyzer=None`` through the pipeline's
closed-5m default analyzer.  A previous version defaulted to the raw
``packages.chantheory.analyze`` function, which returns an ``AnalysisResult``
(not a dict), so the projection either failed validation or produced a
snapshot without Chan overlays.  These tests pin the wiring at the
``build_historical_snapshot`` boundary without providers, SQLite, or the czsc
engine.
"""

from __future__ import annotations

import unittest
from collections.abc import Mapping, Sequence
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from packages.marketdata.services.market_context_service import MarketContextService
from packages.t0assistant.runtime import PipelineMarketInput
from packages.t0assistant.runtime.historical_snapshot import (
    HistoricalSnapshotError,
    build_historical_snapshot,
)

_TRADE_DATE = "2026-07-24"
_MARKET = "sh"
_SYMBOL = "sh.600000"


def _bar(
    timestamp: str,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    amount: float,
    *,
    closed: bool = True,
) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": amount,
        "closed": closed,
    }


def _full_chan_analysis(symbol: str) -> dict[str, Any]:
    """Contract-complete chan_analysis payload for projection validation."""
    return {
        "symbol": symbol,
        "timeframe": "5m",
        "source": "fixture",
        "engine": "czsc",
        "engine_version": "0.10.12",
        "parameters": {},
        "fractals": [],
        "strokes": [],
        "segments": [],
        "pivot_zones": [],
        "divergences": [],
        "structure_alerts": [],
        "signal_series": [],
        "signal_events": [],
        "signal_snapshots": [],
        "candidate_point_events": [],
        "candidate_buy_points": [],
        "candidate_sell_points": [],
        "plot_primitives": [],
        "summary": [],
        "warnings": [],
        "meta": {},
    }


class _StaticMarketInputPort:
    """Returns the same prepared input for every read request."""

    def __init__(self, market_input: PipelineMarketInput) -> None:
        self._market_input = market_input
        self.requests: list[datetime] = []

    def read(self, target_time: datetime) -> PipelineMarketInput:
        self.requests.append(target_time)
        return self._market_input


class _RecordingAnalyzer:
    """Fake CZSC analyzer returning a contract-complete payload."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict[str, Any]]]] = []

    def __call__(
        self,
        bars: Sequence[Mapping[str, Any]],
        symbol: str,
    ) -> dict[str, Any]:
        copied = [dict(b) for b in bars]
        self.calls.append((symbol, copied))
        return _full_chan_analysis(symbol)


class _FakePreparator:
    def __init__(self, prepared: Any) -> None:
        self._prepared = prepared

    def prepare(self, symbol: str, trade_date: Any, *, config: Any) -> Any:
        return self._prepared


class HistoricalSnapshotAnalyzerWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        calendar = MarketContextService([_TRADE_DATE, "2026-07-23"])
        self.session = calendar.require_session(_TRADE_DATE, _MARKET)
        self.market_input = PipelineMarketInput(
            symbol=_SYMBOL,
            trade_date=_TRADE_DATE,
            previous_close=10.15,
            preheat_5m_bars=[
                _bar("2026-07-23 14:55:00", 10.0, 10.1, 9.9, 10.05, 5000, 50500),
                _bar("2026-07-23 15:00:00", 10.05, 10.2, 10.0, 10.15, 6000, 61200),
            ],
            bars_1m=[
                _bar("2026-07-24 09:31:00", 10.2, 10.3, 10.15, 10.25, 100, 1025),
                _bar("2026-07-24 09:32:00", 10.25, 10.4, 10.2, 10.35, 200, 2070),
                _bar("2026-07-24 09:33:00", 10.35, 10.45, 10.3, 10.4, 150, 1560),
            ],
            official_5m_bars=[
                _bar("2026-07-24 09:35:00", 10.2, 10.45, 10.15, 10.4, 450, 4655),
            ],
            quote_snapshots=[],
        )
        self.port = _StaticMarketInputPort(self.market_input)
        self.prepared = SimpleNamespace(
            symbol=_SYMBOL,
            market_session=self.session,
            trade_date=_TRADE_DATE,
            market_input_port=self.port,
        )

    def _build(
        self,
        analyzer: Any,
        recording_default: _RecordingAnalyzer,
    ) -> dict[str, Any]:
        with (
            patch(
                "packages.t0assistant.runtime.historical_snapshot.ReplayDataPreparator",
                return_value=_FakePreparator(self.prepared),
            ),
            patch(
                "packages.t0assistant.runtime.pipeline._default_analyze_5m",
                recording_default,
            ),
        ):
            return build_historical_snapshot(
                symbol=_SYMBOL,
                trade_date=_TRADE_DATE,
                market_data=object(),
                market_context=object(),
                analyzer=analyzer,
            )

    def test_none_analyzer_routes_to_pipeline_default(self) -> None:
        default = _RecordingAnalyzer()

        snapshot = self._build(None, default)

        self.assertEqual(len(default.calls), 1)
        symbol, closed_5m = default.calls[0]
        self.assertEqual(symbol, _SYMBOL)
        self.assertTrue(closed_5m, "default analyzer must receive closed 5m bars")
        self.assertEqual(snapshot["session"]["session_type"], "historical")
        self.assertEqual(snapshot["chan_analysis"]["symbol"], _SYMBOL)

    def test_explicit_analyzer_bypasses_pipeline_default(self) -> None:
        default = _RecordingAnalyzer()
        explicit = _RecordingAnalyzer()

        snapshot = self._build(explicit, default)

        self.assertEqual(len(explicit.calls), 1)
        self.assertEqual(default.calls, [])
        self.assertEqual(snapshot["chan_analysis"]["symbol"], _SYMBOL)

    def test_non_dict_analyzer_result_fails_fast(self) -> None:
        # Regression: a raw chantheory.analyze-style callable returns an
        # AnalysisResult object; the pipeline guard must turn that into a
        # clear failure instead of a snapshot without Chan overlays.
        with self.assertRaises(HistoricalSnapshotError):
            self._build(lambda bars, symbol: object(), _RecordingAnalyzer())


if __name__ == "__main__":
    unittest.main()
