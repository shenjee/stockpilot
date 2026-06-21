"""内存假数据源（Phase 6A）。

只用于单元测试与同步骨架验证。它接收预置的 dict 列表并按方法返回，不做任何
真实采集。这样可以让 ``init-db`` / ``sync`` 在无网络环境也能稳定测试。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class FakeFundamentalDataSource:
    """通过预置 payload 模拟真实数据源。

    每个字段都是 ``List[Dict[str, Any]]``：
    - ``sectors``：板块列表
    - ``sector_constituents``：板块-公司关系
    - ``sector_daily``：板块历史行情
    - ``benchmark_daily``：基准历史行情
    - ``stock_universe``：股票池
    - ``company_daily_snapshot``：公司日度快照
    - ``company_valuation_history``：公司估值历史
    - ``financial_metrics``：公司财务指标

    name 默认 ``fake``，写入 SQLite 时会作为 ``source`` 列写入。
    """

    name: str = "fake"
    sectors: List[Dict[str, Any]] = field(default_factory=list)
    sector_constituents: List[Dict[str, Any]] = field(default_factory=list)
    sector_daily: List[Dict[str, Any]] = field(default_factory=list)
    benchmark_daily: List[Dict[str, Any]] = field(default_factory=list)
    stock_universe: List[Dict[str, Any]] = field(default_factory=list)
    company_daily_snapshot: List[Dict[str, Any]] = field(default_factory=list)
    company_valuation_history: List[Dict[str, Any]] = field(default_factory=list)
    financial_metrics: List[Dict[str, Any]] = field(default_factory=list)
    # 当任意一个方法被调用时若该字段为 True，则抛 ``RuntimeError``，便于
    # 验证“数据源失败时同步任务不破坏缓存且 data_fetch_log 写入失败行”。
    fail: bool = False

    # ---------------- 板块层 ----------------

    def list_sectors(self, classification_system: str) -> List[Dict[str, Any]]:
        self._maybe_fail()
        return [
            s
            for s in self.sectors
            if s.get("classification_system", classification_system) == classification_system
        ]

    def get_sector_constituents(
        self, sector_id: str, classification_system: str, as_of_date: str
    ) -> List[Dict[str, Any]]:
        self._maybe_fail()
        return [
            row
            for row in self.sector_constituents
            if row.get("sector_id") == sector_id
            and row.get("classification_system", classification_system)
            == classification_system
            and row.get("as_of_date", as_of_date) <= as_of_date
        ]

    def get_sector_daily(
        self,
        sector_id: str,
        classification_system: str,
        start_date: str,
        end_date: str,
    ) -> List[Dict[str, Any]]:
        self._maybe_fail()
        return [
            row
            for row in self.sector_daily
            if row.get("sector_id") == sector_id
            and row.get("classification_system", classification_system)
            == classification_system
            and start_date <= row.get("trade_date", "") <= end_date
        ]

    def get_benchmark_daily(
        self, benchmark: str, start_date: str, end_date: str
    ) -> List[Dict[str, Any]]:
        self._maybe_fail()
        # 与 list_sectors / get_sector_constituents 一致：缺省的过滤字段视为"匹配
        # 请求值"，让 sync 层的 enricher 负责填入上下文默认值。
        return [
            row
            for row in self.benchmark_daily
            if row.get("benchmark", benchmark) == benchmark
            and start_date <= row.get("trade_date", "") <= end_date
        ]

    # ---------------- 公司层 ----------------

    def get_stock_universe(self, as_of_date: str) -> List[Dict[str, Any]]:
        self._maybe_fail()
        return [
            row
            for row in self.stock_universe
            if row.get("as_of_date", as_of_date) <= as_of_date
        ]

    def get_company_daily_snapshot(self, trade_date: str) -> List[Dict[str, Any]]:
        self._maybe_fail()
        return [
            row for row in self.company_daily_snapshot if row.get("trade_date") == trade_date
        ]

    def get_company_valuation_history(
        self, codes: List[str], start_date: str, end_date: str
    ) -> List[Dict[str, Any]]:
        self._maybe_fail()
        wanted = set(codes)
        return [
            row
            for row in self.company_valuation_history
            if row.get("code") in wanted
            and start_date <= row.get("trade_date", "") <= end_date
        ]

    def get_financial_metrics(
        self, codes: List[str], as_of_date: str
    ) -> List[Dict[str, Any]]:
        self._maybe_fail()
        wanted = set(codes)
        return [
            row
            for row in self.financial_metrics
            if row.get("code") in wanted
            and row.get("disclosure_date", as_of_date) <= as_of_date
        ]

    # ---------------- internal ----------------

    def _maybe_fail(self) -> None:
        if self.fail:
            raise RuntimeError("fake source failure (test)")


__all__ = ["FakeFundamentalDataSource"]
