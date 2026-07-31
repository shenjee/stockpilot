from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend.replay_application import ReplayApplication  # noqa: E402
from packages.t0assistant.replay import ReplayCommandApi  # noqa: E402
from packages.t0assistant.tests.test_replay_session import _prepare  # noqa: E402


SECURITY = {
    "symbol": "sh.600000",
    "code": "600000",
    "market": "sh",
    "name": "浦发银行",
    "security_type": "a_share",
}


class ReplayApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events: list[dict] = []
        self.application = ReplayApplication(
            service_generation=3,
            prepare=lambda _symbol, _trade_date: _prepare("1m"),
            resolve_security=lambda symbol: (
                SECURITY if symbol == SECURITY["symbol"] else None
            ),
            publish_event=self.events.append,
        )
        self.api = ReplayCommandApi(
            self.application,
            service_generation=3,
            publish_event=self.events.append,
        )
        self.application.bind(self.api)

    def tearDown(self) -> None:
        self.application.close()

    def test_begin_replay_creates_a_ready_session_and_snapshot(self) -> None:
        result = self.api.dispatch(
            "begin_replay",
            {
                "schema_version": "t0_replay_v1",
                "request_id": "begin-1",
                "symbol": "sh.600000",
                "trade_date": "2026-07-24",
            },
        )

        self.assertEqual(result.status, 200)
        self.assertTrue(result.response_delivered())
        self.assertEqual(
            [event["event_type"] for event in self.events],
            ["workbench_snapshot"],
        )

        snapshot = self.api.dispatch(
            "get_replay_snapshot",
            {
                "schema_version": "t0_replay_v1",
                "request_id": "snapshot-1",
                "session_id": result.payload["session_id"],
            },
        )
        self.assertEqual(snapshot.status, 200)
        self.assertEqual(snapshot.payload["snapshot"]["session"]["state"], "ready")

    def test_unknown_security_is_rejected_before_loading(self) -> None:
        result = self.api.dispatch(
            "begin_replay",
            {
                "schema_version": "t0_replay_v1",
                "request_id": "begin-missing",
                "symbol": "sh.699999",
                "trade_date": "2026-07-24",
            },
        )

        self.assertEqual(result.status, 404)
        self.assertEqual(result.payload["error_code"], "symbol_not_found")

    def test_opening_auction_bar_is_consumed_by_the_initial_snapshot(self) -> None:
        prepared = _prepare("1m")
        prepared = replace(
            prepared,
            actual_bar_times=(
                prepared.start_time,
                prepared.start_time + timedelta(minutes=1),
                *prepared.actual_bar_times[1:],
            ),
        )
        application = ReplayApplication(
            service_generation=3,
            prepare=lambda _symbol, _trade_date: prepared,
            resolve_security=lambda _symbol: SECURITY,
            publish_event=lambda _event: None,
        )
        api = ReplayCommandApi(application, service_generation=3)
        application.bind(api)
        try:
            result = api.dispatch(
                "begin_replay",
                {
                    "schema_version": "t0_replay_v1",
                    "request_id": "begin-auction",
                    "symbol": "sh.600000",
                    "trade_date": "2026-07-24",
                },
            )
            result.response_delivered()
            session = application.session(result.payload["session_id"])
            self.assertIsNotNone(session)
            self.assertEqual(
                session.next_bar_time,
                prepared.start_time + timedelta(minutes=1),
            )
        finally:
            application.close()


if __name__ == "__main__":
    unittest.main()
