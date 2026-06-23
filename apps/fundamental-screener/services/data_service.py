"""Fundamental Screener Streamlit 数据服务。

本模块只做两件事：
1. 通过 ``FixtureRepository`` 加载市场快照。
2. 调用 ``packages.fundamentalscreener`` 的 core 函数，得到板块轮动 /
   公司排名 / 财务质量 / 估值 / screen 结果。

不允许在这里：
- 重新排序、重新打分、重新检测异常 flags（直接复用 core 的输出）。
- 拼研报或买卖建议。

返回数据保持原始字段名（snake_case），由 ``app.py`` 负责 UI 渲染。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fundamentalscreener.company_ranking import compute_company_ranking, sort_companies
from fundamentalscreener.config import DEFAULT_PERIODS, DEFAULT_SECTOR_SORT
from fundamentalscreener.financial_quality import compute_financial_quality, sort_financials
from fundamentalscreener.lineage import SnapshotMetadata
from fundamentalscreener.quality import QualityReport
from fundamentalscreener.repositories import FixtureRepository, MarketSnapshot
from fundamentalscreener.schema import (
    CompanyEntry,
    FinancialEntry,
    SectorEntry,
    ValuationEntry,
)
from fundamentalscreener.screening import ScreeningResult, run_screening
from fundamentalscreener.sector_rotation import (
    SectorRotationResult,
    compute_sector_rotation,
    sort_entries,
)
from fundamentalscreener.sqlite_repository import (
    QualityInvalidError,
    SqliteFundamentalRepository,
)
from fundamentalscreener.valuation import compute_valuation, sort_valuations


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class SectorBoardData:
    """板块工作台所需的数据集合。"""

    date: str
    classification_system: str
    benchmark_id: str
    benchmark_name: str
    sectors: List[SectorEntry] = field(default_factory=list)
    chart_series: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    # Phase 7: snapshot 血缘与质量状态（SQLite 数据源有值，fixture 模式留空）。
    data_quality_status: str = ""
    data_cutoff: str = ""
    source_set: Dict[str, str] = field(default_factory=dict)
    fetch_run_id: str = ""
    quality_report_id: str = ""
    quality_issues: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class SectorDetailData:
    """单个板块详情：公司排名 + 板块内 fin/val 对比 + flags。"""

    sector_id: str
    sector_name: str
    companies: List[CompanyEntry] = field(default_factory=list)
    financials: List[FinancialEntry] = field(default_factory=list)
    valuations: List[ValuationEntry] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class SnapshotLoadResult:
    """``load_snapshot_from_db`` 的返回：snapshot + 血缘 metadata + 质量报告。

    - ``metadata`` / ``quality_report`` 仅 SQLite 数据源有值；fixture 路径为 ``None``。
    - ``quality_error`` 在 ``QualityInvalidError`` 抛出时填充，供 UI 展示阻断原因。
    """

    snapshot: Optional[MarketSnapshot] = None
    metadata: Optional[SnapshotMetadata] = None
    quality_report: Optional[QualityReport] = None
    quality_error: Optional[str] = None


# ---------------------------------------------------------------------------
# 加载
# ---------------------------------------------------------------------------


def load_snapshot(fixture_path: Path | str) -> MarketSnapshot:
    """读取 fixture 文件并返回 MarketSnapshot（Phase 0-5 行为，保留不变）。"""

    repo = FixtureRepository(Path(fixture_path))
    return repo.load_snapshot()


def load_snapshot_from_db(
    db_path: Path | str,
    analysis_date: str,
    classification_system: str = "em_industry",
    benchmark: str = "hs300",
) -> SnapshotLoadResult:
    """从 SQLite 读取真实数据（Phase 7 真实数据入口）。

    - 质量状态为 ``invalid`` 时 ``SqliteFundamentalRepository.load_snapshot()`` 抛
      ``QualityInvalidError``，此处捕获并填入 ``quality_error``，返回空 snapshot，
      由 UI 展示阻断原因而非崩溃。
    - 正常时返回 snapshot + metadata + quality_report，供 UI 展示数据日期、来源
      和质量 warnings（docs §19 Phase 7 DoD）。
    """

    repo = SqliteFundamentalRepository(
        db_path,
        analysis_date=analysis_date,
        classification_system=classification_system,
        benchmark=benchmark,
    )
    try:
        snapshot = repo.load_snapshot()
    except QualityInvalidError as exc:
        return SnapshotLoadResult(
            quality_error=str(exc),
            quality_report=repo.quality_report,
        )
    return SnapshotLoadResult(
        snapshot=snapshot,
        metadata=repo.metadata,
        quality_report=repo.quality_report,
    )


# ---------------------------------------------------------------------------
# 板块层
# ---------------------------------------------------------------------------


def build_sector_board(
    snapshot: MarketSnapshot,
    sort: str = DEFAULT_SECTOR_SORT,
    periods: Sequence[int] = DEFAULT_PERIODS,
    top: Optional[int] = None,
    metadata: Optional[SnapshotMetadata] = None,
    quality_report: Optional[QualityReport] = None,
) -> SectorBoardData:
    """调用 ``compute_sector_rotation`` 并整理为视图数据。

    - ``sort`` / ``top`` / ``periods`` 都透传给 core 的排序函数，不重复实现。
    - ``chart_series`` 序列化成 ``{series_id, series_name, type, points}`` 字典，
      便于 Streamlit 直接画线。
    - ``metadata`` / ``quality_report`` 来自 SQLite 数据源（Phase 7），透传到
      ``SectorBoardData`` 供 UI 展示数据日期、来源和质量 warnings。
    """

    result: SectorRotationResult = compute_sector_rotation(
        snapshot, periods=tuple(periods)
    )
    ordered = sort_entries(result.sectors, sort)
    if top is not None and top >= 0:
        ordered = ordered[:top]

    # chart_series 与板块表保持一致：仅保留入选板块 + 基准，避免 Top N
    # 表只显示 1 个板块、走势图却画了所有板块的错位体验。
    selected_ids = {s.sector_id for s in ordered}
    chart_series: List[Dict[str, Any]] = []
    for series in result.chart_series:
        if series.type != "benchmark" and series.series_id not in selected_ids:
            continue
        chart_series.append(
            {
                "series_id": series.series_id,
                "series_name": series.series_name,
                "type": series.type,
                "points": [{"date": p.date, "value": p.value} for p in series.points],
            }
        )

    board = SectorBoardData(
        date=snapshot.date,
        classification_system=snapshot.classification_system,
        benchmark_id=snapshot.benchmark.id,
        benchmark_name=snapshot.benchmark.name,
        sectors=list(ordered),
        chart_series=chart_series,
        warnings=list(result.warnings),
    )
    if metadata is not None:
        board.data_quality_status = metadata.data_quality_status
        board.data_cutoff = metadata.data_cutoff
        board.source_set = metadata.source_set.to_dict()
        board.fetch_run_id = metadata.fetch_run_id
        board.quality_report_id = metadata.quality_report_id
    if quality_report is not None:
        board.quality_issues = [i.to_dict() for i in quality_report.issues]
    return board


# ---------------------------------------------------------------------------
# 板块详情
# ---------------------------------------------------------------------------


def build_sector_detail(
    snapshot: MarketSnapshot,
    sector_id: str,
    company_sort: str = "combined_score",
    top: Optional[int] = None,
) -> SectorDetailData:
    """单板块：公司排名 + 板块成分财务/估值横向对比 + flags。

    - 排序统一用 ``sort_companies``，不做新的排序逻辑。
    - 板块内财务和估值数据直接来自 ``compute_company_ranking`` 暴露的
      ``financials`` / ``valuations`` 映射，保持 Phase 5 的"分数可追溯"语义。
    """

    sector = next((s for s in snapshot.sectors if s.sector_id == sector_id), None)
    if sector is None:
        return SectorDetailData(
            sector_id=sector_id,
            sector_name="",
            warnings=[f"sector_not_found: {sector_id}"],
        )

    ranking = compute_company_ranking(snapshot, sector_id)
    ordered = sort_companies(ranking.companies, company_sort)
    if top is not None and top >= 0:
        ordered = ordered[:top]

    # financials/valuations 仅展示当前板块成分股，且顺序与排名一致。
    financial_entries = [
        ranking.financials[code]
        for code in (e.code for e in ordered)
        if code in ranking.financials
    ]
    valuation_entries = [
        ranking.valuations[code]
        for code in (e.code for e in ordered)
        if code in ranking.valuations
    ]

    return SectorDetailData(
        sector_id=sector.sector_id,
        sector_name=sector.sector_name,
        companies=list(ordered),
        financials=financial_entries,
        valuations=valuation_entries,
        warnings=list(ranking.warnings),
    )


# ---------------------------------------------------------------------------
# 序列化辅助：DataFrame / dict
# ---------------------------------------------------------------------------


def sectors_to_rows(sectors: Sequence[SectorEntry]) -> List[Dict[str, Any]]:
    return [s.to_dict() for s in sectors]


def companies_to_rows(companies: Sequence[CompanyEntry]) -> List[Dict[str, Any]]:
    return [c.to_dict() for c in companies]


def financials_to_rows(items: Sequence[FinancialEntry]) -> List[Dict[str, Any]]:
    return [f.to_dict() for f in items]


def valuations_to_rows(items: Sequence[ValuationEntry]) -> List[Dict[str, Any]]:
    return [v.to_dict() for v in items]


def collect_company_flags(
    companies: Sequence[CompanyEntry],
    financials: Sequence[FinancialEntry],
    valuations: Sequence[ValuationEntry],
) -> List[Dict[str, Any]]:
    """汇总每家公司的异常 flags 和估值 label，便于 UI 单表展示。"""

    fin_index = {f.code: f for f in financials}
    val_index = {v.code: v for v in valuations}
    rows: List[Dict[str, Any]] = []
    for c in companies:
        fin = fin_index.get(c.code)
        val = val_index.get(c.code)
        rows.append(
            {
                "code": c.code,
                "name": c.name,
                "group": c.group,
                "company_flags": list(c.flags or []),
                "financial_flags": list(fin.abnormal_flags) if fin else [],
                "valuation_label": val.label if val else None,
                "warnings": list(c.warnings or []),
            }
        )
    return rows


__all__ = [
    "SectorBoardData",
    "SectorDetailData",
    "SnapshotLoadResult",
    "build_sector_board",
    "build_sector_detail",
    "collect_company_flags",
    "companies_to_rows",
    "financials_to_rows",
    "load_snapshot",
    "load_snapshot_from_db",
    "sectors_to_rows",
    "valuations_to_rows",
]
