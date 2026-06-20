"""Phase 0: CLI smoke tests。

使用 fixture 加载，验证六个子命令均能输出合法 JSON 并满足顶层契约。
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Dict, List

from packages.fundamentalscreener.cli import main

FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "minimal_market.json"
)
MISSING_FIXTURE = Path("/tmp/__fundamentalscreener_missing_fixture__.json")


def _run(argv: List[str]) -> Dict[str, Any]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    assert rc == 0, f"cli exited with {rc}"
    text = buf.getvalue().strip()
    return json.loads(text)


def _run_expect_failure(argv: List[str]) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(argv)
    return rc, out.getvalue(), err.getvalue()


class CLISmokeTests(unittest.TestCase):
    def test_fixture_exists(self) -> None:
        self.assertTrue(FIXTURE.exists(), f"fixture missing: {FIXTURE}")

    def test_sectors_command_returns_stable_json(self) -> None:
        d = _run(
            [
                "sectors",
                "--fixture",
                str(FIXTURE),
                "--format",
                "json",
            ]
        )
        for key in (
            "command",
            "date",
            "classification_system",
            "benchmark",
            "sort",
            "periods",
            "sectors",
            "chart_series",
            "warnings",
        ):
            self.assertIn(key, d)
        self.assertEqual(d["command"], "sectors")
        self.assertEqual(d["date"], "2026-06-19")
        self.assertEqual(d["classification_system"], "concept")
        self.assertEqual(d["benchmark"], "hs300")
        self.assertEqual(d["periods"], [1, 5, 20, 60])
        self.assertEqual(d["sectors"], [])  # Phase 0 不计算
        self.assertEqual(d["chart_series"], [])

    def test_sector_detail_command_warns_when_sector_missing(self) -> None:
        d = _run(
            [
                "sector-detail",
                "--fixture",
                str(FIXTURE),
                "--sector",
                "unknown_sector",
                "--format",
                "json",
            ]
        )
        self.assertEqual(d["command"], "sector-detail")
        self.assertIn("classification_system", d)
        self.assertTrue(any("sector_not_found" in w for w in d["warnings"]))

    def test_companies_command_resolves_sector_by_name(self) -> None:
        d = _run(
            [
                "companies",
                "--fixture",
                str(FIXTURE),
                "--sector",
                "半导体",
                "--format",
                "json",
            ]
        )
        self.assertEqual(d["command"], "companies")
        self.assertEqual(d["sector_id"], "semiconductor")
        self.assertEqual(d["sector_name"], "半导体")
        self.assertIn("classification_system", d)
        self.assertEqual(d["companies"], [])

    def test_financials_command_omits_classification_system(self) -> None:
        d = _run(
            [
                "financials",
                "--fixture",
                str(FIXTURE),
                "--codes",
                "002371,600584",
                "--format",
                "json",
            ]
        )
        self.assertEqual(d["command"], "financials")
        self.assertNotIn("classification_system", d)
        self.assertIn("warnings", d)
        self.assertEqual(d["companies"], [])

    def test_valuations_command_omits_classification_system(self) -> None:
        d = _run(
            [
                "valuations",
                "--fixture",
                str(FIXTURE),
                "--codes",
                "002371",
                "--format",
                "json",
            ]
        )
        self.assertEqual(d["command"], "valuations")
        self.assertNotIn("classification_system", d)
        self.assertEqual(d["companies"], [])

    def test_screen_command_returns_candidates_groups(self) -> None:
        d = _run(
            [
                "screen",
                "--fixture",
                str(FIXTURE),
                "--sector-top",
                "10",
                "--company-top",
                "5",
                "--format",
                "json",
            ]
        )
        self.assertEqual(d["command"], "screen")
        self.assertIn("classification_system", d)
        self.assertEqual(set(d["candidates"].keys()), {"priority", "watch", "cautious"})
        self.assertTrue(d["generated_at"])

    def test_sectors_warns_without_fixture(self) -> None:
        d = _run(["sectors", "--format", "json"])
        self.assertEqual(d["command"], "sectors")
        self.assertTrue(any("no_data_source" in w for w in d["warnings"]))


class CLIInvalidFixtureTests(unittest.TestCase):
    """覆盖 P2：传入无效 fixture 路径时不能静默成功。"""

    def setUp(self) -> None:
        # 确保占位路径真的不存在。
        if MISSING_FIXTURE.exists():
            MISSING_FIXTURE.unlink()

    def _assert_fails(self, argv: List[str]) -> None:
        rc, out, err = _run_expect_failure(argv)
        self.assertNotEqual(rc, 0, f"command unexpectedly succeeded: argv={argv!r} stdout={out!r}")
        self.assertEqual(out, "", f"stdout should be empty on failure, got {out!r}")
        self.assertIn("fixture_not_found", err)

    def test_sectors_invalid_fixture_fails(self) -> None:
        self._assert_fails(
            ["sectors", "--fixture", str(MISSING_FIXTURE), "--date", "2026-06-19"]
        )

    def test_sector_detail_invalid_fixture_fails(self) -> None:
        self._assert_fails(
            [
                "sector-detail",
                "--fixture",
                str(MISSING_FIXTURE),
                "--sector",
                "semiconductor",
                "--date",
                "2026-06-19",
            ]
        )

    def test_companies_invalid_fixture_fails(self) -> None:
        self._assert_fails(
            [
                "companies",
                "--fixture",
                str(MISSING_FIXTURE),
                "--sector",
                "semiconductor",
                "--date",
                "2026-06-19",
            ]
        )

    def test_financials_invalid_fixture_fails(self) -> None:
        self._assert_fails(
            [
                "financials",
                "--fixture",
                str(MISSING_FIXTURE),
                "--codes",
                "002371",
                "--date",
                "2026-06-19",
            ]
        )

    def test_valuations_invalid_fixture_fails(self) -> None:
        self._assert_fails(
            [
                "valuations",
                "--fixture",
                str(MISSING_FIXTURE),
                "--codes",
                "002371",
                "--date",
                "2026-06-19",
            ]
        )

    def test_screen_invalid_fixture_fails(self) -> None:
        self._assert_fails(
            ["screen", "--fixture", str(MISSING_FIXTURE), "--date", "2026-06-19"]
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
