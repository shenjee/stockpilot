"""Repository 层接口与 fixture 实现。

Phase 0 仅提供 ``FixtureRepository``，从 JSON fixture 文件读取板块、公司和基准
数据，供 CLI 在不接真实数据库的情况下完成 schema/契约验证。

约定：
- Repository 不写业务评分逻辑。
- ``FixtureRepository`` 只做加载与简单查询（按 sector_id / 公司 code）。
- 真实 SQLite/数据库实现留给后续 Phase。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class DailyBar:
    """fixture 中的单日行情。"""

    date: str
    close: float
    turnover_amount: float
    turnover_rate: Optional[float] = None

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "DailyBar":
        return cls(
            date=str(raw["date"]),
            close=float(raw["close"]),
            turnover_amount=float(raw.get("turnover_amount", 0.0)),
            turnover_rate=(
                float(raw["turnover_rate"]) if raw.get("turnover_rate") is not None else None
            ),
        )


@dataclass
class BenchmarkData:
    """fixture 中的基准指数。"""

    id: str
    name: str
    daily: List[DailyBar] = field(default_factory=list)


@dataclass
class SectorData:
    """fixture 中的板块。"""

    sector_id: str
    sector_name: str
    constituents: List[str] = field(default_factory=list)
    daily: List[DailyBar] = field(default_factory=list)


@dataclass
class CompanyData:
    """fixture 中的公司。"""

    code: str
    name: str
    sector_id: Optional[str]
    market_cap: Optional[float]
    daily: List[DailyBar] = field(default_factory=list)


@dataclass
class FinancialData:
    """fixture 中单家公司的财务质量指标。

    字段与 docs/fundamental_screener_phase_plan.md §7.3 / §15 对齐。
    所有比例字段都是小数（0.18 表示 18%），便于直接参与计算。

    ``gross_margin_yoy_change`` 是可选字段：MVP 不要求所有数据源都有，缺失时
    ``gross_margin_decline`` flag 跳过判定（不报误警）。
    """

    code: str
    revenue_yoy: Optional[float] = None
    net_profit_yoy: Optional[float] = None
    deducted_net_profit_yoy: Optional[float] = None
    gross_margin: Optional[float] = None
    net_margin: Optional[float] = None
    roe: Optional[float] = None
    operating_cashflow_to_profit: Optional[float] = None
    free_cashflow: Optional[float] = None
    debt_to_asset: Optional[float] = None
    interest_bearing_debt_ratio: Optional[float] = None
    accounts_receivable_yoy: Optional[float] = None
    inventory_yoy: Optional[float] = None
    gross_margin_yoy_change: Optional[float] = None
    name: Optional[str] = None  # 可选：fixture 不传时由 CompanyData 兜底

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "FinancialData":
        def _opt(name: str) -> Optional[float]:
            value = raw.get(name)
            return float(value) if value is not None else None

        return cls(
            code=str(raw["code"]),
            revenue_yoy=_opt("revenue_yoy"),
            net_profit_yoy=_opt("net_profit_yoy"),
            deducted_net_profit_yoy=_opt("deducted_net_profit_yoy"),
            gross_margin=_opt("gross_margin"),
            net_margin=_opt("net_margin"),
            roe=_opt("roe"),
            operating_cashflow_to_profit=_opt("operating_cashflow_to_profit"),
            free_cashflow=_opt("free_cashflow"),
            debt_to_asset=_opt("debt_to_asset"),
            interest_bearing_debt_ratio=_opt("interest_bearing_debt_ratio"),
            accounts_receivable_yoy=_opt("accounts_receivable_yoy"),
            inventory_yoy=_opt("inventory_yoy"),
            gross_margin_yoy_change=_opt("gross_margin_yoy_change"),
            name=(str(raw["name"]) if raw.get("name") is not None else None),
        )


@dataclass
class ValuationData:
    """fixture 中单家公司的估值指标。

    字段与 docs/fundamental_screener_phase_plan.md §7.4 / §16 对齐。``pe``、``pb``、
    ``ps`` 是绝对倍数；``peg``、``dividend_yield``、``pe_percentile``、
    ``pb_percentile`` 是小数；``industry_valuation_position`` 是枚举
    ``low | mid | high | unknown``。
    """

    code: str
    pe: Optional[float] = None
    pb: Optional[float] = None
    ps: Optional[float] = None
    peg: Optional[float] = None
    dividend_yield: Optional[float] = None
    pe_percentile: Optional[float] = None
    pb_percentile: Optional[float] = None
    industry_valuation_position: Optional[str] = None
    name: Optional[str] = None

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ValuationData":
        def _opt(name: str) -> Optional[float]:
            value = raw.get(name)
            return float(value) if value is not None else None

        return cls(
            code=str(raw["code"]),
            pe=_opt("pe"),
            pb=_opt("pb"),
            ps=_opt("ps"),
            peg=_opt("peg"),
            dividend_yield=_opt("dividend_yield"),
            pe_percentile=_opt("pe_percentile"),
            pb_percentile=_opt("pb_percentile"),
            industry_valuation_position=(
                str(raw["industry_valuation_position"])
                if raw.get("industry_valuation_position") is not None
                else None
            ),
            name=(str(raw["name"]) if raw.get("name") is not None else None),
        )


@dataclass
class MarketSnapshot:
    """fixture 加载后的市场快照。"""

    date: str
    classification_system: str
    benchmark: BenchmarkData
    sectors: List[SectorData] = field(default_factory=list)
    companies: List[CompanyData] = field(default_factory=list)
    financials: List[FinancialData] = field(default_factory=list)
    valuations: List[ValuationData] = field(default_factory=list)


class Repository:
    """Repository 接口。Phase 0 仅声明最小读取方法。"""

    def load_snapshot(self) -> MarketSnapshot:  # pragma: no cover - 抽象接口
        raise NotImplementedError

    def list_sectors(self) -> List[SectorData]:  # pragma: no cover
        raise NotImplementedError

    def list_companies(self, sector_id: Optional[str] = None) -> List[CompanyData]:  # pragma: no cover
        raise NotImplementedError

    def get_companies_by_codes(self, codes: Iterable[str]) -> List[CompanyData]:  # pragma: no cover
        raise NotImplementedError


class FixtureRepository(Repository):
    """从 JSON fixture 加载市场快照的 Repository。"""

    def __init__(self, fixture_path: Path | str) -> None:
        self.fixture_path = Path(fixture_path)
        self._snapshot: Optional[MarketSnapshot] = None

    # -------------------------- 加载 --------------------------

    def load_snapshot(self) -> MarketSnapshot:
        if self._snapshot is not None:
            return self._snapshot

        if not self.fixture_path.exists():
            raise FileNotFoundError(f"fixture not found: {self.fixture_path}")

        with self.fixture_path.open("r", encoding="utf-8") as fp:
            raw = json.load(fp)

        benchmark_raw = raw.get("benchmark") or {}
        benchmark = BenchmarkData(
            id=str(benchmark_raw.get("id", "")),
            name=str(benchmark_raw.get("name", "")),
            daily=[DailyBar.from_dict(d) for d in benchmark_raw.get("daily", [])],
        )

        sectors: List[SectorData] = []
        for s in raw.get("sectors", []):
            sectors.append(
                SectorData(
                    sector_id=str(s.get("sector_id", "")),
                    sector_name=str(s.get("sector_name", "")),
                    constituents=[str(c) for c in s.get("constituents", [])],
                    daily=[DailyBar.from_dict(d) for d in s.get("daily", [])],
                )
            )

        companies: List[CompanyData] = []
        for c in raw.get("companies", []):
            companies.append(
                CompanyData(
                    code=str(c.get("code", "")),
                    name=str(c.get("name", "")),
                    sector_id=(str(c["sector_id"]) if c.get("sector_id") is not None else None),
                    market_cap=(
                        float(c["market_cap"]) if c.get("market_cap") is not None else None
                    ),
                    daily=[DailyBar.from_dict(d) for d in c.get("daily", [])],
                )
            )

        snapshot = MarketSnapshot(
            date=str(raw.get("date", "")),
            classification_system=str(raw.get("classification_system", "concept")),
            benchmark=benchmark,
            sectors=sectors,
            companies=companies,
            financials=[FinancialData.from_dict(f) for f in raw.get("financials", [])],
            valuations=[ValuationData.from_dict(v) for v in raw.get("valuations", [])],
        )
        self._snapshot = snapshot
        return snapshot

    # -------------------------- 查询 --------------------------

    def list_sectors(self) -> List[SectorData]:
        return list(self.load_snapshot().sectors)

    def list_companies(self, sector_id: Optional[str] = None) -> List[CompanyData]:
        companies = self.load_snapshot().companies
        if sector_id is None:
            return list(companies)
        return [c for c in companies if c.sector_id == sector_id]

    def find_sector(self, sector_id_or_name: str) -> Optional[SectorData]:
        for s in self.load_snapshot().sectors:
            if s.sector_id == sector_id_or_name or s.sector_name == sector_id_or_name:
                return s
        return None

    def get_companies_by_codes(self, codes: Iterable[str]) -> List[CompanyData]:
        wanted = [c.strip() for c in codes if c and c.strip()]
        if not wanted:
            return []
        index = {c.code: c for c in self.load_snapshot().companies}
        return [index[code] for code in wanted if code in index]

    def get_financials_by_codes(
        self, codes: Iterable[str]
    ) -> List["FinancialData"]:
        """按 codes 顺序返回财务数据；缺失的 code 直接跳过。

        与 ``get_companies_by_codes`` 一致：调用方负责处理"少给了某个 code"
        的提示，repository 不写 warning。
        """

        wanted = [c.strip() for c in codes if c and c.strip()]
        if not wanted:
            return []
        index = {f.code: f for f in self.load_snapshot().financials}
        return [index[code] for code in wanted if code in index]

    def get_valuations_by_codes(
        self, codes: Iterable[str]
    ) -> List["ValuationData"]:
        """按 codes 顺序返回估值数据；缺失的 code 直接跳过。"""

        wanted = [c.strip() for c in codes if c and c.strip()]
        if not wanted:
            return []
        index = {v.code: v for v in self.load_snapshot().valuations}
        return [index[code] for code in wanted if code in index]


__all__ = [
    "BenchmarkData",
    "CompanyData",
    "DailyBar",
    "FinancialData",
    "FixtureRepository",
    "MarketSnapshot",
    "Repository",
    "SectorData",
    "ValuationData",
]
