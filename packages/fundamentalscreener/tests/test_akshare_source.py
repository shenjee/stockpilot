"""Phase 6B: AkShareFundamentalDataSource 单元测试。

测试默认不访问任何真实网络。通过依赖注入把一个 fake akshare 模块传给
``AkShareFundamentalDataSource``，验证：

- 板块列表 / 成分 / 板块行情 / 基准行情的 DataFrame → ``List[Dict]`` 转换。
- ``em_industry`` 口径边界、不支持的 benchmark 抛错、公司层方法在 Phase 6B 返回空。
- ``sync_all`` 配合 AkShare 源能把板块层 4 类实体写入 SQLite 并留下 ``data_fetch_log``。
- 65 个交易日的板块/基准行情足以支撑 60 日收益窗口。
- 数据源抛错时同步任务记失败但不破坏已有缓存。

测试使用 ``_FakeDataFrame`` 替身代替 pandas DataFrame，无需安装 pandas 即可运行。
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict, List, Optional

from packages.fundamentalscreener.data_sources.akshare_source import (
    AkShareFundamentalDataSource,
)
from packages.fundamentalscreener.sqlite_schema import connect
from packages.fundamentalscreener.sync import main, sync_all


# ---------------------------------------------------------------------------
# fake akshare 构造
# ---------------------------------------------------------------------------


class _FakeDataFrame:
    """最小 DataFrame 替身，支持 ``len()`` 和 ``to_dict('records')``。

    AkShare 源代码只用到这两个操作；日期范围过滤在 ``_FakeAkshare`` 里用纯
    Python 完成，因此不需要真正的 pandas DataFrame。
    """

    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None) -> None:
        self._rows = list(rows) if rows else []

    def __len__(self) -> int:
        return len(self._rows)

    def to_dict(self, orient: str = "records") -> List[Dict[str, Any]]:
        return [dict(r) for r in self._rows]


def _empty_df(columns: Optional[List[str]] = None) -> Any:
    return _FakeDataFrame()


def _decompact(d: str) -> str:
    """``"20260320"`` -> ``"2026-03-20"``，用于 fake 内按日期范围过滤。"""

    return f"{d[0:4]}-{d[4:6]}-{d[6:8]}"


class _FakeAkshare:
    """实现 AkShareFundamentalDataSource 依赖的 4 个函数的内存替身。

    历史行情函数会按传入的 ``YYYYMMDD`` 起止日期过滤，借此验证源代码确实把
    ``YYYY-MM-DD`` 压缩成了无分隔符日期再传给 akshare。
    """

    def __init__(
        self,
        boards_df: Any,
        cons_map: Dict[str, Any],
        hist_map: Dict[str, Any],
        benchmark_map: Dict[str, Any],
        fail: bool = False,
    ) -> None:
        self.boards_df = boards_df
        self.cons_map = cons_map
        self.hist_map = hist_map
        self.benchmark_map = benchmark_map
        self.fail = fail
        self.calls: List[Dict[str, Any]] = []

    def _maybe_fail(self) -> None:
        if self.fail:
            raise RuntimeError("fake akshare network failure (test)")

    def stock_board_industry_name_em(self) -> Any:
        self._maybe_fail()
        self.calls.append({"func": "name"})
        return self.boards_df

    def stock_board_industry_cons_em(self, symbol: str) -> Any:
        self._maybe_fail()
        self.calls.append({"func": "cons", "symbol": symbol})
        return self.cons_map.get(symbol, _empty_df(["代码", "名称"]))

    def stock_board_industry_hist_em(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        period: str = "日k",
        adjust: str = "",
    ) -> Any:
        self._maybe_fail()
        self.calls.append(
            {
                "func": "hist",
                "symbol": symbol,
                "start_date": start_date,
                "end_date": end_date,
                "period": period,
                "adjust": adjust,
            }
        )
        df = self.hist_map.get(symbol, _empty_df(["日期", "收盘"]))
        if len(df) == 0:
            return df
        lo = _decompact(start_date)
        hi = _decompact(end_date)
        filtered = [r for r in df.to_dict(orient="records") if lo <= r["日期"] <= hi]
        return _FakeDataFrame(filtered)

    def index_zh_a_hist(
        self,
        symbol: str,
        period: str,
        start_date: str,
        end_date: str,
    ) -> Any:
        self._maybe_fail()
        self.calls.append(
            {
                "func": "index",
                "symbol": symbol,
                "period": period,
                "start_date": start_date,
                "end_date": end_date,
            }
        )
        df = self.benchmark_map.get(symbol, _empty_df(["日期", "收盘"]))
        if len(df) == 0:
            return df
        lo = _decompact(start_date)
        hi = _decompact(end_date)
        filtered = [r for r in df.to_dict(orient="records") if lo <= r["日期"] <= hi]
        return _FakeDataFrame(filtered)


def _gen_weekdays(n: int, end_iso: str) -> List[str]:
    """生成 n 个工作日（升序，以 end_iso 结尾）。"""

    end = date.fromisoformat(end_iso)
    days: List[str] = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d -= timedelta(days=1)
    return list(reversed(days))


def _build_fake_akshare(
    *,
    board_codes: Optional[List[tuple]] = None,
    n_days: int = 65,
    end_iso: str = "2026-06-19",
    fail: bool = False,
) -> _FakeAkshare:
    """构造一个带两块板块 + hs300 基准的 fake akshare。

    ``board_codes`` 是 ``[(bk_code, name), ...]`` 列表，默认两块。
    每块板块生成 ``n_days`` 个工作日行情，成分股固定为两只。
    """

    board_codes = board_codes or [("BK1001", "示例行业A"), ("BK1002", "示例行业B")]
    days = _gen_weekdays(n_days, end_iso)

    boards_rows = [
        {
            "排名": i + 1,
            "板块名称": name,
            "板块代码": code,
            "最新价": 1000.0 + i,
            "涨跌额": 10.0,
            "涨跌幅": 1.0,  # 单位 %
            "总市值": 1_000_000_000_000.0,
            "换手率": 2.0,
            "上涨家数": 30,
            "下跌家数": 10,
            "领涨股票": "示例股票",
            "领涨股票-涨跌幅": 9.98,
        }
        for i, (code, name) in enumerate(board_codes)
    ]
    boards_df = _FakeDataFrame(boards_rows)

    cons_map: Dict[str, Any] = {}
    hist_map: Dict[str, Any] = {}
    for i, (code, name) in enumerate(board_codes):
        cons_map[code] = _FakeDataFrame(
            [
                {"序号": 1, "代码": "002371", "名称": "示例公司A", "最新价": 10.0},
                {"序号": 2, "代码": "600584", "名称": "示例公司B", "最新价": 20.0},
            ]
        )
        hist_rows = [
            {
                "日期": d,
                "开盘": 100.0 + idx,
                "收盘": 101.0 + idx,
                "最高": 102.0 + idx,
                "最低": 99.0 + idx,
                "涨跌幅": 1.0,
                "涨跌额": 1.0,
                "成交量": 1_000_000,
                "成交额": 1_000_000_000.0 + idx,
                "振幅": 3.0,
                "换手率": 2.0,
            }
            for idx, d in enumerate(days)
        ]
        hist_map[code] = _FakeDataFrame(hist_rows)

    # hs300 -> 000300，同样 n_days 个工作日。
    bench_rows = [
        {
            "日期": d,
            "开盘": 3500.0 + idx,
            "收盘": 3510.0 + idx,
            "最高": 3520.0 + idx,
            "最低": 3490.0 + idx,
            "成交量": 10_000_000,
            "成交额": 100_000_000_000.0 + idx,
            "振幅": 1.0,
            "涨跌幅": 0.3,
            "涨跌额": 10.0,
            "换手率": 0.5,
        }
        for idx, d in enumerate(days)
    ]
    benchmark_map = {"000300": _FakeDataFrame(bench_rows)}

    return _FakeAkshare(
        boards_df=boards_df,
        cons_map=cons_map,
        hist_map=hist_map,
        benchmark_map=benchmark_map,
        fail=fail,
    )


# ---------------------------------------------------------------------------
# 转换逻辑测试
# ---------------------------------------------------------------------------


class AkShareSourceTransformTests(unittest.TestCase):
    def test_list_sectors_transforms_em_industry_boards(self) -> None:
        fake = _build_fake_akshare()
        src = AkShareFundamentalDataSource(akshare=fake)
        rows = src.list_sectors("em_industry")
        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first["sector_id"], "BK1001")
        self.assertEqual(first["sector_name"], "示例行业A")
        self.assertEqual(first["classification_system"], "em_industry")
        self.assertIsNotNone(first["source_updated_at"])
        # source / fetch_run_id / created_at / updated_at 由同步层填，源不提供。
        self.assertNotIn("source", first)
        self.assertNotIn("fetch_run_id", first)

    def test_list_sectors_rejects_non_em_industry(self) -> None:
        fake = _build_fake_akshare()
        src = AkShareFundamentalDataSource(akshare=fake)
        self.assertEqual(src.list_sectors("sw_l1"), [])
        self.assertEqual(src.list_sectors("em_concept"), [])
        # 非 em_industry 时不应调用 akshare。
        self.assertEqual(fake.calls, [])

    def test_list_sectors_skips_rows_missing_board_code(self) -> None:
        bad = _FakeDataFrame(
            [
                {"板块名称": "有代码", "板块代码": "BK1001"},
                {"板块名称": "无代码", "板块代码": None},
            ]
        )
        fake = _FakeAkshare(boards_df=bad, cons_map={}, hist_map={}, benchmark_map={})
        src = AkShareFundamentalDataSource(akshare=fake)
        rows = src.list_sectors("em_industry")
        self.assertEqual([r["sector_id"] for r in rows], ["BK1001"])

    def test_get_sector_constituents_transforms_codes(self) -> None:
        fake = _build_fake_akshare()
        src = AkShareFundamentalDataSource(akshare=fake)
        rows = src.get_sector_constituents("BK1001", "em_industry", "2026-06-19")
        self.assertEqual({r["code"] for r in rows}, {"002371", "600584"})
        for r in rows:
            self.assertEqual(r["sector_id"], "BK1001")
            self.assertEqual(r["classification_system"], "em_industry")
            self.assertEqual(r["as_of_date"], "2026-06-19")
        # 传 BK 代码给 akshare（而非板块名称）。
        self.assertTrue(all(c["symbol"] == "BK1001" for c in fake.calls if c["func"] == "cons"))

    def test_get_sector_constituents_non_em_industry_returns_empty(self) -> None:
        src = AkShareFundamentalDataSource(akshare=_build_fake_akshare())
        self.assertEqual(src.get_sector_constituents("BK1001", "sw_l1", "2026-06-19"), [])

    def test_get_sector_daily_transforms_ohlc_and_compacts_dates(self) -> None:
        fake = _build_fake_akshare(n_days=5, end_iso="2026-06-19")
        src = AkShareFundamentalDataSource(akshare=fake)
        rows = src.get_sector_daily(
            "BK1001", "em_industry", "2026-03-20", "2026-06-19"
        )
        self.assertEqual(len(rows), 5)
        first = rows[0]
        self.assertEqual(first["sector_id"], "BK1001")
        self.assertEqual(first["classification_system"], "em_industry")
        self.assertEqual(first["trade_date"], _gen_weekdays(5, "2026-06-19")[0])
        self.assertEqual(first["open"], 100.0)
        self.assertEqual(first["close"], 101.0)
        self.assertEqual(first["high"], 102.0)
        self.assertEqual(first["low"], 99.0)
        self.assertEqual(first["turnover_amount"], 1_000_000_000.0)
        # rising_count / total_count 东方财富板块历史行情不提供，应为 None。
        self.assertNotIn("rising_count", first)
        # 日期被压缩成 YYYYMMDD 传给 akshare。
        hist_call = next(c for c in fake.calls if c["func"] == "hist")
        self.assertEqual(hist_call["start_date"], "20260320")
        self.assertEqual(hist_call["end_date"], "20260619")
        self.assertEqual(hist_call["period"], "日k")
        self.assertEqual(hist_call["adjust"], "")

    def test_get_sector_daily_handles_nan_and_missing_values(self) -> None:
        hist = _FakeDataFrame(
            [
                {"日期": "2026-06-19", "开盘": float("nan"), "收盘": 101.0,
                 "最高": None, "最低": 99.0, "成交额": "非数"},
            ]
        )
        fake = _FakeAkshare(
            boards_df=_FakeDataFrame(), cons_map={}, hist_map={"BK1001": hist},
            benchmark_map={},
        )
        src = AkShareFundamentalDataSource(akshare=fake)
        rows = src.get_sector_daily("BK1001", "em_industry", "2026-06-01", "2026-06-19")
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertIsNone(r["open"])  # NaN -> None
        self.assertEqual(r["close"], 101.0)
        self.assertIsNone(r["high"])  # None -> None
        self.assertEqual(r["low"], 99.0)
        self.assertIsNone(r["turnover_amount"])  # 非数 -> None

    def test_get_benchmark_daily_hs300(self) -> None:
        fake = _build_fake_akshare(n_days=3, end_iso="2026-06-19")
        src = AkShareFundamentalDataSource(akshare=fake)
        rows = src.get_benchmark_daily("hs300", "2026-03-20", "2026-06-19")
        self.assertEqual(len(rows), 3)
        first = rows[0]
        self.assertEqual(first["benchmark"], "hs300")
        self.assertEqual(first["trade_date"], _gen_weekdays(3, "2026-06-19")[0])
        self.assertEqual(first["close"], 3510.0)
        self.assertEqual(first["turnover_amount"], 100_000_000_000.0)
        # 传 000300 指数代码给 index_zh_a_hist。
        idx_call = next(c for c in fake.calls if c["func"] == "index")
        self.assertEqual(idx_call["symbol"], "000300")
        self.assertEqual(idx_call["period"], "daily")
        self.assertEqual(idx_call["start_date"], "20260320")

    def test_get_benchmark_daily_unsupported_raises(self) -> None:
        src = AkShareFundamentalDataSource(akshare=_build_fake_akshare())
        with self.assertRaises(ValueError):
            src.get_benchmark_daily("nope", "2026-03-20", "2026-06-19")

    def test_company_layer_methods_return_empty_in_phase_6b(self) -> None:
        src = AkShareFundamentalDataSource(akshare=_build_fake_akshare())
        self.assertEqual(src.get_stock_universe("2026-06-19"), [])
        self.assertEqual(src.get_company_daily_snapshot("2026-06-19"), [])
        self.assertEqual(src.get_company_valuation_history(["002371"], "2026-03-20", "2026-06-19"), [])
        self.assertEqual(src.get_financial_metrics(["002371"], "2026-06-19"), [])

    def test_empty_dataframes_return_empty_lists(self) -> None:
        fake = _FakeAkshare(
            boards_df=_FakeDataFrame(),
            cons_map={},
            hist_map={},
            benchmark_map={},
        )
        src = AkShareFundamentalDataSource(akshare=fake)
        self.assertEqual(src.list_sectors("em_industry"), [])
        self.assertEqual(src.get_sector_constituents("BK1001", "em_industry", "2026-06-19"), [])
        self.assertEqual(src.get_sector_daily("BK1001", "em_industry", "2026-03-20", "2026-06-19"), [])
        # 未映射的 benchmark symbol -> 空 DF -> 空列表（不会进入 index_zh_a_hist，
        # 因为 hs300 已映射；这里验证空 DF 路径）。
        rows = src.get_benchmark_daily("hs300", "2026-03-20", "2026-06-19")
        self.assertEqual(rows, [])


# ---------------------------------------------------------------------------
# sync_all 集成测试
# ---------------------------------------------------------------------------


class AkShareSyncIntegrationTests(unittest.TestCase):
    def test_sync_all_writes_sector_layer_with_akshare_source(self) -> None:
        fake = _build_fake_akshare()
        src = AkShareFundamentalDataSource(akshare=fake)
        conn = connect(":memory:")
        try:
            result = sync_all(
                conn,
                src,
                analysis_date="2026-06-19",
                classification_system="em_industry",
                benchmark="hs300",
                history_days=90,
            )
            # 板块层 4 个任务必须成功；公司层 4 个任务以 0 行成功。
            by_task = {t["task"]: t for t in result.tasks}
            for task in ("list_sectors", "get_sector_constituents", "get_sector_daily", "get_benchmark_daily"):
                self.assertTrue(by_task[task]["success"], f"{task} should succeed")
                self.assertGreater(by_task[task]["row_count"], 0, f"{task} should write rows")
            for task in ("get_stock_universe", "get_company_daily_snapshot",
                         "get_company_valuation_history", "get_financial_metrics"):
                self.assertTrue(by_task[task]["success"], f"{task} should succeed with 0 rows")
                self.assertEqual(by_task[task]["row_count"], 0)

            # sectors 表写入 2 块，source=akshare_em。
            sectors = conn.execute(
                "SELECT sector_id, sector_name, source, classification_system FROM sectors ORDER BY sector_id"
            ).fetchall()
            self.assertEqual(len(sectors), 2)
            self.assertEqual(sectors[0][0], "BK1001")
            self.assertEqual(sectors[0][1], "示例行业A")
            self.assertEqual(sectors[0][2], "akshare_em")
            self.assertEqual(sectors[0][3], "em_industry")

            # 成分股 4 条（2 板块 × 2 股）。
            n_cons = conn.execute("SELECT COUNT(*) FROM sector_constituents").fetchone()[0]
            self.assertEqual(n_cons, 4)

            # 板块日线 2 × 65 = 130 条。
            n_daily = conn.execute("SELECT COUNT(*) FROM sector_daily_bars").fetchone()[0]
            self.assertEqual(n_daily, 130)

            # 基准日线 65 条，独立表，不带 classification_system。
            n_bench = conn.execute("SELECT COUNT(*) FROM benchmark_daily_bars").fetchone()[0]
            self.assertEqual(n_bench, 65)
            bench = conn.execute(
                "SELECT benchmark, close, turnover_amount FROM benchmark_daily_bars ORDER BY trade_date LIMIT 1"
            ).fetchone()
            self.assertEqual(bench[0], "hs300")
            self.assertEqual(bench[1], 3510.0)

            # data_fetch_log 有成功行，source=akshare_em。
            logs = conn.execute(
                "SELECT task, success, source FROM data_fetch_log ORDER BY id"
            ).fetchall()
            self.assertGreater(len(logs), 0)
            self.assertTrue(all(row[2] == "akshare_em" for row in logs))
            self.assertTrue(any(row[1] == 1 for row in logs))
        finally:
            conn.close()

    def test_sync_all_sector_daily_covers_60_day_return_window(self) -> None:
        # DoD：板块日线和 benchmark 至少支持 1/5/20/60 日收益。
        # 用 65 个工作日行情 + history_days=90，验证每块板块和 benchmark 都写入 >=61 条。
        fake = _build_fake_akshare(n_days=65, end_iso="2026-06-19")
        src = AkShareFundamentalDataSource(akshare=fake)
        conn = connect(":memory:")
        try:
            sync_all(
                conn,
                src,
                analysis_date="2026-06-19",
                classification_system="em_industry",
                benchmark="hs300",
                history_days=90,
            )
            for sector_id in ("BK1001", "BK1002"):
                n = conn.execute(
                    "SELECT COUNT(*) FROM sector_daily_bars WHERE sector_id=?", (sector_id,)
                ).fetchone()[0]
                self.assertGreaterEqual(n, 61, f"{sector_id} needs >=61 bars for 60d return")
            n_bench = conn.execute("SELECT COUNT(*) FROM benchmark_daily_bars").fetchone()[0]
            self.assertGreaterEqual(n_bench, 61, "benchmark needs >=61 bars for 60d return")
        finally:
            conn.close()

    def test_akshare_source_failure_preserves_cache(self) -> None:
        # 第一次成功写入，第二次 fake 抛错：失败任务记入 data_fetch_log，但第一次的
        # 板块/基准缓存不被破坏。
        fake = _build_fake_akshare(n_days=5, end_iso="2026-06-19")
        src = AkShareFundamentalDataSource(akshare=fake)
        conn = connect(":memory:")
        try:
            first = sync_all(
                conn, src, analysis_date="2026-06-19",
                classification_system="em_industry", benchmark="hs300", history_days=90,
            )
            self.assertGreater(first.success_count, 0)
            cached_sectors = conn.execute("SELECT COUNT(*) FROM sectors").fetchone()[0]
            cached_bench = conn.execute("SELECT COUNT(*) FROM benchmark_daily_bars").fetchone()[0]
            self.assertGreater(cached_sectors, 0)
            self.assertGreater(cached_bench, 0)

            # 第二次：fake 网络失败。
            fake.fail = True
            second = sync_all(
                conn, src, analysis_date="2026-06-19",
                classification_system="em_industry", benchmark="hs300", history_days=90,
            )
            self.assertGreater(second.failure_count, 0)
            # 缓存仍在。
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM sectors").fetchone()[0], cached_sectors
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM benchmark_daily_bars").fetchone()[0], cached_bench
            )
            # data_fetch_log 记录了第二次 fetch_run_id 的失败行。
            fail_logs = conn.execute(
                "SELECT COUNT(*) FROM data_fetch_log WHERE success=0 AND fetch_run_id=?",
                (second.fetch_run_id,),
            ).fetchone()[0]
            self.assertGreater(fail_logs, 0)
        finally:
            conn.close()

    def test_sync_cli_with_injected_akshare_source(self) -> None:
        # CLI sync 接线：注入 AkShareFundamentalDataSource(fake) 后 rc=0、输出 JSON、
        # 板块层写入磁盘。不触达 akshare 可用性探测（source 已注入）。
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "fundamental.sqlite"
            src = AkShareFundamentalDataSource(akshare=_build_fake_akshare(n_days=5, end_iso="2026-06-19"))
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(
                    [
                        "sync", "--db", str(db_path), "--date", "2026-06-19",
                        "--classification-system", "em_industry", "--benchmark", "hs300",
                    ],
                    source=src,
                )
            self.assertEqual(rc, 0)
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["command"], "sync")
            self.assertGreater(payload["success_count"], 0)
            conn = connect(str(db_path))
            try:
                self.assertGreater(
                    conn.execute("SELECT COUNT(*) FROM sectors").fetchone()[0], 0
                )
            finally:
                conn.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
