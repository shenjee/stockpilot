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
from unittest.mock import patch

from packages.fundamentalscreener.data_sources.akshare_source import (
    AkShareFundamentalDataSource,
)
from packages.fundamentalscreener.sqlite_schema import connect
from packages.fundamentalscreener.sync import main, sync_all

# THS 成分股抓取测试需要 requests + beautifulsoup4 + lxml 解析器（akshare 的依赖）。
# 项目设计原则是测试在最小依赖环境也能跑（fake 注入不联网），因此缺这些
# 依赖时跳过抓取测试，而非整组失败。仅检查 import 不够：bs4 装了但缺 lxml
# 时 BeautifulSoup(..., features="lxml") 会在调用时才报 FeatureNotFound。
try:
    import requests  # noqa: F401
    from bs4 import BeautifulSoup  # noqa: F401
    BeautifulSoup("<html></html>", features="lxml")
    _HAS_SCRAPE_DEPS = True
except (ImportError, Exception):  # noqa: BLE001
    _HAS_SCRAPE_DEPS = False


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
    """实现 AkShareFundamentalDataSource 依赖的函数的内存替身。

    历史行情函数会按传入的 ``YYYYMMDD`` 起止日期过滤，借此验证源代码确实把
    ``YYYY-MM-DD`` 压缩成了无分隔符日期再传给 akshare。

    同时支持 EM（东方财富）和 THS（同花顺）两套板块 API：
    - EM: ``stock_board_industry_name_em`` / ``_cons_em`` / ``_hist_em``
    - THS: ``stock_board_industry_name_ths`` / ``_index_ths`` / ``_cons_ths``
    """

    def __init__(
        self,
        boards_df: Any = None,
        cons_map: Optional[Dict[str, Any]] = None,
        hist_map: Optional[Dict[str, Any]] = None,
        benchmark_map: Optional[Dict[str, Any]] = None,
        universe_df: Any = None,
        spot_df: Any = None,
        valuation_map: Optional[Dict[str, Dict[str, Any]]] = None,
        financial_map: Optional[Dict[str, Any]] = None,
        fail: bool = False,
        # THS 板块数据
        boards_df_ths: Any = None,
        cons_map_ths: Optional[Dict[str, Any]] = None,
        hist_map_ths: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.boards_df = boards_df or _empty_df()
        self.cons_map = cons_map or {}
        self.hist_map = hist_map or {}
        self.benchmark_map = benchmark_map or {}
        self.universe_df = universe_df or _empty_df()
        self.spot_df = spot_df or _empty_df()
        self.valuation_map = valuation_map or {}
        self.financial_map = financial_map or {}
        self.fail = fail
        self.calls: List[Dict[str, Any]] = []
        # THS
        self.boards_df_ths = boards_df_ths or _empty_df()
        self.cons_map_ths = cons_map_ths or {}
        self.hist_map_ths = hist_map_ths or {}

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

    # ---------------- THS 板块层 ----------------

    def stock_board_industry_name_ths(self) -> Any:
        self._maybe_fail()
        self.calls.append({"func": "name_ths"})
        return self.boards_df_ths

    def stock_board_industry_cons_ths(self, symbol: str) -> Any:
        self._maybe_fail()
        self.calls.append({"func": "cons_ths", "symbol": symbol})
        return self.cons_map_ths.get(symbol, _empty_df(["代码", "名称"]))

    def stock_board_industry_index_ths(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> Any:
        self._maybe_fail()
        self.calls.append(
            {
                "func": "hist_ths",
                "symbol": symbol,
                "start_date": start_date,
                "end_date": end_date,
            }
        )
        df = self.hist_map_ths.get(symbol, _empty_df(["日期", "收盘价"]))
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

    # ---------------- 公司层（Phase 6C）----------------

    def stock_info_a_code_name(self) -> Any:
        self._maybe_fail()
        self.calls.append({"func": "universe"})
        return self.universe_df

    def stock_zh_a_spot_em(self) -> Any:
        self._maybe_fail()
        self.calls.append({"func": "spot"})
        return self.spot_df

    def stock_zh_valuation_baidu(
        self, symbol: str, indicator: str, period: str = "全部"
    ) -> Any:
        self._maybe_fail()
        self.calls.append(
            {"func": "valuation", "symbol": symbol, "indicator": indicator, "period": period}
        )
        return self.valuation_map.get(symbol, {}).get(indicator, _empty_df())

    def stock_financial_analysis_indicator(
        self, symbol: str, start_year: str = "1900"
    ) -> Any:
        self._maybe_fail()
        self.calls.append(
            {"func": "financial", "symbol": symbol, "start_year": start_year}
        )
        return self.financial_map.get(symbol, _empty_df())


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


def _build_ths_fake_akshare(
    *,
    board_codes: Optional[List[tuple]] = None,
    n_days: int = 65,
    end_iso: str = "2026-06-19",
    fail: bool = False,
) -> _FakeAkshare:
    """构造一个带两块同花顺板块 + hs300 基准的 fake akshare。

    ``board_codes`` 是 ``[(ths_code, name), ...]`` 列表，默认两块。
    THS 板块列表列名是 ``name`` / ``code``（不同于 EM 的 ``板块名称`` / ``板块代码``）。
    THS 指数列名是 ``开盘价`` / ``最高价`` / ``最低价`` / ``收盘价``（带"价"后缀）。
    THS 指数接口按板块**名称**查询，因此 ``hist_map_ths`` 的 key 是名称。
    """
    board_codes = board_codes or [("881121", "半导体"), ("881273", "白酒")]
    days = _gen_weekdays(n_days, end_iso)

    boards_rows_ths = [
        {"name": name, "code": code}
        for code, name in board_codes
    ]
    boards_df_ths = _FakeDataFrame(boards_rows_ths)

    cons_map_ths: Dict[str, Any] = {}
    hist_map_ths: Dict[str, Any] = {}
    for code, name in board_codes:
        cons_map_ths[code] = _FakeDataFrame(
            [
                {"序号": 1, "代码": "300077", "名称": "国民技术", "现价": 26.73},
                {"序号": 2, "代码": "002371", "名称": "北方华创", "现价": 350.0},
            ]
        )
        hist_rows = [
            {
                "日期": d,
                "开盘价": 19000.0 + idx,
                "最高价": 19500.0 + idx,
                "最低价": 18500.0 + idx,
                "收盘价": 19200.0 + idx,
                "成交量": 5_000_000,
                "成交额": 50_000_000_000.0 + idx,
            }
            for idx, d in enumerate(days)
        ]
        # THS 指数接口按名称查询
        hist_map_ths[name] = _FakeDataFrame(hist_rows)

    # hs300 基准（与 EM 共用 index_zh_a_hist）
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
        boards_df_ths=boards_df_ths,
        cons_map_ths=cons_map_ths,
        hist_map_ths=hist_map_ths,
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
        src = AkShareFundamentalDataSource(akshare=_build_fake_akshare(), today="2026-06-19")
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


class AkShareSourceTHSTransformTests(unittest.TestCase):
    """同花顺（ths_industry）口径的转换逻辑测试。"""

    def test_list_sectors_transforms_ths_industry_boards(self) -> None:
        fake = _build_ths_fake_akshare()
        src = AkShareFundamentalDataSource(akshare=fake)
        rows = src.list_sectors("ths_industry")
        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first["sector_id"], "881121")
        self.assertEqual(first["sector_name"], "半导体")
        self.assertEqual(first["classification_system"], "ths_industry")
        self.assertIsNotNone(first["source_updated_at"])
        self.assertNotIn("source", first)
        self.assertNotIn("fetch_run_id", first)

    def test_list_sectors_ths_caches_name_mapping(self) -> None:
        """list_sectors 应缓存 code→name 映射，供 get_sector_daily 使用。"""
        fake = _build_ths_fake_akshare()
        src = AkShareFundamentalDataSource(akshare=fake)
        src.list_sectors("ths_industry")
        self.assertEqual(src._ths_name_cache.get("881121"), "半导体")
        self.assertEqual(src._ths_name_cache.get("881273"), "白酒")

    def test_list_sectors_ths_skips_rows_missing_code(self) -> None:
        bad = _FakeDataFrame(
            [
                {"name": "半导体", "code": "881121"},
                {"name": "无代码", "code": None},
            ]
        )
        fake = _FakeAkshare(boards_df_ths=bad)
        src = AkShareFundamentalDataSource(akshare=fake)
        rows = src.list_sectors("ths_industry")
        self.assertEqual([r["sector_id"] for r in rows], ["881121"])

    def test_get_sector_constituents_ths_via_injected_method(self) -> None:
        """THS 成分股通过 fake akshare 的 stock_board_industry_cons_ths 注入。"""
        fake = _build_ths_fake_akshare()
        src = AkShareFundamentalDataSource(akshare=fake)
        rows = src.get_sector_constituents("881121", "ths_industry", "2026-06-19")
        self.assertEqual({r["code"] for r in rows}, {"300077", "002371"})
        for r in rows:
            self.assertEqual(r["sector_id"], "881121")
            self.assertEqual(r["classification_system"], "ths_industry")
            self.assertEqual(r["as_of_date"], "2026-06-19")
        # 传板块代码给 cons_ths
        cons_calls = [c for c in fake.calls if c["func"] == "cons_ths"]
        self.assertTrue(all(c["symbol"] == "881121" for c in cons_calls))

    def test_get_sector_daily_ths_maps_code_to_name(self) -> None:
        """THS 板块日线通过 code→name 映射调用 stock_board_industry_index_ths。

        验证：
        - 日期被压缩成 YYYYMMDD 传给 akshare
        - 传给 index_ths 的是板块**名称**（半导体），不是代码（881121）
        - OHLC 字段从 ``开盘价`` / ``收盘价`` 等带"价"后缀的列名提取
        """
        fake = _build_ths_fake_akshare(n_days=5, end_iso="2026-06-19")
        src = AkShareFundamentalDataSource(akshare=fake)
        # 先调 list_sectors 填充缓存
        src.list_sectors("ths_industry")
        rows = src.get_sector_daily("881121", "ths_industry", "2026-03-20", "2026-06-19")
        self.assertEqual(len(rows), 5)
        first = rows[0]
        self.assertEqual(first["sector_id"], "881121")
        self.assertEqual(first["classification_system"], "ths_industry")
        self.assertEqual(first["trade_date"], _gen_weekdays(5, "2026-06-19")[0])
        self.assertEqual(first["open"], 19000.0)
        self.assertEqual(first["close"], 19200.0)
        self.assertEqual(first["high"], 19500.0)
        self.assertEqual(first["low"], 18500.0)
        self.assertEqual(first["turnover_amount"], 50_000_000_000.0)
        # 传板块名称（而非代码）给 index_ths
        hist_call = next(c for c in fake.calls if c["func"] == "hist_ths")
        self.assertEqual(hist_call["symbol"], "半导体")
        self.assertEqual(hist_call["start_date"], "20260320")
        self.assertEqual(hist_call["end_date"], "20260619")

    def test_get_sector_daily_ths_handles_nan_and_missing(self) -> None:
        hist = _FakeDataFrame(
            [
                {"日期": "2026-06-19", "开盘价": float("nan"), "最高价": None,
                 "最低价": 18500.0, "收盘价": 19200.0, "成交额": "非数"},
            ]
        )
        fake = _FakeAkshare(
            boards_df_ths=_FakeDataFrame([{"name": "半导体", "code": "881121"}]),
            hist_map_ths={"半导体": hist},
        )
        src = AkShareFundamentalDataSource(akshare=fake)
        rows = src.get_sector_daily("881121", "ths_industry", "2026-06-01", "2026-06-19")
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertIsNone(r["open"])  # NaN -> None
        self.assertEqual(r["close"], 19200.0)
        self.assertIsNone(r["high"])  # None -> None
        self.assertEqual(r["low"], 18500.0)
        self.assertIsNone(r["turnover_amount"])  # 非数 -> None

    def test_get_sector_daily_ths_unknown_code_returns_empty(self) -> None:
        """板块代码不在缓存中且 name_ths 也找不到时返回空列表。"""
        fake = _build_ths_fake_akshare()
        src = AkShareFundamentalDataSource(akshare=fake)
        rows = src.get_sector_daily("999999", "ths_industry", "2026-03-20", "2026-06-19")
        self.assertEqual(rows, [])

    def test_ths_constituents_empty_for_empty_sector(self) -> None:
        """空 sector_id 返回空列表，不调用 akshare。"""
        fake = _build_ths_fake_akshare()
        src = AkShareFundamentalDataSource(akshare=fake)
        self.assertEqual(src.get_sector_constituents("", "ths_industry", "2026-06-19"), [])
        # 不应调用 cons_ths
        self.assertFalse(any(c["func"] == "cons_ths" for c in fake.calls))

    def test_ths_and_em_do_not_cross_contaminate(self) -> None:
        """ths_industry 请求不应触发 EM API 调用，反之亦然。"""
        fake = _build_ths_fake_akshare()
        # 同时给 EM 数据，确保 THS 路径不误调 EM
        fake.boards_df = _FakeDataFrame([{"板块名称": "EM板块", "板块代码": "BK1001"}])
        src = AkShareFundamentalDataSource(akshare=fake)
        src.list_sectors("ths_industry")
        # THS 路径只调 name_ths，不调 name（EM）
        self.assertTrue(any(c["func"] == "name_ths" for c in fake.calls))
        self.assertFalse(any(c["func"] == "name" for c in fake.calls))


# ---------------------------------------------------------------------------
# sync_all 集成测试
# ---------------------------------------------------------------------------


class AkShareSyncIntegrationTests(unittest.TestCase):
    def test_sync_all_writes_sector_layer_with_akshare_source(self) -> None:
        fake = _build_fake_akshare()
        src = AkShareFundamentalDataSource(akshare=fake, today="2026-06-19", name="akshare_em")
        conn = connect(":memory:")
        try:
            result = sync_all(
                conn,
                src,
                analysis_date="2026-06-19",
                classification_system="em_industry",
                benchmark="hs300",
                history_days=90,
                codes=["002371"],
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
        src = AkShareFundamentalDataSource(akshare=fake, today="2026-06-19")
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
        src = AkShareFundamentalDataSource(akshare=fake, today="2026-06-19")
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
            src = AkShareFundamentalDataSource(akshare=_build_fake_akshare(n_days=5, end_iso="2026-06-19"), today="2026-06-19")
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


class AkShareTHSSyncIntegrationTests(unittest.TestCase):
    """同花顺（ths_industry）口径的 sync_all 集成测试。"""

    def test_sync_all_writes_ths_sector_layer(self) -> None:
        """THS 板块层 4 个任务成功写入，source=akshare_ths，classification_system=ths_industry。"""
        fake = _build_ths_fake_akshare()
        src = AkShareFundamentalDataSource(akshare=fake, today="2026-06-19")
        conn = connect(":memory:")
        try:
            result = sync_all(
                conn,
                src,
                analysis_date="2026-06-19",
                classification_system="ths_industry",
                benchmark="hs300",
                history_days=90,
            )
            # 板块层 4 个任务必须成功且写入行数 > 0
            by_task = {t["task"]: t for t in result.tasks}
            for task in ("list_sectors", "get_sector_constituents",
                         "get_sector_daily", "get_benchmark_daily"):
                self.assertTrue(by_task[task]["success"], f"{task} should succeed")
                self.assertGreater(by_task[task]["row_count"], 0, f"{task} should write rows")

            # sectors 表写入 2 块，source=akshare_ths，classification_system=ths_industry
            sectors = conn.execute(
                "SELECT sector_id, sector_name, source, classification_system "
                "FROM sectors ORDER BY sector_id"
            ).fetchall()
            self.assertEqual(len(sectors), 2)
            self.assertEqual(sectors[0][0], "881121")
            self.assertEqual(sectors[0][1], "半导体")
            self.assertEqual(sectors[0][2], "akshare_ths")
            self.assertEqual(sectors[0][3], "ths_industry")

            # 成分股 4 条（2 板块 × 2 股）
            n_cons = conn.execute("SELECT COUNT(*) FROM sector_constituents").fetchone()[0]
            self.assertEqual(n_cons, 4)

            # 板块日线 2 × 65 = 130 条
            n_daily = conn.execute("SELECT COUNT(*) FROM sector_daily_bars").fetchone()[0]
            self.assertEqual(n_daily, 130)

            # 板块日线 classification_system=ths_industry
            cs = conn.execute(
                "SELECT DISTINCT classification_system FROM sector_daily_bars"
            ).fetchall()
            self.assertEqual(len(cs), 1)
            self.assertEqual(cs[0][0], "ths_industry")

            # 基准日线 65 条
            n_bench = conn.execute("SELECT COUNT(*) FROM benchmark_daily_bars").fetchone()[0]
            self.assertEqual(n_bench, 65)

            # data_fetch_log source=akshare_ths
            logs = conn.execute(
                "SELECT DISTINCT source FROM data_fetch_log"
            ).fetchall()
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0][0], "akshare_ths")
        finally:
            conn.close()

    def test_sync_all_ths_daily_covers_60_day_window(self) -> None:
        """THS 板块日线和 benchmark 至少 61 条以支撑 60 日收益。"""
        fake = _build_ths_fake_akshare(n_days=65, end_iso="2026-06-19")
        src = AkShareFundamentalDataSource(akshare=fake, today="2026-06-19")
        conn = connect(":memory:")
        try:
            sync_all(
                conn,
                src,
                analysis_date="2026-06-19",
                classification_system="ths_industry",
                benchmark="hs300",
                history_days=90,
            )
            for sector_id in ("881121", "881273"):
                n = conn.execute(
                    "SELECT COUNT(*) FROM sector_daily_bars WHERE sector_id=?",
                    (sector_id,),
                ).fetchone()[0]
                self.assertGreaterEqual(n, 61, f"{sector_id} needs >=61 bars for 60d return")
            n_bench = conn.execute("SELECT COUNT(*) FROM benchmark_daily_bars").fetchone()[0]
            self.assertGreaterEqual(n_bench, 61)
        finally:
            conn.close()

    def test_sync_cli_ths_industry(self) -> None:
        """CLI sync 默认使用 ths_industry 口径，rc=0。"""
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "fundamental.sqlite"
            src = AkShareFundamentalDataSource(
                akshare=_build_ths_fake_akshare(n_days=5, end_iso="2026-06-19"),
                today="2026-06-19",
            )
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(
                    [
                        "sync", "--db", str(db_path), "--date", "2026-06-19",
                        "--classification-system", "ths_industry", "--benchmark", "hs300",
                    ],
                    source=src,
                )
            self.assertEqual(rc, 0)
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["classification_system"], "ths_industry")
            self.assertGreater(payload["success_count"], 0)
            conn = connect(str(db_path))
            try:
                sectors = conn.execute(
                    "SELECT classification_system FROM sectors LIMIT 1"
                ).fetchone()
                self.assertEqual(sectors[0], "ths_industry")
            finally:
                conn.close()


# ---------------------------------------------------------------------------
# THS 成分股抓取路径测试（HTML fixture）
# ---------------------------------------------------------------------------


class _FakeResponse:
    """requests.Response 的最小替身，用于 _scrape_ths_constituents 测试。"""

    def __init__(self, text: str = "", status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


_THS_MAIN_PAGE_HTML = """<html><body>
<table>
<thead><tr><th>序号</th><th>代码</th><th>名称</th><th>现价</th></tr></thead>
<tbody>
<tr><td>1</td><td>300077</td><td>国民技术</td><td>26.73</td></tr>
<tr><td>2</td><td>002371</td><td>北方华创</td><td>350.00</td></tr>
</tbody>
</table>
<div class="m-pager">1  2  3  下一页尾页1/3</div>
</body></html>"""

_THS_AJAX_PAGE_2 = """<table>
<tbody>
<tr><td>21</td><td>600584</td><td>长电科技</td><td>28.50</td></tr>
<tr><td>22</td><td>603501</td><td>韦尔股份</td><td>120.00</td></tr>
</tbody>
</table>"""

_THS_AJAX_PAGE_3 = """<table>
<tbody>
<tr><td>41</td><td>300613</td><td>富瀚微</td><td>45.60</td></tr>
</tbody>
</table>"""

_THS_SINGLE_PAGE_HTML = """<html><body>
<table>
<tbody>
<tr><td>1</td><td>300077</td><td>国民技术</td><td>26.73</td></tr>
<tr><td>2</td><td>002371</td><td>北方华创</td><td>350.00</td></tr>
</tbody>
</table>
<div class="m-pager">1/1</div>
</body></html>"""

_THS_EMPTY_TABLE_HTML = """<html><body>
<table><tbody></tbody></table>
<div class="m-pager">1/1</div>
</body></html>"""


class _ScrapeOnlyTHSFake:
    """无 stock_board_industry_cons_ths 的 fake akshare，强制走抓取路径。"""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def stock_board_industry_name_ths(self) -> Any:
        self.calls.append({"func": "name_ths"})
        return _FakeDataFrame([{"name": "半导体", "code": "881121"}])


@unittest.skipUnless(_HAS_SCRAPE_DEPS, "requires requests + beautifulsoup4 + lxml")
class AkShareTHSScrapeTests(unittest.TestCase):
    """THS 成分股抓取路径（_scrape_ths_constituents）的 HTML fixture 测试。

    真实 AkShare 没有 stock_board_industry_cons_ths 接口，生产路径走网页抓取。
    用 mock requests.get + 真实 BeautifulSoup 验证：单页解析、多页 AJAX 分页、
    HTTP 错误（主页 + AJAX 页）、空表、解析器单元。
    """

    @patch("requests.get")
    def test_scrape_single_page_extracts_all_codes(self, mock_get: Any) -> None:
        """单页（1/1）：解析全部成分股代码，只请求一次。"""
        mock_get.return_value = _FakeResponse(text=_THS_SINGLE_PAGE_HTML)
        src = AkShareFundamentalDataSource(akshare=_FakeAkshare())
        records = src._scrape_ths_constituents("881121")
        codes = {r["代码"] for r in records}
        self.assertEqual(codes, {"300077", "002371"})
        self.assertEqual(mock_get.call_count, 1)

    @patch("requests.get")
    def test_scrape_multi_page_follows_pagination(self, mock_get: Any) -> None:
        """多页（1/3）：第一页解析总页数，后续 2 页走 AJAX 端点。"""

        def _side_effect(
            url: str, headers: Any = None, timeout: Any = None
        ) -> _FakeResponse:
            if "/page/" in url:
                page = int(url.split("/page/")[1].split("/")[0])
                if page == 2:
                    return _FakeResponse(text=_THS_AJAX_PAGE_2)
                if page == 3:
                    return _FakeResponse(text=_THS_AJAX_PAGE_3)
            return _FakeResponse(text=_THS_MAIN_PAGE_HTML)

        mock_get.side_effect = _side_effect
        src = AkShareFundamentalDataSource(akshare=_FakeAkshare())
        records = src._scrape_ths_constituents("881121")
        codes = {r["代码"] for r in records}
        self.assertEqual(codes, {"300077", "002371", "600584", "603501", "300613"})
        # 1 主页 + 2 AJAX = 3
        self.assertEqual(mock_get.call_count, 3)

    @patch("requests.get")
    def test_scrape_http_error_raises(self, mock_get: Any) -> None:
        """HTTP 403 → raise_for_status 抛错，不静默返回空。"""
        mock_get.return_value = _FakeResponse(text="", status_code=403)
        src = AkShareFundamentalDataSource(akshare=_FakeAkshare())
        with self.assertRaises(RuntimeError):
            src._scrape_ths_constituents("881121")

    @patch("requests.get")
    def test_scrape_ajax_http_error_raises(self, mock_get: Any) -> None:
        """AJAX 分页 HTTP 500 也要抛错，不静默跳过。"""

        def _side_effect(
            url: str, headers: Any = None, timeout: Any = None
        ) -> _FakeResponse:
            if "/page/" in url:
                return _FakeResponse(text="", status_code=500)
            return _FakeResponse(text=_THS_MAIN_PAGE_HTML)

        mock_get.side_effect = _side_effect
        src = AkShareFundamentalDataSource(akshare=_FakeAkshare())
        with self.assertRaises(RuntimeError):
            src._scrape_ths_constituents("881121")

    @patch("requests.get")
    def test_scrape_empty_table_returns_empty(self, mock_get: Any) -> None:
        """页面正常加载但表格为空 → 返回空列表（sync 层 guard 会捕获）。"""
        mock_get.return_value = _FakeResponse(text=_THS_EMPTY_TABLE_HTML)
        src = AkShareFundamentalDataSource(akshare=_FakeAkshare())
        records = src._scrape_ths_constituents("881121")
        self.assertEqual(records, [])

    @patch("requests.get")
    def test_get_constituents_falls_through_to_scrape(self, mock_get: Any) -> None:
        """akshare 无 cons_ths 方法时，get_sector_constituents 走抓取路径。"""
        mock_get.return_value = _FakeResponse(text=_THS_SINGLE_PAGE_HTML)
        fake = _ScrapeOnlyTHSFake()
        src = AkShareFundamentalDataSource(akshare=fake)
        rows = src.get_sector_constituents("881121", "ths_industry", "2026-06-19")
        self.assertEqual({r["code"] for r in rows}, {"300077", "002371"})
        self.assertTrue(mock_get.called)

    def test_parse_stock_table_extracts_codes_and_names(self) -> None:
        """_parse_ths_stock_table：提取第二列代码和第三列名称。"""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(_THS_MAIN_PAGE_HTML, features="lxml")
        records = AkShareFundamentalDataSource._parse_ths_stock_table(soup)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0], {"代码": "300077", "名称": "国民技术"})
        self.assertEqual(records[1], {"代码": "002371", "名称": "北方华创"})

    def test_parse_stock_table_skips_non_digit_codes(self) -> None:
        """非数字开头的代码（如表头误入 tbody）应被跳过。"""
        from bs4 import BeautifulSoup

        html = (
            "<table><tbody>"
            '<tr><td>序号</td><td>X</td><td>名称</td></tr>'
            '<tr><td>1</td><td>300077</td><td>国民技术</td></tr>'
            "</tbody></table>"
        )
        soup = BeautifulSoup(html, features="lxml")
        records = AkShareFundamentalDataSource._parse_ths_stock_table(soup)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["代码"], "300077")

    def test_parse_total_pages_extracts_count(self) -> None:
        """_parse_ths_total_pages：从 '1/9' 提取总页数 9。"""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(
            '<div class="m-pager">1  2  3  下一页尾页1/9</div>',
            features="lxml",
        )
        self.assertEqual(
            AkShareFundamentalDataSource._parse_ths_total_pages(soup), 9
        )

    def test_parse_total_pages_no_pager_returns_one(self) -> None:
        """无分页元素时返回 1（单页）。"""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup("<html><body></body></html>", features="lxml")
        self.assertEqual(
            AkShareFundamentalDataSource._parse_ths_total_pages(soup), 1
        )


class AkShareSourceGuardAndLineageTests(unittest.TestCase):
    """P0 失败可见性 + P2 source_name 派生的 sync 集成测试。"""

    def test_sync_ths_empty_constituents_marks_task_failed(self) -> None:
        """P0：板块非空但成分股全空时，get_sector_constituents 标记为失败。

        模拟数据源故障（反爬 403 / 空页）：所有板块的成分股返回空 →
        _fetch_constituents 抛 RuntimeError → _run_task 标记 fetch_failed。
        """
        fake = _build_ths_fake_akshare()
        # 所有板块的成分股都返回空
        fake.cons_map_ths = {}
        src = AkShareFundamentalDataSource(akshare=fake, today="2026-06-19")
        conn = connect(":memory:")
        try:
            result = sync_all(
                conn,
                src,
                analysis_date="2026-06-19",
                classification_system="ths_industry",
                benchmark="hs300",
                history_days=90,
            )
            by_task = {t["task"]: t for t in result.tasks}
            cons_task = by_task["get_sector_constituents"]
            self.assertFalse(cons_task["success"])
            self.assertIn("0 constituents returned", cons_task["error"])
            # data_fetch_log 也记录了失败
            fail_logs = conn.execute(
                "SELECT COUNT(*) FROM data_fetch_log WHERE task=? AND success=0",
                ("get_sector_constituents",),
            ).fetchone()[0]
            self.assertGreater(fail_logs, 0)
        finally:
            conn.close()

    def test_sync_all_derives_source_name_from_classification(self) -> None:
        """P2：EM 口径 + 默认 name=akshare_ths → sync_all 派生 source=akshare_em。

        直接 AkShareFundamentalDataSource() 的 name 默认是 akshare_ths，
        但 sync_all 按 classification_system=em_industry 派生 akshare_em，
        避免把 EM 数据标记成 akshare_ths（lineage 误标）。
        """
        fake = _build_fake_akshare()
        src = AkShareFundamentalDataSource(akshare=fake, today="2026-06-19")
        self.assertEqual(src.name, "akshare_ths")
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
            sources = conn.execute("SELECT DISTINCT source FROM sectors").fetchall()
            self.assertEqual(len(sources), 1)
            self.assertEqual(sources[0][0], "akshare_em")
            log_sources = conn.execute(
                "SELECT DISTINCT source FROM data_fetch_log"
            ).fetchall()
            self.assertEqual(log_sources[0][0], "akshare_em")
        finally:
            conn.close()

    def test_sync_all_ths_derives_akshare_ths(self) -> None:
        """P2：THS 口径 → sync_all 派生 source=akshare_ths，覆盖对象 name。

        对象 name 故意设为 akshare_em，sync_all 仍按 ths_industry 派生 akshare_ths。
        """
        fake = _build_ths_fake_akshare()
        src = AkShareFundamentalDataSource(
            akshare=fake, today="2026-06-19", name="akshare_em"
        )
        conn = connect(":memory:")
        try:
            sync_all(
                conn,
                src,
                analysis_date="2026-06-19",
                classification_system="ths_industry",
                benchmark="hs300",
                history_days=90,
            )
            sources = conn.execute("SELECT DISTINCT source FROM sectors").fetchall()
            self.assertEqual(sources[0][0], "akshare_ths")
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# 公司层转换逻辑测试（Phase 6C）
# ---------------------------------------------------------------------------


def _build_company_fake_akshare() -> _FakeAkshare:
    """构造带公司层数据的 fake akshare。"""
    universe_df = _FakeDataFrame(
        [
            {"code": "600001", "name": "示例SH"},
            {"code": "002371", "name": "示例SZ"},
            {"code": "300001", "name": "示例创业板"},
        ]
    )
    spot_df = _FakeDataFrame(
        [
            {"序号": 1, "代码": "600001", "名称": "示例SH", "最新价": 10.5,
             "涨跌幅": 1.2, "成交额": 1e8, "换手率": 0.5, "总市值": 5e10},
            {"序号": 2, "代码": "002371", "名称": "示例SZ", "最新价": 20.0,
             "涨跌幅": -0.3, "成交额": 2e8, "换手率": 1.0, "总市值": 1e11},
        ]
    )
    days = _gen_weekdays(5, "2026-06-19")
    pe_rows = [{"date": d, "value": 20.0 + i} for i, d in enumerate(days)]
    pb_rows = [{"date": d, "value": 2.0 + i * 0.1} for i, d in enumerate(days)]
    valuation_map = {
        "002371": {
            "市盈率(TTM)": _FakeDataFrame(pe_rows),
            "市净率": _FakeDataFrame(pb_rows),
        }
    }
    fin_rows = [
        {"日期": "2026-03-31", "主营业务收入增长率(%)": 15.0, "净利润增长率(%)": 20.0,
         "销售毛利率(%)": 30.0, "销售净利率(%)": 10.0, "净资产收益率(%)": 12.0,
         "资产负债率(%)": 40.0, "经营现金净流量与净利润的比率(%)": 80.0,
         "长期负债比率(%)": 10.0},
        {"日期": "2025-12-31", "主营业务收入增长率(%)": 10.0, "净利润增长率(%)": 5.0,
         "销售毛利率(%)": 28.0, "销售净利率(%)": 8.0, "净资产收益率(%)": 10.0,
         "资产负债率(%)": 45.0, "经营现金净流量与净利润的比率(%)": 70.0,
         "长期负债比率(%)": 12.0},
    ]
    financial_map = {"002371": _FakeDataFrame(fin_rows)}
    return _FakeAkshare(
        universe_df=universe_df,
        spot_df=spot_df,
        valuation_map=valuation_map,
        financial_map=financial_map,
    )


class AkShareCompanyLayerTests(unittest.TestCase):
    def test_get_stock_universe_transforms_codes_and_markets(self) -> None:
        fake = _build_company_fake_akshare()
        src = AkShareFundamentalDataSource(akshare=fake)
        rows = src.get_stock_universe("2026-06-19")
        self.assertEqual(len(rows), 3)
        by_code = {r["code"]: r for r in rows}
        self.assertEqual(by_code["600001"]["market"], "SH")
        self.assertEqual(by_code["002371"]["market"], "SZ")
        self.assertEqual(by_code["300001"]["market"], "SZ")
        for r in rows:
            self.assertEqual(r["listing_status"], "L")
            self.assertIsNone(r["delisted_at"])
            self.assertEqual(r["as_of_date"], "2026-06-19")
            self.assertIsNotNone(r["source_updated_at"])

    def test_get_stock_universe_skips_missing_code(self) -> None:
        fake = _FakeAkshare(
            universe_df=_FakeDataFrame([
                {"code": "600001", "name": "有代码"},
                {"code": None, "name": "无代码"},
            ])
        )
        src = AkShareFundamentalDataSource(akshare=fake)
        rows = src.get_stock_universe("2026-06-19")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "600001")

    def test_get_company_daily_snapshot_maps_fields(self) -> None:
        fake = _build_company_fake_akshare()
        src = AkShareFundamentalDataSource(akshare=fake, today="2026-06-19")
        rows = src.get_company_daily_snapshot("2026-06-19")
        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first["code"], "600001")
        self.assertEqual(first["trade_date"], "2026-06-19")
        self.assertEqual(first["close"], 10.5)
        # 百分比字段转成小数比率（docs §20: 0.012 表示 1.2%）
        self.assertEqual(first["change_pct"], 0.012)
        self.assertEqual(first["turnover_amount"], 1e8)
        self.assertEqual(first["turnover_rate"], 0.005)
        self.assertEqual(first["market_cap"], 5e10)

    def test_get_company_daily_snapshot_rejects_non_current_date(self) -> None:
        """PIT 守卫：trade_date 不是当前日期时抛 ValueError，避免用实时价格
        伪造历史快照。"""
        fake = _build_company_fake_akshare()
        src = AkShareFundamentalDataSource(akshare=fake, today="2026-06-22")
        with self.assertRaises(ValueError) as ctx:
            src.get_company_daily_snapshot("2026-06-19")
        self.assertIn("not the current date", str(ctx.exception))

    def test_get_company_valuation_history_merges_pe_pb(self) -> None:
        fake = _build_company_fake_akshare()
        src = AkShareFundamentalDataSource(akshare=fake)
        rows = src.get_company_valuation_history(["002371"], "2026-06-01", "2026-06-19")
        self.assertEqual(len(rows), 5)
        first = rows[0]
        self.assertEqual(first["code"], "002371")
        self.assertEqual(first["trade_date"], _gen_weekdays(5, "2026-06-19")[0])
        self.assertEqual(first["pe"], 20.0)
        self.assertEqual(first["pb"], 2.0)
        self.assertIsNone(first["ps"])
        self.assertIsNone(first["dividend_yield"])
        # 验证调用了 PE 和 PB 两个指标
        indicators = [c["indicator"] for c in fake.calls if c["func"] == "valuation"]
        self.assertIn("市盈率(TTM)", indicators)
        self.assertIn("市净率", indicators)

    def test_get_company_valuation_history_filters_by_date_range(self) -> None:
        fake = _build_company_fake_akshare()
        src = AkShareFundamentalDataSource(akshare=fake)
        days = _gen_weekdays(5, "2026-06-19")
        # 只取中间 3 天
        rows = src.get_company_valuation_history(["002371"], days[1], days[3])
        self.assertEqual(len(rows), 3)
        for r in rows:
            self.assertTrue(days[1] <= r["trade_date"] <= days[3])

    def test_get_company_valuation_history_empty_codes(self) -> None:
        src = AkShareFundamentalDataSource(akshare=_build_company_fake_akshare())
        self.assertEqual(src.get_company_valuation_history([], "2026-01-01", "2026-06-19"), [])

    def test_get_financial_metrics_maps_columns_and_derives_periods(self) -> None:
        fake = _build_company_fake_akshare()
        src = AkShareFundamentalDataSource(akshare=fake)
        rows = src.get_financial_metrics(["002371"], "2026-06-19")
        self.assertEqual(len(rows), 2)
        # 按报告期降序（日期最新的在前，因为 sync 读取顺序是 DataFrame 行序）
        q1 = rows[0]
        self.assertEqual(q1["code"], "002371")
        self.assertEqual(q1["report_period"], "2026Q1")
        self.assertEqual(q1["period_end_date"], "2026-03-31")
        self.assertEqual(q1["period_type"], "quarterly")
        self.assertEqual(q1["disclosure_date"], "2026-04-30")
        self.assertEqual(q1["revenue_yoy"], 0.15)
        self.assertEqual(q1["net_profit_yoy"], 0.20)
        self.assertEqual(q1["gross_margin"], 0.30)
        self.assertEqual(q1["roe"], 0.12)
        self.assertEqual(q1["debt_to_asset"], 0.40)
        # 不可得的字段为 None（不用 0 替代）
        self.assertIsNone(q1["deducted_net_profit_yoy"])
        self.assertIsNone(q1["free_cashflow"])
        self.assertIsNone(q1["accounts_receivable_yoy"])

    def test_get_financial_metrics_annual_period_type(self) -> None:
        fake = _build_company_fake_akshare()
        src = AkShareFundamentalDataSource(akshare=fake)
        rows = src.get_financial_metrics(["002371"], "2026-06-19")
        annual = next(r for r in rows if r["period_end_date"] == "2025-12-31")
        self.assertEqual(annual["period_type"], "annual")
        self.assertEqual(annual["report_period"], "2025A")
        self.assertEqual(annual["disclosure_date"], "2026-04-30")

    def test_get_financial_metrics_semiannual_period_type(self) -> None:
        """H1 报告期：period_type 必须是契约值 ``semiannual``（非 ``semi_annual``），
        report_period 为 ``2026H1``，disclosure_date 为 08-31。"""
        fin_rows = [
            {"日期": "2026-06-30", "主营业务收入增长率(%)": 18.0, "净利润增长率(%)": 22.0,
             "销售毛利率(%)": 32.0, "销售净利率(%)": 12.0, "净资产收益率(%)": 14.0,
             "资产负债率(%)": 38.0, "经营现金净流量与净利润的比率(%)": 85.0,
             "长期负债比率(%)": 9.0},
        ]
        fake = _FakeAkshare(financial_map={"002371": _FakeDataFrame(fin_rows)})
        src = AkShareFundamentalDataSource(akshare=fake)
        # H1 披露日为 2026-08-31，as_of_date 必须晚于等于该日才能通过 PIT 过滤。
        rows = src.get_financial_metrics(["002371"], "2026-08-31")
        self.assertEqual(len(rows), 1)
        h1 = rows[0]
        self.assertEqual(h1["period_end_date"], "2026-06-30")
        self.assertEqual(h1["period_type"], "semiannual")
        self.assertEqual(h1["report_period"], "2026H1")
        self.assertEqual(h1["disclosure_date"], "2026-08-31")

    def test_get_financial_metrics_point_in_time_filter(self) -> None:
        # analysis_date = 2026-03-31：Q1(披露日 04-30) 和年报(披露日次年 04-30)
        # 都被过滤；只有 Q3(披露日 2025-10-31) 通过。
        fin_rows = [
            {"日期": "2026-03-31", "主营业务收入增长率(%)": 15.0, "净利润增长率(%)": 20.0,
             "销售毛利率(%)": 30.0, "销售净利率(%)": 10.0, "净资产收益率(%)": 12.0,
             "资产负债率(%)": 40.0, "经营现金净流量与净利润的比率(%)": 80.0,
             "长期负债比率(%)": 10.0},
            {"日期": "2025-12-31", "主营业务收入增长率(%)": 10.0, "净利润增长率(%)": 5.0,
             "销售毛利率(%)": 28.0, "销售净利率(%)": 8.0, "净资产收益率(%)": 10.0,
             "资产负债率(%)": 45.0, "经营现金净流量与净利润的比率(%)": 70.0,
             "长期负债比率(%)": 12.0},
            {"日期": "2025-09-30", "主营业务收入增长率(%)": 8.0, "净利润增长率(%)": 3.0,
             "销售毛利率(%)": 25.0, "销售净利率(%)": 6.0, "净资产收益率(%)": 8.0,
             "资产负债率(%)": 42.0, "经营现金净流量与净利润的比率(%)": 65.0,
             "长期负债比率(%)": 11.0},
        ]
        fake = _FakeAkshare(financial_map={"002371": _FakeDataFrame(fin_rows)})
        src = AkShareFundamentalDataSource(akshare=fake)
        rows = src.get_financial_metrics(["002371"], "2026-03-31")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["period_end_date"], "2025-09-30")
        self.assertEqual(rows[0]["report_period"], "2025Q3")

    def test_get_financial_metrics_empty_codes(self) -> None:
        src = AkShareFundamentalDataSource(akshare=_build_company_fake_akshare())
        self.assertEqual(src.get_financial_metrics([], "2026-06-19"), [])

    def test_company_layer_failure_preserves_sector_cache(self) -> None:
        # 公司层失败不应影响板块层已写入的缓存。
        fake = _build_fake_akshare(n_days=5, end_iso="2026-06-19")
        # 叠加公司层数据
        fake.universe_df = _FakeDataFrame([{"code": "002371", "name": "示例"}])
        src = AkShareFundamentalDataSource(akshare=fake, today="2026-06-19")
        conn = connect(":memory:")
        try:
            sync_all(
                conn, src, analysis_date="2026-06-19",
                classification_system="em_industry", benchmark="hs300", history_days=90,
            )
            cached_sectors = conn.execute("SELECT COUNT(*) FROM sectors").fetchone()[0]
            cached_stocks = conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]
            self.assertGreater(cached_sectors, 0)
            self.assertGreater(cached_stocks, 0)

            # 第二次：公司层抛错
            fake.fail = True
            result = sync_all(
                conn, src, analysis_date="2026-06-19",
                classification_system="em_industry", benchmark="hs300", history_days=90,
            )
            # 板块层和公司层都有失败（因为 fail=True 影响所有方法）
            self.assertGreater(result.failure_count, 0)
            # 但第一次的缓存没被破坏
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM sectors").fetchone()[0], cached_sectors
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0], cached_stocks
            )
        finally:
            conn.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
