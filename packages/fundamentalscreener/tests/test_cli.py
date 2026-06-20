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


def _run_text(argv: List[str]) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    assert rc == 0, f"cli exited with {rc}"
    return buf.getvalue()


def _run_expect_failure(argv: List[str]) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    rc: int
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = main(argv)
        except SystemExit as exc:
            # argparse 自身会通过 SystemExit 报错（例如未知子命令、缺少必填参数）；
            # _parse_periods 等业务校验已改为抛 CLIArgumentError，由 main() 内部
            # 捕获并返回 rc=2，这里只兜底 argparse 的退出。
            code = exc.code
            if isinstance(code, int):
                rc = code
            else:
                # 非整数 code（例如字符串错误信息）当作错误并写入 stderr。
                if code:
                    err.write(str(code))
                    if not str(code).endswith("\n"):
                        err.write("\n")
                rc = 2
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
        # Phase 1 起 sectors 已有真实计算，至少包含 fixture 中的两个板块。
        self.assertEqual(len(d["sectors"]), 2)
        sector_ids = {s["sector_id"] for s in d["sectors"]}
        self.assertEqual(sector_ids, {"semiconductor", "machinery"})
        # chart_series：板块 + 基准。
        types = [c["type"] for c in d["chart_series"]]
        self.assertEqual(types.count("benchmark"), 1)
        self.assertEqual(types.count("sector"), 2)

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
        # Phase 2 起 companies 已有真实计算，半导体板块在 fixture 里有 2 家公司。
        codes = {c["code"] for c in d["companies"]}
        self.assertEqual(codes, {"002371", "600584"})

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
        # Phase 3 起 financials 已有真实计算。
        codes = {c["code"] for c in d["companies"]}
        self.assertEqual(codes, {"002371", "600584"})

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


class CLIPhase1SectorsTests(unittest.TestCase):
    """Phase 1: sectors / sector-detail 真实计算与 markdown 输出。"""

    def test_sectors_sort_changes_first_row(self) -> None:
        # 默认按 return_1d 排序：fixture 中半导体涨、工程机械跌。
        d = _run(["sectors", "--fixture", str(FIXTURE), "--format", "json"])
        self.assertEqual(d["sort"], "return_1d")
        self.assertEqual(d["sectors"][0]["sector_id"], "semiconductor")

        # 切到 return_5d 仍应是半导体（slope 更大）。
        d2 = _run(
            [
                "sectors",
                "--fixture",
                str(FIXTURE),
                "--sort",
                "return_5d",
                "--format",
                "json",
            ]
        )
        self.assertEqual(d2["sort"], "return_5d")
        self.assertEqual(d2["sectors"][0]["sector_id"], "semiconductor")

    def test_sectors_top_truncates(self) -> None:
        d = _run(
            ["sectors", "--fixture", str(FIXTURE), "--top", "1", "--format", "json"]
        )
        self.assertEqual(len(d["sectors"]), 1)

    def test_sectors_relative_return_is_signed_decimal(self) -> None:
        d = _run(["sectors", "--fixture", str(FIXTURE), "--format", "json"])
        rel_values = {s["sector_id"]: s["relative_return"] for s in d["sectors"]}
        # 半导体 close 上涨快于基准 (+0.2 vs +0.1)，相对收益应为正小数。
        self.assertIsNotNone(rel_values["semiconductor"])
        self.assertGreater(rel_values["semiconductor"], 0)
        # 工程机械 close 下跌，相对收益应为负小数。
        self.assertIsNotNone(rel_values["machinery"])
        self.assertLess(rel_values["machinery"], 0)

    def test_sectors_chart_series_includes_benchmark_and_normalized_start(self) -> None:
        d = _run(["sectors", "--fixture", str(FIXTURE), "--format", "json"])
        types = [c["type"] for c in d["chart_series"]]
        self.assertIn("benchmark", types)
        # 起点值应为 100。
        for c in d["chart_series"]:
            if c["points"]:
                self.assertAlmostEqual(c["points"][0]["value"], 100.0, places=4)

    def test_sectors_state_field_present_but_not_default_sort(self) -> None:
        d = _run(["sectors", "--fixture", str(FIXTURE), "--format", "json"])
        for s in d["sectors"]:
            self.assertIn("state", s)
            self.assertIn("rank_change_5d", s)
        # 默认 sort 不应是 state 或 rank_change_5d。
        self.assertNotIn(d["sort"], ("state", "rank_change_5d"))

    def test_sectors_markdown_renders_table(self) -> None:
        out = _run_text(
            [
                "sectors",
                "--fixture",
                str(FIXTURE),
                "--sort",
                "return_5d",
                "--format",
                "markdown",
            ]
        )
        self.assertIn("# fundamental-screener: sectors", out)
        self.assertIn("| sector_id | sector_name |", out)
        self.assertIn("semiconductor", out)
        self.assertIn("machinery", out)
        # 排序字段应出现在元信息块。
        self.assertIn("sort: `return_5d`", out)

    def test_sector_detail_returns_only_target_sector(self) -> None:
        d = _run(
            [
                "sector-detail",
                "--fixture",
                str(FIXTURE),
                "--sector",
                "半导体",
                "--format",
                "json",
            ]
        )
        self.assertEqual(d["command"], "sector-detail")
        self.assertEqual(len(d["sectors"]), 1)
        self.assertEqual(d["sectors"][0]["sector_id"], "semiconductor")
        # chart_series 应只包含目标板块 + 基准。
        ids = {c["series_id"] for c in d["chart_series"]}
        self.assertIn("semiconductor", ids)
        self.assertIn("hs300", ids)
        self.assertNotIn("machinery", ids)


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


class CLIInvalidPeriodsTests(unittest.TestCase):
    """覆盖：--periods 必须是正整数；非法值应通过 stderr + rc=2 报错，不能 traceback。"""

    def _assert_invalid(self, periods: str) -> None:
        rc, out, err = _run_expect_failure(
            [
                "sectors",
                "--fixture",
                str(FIXTURE),
                "--periods",
                periods,
                "--format",
                "json",
            ]
        )
        self.assertEqual(rc, 2, f"--periods {periods!r} should exit with rc=2, got {rc}")
        self.assertEqual(out, "")
        self.assertNotIn("Traceback", err, f"unexpected traceback for --periods {periods!r}")
        self.assertIn("invalid --periods value", err)

    def test_zero_rejected(self) -> None:
        self._assert_invalid("0")

    def test_negative_rejected(self) -> None:
        self._assert_invalid("-1")

    def test_zero_in_list_rejected(self) -> None:
        self._assert_invalid("1,0,5")

    def test_non_integer_rejected(self) -> None:
        self._assert_invalid("abc")


class CLIBenchmarkMismatchTests(unittest.TestCase):
    """覆盖：显式 --benchmark 必须与 fixture 中 benchmark.id 一致。"""

    def test_default_benchmark_uses_fixture_value(self) -> None:
        d = _run(["sectors", "--fixture", str(FIXTURE), "--format", "json"])
        # fixture 内 benchmark.id = "hs300"，缺省时顶层应显示 hs300。
        self.assertEqual(d["benchmark"], "hs300")

    def test_matching_benchmark_passes(self) -> None:
        d = _run(
            [
                "sectors",
                "--fixture",
                str(FIXTURE),
                "--benchmark",
                "hs300",
                "--format",
                "json",
            ]
        )
        self.assertEqual(d["benchmark"], "hs300")

    def test_mismatching_benchmark_fails(self) -> None:
        rc, out, err = _run_expect_failure(
            [
                "sectors",
                "--fixture",
                str(FIXTURE),
                "--benchmark",
                "zz500",
                "--format",
                "json",
            ]
        )
        self.assertNotEqual(rc, 0)
        self.assertEqual(out, "")
        self.assertIn("benchmark_mismatch", err)
        self.assertIn("zz500", err)
        self.assertIn("hs300", err)

    def test_screen_mismatching_benchmark_fails(self) -> None:
        rc, out, err = _run_expect_failure(
            [
                "screen",
                "--fixture",
                str(FIXTURE),
                "--benchmark",
                "zz500",
                "--format",
                "json",
            ]
        )
        self.assertNotEqual(rc, 0)
        self.assertEqual(out, "")
        self.assertIn("benchmark_mismatch", err)


class CLIPhase2CompaniesTests(unittest.TestCase):
    """Phase 2: companies 命令真实计算、排序、--top、markdown / csv 输出。"""

    def test_companies_default_sort_is_combined_score_descending(self) -> None:
        d = _run(
            [
                "companies",
                "--fixture",
                str(FIXTURE),
                "--sector",
                "semiconductor",
                "--format",
                "json",
            ]
        )
        self.assertEqual(d["sort"], "combined_score")
        scores = [c["combined_score"] for c in d["companies"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        # Phase 3/4 未接入，financial_quality_score / valuation_score 必须为 None。
        for c in d["companies"]:
            self.assertIsNone(c["financial_quality_score"])
            self.assertIsNone(c["valuation_score"])
            self.assertIn(c["group"], (None, "priority", "watch", "cautious"))

    def test_companies_market_cap_drives_leader_score(self) -> None:
        d = _run(
            [
                "companies",
                "--fixture",
                str(FIXTURE),
                "--sector",
                "semiconductor",
                "--sort",
                "leader_score",
                "--format",
                "json",
            ]
        )
        # 002371 市值 1200 亿 > 600584 市值 800 亿，leader_score 排序应把 002371 放在前面。
        self.assertEqual(d["companies"][0]["code"], "002371")
        self.assertEqual(d["companies"][-1]["code"], "600584")

    def test_companies_top_truncates(self) -> None:
        d = _run(
            [
                "companies",
                "--fixture",
                str(FIXTURE),
                "--sector",
                "semiconductor",
                "--top",
                "1",
                "--format",
                "json",
            ]
        )
        self.assertEqual(len(d["companies"]), 1)

    def test_companies_sector_return_rank_is_ascending(self) -> None:
        d = _run(
            [
                "companies",
                "--fixture",
                str(FIXTURE),
                "--sector",
                "semiconductor",
                "--sort",
                "sector_return_rank",
                "--format",
                "json",
            ]
        )
        ranks = [c["sector_return_rank"] for c in d["companies"]]
        # rank=1 应在前面（升序）。
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual(ranks[0], 1)

    def test_companies_unknown_sort_rejected_by_argparse(self) -> None:
        rc, out, err = _run_expect_failure(
            [
                "companies",
                "--fixture",
                str(FIXTURE),
                "--sector",
                "semiconductor",
                "--sort",
                "unknown_field",
                "--format",
                "json",
            ]
        )
        self.assertNotEqual(rc, 0)
        self.assertEqual(out, "")

    def test_companies_markdown_renders_table(self) -> None:
        out = _run_text(
            [
                "companies",
                "--fixture",
                str(FIXTURE),
                "--sector",
                "semiconductor",
                "--format",
                "markdown",
            ]
        )
        self.assertIn("# fundamental-screener: companies", out)
        self.assertIn("| code | name |", out)
        self.assertIn("002371", out)
        self.assertIn("600584", out)
        self.assertIn("sector_id: `semiconductor`", out)

    def test_companies_csv_has_header_and_rows(self) -> None:
        out = _run_text(
            [
                "companies",
                "--fixture",
                str(FIXTURE),
                "--sector",
                "semiconductor",
                "--format",
                "csv",
            ]
        )
        lines = [ln for ln in out.splitlines() if ln.strip()]
        self.assertTrue(lines[0].startswith("code,name,market_cap,"))
        self.assertEqual(len(lines), 1 + 2)  # header + 2 公司
        self.assertTrue(any(ln.startswith("002371,") for ln in lines))
        self.assertTrue(any(ln.startswith("600584,") for ln in lines))

    def test_companies_unknown_sector_warns(self) -> None:
        d = _run(
            [
                "companies",
                "--fixture",
                str(FIXTURE),
                "--sector",
                "no_such_sector",
                "--format",
                "json",
            ]
        )
        self.assertIsNone(d["sector_id"])
        self.assertIsNone(d["sector_name"])
        self.assertEqual(d["companies"], [])
        self.assertTrue(any("sector_not_found" in w for w in d["warnings"]))


class CLIPhase3FinancialsTests(unittest.TestCase):
    """Phase 3: financials 命令真实计算、排序、缺失降级。"""

    def test_financials_default_sort_is_score(self) -> None:
        d = _run(
            [
                "financials",
                "--fixture",
                str(FIXTURE),
                "--codes",
                "002371,600584,000001,000002",
                "--format",
                "json",
            ]
        )
        self.assertEqual(d["sort"] if "sort" in d else "score", "score")
        scores = [c["score"] for c in d["companies"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_financials_fixture_flags_are_detected(self) -> None:
        # fixture 里 600584 的应收 yoy(0.40) - 营收 yoy(0.05) = 0.35 → receivable_growth_risk；
        # OCF/profit=0.30 + net_profit_yoy=0.20 → weak_cashflow；
        # gross_margin_yoy_change=-0.03 → gross_margin_decline。
        d = _run(
            [
                "financials",
                "--fixture",
                str(FIXTURE),
                "--codes",
                "600584",
                "--format",
                "json",
            ]
        )
        flags = set(d["companies"][0]["abnormal_flags"])
        self.assertIn("weak_cashflow", flags)
        self.assertIn("receivable_growth_risk", flags)
        self.assertIn("gross_margin_decline", flags)

    def test_financials_unknown_code_warns_top_level(self) -> None:
        d = _run(
            [
                "financials",
                "--fixture",
                str(FIXTURE),
                "--codes",
                "002371,ZZZ",
                "--format",
                "json",
            ]
        )
        codes = [c["code"] for c in d["companies"]]
        self.assertEqual(codes, ["002371"])
        self.assertTrue(any("code_not_found: ZZZ" in w for w in d["warnings"]))

    def test_financials_no_codes_returns_warning(self) -> None:
        d = _run(
            [
                "financials",
                "--fixture",
                str(FIXTURE),
                "--format",
                "json",
            ]
        )
        self.assertEqual(d["companies"], [])
        self.assertTrue(any("no_codes_provided" in w for w in d["warnings"]))

    def test_financials_unknown_sort_rejected_by_argparse(self) -> None:
        rc, out, err = _run_expect_failure(
            [
                "financials",
                "--fixture",
                str(FIXTURE),
                "--codes",
                "002371",
                "--sort",
                "unknown_field",
                "--format",
                "json",
            ]
        )
        self.assertNotEqual(rc, 0)
        self.assertEqual(out, "")

    def test_financials_markdown_renders_table(self) -> None:
        out = _run_text(
            [
                "financials",
                "--fixture",
                str(FIXTURE),
                "--codes",
                "002371,600584",
                "--format",
                "markdown",
            ]
        )
        self.assertIn("# fundamental-screener: financials", out)
        self.assertIn("002371", out)
        self.assertIn("600584", out)
        self.assertIn("abnormal_flags", out)
        # 两个负债指标都应在表头中出现，避免人读表时漏掉关键风险指标。
        self.assertIn("debt/asset", out)
        self.assertIn("ib_debt_ratio", out)

    def test_financials_csv_has_header_and_rows(self) -> None:
        out = _run_text(
            [
                "financials",
                "--fixture",
                str(FIXTURE),
                "--codes",
                "002371,600584",
                "--format",
                "csv",
            ]
        )
        lines = [ln for ln in out.splitlines() if ln.strip()]
        # 不能再退化为占位输出。
        self.assertNotIn("not implemented", lines[0])
        # 表头与 schema.FinancialEntry 字段顺序一致（含 interest_bearing_debt_ratio）。
        header = lines[0]
        self.assertTrue(
            header.startswith(
                "code,name,revenue_yoy,net_profit_yoy,deducted_net_profit_yoy,"
                "gross_margin,net_margin,roe,operating_cashflow_to_profit,"
                "free_cashflow,debt_to_asset,interest_bearing_debt_ratio,"
                "accounts_receivable_yoy,inventory_yoy,score,abnormal_flags,warnings"
            )
        )
        self.assertEqual(len(lines), 1 + 2)  # header + 2 公司
        self.assertTrue(any(ln.startswith("002371,") for ln in lines))
        self.assertTrue(any(ln.startswith("600584,") for ln in lines))

    def test_financials_csv_flags_joined_with_semicolon(self) -> None:
        # 600584 在 fixture 中至少触发 weak_cashflow / receivable_growth_risk /
        # gross_margin_decline 三个 flag，CSV 列里应以 ';' 拼接，而不是被逗号
        # 拆掉。
        out = _run_text(
            [
                "financials",
                "--fixture",
                str(FIXTURE),
                "--codes",
                "600584",
                "--format",
                "csv",
            ]
        )
        target = next(ln for ln in out.splitlines() if ln.startswith("600584,"))
        self.assertIn("weak_cashflow", target)
        self.assertIn("receivable_growth_risk", target)
        self.assertIn("gross_margin_decline", target)
        self.assertIn(";", target)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()