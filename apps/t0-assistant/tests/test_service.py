from __future__ import annotations

import json
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend.service import create_server  # noqa: E402
from packages.t0assistant.replay import ReplayAccepted, ReplayCommandApi  # noqa: E402


class _FakeSearchService:
    def search(self, query: str, limit: int):
        return [
            {
                "symbol": "sh.600519",
                "code": "600519",
                "market": "sh",
                "name": "贵州茅台",
                "security_type": "a_share",
            }
        ][:limit]


class _FakeReplayPort:
    def __init__(self) -> None:
        self.requests = []
        self.started = []

    def execute(self, command, request):
        self.requests.append((command, dict(request)))
        if command == "step_replay":
            return ReplayAccepted(
                session_id=request["session_id"],
                operation_id="operation-step",
                start_operation=lambda: self.started.append("operation-step"),
            )
        return ReplayAccepted(session_id=request.get("session_id", "replay-new"))


class DesktopServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(
            "127.0.0.1",
            0,
            "formal-token",
            5,
            search_service=_FakeSearchService(),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_health_requires_authentication_and_reports_generation(self) -> None:
        with self.assertRaises(HTTPError) as rejected:
            urlopen(f"{self.base_url}/health", timeout=1)
        self.assertEqual(rejected.exception.code, 401)
        rejected.exception.close()

        request = Request(
            f"{self.base_url}/health",
            headers={"Authorization": "Bearer formal-token"},
        )
        with urlopen(request, timeout=1) as response:
            payload = json.load(response)
        self.assertEqual(payload, {"status": "ready", "service_generation": 5})

    def test_unimplemented_app_command_returns_structured_service_error(self) -> None:
        body = json.dumps(
            {
                "schema_version": "t0_app_v1",
                "request_id": "formal-command-1",
                "command": "get_live_snapshot",
                "session_id": "live-1",
                "payload": {},
            }
        ).encode()
        request = Request(
            f"{self.base_url}/api/commands/get_live_snapshot",
            data=body,
            headers={
                "Authorization": "Bearer formal-token",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with self.assertRaises(HTTPError) as rejected:
            urlopen(request, timeout=1)
        self.assertEqual(rejected.exception.code, 503)
        payload = json.load(rejected.exception)
        rejected.exception.close()
        self.assertFalse(payload["accepted"])
        self.assertEqual(payload["error"]["error_code"], "service_unavailable")
        self.assertEqual(payload["error"]["request_id"], "formal-command-1")

    def test_security_search_returns_multiple_result_contract(self) -> None:
        body = json.dumps(
            {
                "schema_version": "t0_app_v1",
                "request_id": "search-1",
                "command": "search_securities",
                "session_id": None,
                "payload": {"query": "gzmt", "limit": 20},
            }
        ).encode()
        request = Request(
            f"{self.base_url}/api/commands/search_securities",
            data=body,
            headers={
                "Authorization": "Bearer formal-token",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=1) as response:
            payload = json.load(response)
        self.assertTrue(payload["accepted"])
        self.assertEqual(payload["data"]["securities"][0]["symbol"], "sh.600519")

    def test_non_object_command_body_is_rejected_without_handler_failure(self) -> None:
        request = Request(
            f"{self.base_url}/api/commands/get_preferences",
            data=b"[]",
            headers={
                "Authorization": "Bearer formal-token",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with self.assertRaises(HTTPError) as rejected:
            urlopen(request, timeout=1)
        self.assertEqual(rejected.exception.code, 400)
        rejected.exception.close()

    def test_replay_command_is_dispatched_through_v1_api(self) -> None:
        port = _FakeReplayPort()
        replay_api = ReplayCommandApi(port, service_generation=5)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.server = create_server(
            "127.0.0.1",
            0,
            "formal-token",
            5,
            search_service=_FakeSearchService(),
            replay_api=replay_api,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        body = json.dumps(
            {
                "schema_version": "t0_replay_v1",
                "request_id": "pause-1",
                "session_id": "replay-1",
                "playing": False,
            }
        ).encode()
        request = Request(
            f"{self.base_url}/api/commands/set_replay_playback",
            data=body,
            headers={
                "Authorization": "Bearer formal-token",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        with urlopen(request, timeout=1) as response:
            payload = json.load(response)

        self.assertEqual(payload["request_id"], "pause-1")
        self.assertEqual(payload["session_id"], "replay-1")
        self.assertEqual(port.requests[0][0], "set_replay_playback")

        step_body = json.dumps(
            {
                "schema_version": "t0_replay_v1",
                "request_id": "step-1",
                "session_id": "replay-1",
            }
        ).encode()
        step_request = Request(
            f"{self.base_url}/api/commands/step_replay",
            data=step_body,
            headers={
                "Authorization": "Bearer formal-token",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(step_request, timeout=1) as response:
            step_payload = json.load(response)

        self.assertEqual(step_payload["operation_id"], "operation-step")
        self.assertEqual(port.started, ["operation-step"])

    def test_non_loopback_binding_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "127.0.0.1"):
            create_server("0.0.0.0", 0, "token", 1)

    def test_replay_api_generation_must_match_server_generation(self) -> None:
        replay_api = ReplayCommandApi(_FakeReplayPort(), service_generation=6)

        with self.assertRaisesRegex(ValueError, "service_generation"):
            create_server(
                "127.0.0.1",
                0,
                "token",
                5,
                replay_api=replay_api,
            )

    def test_websocket_handler_exits_when_the_client_disconnects(self) -> None:
        client = socket.create_connection(("127.0.0.1", self.server.server_port))
        request = (
            "GET /events HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.server.server_port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            "Sec-WebSocket-Protocol: stockpilot-auth.formal-token\r\n"
            "\r\n"
        ).encode()
        client.sendall(request)
        response = client.recv(4096)
        self.assertIn(b"101 Switching Protocols", response)
        deadline = time.monotonic() + 1
        while self.server.active_websockets != 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.server.active_websockets, 1)

        client.close()
        deadline = time.monotonic() + 1
        while self.server.active_websockets and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.server.active_websockets, 0)


if __name__ == "__main__":
    unittest.main()
