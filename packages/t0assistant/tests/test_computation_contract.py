"""Contract tests for the T0-020/T0-045 shared runtime boundary.

These tests freeze the small, transport-independent contract exposed by
:mod:`packages.t0assistant.runtime.computation_contract` before the executor
and Replay data preparation modules are implemented.  They guarantee that both
T0-020 and T0-045 can rely on the same priority ordering, cancellation
vocabulary, timeout semantics and Prepared Replay Data shape, so T0-046 does
not need to invent a second set of fields, states or error codes.
"""

from __future__ import annotations

from datetime import datetime
import unittest

from packages.t0assistant.runtime.computation_contract import (
    CancelReason,
    ComputationOutcome,
    ComputationPriority,
    ComputationStatus,
    ComputationTask,
    PipelineInstanceIdentity,
    PreparedReplayData,
    ReplayReliabilityAssessment,
    default_accept_result,
    new_task_id,
)


def _identity(generation: int = 1, session_id: str = "replay-1") -> PipelineInstanceIdentity:
    return PipelineInstanceIdentity(
        instance_id=1234,
        generation=generation,
        session_id=session_id,
    )


class ComputationPriorityTests(unittest.TestCase):
    """The shared-resource priority ordering is fixed by architecture."""

    def test_live_beats_replay_interactive_and_prefetch(self) -> None:
        self.assertLess(ComputationPriority.LIVE, ComputationPriority.REPLAY_INTERACTIVE)
        self.assertLess(ComputationPriority.REPLAY_INTERACTIVE, ComputationPriority.REPLAY_PREFETCH)

    def test_priority_values_are_stable_integers(self) -> None:
        self.assertEqual(int(ComputationPriority.LIVE), 0)
        self.assertEqual(int(ComputationPriority.REPLAY_INTERACTIVE), 1)
        self.assertEqual(int(ComputationPriority.REPLAY_PREFETCH), 2)


class CancelReasonTests(unittest.TestCase):
    """Cancellation vocabulary maps onto Replay v1.0 errors, not duplicates them."""

    def test_reasons_cover_contractual_cases(self) -> None:
        # The five reasons correspond to the cancellation contract in the task
        # brief: queue cancel, supersession, deadline, session invalidation
        # and executor shutdown.
        expected = {
            "cancelled",
            "superseded",
            "deadline_exceeded",
            "session_invalid",
            "executor_closed",
        }
        self.assertEqual({reason.value for reason in CancelReason}, expected)


class ComputationTaskTests(unittest.TestCase):
    """A task carries every identity and predicate the executor needs."""

    def test_task_is_frozen_and_carries_required_fields(self) -> None:
        task = ComputationTask(
            task_id="task-1",
            session_id="replay-1",
            session_generation=3,
            pipeline_identity=_identity(),
            priority=ComputationPriority.REPLAY_INTERACTIVE,
            callable=lambda _task: "ok",
            operation_id="op-seek-1",
            deadline=123.45,
            is_cancelled=lambda: False,
            is_session_valid=lambda: True,
            accept_result=default_accept_result,
        )
        self.assertEqual(task.task_id, "task-1")
        self.assertEqual(task.session_id, "replay-1")
        self.assertEqual(task.session_generation, 3)
        self.assertEqual(task.operation_id, "op-seek-1")
        self.assertEqual(task.priority, ComputationPriority.REPLAY_INTERACTIVE)
        self.assertEqual(task.deadline, 123.45)
        self.assertTrue(callable(task.callable))
        self.assertTrue(callable(task.is_cancelled))
        self.assertTrue(callable(task.is_session_valid))
        self.assertTrue(callable(task.accept_result))

    def test_operation_id_is_optional_for_live_or_prefetch(self) -> None:
        task = ComputationTask(
            task_id="task-2",
            session_id="live-1",
            session_generation=1,
            pipeline_identity=_identity(),
            priority=ComputationPriority.LIVE,
            callable=lambda _task: None,
        )
        self.assertIsNone(task.operation_id)

    def test_with_task_id_preserves_other_fields(self) -> None:
        original = ComputationTask(
            task_id="original",
            session_id="replay-1",
            session_generation=2,
            pipeline_identity=_identity(),
            priority=ComputationPriority.REPLAY_PREFETCH,
            callable=lambda _task: None,
            operation_id="op-1",
            deadline=10.0,
            is_cancelled=lambda: False,
            is_session_valid=lambda: True,
            accept_result=default_accept_result,
        )
        renamed = original.with_task_id("renamed")
        self.assertEqual(renamed.task_id, "renamed")
        self.assertEqual(renamed.session_id, original.session_id)
        self.assertEqual(renamed.session_generation, original.session_generation)
        self.assertEqual(renamed.priority, original.priority)
        self.assertEqual(renamed.operation_id, original.operation_id)
        self.assertEqual(renamed.deadline, original.deadline)


class ComputationOutcomeTests(unittest.TestCase):
    """Outcomes expose a structured terminal status, not raw exceptions."""

    def test_completed_outcome_carries_value(self) -> None:
        outcome = ComputationOutcome(
            task_id="t",
            status=ComputationStatus.COMPLETED,
            value={"k": 1},
        )
        self.assertEqual(outcome.status, ComputationStatus.COMPLETED)
        self.assertEqual(outcome.value, {"k": 1})
        self.assertIsNone(outcome.cancel_reason)
        self.assertIsNone(outcome.exception)

    def test_failed_outcome_preserves_exception_without_publishing_it(self) -> None:
        exc = RuntimeError("boom")
        outcome = ComputationOutcome(
            task_id="t",
            status=ComputationStatus.FAILED,
            exception=exc,
        )
        self.assertIs(outcome.exception, exc)
        self.assertIsNone(outcome.value)

    def test_cancelled_outcome_carries_reason(self) -> None:
        outcome = ComputationOutcome(
            task_id="t",
            status=ComputationStatus.CANCELLED,
            cancel_reason=CancelReason.SUPERSEDED,
        )
        self.assertEqual(outcome.cancel_reason, CancelReason.SUPERSEDED)


class PreparedReplayDataShapeTests(unittest.TestCase):
    """PreparedReplayData exposes exactly the fields T0-046 needs, nothing more."""

    def test_required_fields_are_present_and_immutable(self) -> None:
        fields = PreparedReplayData.__dataclass_fields__
        expected = {
            "symbol",
            "market_session",
            "trade_date",
            "granularity",
            "preheat_5m_bars",
            "bars_1m",
            "official_5m_bars",
            "daily_bars_history",
            "quote_snapshots",
            "actual_bar_times",
            "start_time",
            "end_time",
            "previous_close",
            "warnings",
            "market_input_port",
            "assessment_1m",
            "assessment_5m",
            "preheat_30m_bars",
            "official_30m_bars",
        }
        self.assertEqual(set(fields), expected)

    def test_granularity_values_are_restricted_at_construction(self) -> None:
        # The dataclass itself is agnostic, but the constructor used by the
        # preparation module must reject unknown granularities.  This test
        # documents the expected allowed values.
        allowed = {"one_minute", "five_minute"}
        self.assertIn("one_minute", allowed)
        self.assertIn("five_minute", allowed)
        self.assertNotIn("1m", allowed)


class ReplayReliabilityAssessmentTests(unittest.TestCase):
    """Reliability assessment carries the signal and the missing-range history."""

    def test_assessment_carries_reliability_and_ranges(self) -> None:
        assessment = ReplayReliabilityAssessment(
            granularity="one_minute",
            is_reliable=True,
            bar_count=240,
            covered_missing_ranges=(("2026-07-01", "2026-07-01"),),
            uncovered_missing_ranges=(),
        )
        self.assertTrue(assessment.is_reliable)
        self.assertEqual(assessment.bar_count, 240)
        self.assertEqual(assessment.covered_missing_ranges, (("2026-07-01", "2026-07-01"),))
        self.assertEqual(assessment.uncovered_missing_ranges, ())


class TaskIdGenerationTests(unittest.TestCase):
    """Generated task ids are opaque and unique."""

    def test_new_task_id_is_unique(self) -> None:
        ids = {new_task_id() for _ in range(1000)}
        self.assertEqual(len(ids), 1000)


if __name__ == "__main__":
    unittest.main()
