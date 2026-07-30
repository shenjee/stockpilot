"""T0-053 Replay end-to-end and determinism acceptance.

The tests deliberately cross the data-preparation, in-memory market input,
shared pipeline, Replay Session, indicator and CZSC adapter boundaries.  They
use repository fixtures only; no provider, network or SQLite access occurs.
Renderer event-gap/rebaseline behavior is covered by
``apps/t0-assistant/tests/backend-gateway.test.mjs``.
"""

from __future__ import annotations

import copy
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
import json
from types import MappingProxyType
import unittest

from packages.t0assistant.runtime import (
    BoundedComputationExecutor,
    NullPlaybackScheduler,
    ReplayDataPreparator,
    ReplayPreparationConfig,
    ReplaySession,
    SimulatedMonotonicClock,
)
from packages.t0assistant.runtime.replay_session import _mutable_json_value
from packages.t0assistant.tests.fixtures.replay_fixtures import (
    SYMBOL,
    TRADE_DATE,
    five_minute_fallback,
    market_context_service,
    one_minute_replay,
)
from packages.t0assistant.tests.test_replay_data import (
    FakeMarketDataPort,
    _populate_from_fixture,
)
from packages.t0assistant.tests.test_replay_session import (
    _CachingAnalyzer,
    _default_analyze_5m,
)


_MARKET_TIMESTAMP = "%Y-%m-%d %H:%M:%S"


def _prepare(fixture):
    port = FakeMarketDataPort()
    _populate_from_fixture(port, fixture)
    prepared = ReplayDataPreparator(
        port,
        market_context_service(),
    ).prepare(
        SYMBOL,
        TRADE_DATE,
        config=ReplayPreparationConfig(),
    )
    return port, prepared


def _session(
    prepared,
    executor: BoundedComputationExecutor,
    *,
    events: list[dict] | None = None,
) -> ReplaySession:
    return ReplaySession(
        "replay-e2e",
        1,
        prepared,
        executor,
        clock=SimulatedMonotonicClock(),
        scheduler=NullPlaybackScheduler(),
        analyzer=_CachingAnalyzer(_default_analyze_5m),
        on_event=(events if events is not None else []).append,
        initial_operation_id="begin-e2e",
    )


def _assert_timestamp_prefix(
    testcase: unittest.TestCase,
    rows: list[dict],
    current_time: str,
    *,
    allow_forming_bar_label: bool = False,
) -> None:
    for row in rows:
        timestamp = row.get("timestamp")
        if not isinstance(timestamp, str) or len(timestamp) != 19:
            continue
        if timestamp <= current_time:
            continue
        testcase.assertTrue(
            allow_forming_bar_label and row.get("closed") is False,
            f"future row leaked past {current_time}: {row}",
        )


def _assert_no_future_business_output(
    testcase: unittest.TestCase,
    snapshot: dict,
) -> None:
    current_time = snapshot["replay"]["current_time"]
    market = snapshot["market"]
    _assert_timestamp_prefix(testcase, market["bars_1m"], current_time)
    _assert_timestamp_prefix(
        testcase,
        market["bars_5m"],
        current_time,
        allow_forming_bar_label=True,
    )

    for timeframe in ("five_minute", "one_minute"):
        pending = [snapshot["indicators"][timeframe]]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                if "timestamp" in value:
                    _assert_timestamp_prefix(testcase, [value], current_time)
                else:
                    pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)

    # Chan Theory output may contain timestamps on strokes, pivot zones,
    # signals and plot primitives.  Walk all nested objects and reject every
    # market timestamp later than the cursor.
    pending = [snapshot["chan_analysis"]]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, str) and len(value) == 19:
            try:
                datetime.strptime(value, _MARKET_TIMESTAMP)
            except ValueError:
                continue
            testcase.assertLessEqual(value, current_time)


class ReplayEndToEndAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.executors: list[BoundedComputationExecutor] = []

    def tearDown(self) -> None:
        for executor in self.executors:
            executor.shutdown(cancel_pending=True, wait=True)

    def _executor(self) -> BoundedComputationExecutor:
        executor = BoundedComputationExecutor(capacity=16, worker_count=1)
        self.executors.append(executor)
        return executor

    def test_one_minute_flow_prepares_once_rebuilds_and_never_reads_future(
        self,
    ) -> None:
        port, prepared = _prepare(one_minute_replay())
        calls_at_ready = tuple(port.call_log)
        events: list[dict] = []
        session = _session(prepared, self._executor(), events=events)
        late = "2026-07-24 10:23:00"
        early = "2026-07-24 09:47:00"

        session.seek(late, "seek-late")
        late_snapshot = session.snapshot()
        _assert_no_future_business_output(self, late_snapshot)
        session.seek(early, "seek-backward")
        rebuilt = session.snapshot()

        self.assertEqual(prepared.granularity, "one_minute")
        self.assertEqual(len(prepared.bars_1m), 240)
        self.assertEqual(port.call_log, list(calls_at_ready))
        self.assertEqual(rebuilt["replay"]["current_time"], early)
        _assert_no_future_business_output(self, rebuilt)
        self.assertTrue(
            all(
                event["revision"] < following["revision"]
                for event, following in zip(events, events[1:])
            )
        )

    def test_frozen_warning_details_are_recursively_json_safe(self) -> None:
        class WarningState(str, Enum):
            DEGRADED = "degraded"

        frozen = MappingProxyType(
            {
                "state": WarningState.DEGRADED,
                "ratio": Decimal("1.25"),
                "observed_at": datetime(2026, 7, 24, 10, 23, 45),
                "trade_date": date(2026, 7, 24),
                "market_time": time(10, 23, 45),
                "nested": (
                    MappingProxyType({"amount": Decimal("10.01")}),
                ),
            }
        )

        thawed = _mutable_json_value(frozen)

        self.assertEqual(
            thawed,
            {
                "state": "degraded",
                "ratio": 1.25,
                "observed_at": "2026-07-24 10:23:45",
                "trade_date": "2026-07-24",
                "market_time": "10:23:45",
                "nested": [{"amount": 10.01}],
            },
        )
        # ``allow_nan=False`` proves the result is valid JSON rather than only
        # serializable by Python's permissive NaN extension.
        json.dumps(thawed, allow_nan=False)

    def test_non_finite_warning_decimal_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be finite"):
            _mutable_json_value(Decimal("NaN"))

    def test_five_minute_fallback_keeps_intraday_empty_and_is_deterministic(
        self,
    ) -> None:
        port, prepared = _prepare(five_minute_fallback())
        calls_at_ready = tuple(port.call_log)
        target = "2026-07-24 10:20:00"

        first = _session(prepared, self._executor())
        second = _session(prepared, self._executor())
        first.seek(target, "seek-target")
        second.seek(target, "seek-target")
        first_snapshot = first.snapshot()
        second_snapshot = second.snapshot()

        self.assertEqual(prepared.granularity, "five_minute")
        self.assertEqual(first_snapshot["market"]["bars_1m"], [])
        self.assertEqual(
            first_snapshot["warnings"][0]["warning_code"],
            "one_minute_data_unavailable",
        )
        self.assertEqual(first_snapshot["replay"]["step_seconds"], 300)
        self.assertEqual(port.call_log, list(calls_at_ready))
        _assert_no_future_business_output(self, first_snapshot)

        # Same fixture, configuration, identity and command sequence produces
        # byte-stable business output, not merely similar chart values.
        first_bytes = json.dumps(
            first_snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        second_bytes = json.dumps(
            second_snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        self.assertEqual(first_bytes, second_bytes)

    def test_backward_seek_removes_every_future_prefix_from_prior_snapshot(
        self,
    ) -> None:
        _port, prepared = _prepare(one_minute_replay())
        session = _session(prepared, self._executor())
        session.seek("2026-07-24 14:32:00", "seek-afternoon")
        future_snapshot = copy.deepcopy(session.snapshot())
        session.seek("2026-07-24 10:02:00", "seek-morning")
        rebuilt = session.snapshot()

        self.assertGreater(
            len(future_snapshot["market"]["bars_1m"]),
            len(rebuilt["market"]["bars_1m"]),
        )
        self.assertNotIn(
            "2026-07-24 14:32:00",
            json.dumps(rebuilt, ensure_ascii=False),
        )
        _assert_no_future_business_output(self, rebuilt)


if __name__ == "__main__":
    unittest.main()
