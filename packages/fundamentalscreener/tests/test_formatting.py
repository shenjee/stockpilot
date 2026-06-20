"""Phase 0: formatting JSON 输出测试。"""

from __future__ import annotations

import json
import unittest

from packages.fundamentalscreener.formatting import (
    format_csv,
    format_json,
    format_markdown,
    format_output,
)


class FormatJsonTests(unittest.TestCase):
    def test_format_json_round_trip(self) -> None:
        payload = {
            "command": "sectors",
            "date": "2026-06-19",
            "warnings": [],
            "sectors": [],
        }
        text = format_json(payload)
        self.assertEqual(json.loads(text), payload)

    def test_format_json_preserves_unicode(self) -> None:
        payload = {"command": "sectors", "sector_name": "半导体", "warnings": []}
        text = format_json(payload)
        # 不应转义为 \uXXXX。
        self.assertIn("半导体", text)


class FormatDispatcherTests(unittest.TestCase):
    def test_format_output_json(self) -> None:
        payload = {"command": "sectors", "date": "2026-06-19", "warnings": []}
        text = format_output(payload, "json")
        self.assertEqual(json.loads(text)["command"], "sectors")

    def test_format_output_markdown_placeholder(self) -> None:
        payload = {"command": "sectors", "date": "2026-06-19"}
        text = format_markdown(payload)
        self.assertIn("sectors", text)
        self.assertIn("2026-06-19", text)

    def test_format_output_csv_placeholder(self) -> None:
        payload = {"command": "sectors"}
        text = format_csv(payload)
        self.assertIn("sectors", text)

    def test_format_output_rejects_unknown_format(self) -> None:
        with self.assertRaises(ValueError):
            format_output({"command": "sectors"}, "xml")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
