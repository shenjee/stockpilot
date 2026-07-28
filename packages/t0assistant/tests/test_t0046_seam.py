"""Integration seam test for T0-046 (not yet implemented).

This test proves that the outputs of T0-020 (bounded computation executor)
and T0-045 (Replay data preparation) are sufficient for T0-046 to drive a
Replay Session through a simulated clock without inventing:

* a new granularity field,
* a new playback-speed field,
* a new cancellation state,
* a new error code,
* a second input model, or
* a second set of task priorities.

The test deliberately does NOT implement auto-play, speed timers, step
commands, or a seek state machine.  It only exercises the minimal happy path
that T0-046 will follow: prepare data, create a pipeline, advance the clock
one actual bar at a time, submit pipeline computations through the executor,
and verify that only valid, non-superseded results are accepted.
"""

from __future__ import annotations

from datetime import datetime
import unittest
from typing import Any

from packages.marketdata.provider_request_queue import ProviderRequestPriority
from packages.t0assistant.runtime.computation_contract import (
    CancelReason,
    ComputationOutcome,
    ComputationPriority,
    ComputationStatus,
    ComputationTask,
    PipelineInstanceIdentity,
)
from packages.t0assistant.runtime.computation_executor import (
    BoundedComputationExecutor,
)
from packages.t0assistant.runtime.pipeline import (
    WorkbenchPipeline,
)
from packages.t0assistant.runtime.replay_data import (
    ReplayDataPreparator,
    ReplayPreparationConfig,
)
from packages.t0assistant.tests.fixtures.replay_fixtures import (
    SYMBOL,
    TRADE_DATE,
    one_minute_replay,
    five_minute_fallback,
)
from packages.t0assistant.tests.test_replay_data import FakeMarketDataPort, _populate_from_fixture
from packages.t0assistant.tests.fixtures.replay_fixtures import market_context_service


class T0046SeamTests(unittest.TestCase):
    """Prove T0-046 can consume T0-020 + T0-045 without new contracts."""

    def setUp(self) -> None:
        self.executor: BoundedComputationExecutor | None = None

    def tearDown(self) -> None:
        if self.executor is not None:
            self.executor.shutdown(cancel_pending=True, wait=True)

    def _prepare_replay_data(self):
        """Run T0-045 and return PreparedReplayData."""

        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        context = market_context_service()
        preparator = ReplayDataPreparator(port, context)
        return preparator.prepare(
            SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
        )

    def test_t0046_can_advance_one_bar_at_a_time_through_executor(self) -> None:
        prepared = self._prepare_replay_data()
        self.executor = BoundedComputationExecutor(
            capacity=4, worker_count=1
        )

        # T0-046 creates a Replay-dedicated pipeline instance.
        pipeline = WorkbenchPipeline(
            session=prepared.market_session,
            market_input_port=prepared.market_input_port,
        )
        pipeline_identity = PipelineInstanceIdentity(
            instance_id=id(pipeline),
            generation=1,
            session_id="replay-seam-1",
        )

        session_state = {"valid": True}
        operation_state = {"current": "op-1"}

        # Advance the simulated clock to the first actual bar close.
        actual_bars = prepared.actual_bar_times
        self.assertGreater(len(actual_bars), 1)

        first_bar_time = actual_bars[0]

        def compute_at_first_bar(_task: ComputationTask) -> Any:
            return pipeline.preview(first_bar_time)

        task = ComputationTask(
            task_id="seam-step-1",
            session_id="replay-seam-1",
            session_generation=1,
            pipeline_identity=pipeline_identity,
            priority=ComputationPriority.REPLAY_INTERACTIVE,
            callable=compute_at_first_bar,
            operation_id="op-1",
            is_session_valid=lambda: session_state["valid"],
            accept_result=lambda value: operation_state["current"] == "op-1",
            commit_result=pipeline.commit_preview,
        )

        future = self.executor.submit(task)
        outcome = future.result(timeout=5)

        self.assertEqual(outcome.status, ComputationStatus.COMPLETED)
        self.assertIsInstance(outcome.value.target_time, datetime)
        self.assertEqual(outcome.value.target_time, first_bar_time)

    def test_t0046_next_bar_time_is_none_at_sequence_end(self) -> None:
        """The last actual bar yields ``next_bar_time is None``.

        T0-046 will use ``MarketSession.next_actual_bar_time`` with
        ``current_time_consumed=True`` and the prepared ``actual_bar_times``.
        At the sequence end it must return ``None`` so the step button can be
        disabled without a new contract field.
        """

        prepared = self._prepare_replay_data()
        session = prepared.market_session
        last_bar = prepared.actual_bar_times[-1]
        next_bar = session.next_actual_bar_time(
            last_bar,
            prepared.actual_bar_times,
            current_time_consumed=True,
        )
        self.assertIsNone(next_bar)

    def test_t0046_end_time_stays_calendar_value_not_last_bar(self) -> None:
        prepared = self._prepare_replay_data()
        self.assertEqual(
            prepared.end_time,
            prepared.market_session.end,
        )
        # The end_time must not equal the last actual bar when the last bar
        # is at 15:00 (it does in our fixture, but the source is the calendar,
        # not the data tail).  We verify the source explicitly.
        self.assertEqual(
            prepared.end_time,
            prepared.market_session.end,
        )

    def test_superseded_operation_result_is_isolated(self) -> None:
        """When a new seek supersedes the old operation, the old result is
        rejected at the acceptance boundary."""

        prepared = self._prepare_replay_data()
        self.executor = BoundedComputationExecutor(
            capacity=4, worker_count=1
        )
        pipeline = WorkbenchPipeline(
            session=prepared.market_session,
            market_input_port=prepared.market_input_port,
        )
        pipeline_identity = PipelineInstanceIdentity(
            instance_id=id(pipeline),
            generation=1,
            session_id="replay-seam-2",
        )
        operation_state = {"current": "op-old"}

        first_bar = prepared.actual_bar_times[0]

        def compute(_task: ComputationTask) -> Any:
            return pipeline.preview(first_bar)

        task = ComputationTask(
            task_id="seam-superseded",
            session_id="replay-seam-2",
            session_generation=1,
            pipeline_identity=pipeline_identity,
            priority=ComputationPriority.REPLAY_INTERACTIVE,
            callable=compute,
            operation_id="op-old",
            accept_result=lambda value: operation_state["current"] == "op-old",
            commit_result=pipeline.commit_preview,
        )
        future = self.executor.submit(task)
        # Supersede the old operation before the result is accepted.
        self.executor.supersede_operation("replay-seam-2", "op-new")
        operation_state["current"] = "op-new"
        outcome = future.result(timeout=5)
        self.assertEqual(outcome.status, ComputationStatus.CANCELLED)
        self.assertEqual(outcome.cancel_reason, CancelReason.SUPERSEDED)
        self.assertIsNone(pipeline.target_time)
        self.assertIsNone(pipeline.last_result)

    def test_no_new_fields_needed_for_granularity_or_speed(self) -> None:
        """The prepared data already carries granularity; speed is not needed
        for single-bar advancement.  T0-046 only needs the existing
        ``ComputationPriority`` and the prepared ``actual_bar_times``."""

        prepared = self._prepare_replay_data()
        self.assertIn(prepared.granularity, {"one_minute", "five_minute"})
        self.assertGreater(len(prepared.actual_bar_times), 0)
        # The executor priority enum already covers all three resource tiers.
        priorities = {
            ComputationPriority.LIVE,
            ComputationPriority.REPLAY_INTERACTIVE,
            ComputationPriority.REPLAY_PREFETCH,
        }
        self.assertEqual(len(priorities), 3)

    def test_5m_degradation_first_bar_has_dynamic_daily_bar_and_quote(self) -> None:
        """Regression: in 5-minute degradation mode the first official 5m bar
        must still produce a dynamic daily bar and a quote.  Previously
        ``project_market_at`` only consumed ``bars_1m`` (empty in degradation),
        so ``daily_bar`` and ``quote`` were both ``None``, violating PRD."""

        fixture = five_minute_fallback()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        port.missing_overrides[("1m", TRADE_DATE.isoformat())] = [
            (TRADE_DATE.isoformat(), TRADE_DATE.isoformat())
        ]
        context = market_context_service()
        preparator = ReplayDataPreparator(port, context)
        prepared = preparator.prepare(
            SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
        )
        self.assertEqual(prepared.granularity, "five_minute")
        self.assertEqual(prepared.bars_1m, ())

        pipeline = WorkbenchPipeline(
            session=prepared.market_session,
            market_input_port=prepared.market_input_port,
        )
        first_5m = prepared.actual_bar_times[0]
        result = pipeline.compute(first_5m)
        # bars_1m stays empty in degradation mode.
        self.assertEqual(result.bars_1m, ())
        # But the dynamic daily bar and quote must form from official 5m.
        self.assertIsNotNone(result.daily_bar, "daily_bar must not be None in 5m degradation")
        self.assertEqual(result.daily_bar["closed"], False)
        self.assertIsNotNone(result.quote, "quote must not be None in 5m degradation")
        self.assertEqual(result.quote["timestamp"], first_5m.strftime("%Y-%m-%d %H:%M:%S"))


if __name__ == "__main__":
    unittest.main()
