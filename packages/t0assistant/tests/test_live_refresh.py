"""Deterministic tests for T0-024 independent Live refresh orchestration."""

from __future__ import annotations

from datetime import datetime, timedelta
from threading import Event, Thread
import unittest

from packages.t0assistant.runtime import (
    BoundedComputationExecutor,
    LiveRefreshBackoff,
    LiveIncrementalUpdate,
    LiveRefreshIntervals,
    LiveRefreshKind,
    LiveRefreshResult,
    LiveRefreshScheduler,
    LiveRefreshValidationError,
    SessionSpec,
    SessionType,
)


def _update(
    kind: LiveRefreshKind,
    *,
    session_id: str = "live-1",
    generation: int = 7,
) -> LiveIncrementalUpdate:
    if kind is LiveRefreshKind.QUOTE:
        payload = {
            "target": "quote",
            "bars": [],
            "quote": {
                "timestamp": "2026-07-24 09:30:03",
                "last_price": 10.03,
                "previous_close": 10.0,
                "open": 10.0,
                "high": 10.05,
                "low": 9.99,
                "volume": 300.0,
                "amount": 3009.0,
                "change_percent": 0.3,
                "volume_ratio": None,
                "order_imbalance": None,
                "turnover_rate": None,
            },
        }
        event_type = "market_update"
    elif kind is LiveRefreshKind.ONE_MINUTE:
        payload = {"target": "bars_1m", "bars": [], "quote": None}
        event_type = "market_update"
    else:
        payload = {"target": "bars_5m", "bars": [], "quote": None}
        event_type = "market_update"
    return LiveIncrementalUpdate(
        session_id=session_id,
        generation=generation,
        event_type=event_type,
        payload=payload,
    )


class _FakeInput:
    def __init__(self) -> None:
        self.calls: list[
            tuple[LiveRefreshKind, datetime, datetime | None]
        ] = []
        self.results: dict[LiveRefreshKind, list[object]] = {
            kind: [] for kind in LiveRefreshKind
        }

    def queue(self, kind: LiveRefreshKind, *results: object) -> None:
        self.results[kind].extend(results)

    def refresh(
        self,
        kind: LiveRefreshKind,
        spec: SessionSpec,
        *,
        observed_at: datetime,
        latest_data_time: datetime | None,
    ) -> LiveRefreshResult:
        self.calls.append((kind, observed_at, latest_data_time))
        queued = self.results[kind]
        result = queued.pop(0) if queued else LiveRefreshResult.no_change()
        if isinstance(result, BaseException):
            raise result
        return result  # type: ignore[return-value]


class LiveRefreshSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = BoundedComputationExecutor(capacity=12, worker_count=3)
        self.input = _FakeInput()
        self.updates: list[LiveIncrementalUpdate] = []
        self.failures: list[tuple[LiveRefreshKind, BaseException]] = []
        self.spec = SessionSpec(
            session_id="live-1",
            session_type=SessionType.LIVE,
            symbol="sh.600000",
            generation=7,
            trade_date=None,
        )
        self.intervals = LiveRefreshIntervals(
            quote=timedelta(seconds=2),
            one_minute=timedelta(seconds=10),
            official_five_minute=timedelta(seconds=30),
        )
        self.scheduler = LiveRefreshScheduler(
            self.spec,
            self.input,
            self.executor,
            on_update=self.updates.append,
            intervals=self.intervals,
            on_failure=lambda kind, exc, _epoch=None: self.failures.append((kind, exc)),
        )
        self.t0 = datetime(2026, 7, 24, 9, 30, 0)

    def tearDown(self) -> None:
        self.scheduler.retire()
        self.executor.shutdown(cancel_pending=True, wait=True)

    def test_three_branches_advance_on_independent_cadences_and_watermarks(self) -> None:
        quote_time = self.t0 + timedelta(seconds=3)
        one_minute_time = self.t0 + timedelta(minutes=1)
        five_minute_time = self.t0 + timedelta(minutes=5)
        self.input.queue(
            LiveRefreshKind.QUOTE,
            LiveRefreshResult(quote_time, (_update(LiveRefreshKind.QUOTE),)),
            LiveRefreshResult(
                quote_time + timedelta(seconds=2),
                (_update(LiveRefreshKind.QUOTE),),
            ),
        )
        self.input.queue(
            LiveRefreshKind.ONE_MINUTE,
            LiveRefreshResult(
                one_minute_time, (_update(LiveRefreshKind.ONE_MINUTE),)
            ),
        )
        self.input.queue(
            LiveRefreshKind.OFFICIAL_FIVE_MINUTE,
            LiveRefreshResult(
                five_minute_time,
                (_update(LiveRefreshKind.OFFICIAL_FIVE_MINUTE),),
            ),
        )

        initial = self.scheduler.run_due(self.t0)
        self.assertEqual(len(self.input.calls), 3)
        self.assertEqual(
            initial[LiveRefreshKind.QUOTE].latest_data_time, quote_time
        )
        self.assertEqual(
            initial[LiveRefreshKind.ONE_MINUTE].latest_data_time, one_minute_time
        )
        self.assertEqual(
            initial[LiveRefreshKind.OFFICIAL_FIVE_MINUTE].latest_data_time,
            five_minute_time,
        )

        self.scheduler.run_due(self.t0 + timedelta(seconds=2))
        self.assertEqual(
            [call[0] for call in self.input.calls],
            [
                LiveRefreshKind.QUOTE,
                LiveRefreshKind.ONE_MINUTE,
                LiveRefreshKind.OFFICIAL_FIVE_MINUTE,
                LiveRefreshKind.QUOTE,
            ],
        )
        self.assertEqual(self.input.calls[-1][2], quote_time)
        states = self.scheduler.states
        self.assertEqual(
            states[LiveRefreshKind.QUOTE].latest_data_time,
            quote_time + timedelta(seconds=2),
        )
        self.assertEqual(
            states[LiveRefreshKind.ONE_MINUTE].latest_data_time, one_minute_time
        )
        self.assertEqual(
            states[LiveRefreshKind.OFFICIAL_FIVE_MINUTE].latest_data_time,
            five_minute_time,
        )

    def test_no_new_official_five_minute_bar_is_successful_noop(self) -> None:
        self.input.queue(
            LiveRefreshKind.OFFICIAL_FIVE_MINUTE,
            LiveRefreshResult.no_change(),
            LiveRefreshResult.no_change(),
        )

        first = self.scheduler.run_due(self.t0)
        second = self.scheduler.retry(
            LiveRefreshKind.OFFICIAL_FIVE_MINUTE,
            self.t0 + timedelta(seconds=1),
        )

        self.assertIsNone(
            first[LiveRefreshKind.OFFICIAL_FIVE_MINUTE].last_failure
        )
        self.assertEqual(
            first[LiveRefreshKind.OFFICIAL_FIVE_MINUTE].last_success_at, self.t0
        )
        self.assertIsNone(second.last_failure)
        self.assertEqual(
            second.last_success_at, self.t0 + timedelta(seconds=1)
        )
        self.assertEqual(self.failures, [])

    def test_failure_in_one_branch_does_not_block_other_due_branches(self) -> None:
        quote_failure = RuntimeError("quote unavailable")
        one_minute_time = self.t0 + timedelta(minutes=1)
        self.input.queue(LiveRefreshKind.QUOTE, quote_failure)
        self.input.queue(
            LiveRefreshKind.ONE_MINUTE,
            LiveRefreshResult(
                one_minute_time, (_update(LiveRefreshKind.ONE_MINUTE),)
            ),
        )
        self.input.queue(
            LiveRefreshKind.OFFICIAL_FIVE_MINUTE,
            LiveRefreshResult.no_change(),
        )

        states = self.scheduler.run_due(self.t0)

        self.assertIs(states[LiveRefreshKind.QUOTE].last_failure, quote_failure)
        self.assertEqual(
            states[LiveRefreshKind.ONE_MINUTE].latest_data_time, one_minute_time
        )
        self.assertEqual(
            states[LiveRefreshKind.ONE_MINUTE].last_success_at, self.t0
        )
        self.assertEqual(
            states[LiveRefreshKind.OFFICIAL_FIVE_MINUTE].last_success_at,
            self.t0,
        )
        self.assertEqual(self.failures, [(LiveRefreshKind.QUOTE, quote_failure)])

    def test_failure_callback_exception_is_logged_without_replacing_branch_failure(
        self,
    ) -> None:
        original_failure = RuntimeError("quote unavailable")
        callback_failure = ValueError("callback broken")
        self.input.queue(LiveRefreshKind.QUOTE, original_failure)

        def bad_callback(kind, failure, _epoch=None):
            raise callback_failure

        scheduler = LiveRefreshScheduler(
            self.spec,
            self.input,
            self.executor,
            on_update=self.updates.append,
            intervals=self.intervals,
            on_failure=bad_callback,
        )
        self.addCleanup(scheduler.retire)

        with self.assertLogs(
            "packages.t0assistant.runtime.live_refresh",
            level="ERROR",
        ) as captured:
            state = scheduler.retry(LiveRefreshKind.QUOTE, self.t0)

        self.assertIs(state.last_failure, original_failure)
        self.assertEqual(state.latest_data_time, None)
        self.assertIn(
            "live refresh failure callback raised",
            captured.output[0],
        )
        self.assertIn("callback broken", captured.output[0])

    def test_slow_quote_does_not_prevent_one_minute_work_from_running(self) -> None:
        quote_entered = Event()
        release_quote = Event()
        one_minute_completed = Event()

        def concurrent_refresh(
            kind,
            spec,
            *,
            observed_at,
            latest_data_time,
        ):
            if kind is LiveRefreshKind.QUOTE:
                quote_entered.set()
                release_quote.wait(timeout=1)
            elif kind is LiveRefreshKind.ONE_MINUTE:
                one_minute_completed.set()
            return LiveRefreshResult.no_change()

        self.input.refresh = concurrent_refresh  # type: ignore[method-assign]

        from threading import Thread

        thread = Thread(target=lambda: self.scheduler.run_due(self.t0))
        thread.start()
        self.assertTrue(quote_entered.wait(timeout=1))
        self.assertTrue(one_minute_completed.wait(timeout=1))
        release_quote.set()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())

    def test_idle_profile_skips_provider_reads(self) -> None:
        self.scheduler.run_due(self.t0)
        initial_calls = len(self.input.calls)
        states = self.scheduler.run_due(
            self.t0 + timedelta(seconds=1),
            polling_profile="idle",
        )
        self.assertEqual(len(self.input.calls), initial_calls)
        self.assertEqual(
            states[LiveRefreshKind.QUOTE].last_attempt_at,
            self.t0,
        )

    def test_reconciliation_runs_all_branches_once(self) -> None:
        self.input.queue(
            LiveRefreshKind.QUOTE,
            LiveRefreshResult.no_change(),
        )
        self.input.queue(
            LiveRefreshKind.ONE_MINUTE,
            LiveRefreshResult.no_change(),
        )
        self.input.queue(
            LiveRefreshKind.OFFICIAL_FIVE_MINUTE,
            LiveRefreshResult.no_change(),
        )
        self.scheduler.set_polling_profile("idle")
        self.scheduler.run_reconciliation(self.t0 + timedelta(hours=6))
        self.assertEqual(len(self.input.calls), 3)
        self.assertEqual(self.scheduler.polling_profile, "idle")

    def test_stale_market_epoch_result_is_discarded(self) -> None:
        class _EpochInput(_FakeInput):
            @property
            def market_epoch(self) -> int:
                return 2

        epoch_input = _EpochInput()
        epoch_input.queue(
            LiveRefreshKind.ONE_MINUTE,
            LiveRefreshResult(
                self.t0 + timedelta(minutes=1),
                (_update(LiveRefreshKind.ONE_MINUTE),),
                market_epoch=1,
            ),
        )
        scheduler = LiveRefreshScheduler(
            self.spec,
            epoch_input,
            self.executor,
            on_update=self.updates.append,
            intervals=self.intervals,
        )
        self.addCleanup(scheduler.retire)

        scheduler.retry(LiveRefreshKind.ONE_MINUTE, self.t0)
        self.assertEqual(self.updates, [])
        state = scheduler.state_for(LiveRefreshKind.ONE_MINUTE)
        self.assertIsNone(state.latest_data_time)

    def test_stale_epoch_skips_watermark_failure(self) -> None:
        class _EpochInput(_FakeInput):
            @property
            def market_epoch(self) -> int:
                return 1

        epoch_input = _EpochInput()
        monday = datetime(2026, 7, 27, 9, 31)
        scheduler = LiveRefreshScheduler(
            self.spec,
            epoch_input,
            self.executor,
            on_update=self.updates.append,
            intervals=self.intervals,
            on_failure=lambda kind, exc, _epoch=None: self.failures.append((kind, exc)),
            initial_data_times={LiveRefreshKind.ONE_MINUTE: monday},
        )
        self.addCleanup(scheduler.retire)
        epoch_input.queue(
            LiveRefreshKind.ONE_MINUTE,
            LiveRefreshResult(
                datetime(2026, 7, 24, 15, 1),
                (_update(LiveRefreshKind.ONE_MINUTE),),
                market_epoch=0,
            ),
        )

        state = scheduler.retry(LiveRefreshKind.ONE_MINUTE, monday)

        self.assertEqual(self.updates, [])
        self.assertEqual(self.failures, [])
        self.assertEqual(state.latest_data_time, monday)
        self.assertEqual(state.consecutive_failures, 0)
        self.assertIsNone(state.last_failure)

    def test_manual_retry_only_runs_requested_branch_and_keeps_other_schedules(self) -> None:
        self.scheduler.run_due(self.t0)
        initial_calls = len(self.input.calls)
        one_minute_next = self.scheduler.state_for(
            LiveRefreshKind.ONE_MINUTE
        ).next_due_at

        retried = self.scheduler.retry(
            LiveRefreshKind.QUOTE,
            self.t0 + timedelta(seconds=1),
        )

        self.assertEqual(len(self.input.calls), initial_calls + 1)
        self.assertEqual(self.input.calls[-1][0], LiveRefreshKind.QUOTE)
        self.assertEqual(
            self.scheduler.state_for(
                LiveRefreshKind.ONE_MINUTE
            ).next_due_at,
            one_minute_next,
        )
        self.assertEqual(
            retried.next_due_at, self.t0 + timedelta(seconds=3)
        )

    def test_consecutive_failures_back_off_without_losing_last_success(self) -> None:
        newest = self.t0 + timedelta(minutes=1)
        self.input.queue(
            LiveRefreshKind.ONE_MINUTE,
            LiveRefreshResult(newest, (_update(LiveRefreshKind.ONE_MINUTE),)),
            RuntimeError("provider failed once"),
            RuntimeError("provider failed twice"),
        )
        scheduler = LiveRefreshScheduler(
            self.spec,
            self.input,
            self.executor,
            on_update=self.updates.append,
            intervals=self.intervals,
            backoff=LiveRefreshBackoff(multiplier=2, maximum=timedelta(seconds=60)),
        )
        self.addCleanup(scheduler.retire)

        scheduler.retry(LiveRefreshKind.ONE_MINUTE, self.t0)
        first_failure = scheduler.retry(
            LiveRefreshKind.ONE_MINUTE,
            self.t0 + timedelta(seconds=1),
        )
        second_failure = scheduler.retry(
            LiveRefreshKind.ONE_MINUTE,
            self.t0 + timedelta(seconds=2),
        )

        self.assertEqual(first_failure.latest_data_time, newest)
        self.assertEqual(first_failure.consecutive_failures, 1)
        self.assertEqual(
            first_failure.next_due_at,
            self.t0 + timedelta(seconds=21),
        )
        self.assertEqual(second_failure.latest_data_time, newest)
        self.assertEqual(second_failure.consecutive_failures, 2)
        self.assertEqual(
            second_failure.next_due_at,
            self.t0 + timedelta(seconds=42),
        )

    def test_manual_retry_bypasses_backoff_and_success_resets_it(self) -> None:
        self.input.queue(
            LiveRefreshKind.QUOTE,
            RuntimeError("provider failed"),
            LiveRefreshResult.no_change(),
        )
        scheduler = LiveRefreshScheduler(
            self.spec,
            self.input,
            self.executor,
            on_update=self.updates.append,
            intervals=self.intervals,
            backoff=LiveRefreshBackoff(multiplier=4, maximum=timedelta(seconds=60)),
        )
        self.addCleanup(scheduler.retire)

        failed = scheduler.retry(LiveRefreshKind.QUOTE, self.t0)
        self.assertEqual(failed.consecutive_failures, 1)
        self.assertEqual(failed.next_due_at, self.t0 + timedelta(seconds=8))

        scheduler.run_due(self.t0 + timedelta(seconds=2))
        self.assertEqual(
            [call[0] for call in self.input.calls].count(LiveRefreshKind.QUOTE),
            1,
        )

        recovered = scheduler.retry(
            LiveRefreshKind.QUOTE,
            self.t0 + timedelta(seconds=3),
        )
        self.assertEqual(
            [call[0] for call in self.input.calls].count(LiveRefreshKind.QUOTE),
            2,
        )
        self.assertEqual(recovered.consecutive_failures, 0)
        self.assertIsNone(recovered.last_failure)
        self.assertEqual(
            recovered.next_due_at,
            self.t0 + timedelta(seconds=5),
        )

    def test_watermark_never_moves_backwards_and_previous_success_is_kept(self) -> None:
        newest = self.t0 + timedelta(minutes=1)
        self.input.queue(
            LiveRefreshKind.ONE_MINUTE,
            LiveRefreshResult(newest, (_update(LiveRefreshKind.ONE_MINUTE),)),
            LiveRefreshResult(
                newest - timedelta(seconds=1),
                (_update(LiveRefreshKind.ONE_MINUTE),),
            ),
        )
        self.scheduler.run_due(self.t0)

        state = self.scheduler.retry(
            LiveRefreshKind.ONE_MINUTE,
            self.t0 + timedelta(seconds=1),
        )

        self.assertEqual(state.latest_data_time, newest)
        self.assertIsInstance(state.last_failure, LiveRefreshValidationError)

    def test_retire_rejects_an_inflight_result_and_future_ticks(self) -> None:
        entered = Event()
        release = Event()
        original_refresh = self.input.refresh

        def blocking_refresh(*args, **kwargs):
            if args[0] is LiveRefreshKind.QUOTE:
                entered.set()
                release.wait(timeout=1)
            return original_refresh(*args, **kwargs)

        self.input.refresh = blocking_refresh  # type: ignore[method-assign]
        thread_done = Event()

        def run() -> None:
            self.scheduler.run_due(self.t0)
            thread_done.set()

        from threading import Thread

        thread = Thread(target=run)
        thread.start()
        self.assertTrue(entered.wait(timeout=1))
        self.assertTrue(
            self.scheduler.state_for(LiveRefreshKind.QUOTE).in_flight
        )
        calls_before_retry = len(self.input.calls)
        self.scheduler.retry(LiveRefreshKind.QUOTE, self.t0)
        self.assertEqual(len(self.input.calls), calls_before_retry)
        self.scheduler.retire()
        release.set()
        self.assertTrue(thread_done.wait(timeout=1))
        thread.join(timeout=1)

        self.assertEqual(self.updates, [])
        calls_after_retire = len(self.input.calls)
        self.scheduler.run_due(self.t0 + timedelta(hours=1))
        self.assertEqual(len(self.input.calls), calls_after_retire)

    def test_rejects_cross_session_and_branch_incompatible_updates(self) -> None:
        bad_identity = _update(
            LiveRefreshKind.QUOTE,
            session_id="other-live",
        )
        self.input.queue(
            LiveRefreshKind.QUOTE,
            LiveRefreshResult(self.t0, (bad_identity,)),
        )
        self.scheduler.retry(LiveRefreshKind.QUOTE, self.t0)
        self.assertIsInstance(
            self.scheduler.state_for(LiveRefreshKind.QUOTE).last_failure,
            LiveRefreshValidationError,
        )

        bad_event = LiveIncrementalUpdate(
            session_id="live-1",
            generation=7,
            event_type="chan_analysis_replaced",
            payload={},
        )
        self.input.queue(
            LiveRefreshKind.ONE_MINUTE,
            LiveRefreshResult(self.t0, (bad_event,)),
        )
        self.scheduler.retry(LiveRefreshKind.ONE_MINUTE, self.t0)
        self.assertIsInstance(
            self.scheduler.state_for(LiveRefreshKind.ONE_MINUTE).last_failure,
            LiveRefreshValidationError,
        )


class LiveRefreshValidationTests(unittest.TestCase):
    def test_intervals_must_be_positive(self) -> None:
        with self.assertRaises(LiveRefreshValidationError):
            LiveRefreshIntervals(quote=timedelta(0))

    def test_requires_live_session_spec(self) -> None:
        executor = BoundedComputationExecutor(capacity=2, worker_count=1)
        self.addCleanup(
            lambda: executor.shutdown(cancel_pending=True, wait=True)
        )
        with self.assertRaises(LiveRefreshValidationError):
            LiveRefreshScheduler(
                SessionSpec(
                    session_id="replay-1",
                    session_type=SessionType.REPLAY,
                    symbol="sh.600000",
                    generation=1,
                    trade_date="2026-07-24",
                ),
                _FakeInput(),
                executor,
                on_update=lambda update: None,
            )

    def test_initial_watermarks_are_forwarded_to_first_refresh(self) -> None:
        executor = BoundedComputationExecutor(capacity=3, worker_count=1)
        self.addCleanup(
            lambda: executor.shutdown(cancel_pending=True, wait=True)
        )
        input_port = _FakeInput()
        initial = datetime(2026, 7, 24, 9, 35)
        scheduler = LiveRefreshScheduler(
            SessionSpec(
                session_id="live-1",
                session_type=SessionType.LIVE,
                symbol="sh.600000",
                generation=1,
                trade_date=None,
            ),
            input_port,
            executor,
            on_update=lambda update: None,
            initial_data_times={LiveRefreshKind.OFFICIAL_FIVE_MINUTE: initial},
        )
        self.addCleanup(scheduler.retire)

        scheduler.retry(
            LiveRefreshKind.OFFICIAL_FIVE_MINUTE,
            datetime(2026, 7, 24, 9, 36),
        )

        self.assertEqual(input_port.calls[0][2], initial)


class _PausingFailureInput(_FakeInput):
    """Block one branch until released, then raise a provider failure."""

    def __init__(self, *, pause: Event, release: Event) -> None:
        super().__init__()
        self._pause = pause
        self._release = release
        self._epoch = 0

    @property
    def market_epoch(self) -> int:
        return self._epoch

    def set_market_epoch(self, epoch: int) -> None:
        self._epoch = epoch

    def refresh(
        self,
        kind: LiveRefreshKind,
        spec: SessionSpec,
        *,
        observed_at: datetime,
        latest_data_time: datetime | None,
    ) -> LiveRefreshResult:
        self.calls.append((kind, observed_at, latest_data_time))
        if kind is LiveRefreshKind.ONE_MINUTE:
            self._pause.set()
            self._release.wait(timeout=2)
            raise RuntimeError("stale provider failure")
        return LiveRefreshResult.no_change(market_epoch=self._epoch)


class StaleEpochRefreshFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = BoundedComputationExecutor(capacity=4, worker_count=2)
        self.updates: list[LiveIncrementalUpdate] = []
        self.failures: list[tuple[LiveRefreshKind, BaseException, int | None]] = []
        self.spec = SessionSpec(
            session_id="live-1",
            session_type=SessionType.LIVE,
            symbol="sh.600000",
            generation=7,
            trade_date=None,
        )
        self.t0 = datetime(2026, 7, 24, 9, 35)

    def test_stale_epoch_refresh_failure_does_not_pollute_new_epoch(self) -> None:
        pause = Event()
        release = Event()
        input_port = _PausingFailureInput(pause=pause, release=release)
        scheduler = LiveRefreshScheduler(
            self.spec,
            input_port,
            self.executor,
            on_update=self.updates.append,
            on_failure=lambda kind, exc, epoch: self.failures.append(
                (kind, exc, epoch)
            ),
            initial_data_times={LiveRefreshKind.ONE_MINUTE: self.t0},
        )
        self.addCleanup(scheduler.retire)

        thread = Thread(
            target=scheduler.retry,
            args=(LiveRefreshKind.ONE_MINUTE, self.t0),
        )
        thread.start()
        self.assertTrue(pause.wait(timeout=2))

        input_port.set_market_epoch(1)
        scheduler.reset_branch_watermarks(
            {LiveRefreshKind.ONE_MINUTE: datetime(2026, 7, 27, 9, 31)},
            market_epoch=1,
        )
        release.set()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

        state = scheduler.state_for(LiveRefreshKind.ONE_MINUTE)
        self.assertEqual(self.failures, [])
        self.assertEqual(state.consecutive_failures, 0)
        self.assertIsNone(state.last_failure)
        self.assertEqual(state.latest_data_time, datetime(2026, 7, 27, 9, 31))
        self.assertEqual(self.updates, [])


class _PausingRecordFailureScheduler(LiveRefreshScheduler):
    """Pause at _record_failure entry so day-switch can advance epoch first."""

    def __init__(
        self,
        *args,
        pause_at_failure: Event,
        release_failure: Event,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._pause_at_failure = pause_at_failure
        self._release_failure = release_failure

    def _record_failure(
        self,
        kind: LiveRefreshKind,
        observed_at: datetime,
        failure: BaseException,
        *,
        market_epoch: int | None = None,
    ) -> None:
        self._pause_at_failure.set()
        self._release_failure.wait(timeout=2)
        super()._record_failure(
            kind,
            observed_at,
            failure,
            market_epoch=market_epoch,
        )


class StaleEpochFailureToctouTests(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = BoundedComputationExecutor(capacity=4, worker_count=2)
        self.updates: list[LiveIncrementalUpdate] = []
        self.failures: list[tuple[LiveRefreshKind, BaseException, int | None]] = []
        self.published_failures: list[dict] = []
        self.spec = SessionSpec(
            session_id="live-1",
            session_type=SessionType.LIVE,
            symbol="sh.600000",
            generation=7,
            trade_date=None,
        )
        self.t0 = datetime(2026, 7, 24, 9, 35)

    def test_record_failure_epoch_check_is_linearized_with_branch_state(self) -> None:
        from packages.t0assistant.runtime.live_projection_store import (
            LiveProjectionStore,
        )

        class _Coordinator:
            def commit_if_accepted(self, *, session_type, session_id, generation, commit):
                commit()
                return True

        store = LiveProjectionStore(_Coordinator(), service_generation=1)
        pause_at_failure = Event()
        release_failure = Event()
        provider_pause = Event()
        provider_release = Event()
        input_port = _PausingFailureInput(
            pause=provider_pause,
            release=provider_release,
        )

        def on_failure(kind, exc, epoch):
            self.failures.append((kind, exc, epoch))
            accepted = store.accept_operation_failure(
                session_id=self.spec.session_id,
                generation=self.spec.generation,
                operation_id=f"live-refresh-{kind.value}",
                market_epoch=epoch,
                payload={"error_code": "calculation_failed"},
            )
            if accepted is not None:
                self.published_failures.append(accepted.to_envelope())

        scheduler = _PausingRecordFailureScheduler(
            self.spec,
            input_port,
            self.executor,
            on_update=self.updates.append,
            on_failure=on_failure,
            pause_at_failure=pause_at_failure,
            release_failure=release_failure,
            initial_data_times={LiveRefreshKind.ONE_MINUTE: self.t0},
        )
        self.addCleanup(scheduler.retire)

        thread = Thread(
            target=scheduler.retry,
            args=(LiveRefreshKind.ONE_MINUTE, self.t0),
        )
        thread.start()
        self.assertTrue(provider_pause.wait(timeout=2))
        provider_release.set()
        self.assertTrue(pause_at_failure.wait(timeout=2))

        scheduler.reset_branch_watermarks(
            {LiveRefreshKind.ONE_MINUTE: datetime(2026, 7, 27, 9, 31)},
            market_epoch=1,
        )
        release_failure.set()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

        state = scheduler.state_for(LiveRefreshKind.ONE_MINUTE)
        self.assertEqual(self.failures, [])
        self.assertEqual(self.published_failures, [])
        self.assertEqual(state.consecutive_failures, 0)
        self.assertIsNone(state.last_failure)
        self.assertEqual(state.latest_data_time, datetime(2026, 7, 27, 9, 31))
        self.assertIsNone(store.current_revision)


if __name__ == "__main__":
    unittest.main()
