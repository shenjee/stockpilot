"""09:30 atomic day switch and phase-aware polling (#130 PR-B)."""

from __future__ import annotations

import threading
import unittest
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Sequence
from unittest.mock import patch

from packages.marketdata.calendar_query import FixtureCalendarQuery
from packages.marketdata.services.market_context_service import MarketContextService
from packages.t0assistant.runtime.computation_executor import BoundedComputationExecutor
from packages.t0assistant.runtime.coordinator import SessionSpec, SessionType
from packages.t0assistant.runtime.live_market_view import (
    resolve_polling_profile,
    should_run_close_reconciliation,
)
from packages.t0assistant.runtime.live_projection_store import (
    LiveIncrementalUpdate,
    LiveProjectionStore,
)
from packages.t0assistant.runtime.live_refresh import (
    LiveRefreshKind,
    LiveRefreshResult,
    LiveRefreshScheduler,
)
from packages.t0assistant.runtime.live_runtime import BranchingLiveInput
from packages.t0assistant.runtime.live_session import (
    LiveSession,
    LiveSnapshotCandidate,
    PreparedLiveWarmup,
)
from packages.t0assistant.runtime.pipeline import PipelineMarketInput, WorkbenchPipeline


def _bar(timestamp: str, close: float = 10.0, *, closed: bool = True) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 100.0,
        "amount": close * 100.0,
        "closed": closed,
    }


def _quote(timestamp: str, price: float = 10.0) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "latest_price": price,
        "previous_close": 9.9,
        "open": 10.0,
        "high": price,
        "low": price,
        "volume": 100.0,
        "amount": price * 100.0,
        "change_percent": 0.0,
        "volume_ratio": None,
        "order_imbalance": None,
        "turnover_rate": None,
    }


class _SwitchableSource:
    def __init__(self) -> None:
        self.context = MarketContextService(["2026-07-24", "2026-07-27"])
        self.friday = self.context.require_session("2026-07-24", "sh")
        self.monday = self.context.require_session("2026-07-27", "sh")
        self.prepare_calls = 0

    def _friday_input(self, spec: SessionSpec) -> PipelineMarketInput:
        return PipelineMarketInput(
            symbol=spec.symbol,
            trade_date=date(2026, 7, 24),
            previous_close=10.0,
            preheat_5m_bars=[_bar("2026-07-23 15:00:00")],
            bars_1m=[_bar("2026-07-24 15:00:00")],
            official_5m_bars=[_bar("2026-07-24 15:00:00")],
            daily_bars_history=[],
            quote_snapshots=[],
        )

    def _monday_input(self, spec: SessionSpec) -> PipelineMarketInput:
        return PipelineMarketInput(
            symbol=spec.symbol,
            trade_date=date(2026, 7, 27),
            previous_close=10.0,
            preheat_5m_bars=[_bar("2026-07-24 15:00:00")],
            bars_1m=[_bar("2026-07-27 09:31:00", 10.2)],
            official_5m_bars=[],
            daily_bars_history=[],
            quote_snapshots=[_quote("2026-07-27 09:31:00", 10.2)],
        )

    def prepare(self, spec, *, minimum_preheat_5m):
        self.prepare_calls += 1
        if self.prepare_calls == 1:
            market_input = self._friday_input(spec)
            return PreparedLiveWarmup(
                market_session=self.friday,
                target_time=datetime(2026, 7, 24, 15, 0),
                observed_now=datetime(2026, 7, 24, 15, 0),
                market_candidate_trade_date=date(2026, 7, 24),
                market_input_port=_Port(market_input),
                calendar_status="available",
                market_phase="closed",
            )
        market_input = self._monday_input(spec)
        return PreparedLiveWarmup(
            market_session=self.monday,
            target_time=datetime(2026, 7, 27, 9, 31),
            observed_now=datetime(2026, 7, 27, 9, 31),
            market_candidate_trade_date=date(2026, 7, 27),
            market_input_port=_Port(market_input),
            calendar_status="available",
            market_phase="morning",
        )

    def load_refresh_bars(self, spec, *, timeframe, trade_date) -> Sequence[Mapping]:
        if str(trade_date) == "2026-07-27" and timeframe == "1m":
            return (_bar("2026-07-27 09:31:00", 10.2),)
        return (_bar("2026-07-24 15:00:00"),)

    def load_refresh_quotes(self, spec, *, trade_date) -> Sequence[Mapping]:
        if str(trade_date) == "2026-07-27":
            return (_quote("2026-07-27 09:31:00", 10.2),)
        return ()


class _Port:
    def __init__(self, value: PipelineMarketInput) -> None:
        self._value = value

    def read(self, target_time):
        return self._value


class PollingProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calendar = FixtureCalendarQuery(
            ["2026-07-24", "2026-07-27"],
            coverage_start="2026-07-24",
            coverage_end="2026-07-27",
        )

    def test_pre_open_is_idle(self) -> None:
        self.assertEqual(
            resolve_polling_profile(
                market_phase="pre_open",
                calendar_status="available",
                pinned_trade_date=date(2026, 7, 24),
                observed_at=datetime(2026, 7, 27, 8, 30),
                calendar=self.calendar,
                market="sh",
            ),
            "idle",
        )

    def test_morning_is_active(self) -> None:
        self.assertEqual(
            resolve_polling_profile(
                market_phase="morning",
                calendar_status="available",
                pinned_trade_date=date(2026, 7, 27),
                observed_at=datetime(2026, 7, 27, 10, 0),
                calendar=self.calendar,
                market="sh",
            ),
            "active",
        )

    def test_awaiting_day_switch_is_reduced(self) -> None:
        self.assertEqual(
            resolve_polling_profile(
                market_phase="pre_open",
                calendar_status="available",
                pinned_trade_date=date(2026, 7, 24),
                observed_at=datetime(2026, 7, 27, 9, 35),
                calendar=self.calendar,
                market="sh",
                awaiting_day_switch=True,
            ),
            "reduced",
        )

    def test_close_reconciliation_window(self) -> None:
        self.assertTrue(
            should_run_close_reconciliation(
                market_phase="closed",
                observed_at=datetime(2026, 7, 24, 15, 6),
                close_reconcile_status="not_started",
            )
        )
        self.assertFalse(
            should_run_close_reconciliation(
                market_phase="closed",
                observed_at=datetime(2026, 7, 24, 15, 6),
                close_reconcile_status="completed",
            )
        )


class AtomicDaySwitchTests(unittest.TestCase):
    def _spec(self) -> SessionSpec:
        return SessionSpec(
            session_id="live-1",
            session_type=SessionType.LIVE,
            symbol="sh.600000",
            generation=1,
            trade_date=None,
        )

    def test_auction_quote_before_0930_does_not_switch(self) -> None:
        class _AuctionSource(_SwitchableSource):
            def load_refresh_quotes(self, spec, *, trade_date):
                if str(trade_date) == "2026-07-27":
                    return (_quote("2026-07-27 09:20:00", 10.1),)
                return ()

        source = _AuctionSource()
        switched: list[tuple[LiveSnapshotCandidate, int]] = []
        port = BranchingLiveInput(
            source,
            calendar=FixtureCalendarQuery(
                ["2026-07-24", "2026-07-27"],
                coverage_start="2026-07-24",
                coverage_end="2026-07-27",
            ),
            on_day_switched=lambda candidate, epoch: switched.append((candidate, epoch)),
        )
        port.prepare(self._spec(), minimum_preheat_5m=1)

        result = port.refresh(
            LiveRefreshKind.QUOTE,
            self._spec(),
            observed_at=datetime(2026, 7, 27, 9, 20),
            latest_data_time=None,
        )

        self.assertEqual(result.updates, ())
        self.assertEqual(switched, [])
        self.assertEqual(port.market_epoch, 0)
        self.assertEqual(source.prepare_calls, 1)

    def test_post_open_quote_triggers_atomic_switch(self) -> None:
        source = _SwitchableSource()
        switched: list[tuple[LiveSnapshotCandidate, int]] = []
        port = BranchingLiveInput(
            source,
            calendar=FixtureCalendarQuery(
                ["2026-07-24", "2026-07-27"],
                coverage_start="2026-07-24",
                coverage_end="2026-07-27",
            ),
            on_day_switched=lambda candidate, epoch: switched.append((candidate, epoch)),
        )
        port.prepare(self._spec(), minimum_preheat_5m=1)

        result = port.refresh(
            LiveRefreshKind.QUOTE,
            self._spec(),
            observed_at=datetime(2026, 7, 27, 9, 31),
            latest_data_time=None,
        )

        self.assertEqual(result.updates, ())
        self.assertEqual(len(switched), 1)
        self.assertEqual(switched[0][1], 1)
        self.assertEqual(
            switched[0][0].pipeline_result.trade_date.isoformat(),
            "2026-07-27",
        )
        self.assertEqual(switched[0][0].market_phase, "morning")
        self.assertEqual(switched[0][0].polling_profile, "active")
        self.assertEqual(source.prepare_calls, 2)

    def test_first_closed_one_minute_triggers_switch(self) -> None:
        source = _SwitchableSource()
        switched: list[tuple[LiveSnapshotCandidate, int]] = []
        port = BranchingLiveInput(
            source,
            calendar=FixtureCalendarQuery(
                ["2026-07-24", "2026-07-27"],
                coverage_start="2026-07-24",
                coverage_end="2026-07-27",
            ),
            on_day_switched=lambda candidate, epoch: switched.append((candidate, epoch)),
        )
        port.prepare(self._spec(), minimum_preheat_5m=1)

        result = port.refresh(
            LiveRefreshKind.ONE_MINUTE,
            self._spec(),
            observed_at=datetime(2026, 7, 27, 9, 31),
            latest_data_time=datetime(2026, 7, 24, 15, 0),
        )

        self.assertEqual(result.updates, ())
        self.assertEqual(len(switched), 1)
        self.assertEqual(switched[0][0].pipeline_result.trade_date.isoformat(), "2026-07-27")

    def test_refresh_after_switch_uses_new_trade_date(self) -> None:
        source = _SwitchableSource()
        port = BranchingLiveInput(
            source,
            calendar=FixtureCalendarQuery(
                ["2026-07-24", "2026-07-27"],
                coverage_start="2026-07-24",
                coverage_end="2026-07-27",
            ),
        )
        port.prepare(self._spec(), minimum_preheat_5m=1)
        port.refresh(
            LiveRefreshKind.QUOTE,
            self._spec(),
            observed_at=datetime(2026, 7, 27, 9, 31),
            latest_data_time=None,
        )
        self.assertEqual(port.market_epoch, 1)

        result = port.refresh(
            LiveRefreshKind.ONE_MINUTE,
            self._spec(),
            observed_at=datetime(2026, 7, 27, 9, 32),
            latest_data_time=None,
        )
        self.assertNotEqual(result.updates, ())
        self.assertEqual(
            result.updates[0].payload["bars"][0]["timestamp"],
            "2026-07-27 09:31:00",
        )


def _snapshot_trade_date(snapshot: dict) -> str:
    return str(snapshot["session"]["trade_date"])


def _assert_revision_trade_dates_consistent(
    test_case: unittest.TestCase,
    snapshot: dict,
) -> None:
    trade_date = _snapshot_trade_date(snapshot)
    market = snapshot["market"]
    quote = market.get("quote")
    if isinstance(quote, dict) and quote.get("timestamp"):
        test_case.assertTrue(str(quote["timestamp"]).startswith(trade_date))
    for bar in market.get("bars_1m", ()):
        test_case.assertTrue(str(bar["timestamp"]).startswith(trade_date))


class _PausingRefreshScheduler(LiveRefreshScheduler):
    """Pause after the epoch gate and before publishing branch updates."""

    def __init__(
        self,
        *args,
        pause_before_publish: threading.Event,
        release_publish: threading.Event,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._pause_before_publish = pause_before_publish
        self._release_publish = release_publish

    def _accept_result(
        self,
        kind: LiveRefreshKind,
        observed_at: datetime,
        result: object,
    ) -> None:
        if not isinstance(result, LiveRefreshResult):
            super()._accept_result(kind, observed_at, result)
            return
        result_epoch = result.market_epoch
        with self._lock:
            if (
                result_epoch is not None
                and self._scheduler_market_epoch is not None
                and result_epoch != self._scheduler_market_epoch
            ):
                return
        if result.updates:
            self._pause_before_publish.set()
            self._release_publish.wait(timeout=2)
        super()._accept_result(kind, observed_at, result)


class _CannedRefreshInput:
    """Return one canned branch result while delegating epoch to the live port."""

    def __init__(
        self,
        port: BranchingLiveInput,
        kind: LiveRefreshKind,
        result: LiveRefreshResult,
    ) -> None:
        self._port = port
        self._kind = kind
        self._result = result

    @property
    def market_epoch(self) -> int:
        return self._port.market_epoch

    def refresh(
        self,
        kind: LiveRefreshKind,
        spec: SessionSpec,
        *,
        observed_at: datetime,
        latest_data_time: datetime | None,
    ) -> LiveRefreshResult:
        if kind is self._kind:
            return self._result
        return self._port.refresh(
            kind,
            spec,
            observed_at=observed_at,
            latest_data_time=latest_data_time,
        )


class _FakeCoordinator:
    def __init__(self, session_id: str, generation: int) -> None:
        self._accepted = (session_id, generation)

    def commit_if_accepted(self, *, session_type, session_id, generation, commit):
        if self._accepted != (session_id, generation):
            return False
        commit()
        return True


class StaleEpochRaceTests(unittest.TestCase):
    def _spec(self) -> SessionSpec:
        return SessionSpec(
            session_id="live-1",
            session_type=SessionType.LIVE,
            symbol="sh.600000",
            generation=1,
            trade_date=None,
        )

    def _accept_switch(self, store: LiveProjectionStore, switched: list[LiveSnapshotCandidate]):
        def _handler(candidate: LiveSnapshotCandidate, epoch: int) -> None:
            switched.append(candidate)
            store.accept_candidate(candidate)

        return _handler

    def test_scheduler_toctou_old_incremental_rejected_by_store(self) -> None:
        source = _SwitchableSource()
        calendar = FixtureCalendarQuery(
            ["2026-07-24", "2026-07-27"],
            coverage_start="2026-07-24",
            coverage_end="2026-07-27",
        )
        switched: list[LiveSnapshotCandidate] = []
        coordinator = _FakeCoordinator("live-1", 1)
        store = LiveProjectionStore(coordinator, service_generation=1)
        port = BranchingLiveInput(
            source,
            calendar=calendar,
            on_day_switched=self._accept_switch(store, switched),
        )
        port.prepare(self._spec(), minimum_preheat_5m=1)

        friday_input = source._friday_input(self._spec())
        friday_result = WorkbenchPipeline(
            session=source.friday,
            market_input_port=_Port(friday_input),
        ).preview(datetime(2026, 7, 24, 15, 0))
        store.accept_candidate(
            LiveSnapshotCandidate(
                session_id="live-1",
                generation=1,
                symbol="sh.600000",
                pipeline_result=friday_result,
                market_epoch=0,
            )
        )
        revision_after_friday = store.current_revision

        stale = port.refresh(
            LiveRefreshKind.ONE_MINUTE,
            self._spec(),
            observed_at=datetime(2026, 7, 24, 15, 1),
            latest_data_time=None,
        )
        self.assertEqual(stale.market_epoch, 0)
        self.assertNotEqual(stale.updates, ())

        pause_before_publish = threading.Event()
        release_publish = threading.Event()
        executor = BoundedComputationExecutor(capacity=4, worker_count=2)
        scheduler = _PausingRefreshScheduler(
            self._spec(),
            _CannedRefreshInput(port, LiveRefreshKind.ONE_MINUTE, stale),
            executor,
            on_update=store.accept_incremental,
            pause_before_publish=pause_before_publish,
            release_publish=release_publish,
        )
        self.addCleanup(lambda: (
            scheduler.retire(),
            executor.shutdown(cancel_pending=True, wait=True),
        ))

        worker = threading.Thread(
            target=lambda: scheduler.retry(
                LiveRefreshKind.ONE_MINUTE,
                datetime(2026, 7, 27, 9, 31),
            )
        )
        worker.start()
        self.assertTrue(pause_before_publish.wait(timeout=2))

        port.refresh(
            LiveRefreshKind.QUOTE,
            self._spec(),
            observed_at=datetime(2026, 7, 27, 9, 31),
            latest_data_time=None,
        )
        self.assertEqual(len(switched), 1)
        self.assertEqual(store.published_market_epoch, 1)
        revision_after_switch = store.current_revision
        self.assertGreater(revision_after_switch, revision_after_friday)

        release_publish.set()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())

        self.assertEqual(store.current_revision, revision_after_switch)
        snapshot = store.get_live_snapshot(session_id="live-1", generation=1)
        self.assertEqual(_snapshot_trade_date(snapshot), "2026-07-27")
        self.assertFalse(
            any(
                str(row["timestamp"]).startswith("2026-07-24 15:01")
                for row in snapshot["market"]["bars_1m"]
            )
        )
        _assert_revision_trade_dates_consistent(self, snapshot)

    def test_new_epoch_incremental_blocked_until_switch_baseline_published(self) -> None:
        source = _SwitchableSource()
        calendar = FixtureCalendarQuery(
            ["2026-07-24", "2026-07-27"],
            coverage_start="2026-07-24",
            coverage_end="2026-07-27",
        )
        publish_blocked = threading.Event()
        release_publish = threading.Event()
        switched: list[LiveSnapshotCandidate] = []

        def _blocked_switch(candidate: LiveSnapshotCandidate, epoch: int) -> None:
            publish_blocked.set()
            release_publish.wait(timeout=2)
            switched.append(candidate)
            store.accept_candidate(candidate)

        coordinator = _FakeCoordinator("live-1", 1)
        store = LiveProjectionStore(coordinator, service_generation=1)
        port = BranchingLiveInput(
            source,
            calendar=calendar,
            on_day_switched=_blocked_switch,
        )
        port.prepare(self._spec(), minimum_preheat_5m=1)
        friday_input = source._friday_input(self._spec())
        friday_result = WorkbenchPipeline(
            session=source.friday,
            market_input_port=_Port(friday_input),
        ).preview(datetime(2026, 7, 24, 15, 0))
        store.accept_candidate(
            LiveSnapshotCandidate(
                session_id="live-1",
                generation=1,
                symbol="sh.600000",
                pipeline_result=friday_result,
                market_epoch=0,
            )
        )
        revision_before = store.current_revision

        switch_thread = threading.Thread(
            target=lambda: port.refresh(
                LiveRefreshKind.QUOTE,
                self._spec(),
                observed_at=datetime(2026, 7, 27, 9, 31),
                latest_data_time=None,
            )
        )
        switch_thread.start()
        self.assertTrue(publish_blocked.wait(timeout=2))
        self.assertEqual(port.market_epoch, 1)

        ahead = port.refresh(
            LiveRefreshKind.ONE_MINUTE,
            self._spec(),
            observed_at=datetime(2026, 7, 27, 9, 32),
            latest_data_time=None,
        )
        self.assertEqual(ahead.market_epoch, 1)
        for update in ahead.updates:
            self.assertIsNone(store.accept_incremental(update))

        self.assertEqual(store.current_revision, revision_before)
        self.assertEqual(store.published_market_epoch, 0)

        release_publish.set()
        switch_thread.join(timeout=5)
        self.assertFalse(switch_thread.is_alive())
        self.assertEqual(len(switched), 1)
        self.assertEqual(store.published_market_epoch, 1)
        self.assertGreater(store.current_revision, revision_before)

        for update in ahead.updates:
            accepted = store.accept_incremental(update)
            self.assertIsNotNone(accepted)
            break


class DaySwitchFailureAtomicityTests(unittest.TestCase):
    def _spec(self) -> SessionSpec:
        return SessionSpec(
            session_id="live-1",
            session_type=SessionType.LIVE,
            symbol="sh.600000",
            generation=1,
            trade_date=None,
        )

    def test_preview_failure_does_not_commit_partial_day_switch(self) -> None:
        source = _SwitchableSource()
        switched: list[LiveSnapshotCandidate] = []
        port = BranchingLiveInput(
            source,
            calendar=FixtureCalendarQuery(
                ["2026-07-24", "2026-07-27"],
                coverage_start="2026-07-24",
                coverage_end="2026-07-27",
            ),
            on_day_switched=lambda candidate, epoch: switched.append(candidate),
        )
        port.prepare(self._spec(), minimum_preheat_5m=1)

        original_preview = WorkbenchPipeline.preview

        def _fail_monday_preview(self, target_time=None):
            if self.session.trade_date == date(2026, 7, 27):
                raise RuntimeError("preview failed")
            return original_preview(self, target_time)

        with patch.object(WorkbenchPipeline, "preview", _fail_monday_preview):
            port.refresh(
                LiveRefreshKind.QUOTE,
                self._spec(),
                observed_at=datetime(2026, 7, 27, 9, 31),
                latest_data_time=None,
            )

        self.assertEqual(port.market_epoch, 0)
        self.assertEqual(switched, [])
        self.assertEqual(source.prepare_calls, 2)

        port.refresh(
            LiveRefreshKind.QUOTE,
            self._spec(),
            observed_at=datetime(2026, 7, 27, 9, 31),
            latest_data_time=None,
        )
        self.assertEqual(len(switched), 1)
        self.assertEqual(port.market_epoch, 1)
        self.assertEqual(
            switched[0].pipeline_result.trade_date.isoformat(),
            "2026-07-27",
        )


class CloseReconciliationRetryTests(unittest.TestCase):
    def _spec(self) -> SessionSpec:
        return SessionSpec(
            session_id="live-1",
            session_type=SessionType.LIVE,
            symbol="sh.600000",
            generation=1,
            trade_date=None,
        )

    def _closed_port(self) -> BranchingLiveInput:
        class _ClosedSource(_SwitchableSource):
            quote_failures = 0

            def prepare(self, spec, *, minimum_preheat_5m):
                market_input = self._friday_input(spec)
                return PreparedLiveWarmup(
                    market_session=self.friday,
                    target_time=datetime(2026, 7, 24, 15, 6),
                    observed_now=datetime(2026, 7, 24, 15, 6),
                    market_candidate_trade_date=date(2026, 7, 24),
                    market_input_port=_Port(market_input),
                    calendar_status="available",
                    market_phase="closed",
                )

            def load_refresh_quotes(self, spec, *, trade_date):
                if self.quote_failures == 0:
                    self.quote_failures += 1
                    raise RuntimeError("quote reconcile failed")
                return ()

        return BranchingLiveInput(
            _ClosedSource(),
            calendar=FixtureCalendarQuery(
                ["2026-07-24"],
                coverage_start="2026-07-24",
                coverage_end="2026-07-24",
            ),
        )

    def test_failed_reconciliation_stays_retryable_until_success(self) -> None:
        port = self._closed_port()
        port.prepare(self._spec(), minimum_preheat_5m=1)
        observed_at = datetime(2026, 7, 24, 15, 6)

        self.assertTrue(port.maybe_reconcile_close(self._spec(), observed_at))
        self.assertEqual(port.close_reconcile_status, "in_progress")

        executor = BoundedComputationExecutor(capacity=12, worker_count=3)
        scheduler = LiveRefreshScheduler(
            self._spec(),
            port,
            executor,
            on_update=lambda update: None,
        )
        self.addCleanup(lambda: (
            scheduler.retire(),
            executor.shutdown(cancel_pending=True, wait=True),
        ))

        states = scheduler.run_reconciliation(observed_at)
        port.finish_close_reconciliation(states, observed_at)

        self.assertEqual(port.close_reconcile_status, "retry_pending")
        self.assertFalse(
            port.maybe_reconcile_close(
                self._spec(),
                observed_at + timedelta(seconds=1),
            )
        )
        self.assertEqual(
            resolve_polling_profile(
                market_phase="closed",
                calendar_status="available",
                pinned_trade_date=date(2026, 7, 24),
                observed_at=observed_at + timedelta(seconds=1),
                calendar=None,
                market="sh",
                close_reconcile_status=port.close_reconcile_status,
                close_reconcile_retry_due=False,
            ),
            "reduced",
        )

        retry_at = observed_at + timedelta(seconds=30)
        self.assertTrue(port.maybe_reconcile_close(self._spec(), retry_at))
        states = scheduler.run_reconciliation(retry_at)
        port.finish_close_reconciliation(states, retry_at)
        self.assertEqual(port.close_reconcile_status, "completed")

        self.assertFalse(port.maybe_reconcile_close(self._spec(), retry_at + timedelta(seconds=1)))
        scheduler.run_reconciliation(retry_at + timedelta(seconds=1))
        self.assertEqual(port.close_reconcile_status, "completed")


if __name__ == "__main__":
    unittest.main()
