"""Shared live-five-minute-merge-v1 fixture: Python and Renderer must agree."""

from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path

from packages.t0assistant.runtime.live_projection_store import (
    _merge_five_minute_bars,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE_PATH = (
    _REPOSITORY_ROOT
    / "apps"
    / "t0-assistant"
    / "contracts"
    / "fixtures"
    / "live-five-minute-merge-v1.json"
)
_FIXTURE = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


class LiveFiveMinuteMergeContractTests(unittest.TestCase):
    def test_fixture_shape_is_complete(self) -> None:
        self.assertEqual(_FIXTURE["schema_version"], "live_five_minute_merge_v1")
        self.assertIsInstance(_FIXTURE["initial_bars_5m"], list)
        self.assertGreaterEqual(len(_FIXTURE["steps"]), 5)
        self.assertEqual(
            [step["id"] for step in _FIXTURE["steps"]],
            [
                "revise_dynamic_bucket",
                "replace_with_new_dynamic_bucket",
                "official_close_keeps_current_dynamic",
                "late_official_without_current_dynamic_drops_unclosed",
                "rebaseline_full_bars",
            ],
        )

    def test_python_merge_matches_every_shared_fixture_step(self) -> None:
        bars = deepcopy(_FIXTURE["initial_bars_5m"])
        for step in _FIXTURE["steps"]:
            op = step["op"]
            if op == "merge":
                bars = _merge_five_minute_bars(bars, step["incoming"])
            elif op == "replace":
                bars = deepcopy(step["bars_5m"])
            else:
                self.fail(f"unknown op {op} in step {step['id']}")
            self.assertEqual(
                bars,
                step["expected_bars_5m"],
                msg=f"Python mismatch at step {step['id']}",
            )


if __name__ == "__main__":
    unittest.main()
