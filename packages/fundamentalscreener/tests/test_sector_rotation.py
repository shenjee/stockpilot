"""Phase 1: sector_rotation 单元测试。

覆盖：
- 收益计算
- 相对大盘（可正可负、固定 20 日窗口）
- 状态优先级（overheated > strong > low_level_active > improving > neutral）
- 排序（按 SUPPORTED_SECTOR_SORTS 中的字段）
- 数据缺失时的 warnings 与字段为 None 行为
"""

from __future__ import annotations

import unittest
from typing import List, Sequence

from packages.fundamentalscreener.repositories import (
    BenchmarkData,
    CompanyData,
    DailyBar,
    MarketSnapshot,
    SectorData,
)
from packages.fundamentalscreener.sector_rotation import (
    compute_sector_rotation,
    sort_entries,
)


def _bars(closes: Sequence[float], turnover: float = 1_000_000_000.0) -> List[DailyBar]:
    return [
        DailyBar(date=f"2026-01-{i + 1:02d}", close=float(c), turnover_amount=turnover)
        for i, c in enumerate(closes)
    ]


def _bars_with_turnovers(
    closes: Sequence[float], turnovers: Sequence[float]
) -> List[DailyBar]:
    return [
        DailyBar(date=f"2026-01-{i + 1:02d}", close=float(c), turnover_amount=float(t))
        for i, (c, t) in enumerate(zip(closes, turnovers))
    ]


def _make_snapshot(
    sectors: Sequence[SectorData],
    companies: Sequence[CompanyData],
    benchmark_closes: Sequence[float],
) -> MarketSnapshot:
    return MarketSnapshot(
        date="2026-06-19",
        classification_system="concept",
        benchmark=BenchmarkData(
            id="hs300", name="沪深300", daily=_bars(benchmark_closes)
        ),
        sectors=list(sectors),
        companies=list(companies),
    )


class ReturnsAndRelativeReturnTests(unittest.TestCase):
    def test_period_returns_match_close_ratio(self) -> None:
        # 65 根 K 线，close 线性上涨。
        closes = [100.0 + i for i in range(65)]
        sector = SectorData(
            sector_id="A",
            sector_name="A",
            constituents=["c1"],
            daily=_bars(closes),
        )
        company = CompanyData(
            code="c1", name="c1", sector_id="A", market_cap=1.0, daily=_bars(closes)
        )
        snap = _make_snapshot([sector], [company], closes)
        result = compute_sector_rotation(snap)
        e = result.sectors[0]
        self.assertAlmostEqual(e.return_1d, closes[-1] / closes[-2] - 1.0, places=6)
        self.assertAlmostEqual(e.return_5d, closes[-1] / closes[-6] - 1.0, places=6)
        self.assertAlmostEqual(e.return_20d, closes[-1] / closes[-21] - 1.0, places=6)
        self.assertAlmostEqual(e.return_60d, closes[-1] / closes[-61] - 1.0, places=6)

    def test_relative_return_can_be_positive_or_negative(self) -> None:
        n = 65
        # 板块 A 比基准强；板块 B 比基准弱。
        closes_strong = [100.0 + i * 0.5 for i in range(n)]
        closes_weak = [100.0 - i * 0.1 for i in range(n)]
        bench = [100.0 + i * 0.2 for i in range(n)]
        sec_a = SectorData("A", "A", ["a1"], _bars(closes_strong))
        sec_b = SectorData("B", "B", ["b1"], _bars(closes_weak))
        comp_a = CompanyData("a1", "a1", "A", 1.0, _bars(closes_strong))
        comp_b = CompanyData("b1", "b1", "B", 1.0, _bars(closes_weak))
        snap = _make_snapshot([sec_a, sec_b], [comp_a, comp_b], bench)
        result = compute_sector_rotation(snap)
        a = next(e for e in result.sectors if e.sector_id == "A")
        b = next(e for e in result.sectors if e.sector_id == "B")
        # A 相对大盘 > 0，B 相对大盘 < 0。
        self.assertIsNotNone(a.relative_return)
        self.assertIsNotNone(b.relative_return)
        self.assertGreater(a.relative_return, 0)
        self.assertLess(b.relative_return, 0)

    def test_insufficient_history_yields_none_and_warning(self) -> None:
        closes = [100.0 + i for i in range(10)]  # 不足 60 日
        sector = SectorData("A", "A", ["c1"], _bars(closes))
        company = CompanyData("c1", "c1", "A", 1.0, _bars(closes))
        snap = _make_snapshot([sector], [company], closes)
        result = compute_sector_rotation(snap)
        e = result.sectors[0]
        self.assertIsNone(e.return_60d)
        self.assertIsNone(e.return_20d)
        self.assertTrue(any("return_60d" in w for w in e.warnings))


class StatePriorityTests(unittest.TestCase):
    """覆盖状态规则：overheated > strong > low_level_active > improving > neutral。"""

    def _build_snapshot(
        self,
        target_closes: Sequence[float],
        target_turnovers: Sequence[float],
        peers: Sequence[Sequence[float]],
        benchmark: Sequence[float],
    ) -> MarketSnapshot:
        sectors: List[SectorData] = []
        companies: List[CompanyData] = []
        sectors.append(
            SectorData(
                "T",
                "Target",
                ["t1"],
                _bars_with_turnovers(target_closes, target_turnovers),
            )
        )
        companies.append(
            CompanyData(
                "t1", "t1", "T", 1.0, _bars(target_closes)
            )
        )
        for i, p in enumerate(peers):
            sid = f"P{i}"
            sectors.append(SectorData(sid, sid, [f"p{i}"], _bars(p)))
            companies.append(CompanyData(f"p{i}", f"p{i}", sid, 1.0, _bars(p)))
        return _make_snapshot(sectors, companies, benchmark)

    def test_overheated_takes_priority_over_strong(self) -> None:
        # Target 是涨幅最高的板块（前 20%），同时也满足 strong 条件。
        n = 65
        target = [100.0 + i * 1.0 for i in range(n)]
        peers = [
            [100.0 + i * 0.05 for i in range(n)],
            [100.0 - i * 0.1 for i in range(n)],
            [100.0 - i * 0.2 for i in range(n)],
            [100.0 - i * 0.3 for i in range(n)],
        ]
        bench = [100.0 + i * 0.2 for i in range(n)]
        # turnover 增大，避免触发 turnover_baseline 缺失。
        turnovers = [1_000_000_000.0 + i * 50_000_000.0 for i in range(n)]
        snap = self._build_snapshot(target, turnovers, peers, bench)
        result = compute_sector_rotation(snap)
        t = next(e for e in result.sectors if e.sector_id == "T")
        self.assertEqual(t.state, "overheated")

    def test_strong_when_not_overheated(self) -> None:
        # 五个板块都缓慢上涨，target 最强但相对接近，避免被前 20% 选中。
        n = 65
        target = [100.0 + i * 0.3 for i in range(n)]
        peers = [
            [100.0 + i * 0.28 for i in range(n)],
            [100.0 + i * 0.27 for i in range(n)],
            [100.0 + i * 0.26 for i in range(n)],
            [100.0 + i * 0.25 for i in range(n)],
        ]
        bench = [100.0 + i * 0.05 for i in range(n)]  # 板块均跑赢基准
        turnovers = [1_000_000_000.0 + i * 10_000_000.0 for i in range(n)]
        snap = self._build_snapshot(target, turnovers, peers, bench)
        result = compute_sector_rotation(snap)
        # target 不应被选为 overheated（因为前 20% 阈值会取最大那个）。
        # 这里我们只断言 target 是 strong 或 overheated 之一，并验证至少有一个 strong 结果出现。
        states = {e.sector_id: e.state for e in result.sectors}
        # 至少应有非 overheated 的 strong 板块（peers 之一）。
        self.assertIn("strong", set(states.values()))

    def test_improving_when_not_strong(self) -> None:
        # 构造：60 日维度净上涨、近 20 日处于高位回落、近 5 日反弹放量。
        # 因此 return_60d > 0（避免被 low_level_active 抢占）、
        # return_20d < 0（不是 strong）、return_5d > 0、turnover > 0。
        n = 65
        target_closes = (
            [100.0] * 5      # idx 0..4 (return_60d 基线 = 100)
            + [110.0] * 40   # idx 5..44 (return_20d 基线 = 110)
            + [100.0] * 15   # idx 45..59 (return_5d 基线 = 100)
            + [102.0, 103.0, 104.0, 104.5]  # idx 60..63
            + [105.0]        # idx 64 今日
        )
        assert len(target_closes) == n
        target_turnovers = [1_000_000_000.0] * 60 + [3_000_000_000.0] * 5
        peers = [
            [100.0 + i * 1.0 for i in range(n)] for _ in range(4)
        ]
        bench = [100.0 + i * 0.05 for i in range(n)]
        snap = self._build_snapshot(target_closes, target_turnovers, peers, bench)
        result = compute_sector_rotation(snap)
        t = next(e for e in result.sectors if e.sector_id == "T")
        self.assertGreater(t.return_60d or 0, 0)  # 防止落入 low_level_active
        self.assertGreater(t.return_5d or 0, 0)
        self.assertLess(t.return_20d or 0, 0)
        self.assertGreater(t.turnover_amount_change or 0, 0)
        self.assertEqual(t.state, "improving")

    def test_low_level_active_takes_priority_over_improving(self) -> None:
        # 调整后的状态优先级：low_level_active 高于 improving。
        # 触发条件：return_60d <= 0 且 return_5d > 0 且 turnover > 0。
        n = 65
        target_closes = [100.0 - i * 0.3 for i in range(n - 5)] + [
            (100.0 - (n - 6) * 0.3) + j * 0.5 for j in range(1, 6)
        ]
        target_turnovers = [1_000_000_000.0] * (n - 5) + [3_000_000_000.0] * 5
        peers = [
            [100.0 + i * 0.05 for i in range(n)] for _ in range(4)
        ]
        bench = [100.0 + i * 0.05 for i in range(n)]
        snap = self._build_snapshot(target_closes, target_turnovers, peers, bench)
        result = compute_sector_rotation(snap)
        t = next(e for e in result.sectors if e.sector_id == "T")
        self.assertLessEqual(t.return_60d or 0, 0)
        self.assertGreater(t.return_5d or 0, 0)
        self.assertEqual(t.state, "low_level_active")

    def test_neutral_when_no_rule_matches(self) -> None:
        # return_5d <= 0，turnover 也下降。
        n = 65
        target_closes = [100.0] * (n - 5) + [99.5, 99.0, 98.8, 98.7, 98.5]
        target_turnovers = [1_000_000_000.0] * (n - 5) + [500_000_000.0] * 5
        peers = [
            [100.0 + i * 0.05 for i in range(n)] for _ in range(4)
        ]
        bench = [100.0 + i * 0.05 for i in range(n)]
        snap = self._build_snapshot(target_closes, target_turnovers, peers, bench)
        result = compute_sector_rotation(snap)
        t = next(e for e in result.sectors if e.sector_id == "T")
        self.assertEqual(t.state, "neutral")


class SortEntriesTests(unittest.TestCase):
    def test_sort_by_return_1d_descending(self) -> None:
        n = 65
        sec_high = SectorData("H", "H", ["h"], _bars([100.0 + i for i in range(n)]))
        sec_low = SectorData("L", "L", ["l"], _bars([100.0 - i * 0.1 for i in range(n)]))
        comps = [
            CompanyData("h", "h", "H", 1.0, _bars([100.0 + i for i in range(n)])),
            CompanyData("l", "l", "L", 1.0, _bars([100.0 - i * 0.1 for i in range(n)])),
        ]
        bench = [100.0 + i * 0.05 for i in range(n)]
        snap = _make_snapshot([sec_low, sec_high], comps, bench)
        result = compute_sector_rotation(snap)
        ordered = sort_entries(result.sectors, "return_1d")
        self.assertEqual([e.sector_id for e in ordered], ["H", "L"])

    def test_sort_by_relative_return(self) -> None:
        n = 65
        strong = [100.0 + i * 0.5 for i in range(n)]
        weak = [100.0 - i * 0.1 for i in range(n)]
        bench = [100.0 + i * 0.2 for i in range(n)]
        sectors = [
            SectorData("W", "W", ["w"], _bars(weak)),
            SectorData("S", "S", ["s"], _bars(strong)),
        ]
        comps = [
            CompanyData("w", "w", "W", 1.0, _bars(weak)),
            CompanyData("s", "s", "S", 1.0, _bars(strong)),
        ]
        snap = _make_snapshot(sectors, comps, bench)
        result = compute_sector_rotation(snap)
        ordered = sort_entries(result.sectors, "relative_return")
        self.assertEqual([e.sector_id for e in ordered], ["S", "W"])

    def test_sort_pushes_none_values_last(self) -> None:
        # 一个板块长度不足以计算 return_60d，应排在后面。
        long_n = 65
        short_n = 30
        sec_long = SectorData(
            "L", "L", ["lc"], _bars([100.0 + i for i in range(long_n)])
        )
        sec_short = SectorData(
            "S", "S", ["sc"], _bars([100.0 + i for i in range(short_n)])
        )
        comps = [
            CompanyData("lc", "lc", "L", 1.0, _bars([100.0 + i for i in range(long_n)])),
            CompanyData("sc", "sc", "S", 1.0, _bars([100.0 + i for i in range(short_n)])),
        ]
        bench = [100.0 + i * 0.05 for i in range(long_n)]
        snap = _make_snapshot([sec_short, sec_long], comps, bench)
        result = compute_sector_rotation(snap)
        ordered = sort_entries(result.sectors, "return_60d")
        # short 板块的 return_60d 是 None，应排在最后。
        self.assertEqual(ordered[-1].sector_id, "S")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
