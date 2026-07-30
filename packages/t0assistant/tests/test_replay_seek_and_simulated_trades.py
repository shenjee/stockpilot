from __future__ import annotations

import copy
from datetime import timedelta
import threading
from tempfile import TemporaryDirectory
import unittest

from packages.t0assistant.repositories.app_database import open_app_database
from packages.t0assistant.runtime.computation_executor import (
    BoundedComputationExecutor,
)
from packages.t0assistant.runtime.replay_clock import (
    NullPlaybackScheduler,
    SimulatedMonotonicClock,
)
from packages.t0assistant.runtime.replay_session import ReplaySession
from packages.t0assistant.trading.simulated_api import SimulatedTradeCommandApi
from packages.t0assistant.tests.test_replay_session import (
    _CachingAnalyzer,
    _default_analyze_5m,
    _prepare,
)


def _business_snapshot(snapshot: dict) -> dict:
    result = copy.deepcopy(snapshot)
    result["session"]["revision"] = 0
    return result


class ReplaySeekTests(unittest.TestCase):
    def setUp(self) -> None:
        self.executors: list[BoundedComputationExecutor] = []

    def tearDown(self) -> None:
        for executor in self.executors:
            executor.shutdown(cancel_pending=True, wait=True)

    def _session(self, *, trade_events: list | None = None) -> ReplaySession:
        prepared = _prepare("1m")
        executor = BoundedComputationExecutor(capacity=8, worker_count=1)
        self.executors.append(executor)
        if trade_events is None:
            trade_events = []
        return ReplaySession(
            "seek-session",
            1,
            prepared,
            executor,
            clock=SimulatedMonotonicClock(),
            scheduler=NullPlaybackScheduler(),
            analyzer=_CachingAnalyzer(_default_analyze_5m),
            on_trade_event=trade_events.append,
        )

    def test_forward_seek_consumes_only_target_prefix(self) -> None:
        session = self._session()
        target = session.start_time + timedelta(minutes=11)

        result = session.seek(target, "seek-forward")

        self.assertEqual(result.outcome_status, "completed")
        self.assertFalse(result.rebuilt)
        snapshot = session.snapshot()
        self.assertEqual(snapshot["replay"]["current_time"], target.strftime("%Y-%m-%d %H:%M:%S"))
        self.assertTrue(
            all(bar["timestamp"] <= snapshot["replay"]["current_time"] for bar in snapshot["market"]["bars_1m"])
        )
        self.assertTrue(
            all(point["timestamp"] <= snapshot["replay"]["current_time"] for point in snapshot["indicators"]["one_minute"]["vwap"])
        )

    def test_backward_seek_replaces_pipeline_and_removes_future_data(self) -> None:
        session = self._session()
        late = session.start_time + timedelta(minutes=40)
        early = session.start_time + timedelta(minutes=7)
        session.seek(late, "seek-late")
        old_pipeline = session._pipeline

        result = session.seek(early, "seek-early")

        self.assertTrue(result.rebuilt)
        self.assertIsNot(session._pipeline, old_pipeline)
        snapshot = session.snapshot()
        current = snapshot["replay"]["current_time"]
        self.assertTrue(
            all(bar["timestamp"] <= current for bar in snapshot["market"]["bars_1m"])
        )
        # A forming 5m candle may carry its future close label, but it is
        # explicitly unclosed and contains only the consumed 1m prefix.
        self.assertTrue(
            all(
                bar["timestamp"] <= current or bar["closed"] is False
                for bar in snapshot["market"]["bars_5m"]
            )
        )
        for series in snapshot["indicators"]["one_minute"]["macd"].values():
            if isinstance(series, list):
                self.assertTrue(all(point["timestamp"] <= current for point in series))

    def test_same_target_rebuild_is_deterministic(self) -> None:
        session = self._session()
        target = session.start_time + timedelta(minutes=26)
        session.seek(target, "seek-first")
        first = _business_snapshot(session.snapshot())
        session.seek(session.start_time, "seek-reset")
        session.seek(target, "seek-second")
        second = _business_snapshot(session.snapshot())
        self.assertEqual(first, second)

    def test_new_seek_supersedes_late_old_seek(self) -> None:
        prepared = _prepare("1m")
        executor = BoundedComputationExecutor(capacity=8, worker_count=2)
        self.executors.append(executor)
        started = threading.Event()
        release = threading.Event()
        delegate = _CachingAnalyzer(_default_analyze_5m)
        calls = 0

        def analyzer(bars, symbol):
            nonlocal calls
            calls += 1
            if calls == 3:
                started.set()
                release.wait(timeout=5)
            return delegate(bars, symbol)

        session = ReplaySession(
            "seek-latest-wins",
            1,
            prepared,
            executor,
            clock=SimulatedMonotonicClock(),
            scheduler=NullPlaybackScheduler(),
            analyzer=analyzer,
        )
        session.seek(
            session.start_time + timedelta(minutes=15),
            "seek-baseline",
        )
        old_results = []
        old_thread = threading.Thread(
            target=lambda: old_results.append(
                session.seek(
                    session.start_time + timedelta(minutes=30),
                    "seek-old",
                )
            ),
            daemon=True,
        )
        old_thread.start()
        self.assertTrue(started.wait(timeout=5))

        newest = session.seek(session.start_time, "seek-new")
        release.set()
        old_thread.join(timeout=5)

        self.assertEqual(newest.outcome_status, "completed")
        self.assertEqual(session.current_time, session.start_time)
        self.assertEqual(len(old_results), 1)
        self.assertEqual(old_results[0].outcome_status, "dropped")


class ReplaySimulatedTradeTests(ReplaySeekTests):
    def test_simulated_trade_lives_only_in_session_and_uses_shared_model(self) -> None:
        trade_events: list = []
        session = self._session(trade_events=trade_events)
        session.seek(session.start_time + timedelta(minutes=10), "seek-trade-time")

        record = session.create_simulated_trade(
            {
                "trade_scope": "simulated",
                "symbol": session.symbol,
                "side": "buy",
                "executed_at": session.current_time.strftime("%Y-%m-%d %H:%M"),
                "price": 10.25,
                "quantity": 200,
                "fee": None,
                "note": "Replay",
                "fee_plan_id": None,
            },
            trade_id="sim-1",
        )

        self.assertEqual(record.trade.trade_scope.value, "simulated")
        self.assertEqual(session.simulated_trades[0].trade_id, "sim-1")
        self.assertEqual(trade_events[-1]["session_id"], session.session_id)
        self.assertEqual(trade_events[-1]["payload"]["trades"][0]["trade_scope"], "simulated")
        session.retire()
        self.assertEqual(session.simulated_trades, ())

    def test_future_or_real_trade_is_rejected(self) -> None:
        session = self._session()
        with self.assertRaisesRegex(ValueError, "simulated scope"):
            session.create_simulated_trade(
                {
                    "trade_scope": "real",
                    "symbol": session.symbol,
                    "side": "buy",
                    "executed_at": session.current_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "price": 10,
                    "quantity": 100,
                    "fee": None,
                    "note": "",
                    "fee_plan_id": None,
                }
            )
        with self.assertRaisesRegex(ValueError, "later than the Replay cursor"):
            session.create_simulated_trade(
                {
                    "trade_scope": "simulated",
                    "symbol": session.symbol,
                    "side": "sell",
                    "executed_at": (session.current_time + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"),
                    "price": 10,
                    "quantity": 100,
                    "fee": None,
                    "note": "",
                    "fee_plan_id": None,
                }
            )

    def test_app_trade_commands_route_to_session_memory(self) -> None:
        trade_events: list = []
        session = self._session(trade_events=trade_events)
        session.seek(session.start_time + timedelta(minutes=10), "seek-command")
        api = SimulatedTradeCommandApi(
            lambda session_id: session if session_id == session.session_id else None
        )
        base = {
            "schema_version": "t0_app_v1",
            "request_id": "trade-request",
            "session_id": session.session_id,
        }
        draft = {
            "trade_scope": "simulated",
            "symbol": session.symbol,
            "side": "buy",
            "executed_at": session.current_time.strftime("%Y-%m-%d %H:%M:%S"),
            "price": 10.25,
            "quantity": 100,
            "fee": None,
            "note": "",
            "fee_plan_id": None,
        }
        created = api.dispatch(
            "create_trade",
            {**base, "payload": {"trade": draft}},
        )
        self.assertTrue(created["accepted"])
        self.assertEqual(len(session.simulated_trades), 1)
        trade_id = session.simulated_trades[0].trade_id
        deleted = api.dispatch(
            "delete_trade",
            {
                **base,
                "request_id": "delete-request",
                "payload": {
                    "trade_id": trade_id,
                    "trade_scope": "simulated",
                },
            },
        )
        self.assertTrue(deleted["accepted"])
        self.assertEqual(session.simulated_trades, ())
        self.assertEqual(
            [event["payload"]["trades"] for event in trade_events[-2:]],
            [[trade_events[-2]["payload"]["trades"][0]], []],
        )

    def test_simulated_trade_never_writes_app_sqlite(self) -> None:
        session = self._session()
        session.seek(session.start_time + timedelta(minutes=5), "seek-sqlite")
        with TemporaryDirectory() as directory:
            database = open_app_database(f"{directory}/app.sqlite")
            before = database.connection.total_changes
            session.create_simulated_trade(
                {
                    "trade_scope": "simulated",
                    "symbol": session.symbol,
                    "side": "buy",
                    "executed_at": session.current_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "price": 10,
                    "quantity": 100,
                    "fee": None,
                    "note": "",
                    "fee_plan_id": None,
                },
                trade_id="sim-no-sqlite",
            )
            session.update_simulated_trade(
                "sim-no-sqlite",
                {
                    "trade_scope": "simulated",
                    "symbol": session.symbol,
                    "side": "sell",
                    "executed_at": session.current_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "price": 10.1,
                    "quantity": 100,
                    "fee": None,
                    "note": "",
                    "fee_plan_id": None,
                },
            )
            session.delete_simulated_trade("sim-no-sqlite")
            self.assertEqual(database.connection.total_changes, before)
            count = database.connection.execute(
                "SELECT COUNT(*) FROM trades"
            ).fetchone()[0]
            self.assertEqual(count, 0)
            database.connection.close()


if __name__ == "__main__":
    unittest.main()
