"""T0-051 deterministic Live acceptance through the HTTP delivery boundary."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen


APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend.event_publisher import EventPublisher  # noqa: E402
from backend.live_application import LiveApplicationApi, LiveSessionFactory  # noqa: E402
from backend.service import create_server  # noqa: E402
from packages.t0assistant.preferences import PreferenceService  # noqa: E402
from packages.t0assistant.repositories import (  # noqa: E402
    SqlitePreferenceRepository,
    open_app_database,
)
from test_live_application import _DeterministicLiveInput, _chan  # noqa: E402


class LiveEndToEndAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = open_app_database(Path(self.tempdir.name) / "app.sqlite")
        self.publisher = EventPublisher(service_generation=7)
        self.events = self.publisher.subscribe()
        input_port = _DeterministicLiveInput(
            [object(), RuntimeError("injected provider failure"), object()]
        )
        self.app = LiveApplicationApi(
            service_generation=7,
            session_factory=LiveSessionFactory(
                input_port,
                analyzer=lambda bars, symbol: _chan(symbol),
            ),
            preference_service=PreferenceService(
                SqlitePreferenceRepository(self.database)
            ),
            event_publisher=self.publisher,
            restore_on_startup=False,
        )
        self.server = create_server(
            "127.0.0.1",
            0,
            "live-token",
            7,
            live_application_api=self.app,
            event_publisher=self.publisher,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.publisher.unsubscribe(self.events)
        self.database.close()
        self.tempdir.cleanup()

    def command(self, name: str, session_id, payload: dict) -> dict:
        body = json.dumps(
            {
                "schema_version": "t0_app_v1",
                "request_id": f"request-{name}",
                "command": name,
                "session_id": session_id,
                "payload": payload,
            }
        ).encode()
        request = Request(
            f"{self.base_url}/api/commands/{name}",
            data=body,
            headers={
                "Authorization": "Bearer live-token",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=1) as response:
            return json.load(response)

    def test_select_snapshot_failure_retention_and_clean_retry(self) -> None:
        selected = self.command(
            "select_security",
            None,
            {"symbol": "sh.600000"},
        )
        baseline = self.events.get(timeout=1)
        fetched = self.command(
            "get_live_snapshot",
            selected["data"]["session_id"],
            {},
        )
        failed = self.command(
            "retry_live",
            selected["data"]["session_id"],
            {},
        )
        failure = self.events.get(timeout=1)

        self.assertEqual(fetched["data"], baseline["payload"])
        self.assertEqual(failure["event_type"], "operation_failed")
        self.assertTrue(self.app.store.has_snapshot)
        self.assertEqual(self.app.store.current_revision, baseline["revision"])

        recovered = self.command(
            "retry_live",
            failed["data"]["session_id"],
            {},
        )
        replacement = self.events.get(timeout=1)
        self.assertEqual(replacement["event_type"], "workbench_snapshot")
        self.assertEqual(
            recovered["data"]["session_id"],
            replacement["session_id"],
        )
        self.assertNotEqual(replacement["session_id"], baseline["session_id"])


if __name__ == "__main__":
    unittest.main()
