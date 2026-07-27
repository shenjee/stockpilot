import sys
from concurrent.futures import TimeoutError as FutureTimeoutError
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages"))

from marketdata.provider_request_queue import (  # noqa: E402
    ProviderQueueFullError,
    ProviderRequestPriority,
    ProviderRequestQueue,
)
from marketdata.provider_result import MarketDataResult, ProviderIssue  # noqa: E402


class ProviderRequestQueueTests(unittest.TestCase):
    def setUp(self):
        self.queue = ProviderRequestQueue(capacity=8)

    def tearDown(self):
        self.queue.shutdown(cancel_pending=True)

    def test_live_request_runs_before_queued_replay_work(self):
        blocker_started = threading.Event()
        release_blocker = threading.Event()
        order = []

        def blocker():
            blocker_started.set()
            release_blocker.wait(2)
            return "blocker"

        first = self.queue.submit("blocker", blocker)
        self.assertTrue(blocker_started.wait(1))
        replay = self.queue.submit(
            "replay",
            lambda: order.append("replay") or "replay",
            priority=ProviderRequestPriority.REPLAY_PREFETCH,
        )
        live = self.queue.submit(
            "live",
            lambda: order.append("live") or "live",
            priority=ProviderRequestPriority.LIVE,
        )

        release_blocker.set()
        self.assertEqual(first.result(1).result, "blocker")
        self.assertEqual(live.result(1).result, "live")
        self.assertEqual(replay.result(1).result, "replay")
        self.assertEqual(order, ["live", "replay"])

    def test_identical_requests_share_one_provider_operation(self):
        started = threading.Event()
        release = threading.Event()
        call_count = 0

        def operation():
            nonlocal call_count
            call_count += 1
            started.set()
            release.wait(2)
            return MarketDataResult(success=True, data=[{"date": "2026-07-24"}])

        first = self.queue.submit(("kline", "600519"), operation)
        self.assertTrue(started.wait(1))
        second = self.queue.submit(
            ("kline", "600519"),
            lambda: self.fail("coalesced operation must not execute"),
        )
        release.set()

        first_outcome = first.result(1)
        second_outcome = second.result(1)
        self.assertEqual(call_count, 1)
        self.assertFalse(first_outcome.coalesced)
        self.assertTrue(second_outcome.coalesced)
        self.assertIs(first_outcome.result, second_outcome.result)

    def test_live_subscriber_promotes_queued_replay_request(self):
        blocker_started = threading.Event()
        release_blocker = threading.Event()
        order = []

        def blocker():
            blocker_started.set()
            release_blocker.wait(2)
            return "blocker"

        first = self.queue.submit("blocker", blocker)
        self.assertTrue(blocker_started.wait(1))
        replay_target = self.queue.submit(
            "shared-target",
            lambda: order.append("shared-target") or "shared-target",
            priority=ProviderRequestPriority.REPLAY_PREFETCH,
        )
        other_replay = self.queue.submit(
            "other-replay",
            lambda: order.append("other-replay") or "other-replay",
            priority=ProviderRequestPriority.REPLAY_INTERACTIVE,
        )
        live_target = self.queue.submit(
            "shared-target",
            lambda: self.fail("coalesced Live operation must not execute"),
            priority=ProviderRequestPriority.LIVE,
        )

        release_blocker.set()
        self.assertEqual(first.result(1).result, "blocker")
        self.assertEqual(live_target.result(1).result, "shared-target")
        self.assertEqual(replay_target.result(1).result, "shared-target")
        self.assertEqual(other_replay.result(1).result, "other-replay")
        self.assertEqual(order, ["shared-target", "other-replay"])

    def test_capacity_counts_unique_work_but_allows_coalescing(self):
        queue = ProviderRequestQueue(capacity=1)
        started = threading.Event()
        release = threading.Event()
        try:
            def operation():
                started.set()
                release.wait(2)
                return "done"

            first = queue.submit(
                "same",
                operation,
            )
            self.assertTrue(started.wait(1))
            joined = queue.submit("same", lambda: "not-used")
            with self.assertRaises(ProviderQueueFullError):
                queue.submit("different", lambda: "different")
            release.set()
            self.assertEqual(first.result(1).result, "done")
            self.assertEqual(joined.result(1).result, "done")
        finally:
            release.set()
            queue.shutdown(cancel_pending=True)

    def test_retry_is_coordinated_once_for_all_subscribers(self):
        attempts = 0

        def operation():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return MarketDataResult(
                    success=False,
                    data=[],
                    issues=[
                        ProviderIssue(
                            level="error",
                            reason_code="request_failed",
                            message="temporary",
                        )
                    ],
                )
            return MarketDataResult(success=True, data=["ok"])

        outcome = self.queue.execute("retry", operation, max_attempts=2, timeout=1)

        self.assertEqual(attempts, 2)
        self.assertTrue(outcome.result.success)
        self.assertEqual(outcome.result.data, ["ok"])

    def test_retry_coordinates_legacy_provider_exception(self):
        attempts = 0

        def operation():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TimeoutError("temporary")
            return "ok"

        outcome = self.queue.execute(
            "legacy-exception",
            operation,
            max_attempts=2,
            timeout=1,
        )

        self.assertEqual(attempts, 2)
        self.assertEqual(outcome.result, "ok")

    def test_first_submission_owns_retry_policy_for_coalesced_request(self):
        started = threading.Event()
        release = threading.Event()
        attempts = 0

        def operation():
            nonlocal attempts
            attempts += 1
            started.set()
            release.wait(2)
            return MarketDataResult(success=False, data=[])

        first = self.queue.submit("retry-owner", operation, max_attempts=1)
        self.assertTrue(started.wait(1))
        joined = self.queue.submit(
            "retry-owner",
            lambda: self.fail("coalesced operation must not execute"),
            max_attempts=3,
        )
        release.set()

        self.assertFalse(first.result(1).result.success)
        self.assertFalse(joined.result(1).result.success)
        self.assertEqual(attempts, 1)

    def test_retired_session_result_is_marked_not_publishable(self):
        started = threading.Event()
        release = threading.Event()
        active = True

        def operation():
            started.set()
            release.wait(2)
            return MarketDataResult(success=True, data=["cacheable"])

        future = self.queue.submit(
            "late-result",
            operation,
            session_validator=lambda: active,
        )
        self.assertTrue(started.wait(1))
        active = False
        release.set()

        outcome = future.result(1)
        self.assertTrue(outcome.executed)
        self.assertFalse(outcome.session_valid)
        self.assertEqual(outcome.result.data, ["cacheable"])

    def test_request_is_skipped_when_session_is_already_retired(self):
        called = False

        def operation():
            nonlocal called
            called = True

        outcome = self.queue.execute(
            "retired-before-start",
            operation,
            session_validator=lambda: False,
            timeout=1,
        )

        self.assertFalse(called)
        self.assertFalse(outcome.executed)
        self.assertFalse(outcome.session_valid)
        self.assertIsNone(outcome.result)

    def test_execute_stops_waiting_when_session_retires_mid_flight(self):
        started = threading.Event()
        release = threading.Event()
        retired = {"value": False}
        outcome_holder = {}

        def operation():
            started.set()
            release.wait(2)
            return MarketDataResult(success=True, data=["late"])

        def run_execute():
            outcome_holder["outcome"] = self.queue.execute(
                "retire-mid-flight",
                operation,
                session_validator=lambda: not retired["value"],
                timeout=1,
            )

        thread = threading.Thread(target=run_execute)
        thread.start()
        self.assertTrue(started.wait(1))
        retired["value"] = True
        thread.join(1)
        self.assertFalse(thread.is_alive())
        outcome = outcome_holder["outcome"]
        self.assertTrue(outcome.executed)
        self.assertFalse(outcome.session_valid)
        self.assertIsNone(outcome.result)
        self.assertTrue(outcome.subscriber_detached)
        release.set()

    def test_execute_timeout_cancels_subscriber_wait_only(self):
        started = threading.Event()
        release = threading.Event()

        def operation():
            started.set()
            release.wait(2)
            return MarketDataResult(success=True, data=["late"])

        future = self.queue.submit("shared-timeout", operation)
        self.assertTrue(started.wait(1))
        with self.assertRaises(FutureTimeoutError):
            self.queue.execute(
                "shared-timeout",
                lambda: self.fail("second operation should not run"),
                timeout=0.05,
            )
        release.set()
        self.assertEqual(future.result(1).result.data, ["late"])

    def test_queued_timeout_detaches_only_subscriber_and_skips_unstarted_request(self):
        first_started = threading.Event()
        release_first = threading.Event()
        timed_out_started = threading.Event()

        def blocking_operation():
            first_started.set()
            release_first.wait(2)
            return "blocking"

        def should_not_run():
            timed_out_started.set()
            return "late"

        first = self.queue.submit("first-request", blocking_operation)
        self.assertTrue(first_started.wait(1))

        with self.assertRaises(FutureTimeoutError):
            self.queue.execute(
                "timed-out-request",
                should_not_run,
                timeout=0.05,
            )

        release_first.set()
        self.assertEqual(first.result(1).result, "blocking")
        self.assertFalse(timed_out_started.is_set())

    def test_coalesced_timeout_does_not_detach_other_valid_subscribers(self):
        started = threading.Event()
        release = threading.Event()
        completed = {}

        def operation():
            started.set()
            release.wait(2)
            return "shared"

        first_future = self.queue.submit("shared-request", operation)
        self.assertTrue(started.wait(1))

        def wait_with_timeout():
            with self.assertRaises(FutureTimeoutError):
                self.queue.execute(
                    "shared-request",
                    lambda: self.fail("coalesced operation must not execute"),
                    timeout=0.05,
                )
            completed["timed_out"] = True

        thread = threading.Thread(target=wait_with_timeout)
        thread.start()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertTrue(completed["timed_out"])

        release.set()
        self.assertEqual(first_future.result(1).result, "shared")

    def test_provider_timeout_error_does_not_spin_forever_with_session_validator(self):
        finished = {}

        def operation():
            raise TimeoutError("provider timeout")

        def run_execute():
            try:
                self.queue.execute(
                    "provider-timeout",
                    operation,
                    session_validator=lambda: True,
                )
            except TimeoutError as exc:
                finished["error"] = str(exc)

        thread = threading.Thread(target=run_execute)
        thread.start()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(finished["error"], "provider timeout")

    def test_detached_wait_does_not_stop_worker_from_processing_next_request(self):
        started = threading.Event()
        release = threading.Event()
        retired = {"value": False}
        order: list[str] = []

        def blocking_operation():
            started.set()
            release.wait(2)
            order.append("blocking")
            return "blocking"

        def second_operation():
            order.append("second")
            return "second"

        holder = {}

        def detach_waiter():
            holder["outcome"] = self.queue.execute(
                "detach-request",
                blocking_operation,
                session_validator=lambda: not retired["value"],
                timeout=1,
            )

        thread = threading.Thread(target=detach_waiter)
        thread.start()
        self.assertTrue(started.wait(1))
        retired["value"] = True
        thread.join(1)
        self.assertFalse(thread.is_alive())
        release.set()
        self.assertEqual(
            self.queue.execute("second-request", second_operation, timeout=1).result,
            "second",
        )
        self.assertEqual(holder["outcome"].subscriber_detached, True)


if __name__ == "__main__":
    unittest.main()
