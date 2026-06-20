"""Fundamental Screener 稳定 schema 定义。

本文件冻结 Phase 0 输出契约对应的数据结构，字段名与 docs/fundamental_screener_phase_plan.md
第 7 节保持一致。所有数据类只负责结构定义和序列化，不做业务计算。

序列化规则：
- 所有数据类提供 ``to_dict()`` 方法。
- ``None`` 字段保留为 ``null``，便于 Phase 1+ 渐进填充。
- 字段统一 ``snake_case``。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# 通用结构
# ---------------------------------------------------------------------------


@dataclass
class ChartSeriesPoint:
    """单点走势数据。"""

    date: str
    value: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ChartSeries:
    """板块走势曲线或基准走势曲线。"""

    series_id: str
    series_name: str
    type: str  # "sector" | "benchmark"
    points: List[ChartSeriesPoint] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "series_id": self.series_id,
            "series_name": self.series_name,
            "type": self.type,
            "points": [p.to_dict() for p in self.points],
        }


# ---------------------------------------------------------------------------
# sectors / sector-detail
# ---------------------------------------------------------------------------


@dataclass
class SectorEntry:
    """板块轮动指标行。Phase 0 允许全部计算字段为 None。"""

    sector_id: str
    sector_name: str
    classification_system: str
    return_1d: Optional[float] = None
    return_5d: Optional[float] = None
    return_20d: Optional[float] = None
    return_60d: Optional[float] = None
    relative_return: Optional[float] = None
    turnover_amount_change: Optional[float] = None
    market_turnover_share: Optional[float] = None
    rising_stock_ratio: Optional[float] = None
    rank_change_5d: Optional[int] = None
    state: Optional[str] = None
    score: Optional[float] = None
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SectorsPayload:
    """sectors 命令的顶层 JSON。"""

    command: str
    date: str
    classification_system: str
    benchmark: str
    sort: str
    periods: List[int]
    sectors: List[SectorEntry] = field(default_factory=list)
    chart_series: List[ChartSeries] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": self.command,
            "date": self.date,
            "classification_system": self.classification_system,
            "benchmark": self.benchmark,
            "sort": self.sort,
            "periods": list(self.periods),
            "sectors": [s.to_dict() for s in self.sectors],
            "chart_series": [c.to_dict() for c in self.chart_series],
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# companies
# ---------------------------------------------------------------------------


@dataclass
class CompanyEntry:
    """板块内公司排名行。"""

    code: str
    name: str
    market_cap: Optional[float] = None
    turnover_amount: Optional[float] = None
    turnover_rate: Optional[float] = None
    sector_return_rank: Optional[int] = None
    leader_score: Optional[float] = None
    attention_score: Optional[float] = None
    financial_quality_score: Optional[float] = None
    valuation_score: Optional[float] = None
    combined_score: Optional[float] = None
    group: Optional[str] = None
    flags: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CompaniesPayload:
    """companies 命令的顶层 JSON。"""

    command: str
    date: str
    classification_system: str
    sector_id: Optional[str]
    sector_name: Optional[str]
    sort: str
    companies: List[CompanyEntry] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": self.command,
            "date": self.date,
            "classification_system": self.classification_system,
            "sector_id": self.sector_id,
            "sector_name": self.sector_name,
            "sort": self.sort,
            "companies": [c.to_dict() for c in self.companies],
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# financials
# ---------------------------------------------------------------------------


@dataclass
class FinancialEntry:
    """单家公司财务质量行。"""

    code: str
    name: str
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
    score: Optional[float] = None
    abnormal_flags: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FinancialsPayload:
    """financials 命令的顶层 JSON。按 codes 查询，不绑定板块分类。"""

    command: str
    date: str
    companies: List[FinancialEntry] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": self.command,
            "date": self.date,
            "companies": [c.to_dict() for c in self.companies],
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# valuations
# ---------------------------------------------------------------------------


@dataclass
class ValuationEntry:
    """单家公司估值行。"""

    code: str
    name: str
    pe: Optional[float] = None
    pb: Optional[float] = None
    ps: Optional[float] = None
    peg: Optional[float] = None
    dividend_yield: Optional[float] = None
    pe_percentile: Optional[float] = None
    pb_percentile: Optional[float] = None
    industry_valuation_position: Optional[str] = None
    score: Optional[float] = None
    label: Optional[str] = None
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ValuationsPayload:
    """valuations 命令的顶层 JSON。按 codes 查询，不绑定板块分类。"""

    command: str
    date: str
    companies: List[ValuationEntry] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": self.command,
            "date": self.date,
            "companies": [c.to_dict() for c in self.companies],
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# screen
# ---------------------------------------------------------------------------


@dataclass
class CandidatesPayload:
    """screen 候选分组。"""

    priority: List[Dict[str, Any]] = field(default_factory=list)
    watch: List[Dict[str, Any]] = field(default_factory=list)
    cautious: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "priority": list(self.priority),
            "watch": list(self.watch),
            "cautious": list(self.cautious),
        }


@dataclass
class ScreenPayload:
    """screen 命令的顶层 JSON。"""

    command: str
    date: str
    classification_system: str
    benchmark: str
    selected_sectors: List[Dict[str, Any]] = field(default_factory=list)
    candidates: CandidatesPayload = field(default_factory=CandidatesPayload)
    warnings: List[str] = field(default_factory=list)
    generated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": self.command,
            "date": self.date,
            "classification_system": self.classification_system,
            "benchmark": self.benchmark,
            "selected_sectors": list(self.selected_sectors),
            "candidates": self.candidates.to_dict(),
            "warnings": list(self.warnings),
            "generated_at": self.generated_at,
        }


__all__ = [
    "CandidatesPayload",
    "ChartSeries",
    "ChartSeriesPoint",
    "CompaniesPayload",
    "CompanyEntry",
    "FinancialEntry",
    "FinancialsPayload",
    "ScreenPayload",
    "SectorEntry",
    "SectorsPayload",
    "ValuationEntry",
    "ValuationsPayload",
]
