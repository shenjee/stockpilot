"""HTTP transport wiring for App-v1 fee-plan commands."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend.service import create_server  # noqa: E402
from packages.t0assistant.preferences import FeePlanService  # noqa: E402
from packages.t0assistant.preferences.fee_plan_api import FeePlanCommandApi  # noqa: E402
from packages.t0assistant.repositories import (  # noqa: E402
    SqliteFeePlanRepository,
    open_app_database,
)


class FeePlanServiceWiringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = open_app_database(Path(self.tempdir.name) / "app.sqlite")
        self.api = FeePlanCommandApi(
            FeePlanService(SqliteFeePlanRepository(self.database)),
            service_generation=9,
        )
        self.server = create_server(
            "127.0.0.1",
            0,
            "fee-token",
            9,
            fee_plan_api=self.api,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.database.close()
        self.tempdir.cleanup()

    def post(self, command: str, payload: dict) -> dict:
        envelope = {
            "schema_version": "t0_app_v1",
            "request_id": f"wire-{command}",
            "command": command,
            "session_id": None,
            "payload": payload,
        }
        request = Request(
            f"http://127.0.0.1:{self.server.server_port}/api/commands/{command}",
            data=json.dumps(envelope).encode(),
            method="POST",
            headers={
                "Authorization": "Bearer fee-token",
                "Content-Type": "application/json",
            },
        )
        with urlopen(request, timeout=2) as response:
            return json.load(response)

    def post_envelope(self, command: str, envelope: dict) -> tuple[int, dict]:
        request = Request(
            f"http://127.0.0.1:{self.server.server_port}/api/commands/{command}",
            data=json.dumps(envelope).encode(),
            method="POST",
            headers={
                "Authorization": "Bearer fee-token",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=2) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            try:
                return error.code, json.load(error)
            finally:
                error.close()

    def test_list_and_calculate_reach_persistent_domain_services(self) -> None:
        plans = self.post("list_fee_plans", {})
        self.assertTrue(plans["accepted"])
        self.assertEqual(
            plans["data"]["fee_plans"][0]["fee_plan_id"], "shenwan-hongyuan"
        )
        fee = self.post(
            "calculate_trade_fee",
            {
                "fee_plan_id": "shenwan-hongyuan",
                "security_type": "etf",
                "side": "buy",
                "price": "4.25",
                "quantity": 1000,
            },
        )
        self.assertTrue(fee["accepted"])
        self.assertEqual(fee["data"]["commission"], 5.0)
        self.assertEqual(fee["data"]["stamp_duty"], 0.0)

    def test_transport_uses_fee_plan_validation_identity(self) -> None:
        status, response = self.post_envelope(
            "list_fee_plans",
            {
                "schema_version": "t0_app_v1",
                "command": "list_fee_plans",
                "session_id": None,
                "payload": {},
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(response["request_id"], "missing-request-id")
        self.assertEqual(
            response["error"]["error_code"], "invalid_fee_plan_request"
        )
        self.assertEqual(
            response["error"]["affected_capability"], "preferences"
        )


if __name__ == "__main__":
    unittest.main()
