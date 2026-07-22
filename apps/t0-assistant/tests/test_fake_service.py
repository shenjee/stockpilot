from __future__ import annotations

import json
import sys
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend.fake_service import create_server  # noqa: E402


class FakeServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server("127.0.0.1", 0, "test-token", 3)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_health_requires_token_and_reports_generation(self) -> None:
        with self.assertRaises(HTTPError) as rejected:
            urlopen(f"{self.base_url}/health", timeout=1)
        self.assertEqual(rejected.exception.code, 401)
        rejected.exception.close()

        request = Request(
            f"{self.base_url}/health",
            headers={"Authorization": "Bearer test-token"},
        )
        with urlopen(request, timeout=1) as response:
            payload = json.load(response)
        self.assertEqual(payload, {"status": "ready", "service_generation": 3})

    def test_non_loopback_binding_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "127.0.0.1"):
            create_server("0.0.0.0", 0, "token", 1)


if __name__ == "__main__":
    unittest.main()
