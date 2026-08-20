"""Pinned-day market_phase advances with the wall clock (#130 PR-A)."""

from __future__ import annotations

import threading
import unittest
from datetime import date, datetime
from typing import Any, Mapping, Sequence

from packages.marketdata.calendar_query import FixtureCalendarQuery
from packages.marketdata.services.market_context_service import MarketContextService
from packages.t0assistant.runtime.coordinator import SessionSpec, SessionType
from packages.marketdata.t0_schema import InstrumentIdentity, InstrumentType
from packages.t0assistant.runtime.live_refresh import LiveRefreshKind
from packages.t0assistant.runtime.live_runtime import (
    BranchingLiveInput,
    _resolve_pinned_live_view,
)
from packages.t0assistant.runtime.live_session import (
    LiveSnapshotCandidate,
    PreparedLiveWarmup,
)
from packages.t0assistant.runtime.pipeline import PipelineMarketInput


_STOCK = InstrumentIdentity(
    symbol="sh.600000",
    code="600000",
    market="sh",
    name="Test Stock",
    instrument_type=InstrumentType.STOCK,
)


def _bar(timestamp: str, close: float = 10.0) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 100.0,
        "amount": close * 100.0,
        "closed": True,
    }


class _PhaseSource:
    def __init__(self) -> None:
        self.context = MarketContextService(["2026-07-23", "2026-07-24"])
        self.session = self.context.require_session("2026-07-24", "sh")
        self.fail_reads = False

    def prepare(self, spec, *, minimum_preheat_5m, target_trade_date=None):
        market_input = PipelineMarketInput(
            symbol=spec.symbol,
            trade_date=date(2026, 7, 24),
            previous_close=10.0,
            preheat_5m_bars=[
                _bar("2026-07-23 14:55:00"),
                _bar("2026-07-23 15:00:00"),
            ],
            bars_1m=[_bar("2026-07-24 09:31:00", 10.2)],
            official_5m_bars=[],
            daily_bars_history=[],
            quote_snapshots=[],
        )

        class _Port:
            def read(self, target_time):
                return market_input

        return PreparedLiveWarmup(
            market_session=self.session,
            target_time=datetime(2026, 7, 24, 9, 31),
            observed_now=datetime(2026, 7, 24, 9, 31),
            market_candidate_trade_date=date(2026, 7, 24),
            market_input_port=_Port(),
            calendar_status="available",
            market_phase="morning",
        )

    def load_refresh_bars(self, spec, *, timeframe, trade_date) -> Sequence[Mapping]:
        if self.fail_reads:
            raise RuntimeError("provider down")
        return (_bar("2026-07-24 09:31:00", 10.2),)

    def load_refresh_quotes(self, spec, *, trade_date) -> Sequence[Mapping]:
        if self.fail_reads:
            raise RuntimeError("provider down")
        return ()


class ResolvePinnedLiveViewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = MarketContextService(
            ["2026-07-24", "2026-07-27"],
            coverage_start="2026-07-24",
            coverage_end="2026-07-27",
        ).require_session("2026-07-24", "sh")
        self.calendar = FixtureCalendarQuery(
            ["2026-07-24", "2026-07-27"],
            coverage_start="2026-07-24",
            coverage_end="2026-07-27",
        )

    def test_same_day_advances_through_session_phases(self) -> None:
        self.assertEqual(
            _resolve_pinned_live_view(
                self.session,
                observed_at=datetime(2026, 7, 24, 10, 0),
                calendar_status="available",
                calendar=self.calendar,
            ).market_phase,
            "morning",
        )
        self.assertEqual(
            _resolve_pinned_live_view(
                self.session,
                observed_at=datetime(2026, 7, 24, 12, 0),
                calendar_status="available",
                calendar=self.calendar,
            ).market_phase,
            "lunch_break",
        )
        self.assertEqual(
            _resolve_pinned_live_view(
                self.session,
                observed_at=datetime(2026, 7, 24, 14, 0),
                calendar_status="available",
                calendar=self.calendar,
            ).market_phase,
            "afternoon",
        )
        closed = _resolve_pinned_live_view(
            self.session,
            observed_at=datetime(2026, 7, 24, 15, 30),
            calendar_status="available",
            calendar=self.calendar,
        )
        self.assertEqual(closed.market_phase, "closed")
        self.assertEqual(closed.calendar_status, "available")

    def test_next_open_day_uses_pre_open_then_market_closed(self) -> None:
        pre_open = _resolve_pinned_live_view(
            self.session,
            observed_at=datetime(2026, 7, 27, 8, 0),
            calendar_status="available",
            calendar=self.calendar,
        )
        self.assertEqual(pre_open.market_phase, "pre_open")
        self.assertEqual(pre_open.calendar_status, "available")
        after_open = _resolve_pinned_live_view(
            self.session,
            observed_at=datetime(2026, 7, 27, 10, 0),
            calendar_status="available",
            calendar=self.calendar,
        )
        self.assertEqual(after_open.market_phase, "market_closed")
        self.assertEqual(after_open.calendar_status, "available")

    def test_weekend_is_market_closed_with_available_calendar(self) -> None:
        morning = _resolve_pinned_live_view(
            self.session,
            observed_at=datetime(2026, 7, 25, 8, 0),
            calendar_status="available",
            calendar=self.calendar,
        )
        self.assertEqual(morning.market_phase, "market_closed")
        self.assertEqual(morning.calendar_status, "available")
        midday = _resolve_pinned_live_view(
            self.session,
            observed_at=datetime(2026, 7, 25, 10, 0),
            calendar_status="available",
            calendar=self.calendar,
        )
        self.assertEqual(midday.market_phase, "market_closed")
        self.assertEqual(midday.calendar_status, "available")

    def test_weekday_holiday_is_market_closed_before_open(self) -> None:
        # 2026-07-28 is a Tuesday inside coverage but not an open day.
        calendar = FixtureCalendarQuery(
            ["2026-07-24", "2026-07-27", "2026-07-29"],
            coverage_start="2026-07-24",
            coverage_end="2026-07-29",
        )
        resolved = _resolve_pinned_live_view(
            self.session,
            observed_at=datetime(2026, 7, 28, 8, 0),
            calendar_status="available",
            calendar=calendar,
        )
        self.assertEqual(resolved.market_phase, "market_closed")
        self.assertEqual(resolved.calendar_status, "available")

    def test_coverage_gap_degrades_to_unavailable_unknown(self) -> None:
        # Coverage ends Friday; Saturday is outside and must not stay available.
        calendar = FixtureCalendarQuery(
            ["2026-07-24"],
            coverage_start="2026-07-24",
            coverage_end="2026-07-24",
        )
        resolved = _resolve_pinned_live_view(
            self.session,
            observed_at=datetime(2026, 7, 25, 10, 0),
            calendar_status="available",
            calendar=calendar,
        )
        self.assertEqual(resolved.market_phase, "unknown")
        self.assertEqual(resolved.calendar_status, "unavailable")

    def test_without_calendar_degrades_to_unavailable_unknown(self) -> None:
        resolved = _resolve_pinned_live_view(
            self.session,
            observed_at=datetime(2026, 7, 27, 8, 0),
            calendar_status="available",
            calendar=None,
        )
        self.assertEqual(resolved.market_phase, "unknown")
        self.assertEqual(resolved.calendar_status, "unavailable")


class BranchingLiveInputPhaseRefreshTests(unittest.TestCase):
    def _spec(self) -> SessionSpec:
        return SessionSpec(
            session_id="live-1",
            session_type=SessionType.LIVE,
            symbol="sh.600000",
            generation=1,
            trade_date=None,
            instrument=_STOCK,
        )

    def test_phase_change_republishes_full_snapshot(self) -> None:
        source = _PhaseSource()
        published: list[LiveSnapshotCandidate] = []
        port = BranchingLiveInput(
            source,
            calendar=FixtureCalendarQuery(
                ["2026-07-23", "2026-07-24"],
                coverage_start="2026-07-23",
                coverage_end="2026-07-24",
            ),
            on_projection_refresh=published.append,
        )
        port.prepare(self._spec(), minimum_preheat_5m=2)

        result = port.refresh(
            LiveRefreshKind.ONE_MINUTE,
            self._spec(),
            observed_at=datetime(2026, 7, 24, 12, 0),
            latest_data_time=datetime(2026, 7, 24, 9, 31),
        )

        self.assertEqual(result.updates, ())
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0].market_phase, "lunch_break")
        self.assertEqual(published[0].calendar_status, "available")

    def test_coverage_gap_publishes_unavailable_unknown_snapshot(self) -> None:
        source = _PhaseSource()
        published: list[LiveSnapshotCandidate] = []
        port = BranchingLiveInput(
            source,
            calendar=FixtureCalendarQuery(
                ["2026-07-24"],
                coverage_start="2026-07-24",
                coverage_end="2026-07-24",
            ),
            on_projection_refresh=published.append,
        )
        port.prepare(self._spec(), minimum_preheat_5m=2)

        result = port.refresh(
            LiveRefreshKind.ONE_MINUTE,
            self._spec(),
            observed_at=datetime(2026, 7, 25, 10, 0),
            latest_data_time=datetime(2026, 7, 24, 9, 31),
        )

        self.assertEqual(result.updates, ())
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0].calendar_status, "unavailable")
        self.assertEqual(published[0].market_phase, "unknown")

    def test_phase_advances_when_provider_fails(self) -> None:
        source = _PhaseSource()
        source.fail_reads = True
        published: list[LiveSnapshotCandidate] = []
        port = BranchingLiveInput(
            source,
            on_projection_refresh=published.append,
        )
        port.prepare(self._spec(), minimum_preheat_5m=2)

        with self.assertRaisesRegex(RuntimeError, "provider down"):
            port.refresh(
                LiveRefreshKind.ONE_MINUTE,
                self._spec(),
                observed_at=datetime(2026, 7, 24, 12, 0),
                latest_data_time=datetime(2026, 7, 24, 9, 31),
            )

        self.assertEqual(len(published), 1)
        self.assertEqual(published[0].market_phase, "lunch_break")

    def test_concurrent_phase_transition_publishes_once(self) -> None:
        barrier = threading.Barrier(2)

        class _RacingSource(_PhaseSource):
            def load_refresh_bars(self, spec, *, timeframe, trade_date):
                barrier.wait(timeout=2)
                close = 10.3 if timeframe == "1m" else 10.4
                return (_bar(f"2026-07-24 09:3{2 if timeframe == '1m' else 5}:00", close),)

        racing = _RacingSource()
        published: list[LiveSnapshotCandidate] = []
        publish_lock = threading.Lock()

        def _capture(candidate: LiveSnapshotCandidate) -> None:
            with publish_lock:
                published.append(candidate)

        port = BranchingLiveInput(
            racing,
            on_projection_refresh=_capture,
        )
        port.prepare(self._spec(), minimum_preheat_5m=2)
        errors: list[BaseException] = []

        def _run(kind: LiveRefreshKind) -> None:
            try:
                port.refresh(
                    kind,
                    self._spec(),
                    observed_at=datetime(2026, 7, 24, 12, 0),
                    latest_data_time=datetime(2026, 7, 24, 9, 31),
                )
            except BaseException as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        threads = [
            threading.Thread(
                target=_run,
                args=(LiveRefreshKind.ONE_MINUTE,),
            ),
            threading.Thread(
                target=_run,
                args=(LiveRefreshKind.OFFICIAL_FIVE_MINUTE,),
            ),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual(errors, [])
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0].market_phase, "lunch_break")


if __name__ == "__main__":
    unittest.main()
