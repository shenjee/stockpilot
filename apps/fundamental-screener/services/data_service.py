"""Fundamental Screener Streamlit 数据服务。

本模块做三件事：
1. 提供产品级数据入口 ``load_latest_snapshot`` / ``refresh_market_data`` /
   ``load_or_refresh_snapshot``，自动管理内部 SQLite 缓存，不暴露数据库路径
   或 fixture 细节给前端。
2. 通过 ``FixtureRepository`` 加载市场快照（仅测试辅助，不在产品路径调用）。
3. 调用 ``packages.fundamentalscreener`` 的 core 函数，得到板块轮动 /
   公司排名 / 财务质量 / 估值 / screen 结果。

``FrontendSnapshotResult`` 是前端适配结构，只存在于本层，不下沉到 core。
core 继续保留 ``MarketSnapshot``、repository、``sync_all()`` 结果等领域对象。

不允许在这里：
- 重新排序、重新打分、重新检测异常 flags（直接复用 core 的输出）。
- 拼研报或买卖建议。

返回数据保持原始字段名（snake_case），由 ``app.py`` 负责 UI 渲染。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from fundamentalscreener import snapshot_service as fs_snapshot_service
from fundamentalscreener.company_ranking import compute_company_ranking, sort_companies
from fundamentalscreener.config import DEFAULT_PERIODS, DEFAULT_SECTOR_SORT
from fundamentalscreener.lineage import SnapshotMetadata
from fundamentalscreener.quality import QualityReport
from fundamentalscreener.repositories import FixtureRepository, MarketSnapshot
from fundamentalscreener.schema import (
    CompanyEntry,
    FinancialEntry,
    SectorEntry,
    ValuationEntry,
)
from fundamentalscreener.sector_rotation import (
    SectorRotationResult,
    compute_sector_rotation,
    sort_entries,
)
from services.row_builders import (
    collect_company_flags,
    companies_to_rows,
    financials_to_rows,
    sectors_to_rows,
    valuations_to_rows,
)


# ---------------------------------------------------------------------------
# 产品级常量
# ---------------------------------------------------------------------------

# 仓库根目录：apps/fundamental-screener/services/data_service.py -> parents[3]
_ROOT = Path(__file__).resolve().parents[3]

# 内部默认 SQLite 缓存路径（前端计划 Step 2）。
DEFAULT_DB_PATH: Path = _ROOT / "stockpilot" / "db" / "fundamental_data.sqlite"

# 产品默认口径：同花顺行业板块。
DEFAULT_CLASSIFICATION_SYSTEM: str = "ths_industry"

DEFAULT_BENCHMARK: str = "hs300"

DEFAULT_HISTORY_DAYS: int = 90


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


@dataclass
class FrontendSnapshotResult:
    """前端适配层统一返回结构（前端计划 §2.8）。

    只存在于 ``data_service.py``，不下沉到 core。把缓存状态、刷新状态、质量报告、
    用户提示语包装成 UI 好消费的结果。

    ``status`` 取值：
    - ``ok``：正常展示。
    - ``degraded``：展示结果，同时提示部分财务/估值缺失。
    - ``stale``：展示最近可用缓存，同时提示数据不是最新。
    - ``invalid``：不展示评分结果，展示阻断原因。
    - ``no_cache``：无本地数据，展示空状态和获取按钮。
    - ``refresh_failed``：刷新失败但有旧缓存，展示旧缓存和失败原因。
    """

    snapshot: Optional[MarketSnapshot] = None
    metadata: Optional[SnapshotMetadata] = None
    quality_report: Optional[QualityReport] = None
    status: str = "ok"
    message: str = ""
    refresh_result: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# 加载
# ---------------------------------------------------------------------------


def _resolve_db_path(db_path: Optional[Path | str]) -> Path:
    return Path(db_path) if db_path else DEFAULT_DB_PATH


def load_snapshot(fixture_path: Path | str) -> MarketSnapshot:
    """读取 fixture 文件并返回 MarketSnapshot（Phase 0-5 行为，保留不变）。"""

    repo = FixtureRepository(Path(fixture_path))
    return repo.load_snapshot()


def load_snapshot_from_db(
    db_path: Path | str,
    analysis_date: str,
    classification_system: str = DEFAULT_CLASSIFICATION_SYSTEM,
    benchmark: str = DEFAULT_BENCHMARK,
) -> SnapshotLoadResult:
    """从 SQLite 读取真实数据（Phase 7 真实数据入口）。"""

    result = fs_snapshot_service.load_snapshot_from_db(
        db_path,
        analysis_date=analysis_date,
        classification_system=classification_system,
        benchmark=benchmark,
    )
    return SnapshotLoadResult(
        snapshot=result.snapshot,
        metadata=result.metadata,
        quality_report=result.quality_report,
        quality_error=result.quality_error,
    )


# ---------------------------------------------------------------------------
# 产品级入口（前端计划 Step 2）
# ---------------------------------------------------------------------------


def _find_latest_analysis_date(
    db_path: Path,
    classification_system: Optional[str] = None,
) -> Optional[str]:
    """查询 SQLite 缓存中指定口径的最新可用分析日期。"""

    return fs_snapshot_service._find_latest_analysis_date(
        db_path,
        classification_system,
    )


def _has_cache_for_date(
    db_path: Path,
    analysis_date: str,
    classification_system: str,
) -> bool:
    """检查缓存中是否存在该口径下 ``trade_date <= analysis_date`` 的板块行情。"""

    return fs_snapshot_service._has_cache_for_date(
        db_path,
        analysis_date,
        classification_system,
    )


def get_latest_cached_date(
    db_path: Optional[Path | str] = None,
    classification_system: str = DEFAULT_CLASSIFICATION_SYSTEM,
) -> Optional[str]:
    """返回缓存中该口径下最新可用分析日期（``YYYY-MM-DD``），无缓存返回 ``None``。

    供前端设置日期控件的默认值：有缓存时默认指向最新数据日，无缓存时由调用方
    回退到 ``date.today()``。
    """

    return fs_snapshot_service.get_latest_cached_date(
        _resolve_db_path(db_path),
        classification_system,
    )


def load_latest_snapshot(
    db_path: Optional[Path | str] = None,
    analysis_date: Optional[str] = None,
    classification_system: str = DEFAULT_CLASSIFICATION_SYSTEM,
    benchmark: str = DEFAULT_BENCHMARK,
) -> FrontendSnapshotResult:
    """读取最新可用真实数据缓存（前端计划 §2.8）。"""

    result = fs_snapshot_service.load_latest_snapshot(
        db_path=_resolve_db_path(db_path),
        analysis_date=analysis_date,
        classification_system=classification_system,
        benchmark=benchmark,
    )
    message = ""
    if result.status == "no_cache":
        message = "暂无本地数据，请点击获取数据。"
    elif result.status == "invalid":
        message = result.reason
    return FrontendSnapshotResult(
        snapshot=result.snapshot,
        metadata=result.metadata,
        quality_report=result.quality_report,
        status=result.status,
        message=message,
    )


def refresh_market_data(
    db_path: Optional[Path | str] = None,
    analysis_date: Optional[str] = None,
    classification_system: str = DEFAULT_CLASSIFICATION_SYSTEM,
    benchmark: str = DEFAULT_BENCHMARK,
    history_days: int = DEFAULT_HISTORY_DAYS,
    codes: Optional[Sequence[str]] = None,
    source: Optional[Any] = None,
) -> FrontendSnapshotResult:
    """同步数据并写入内部缓存，然后读取最新快照。"""

    result = fs_snapshot_service.refresh_market_data(
        db_path=_resolve_db_path(db_path),
        analysis_date=analysis_date,
        classification_system=classification_system,
        benchmark=benchmark,
        history_days=history_days,
        codes=codes,
        source=source,
    )
    message = ""
    if result.status == "refresh_failed":
        message = f"数据刷新失败，展示最近可用缓存：{result.reason}"
    elif result.status == "invalid":
        message = result.reason
    elif result.status == "no_cache" and result.reason:
        message = f"数据刷新失败且无可用缓存。原因：{result.reason}"
    elif result.status == "no_cache":
        message = "数据刷新失败且无可用缓存。"
    return FrontendSnapshotResult(
        snapshot=result.snapshot,
        metadata=result.metadata,
        quality_report=result.quality_report,
        status=result.status,
        message=message,
        refresh_result=result.refresh_result,
    )


def load_or_refresh_snapshot(
    refresh: bool = False,
    db_path: Optional[Path | str] = None,
    analysis_date: Optional[str] = None,
    classification_system: str = DEFAULT_CLASSIFICATION_SYSTEM,
    benchmark: str = DEFAULT_BENCHMARK,
    history_days: int = DEFAULT_HISTORY_DAYS,
    codes: Optional[Sequence[str]] = None,
    source: Optional[Any] = None,
) -> FrontendSnapshotResult:
    """前端一站式入口，按按钮状态决定是否刷新。

    ``refresh=True`` 时调用 ``refresh_market_data()``；
    ``refresh=False`` 时调用 ``load_latest_snapshot()``。
    """

    if refresh:
        return refresh_market_data(
            db_path=db_path,
            analysis_date=analysis_date,
            classification_system=classification_system,
            benchmark=benchmark,
            history_days=history_days,
            codes=codes,
            source=source,
        )
    return load_latest_snapshot(
        db_path=db_path,
        analysis_date=analysis_date,
        classification_system=classification_system,
        benchmark=benchmark,
    )


# ---------------------------------------------------------------------------
# 板块详情按需加载（§15.9）
# ---------------------------------------------------------------------------


@dataclass
class SectorDetailResult:
    """板块详情按需加载结果。

    ``status`` 取值：
    - ``ok``：正常展示。
    - ``degraded`` / ``stale``：展示结果，同时提示数据质量问题。
    - ``refresh_failed``：刷新失败但有旧缓存，展示旧缓存和失败原因。
    - ``no_cache``：无本地数据。
    - ``invalid``：质量检查阻断，无法生成快照。
    """

    detail: Optional[SectorDetailData] = None
    status: str = "ok"
    message: str = ""
    refresh_result: Optional[Dict[str, Any]] = None


def has_sector_detail_cache(
    sector_id: str,
    db_path: Optional[Path | str] = None,
    analysis_date: Optional[str] = None,
    classification_system: str = DEFAULT_CLASSIFICATION_SYSTEM,
) -> bool:
    """检查指定板块是否已有成分股缓存（§15.9 按需加载判断入口）。

    返回 ``True`` 时该板块在 ``sector_constituents`` 表中有 ``as_of_date <=
    analysis_date`` 的记录，可直接从缓存构建详情；返回 ``False`` 时需触发
    ``refresh_sector_detail`` 同步重量层数据。
    """

    return fs_snapshot_service.has_sector_detail_cache(
        sector_id,
        db_path=_resolve_db_path(db_path),
        analysis_date=analysis_date,
        classification_system=classification_system,
    )


def refresh_sector_detail(
    sector_id: str,
    db_path: Optional[Path | str] = None,
    analysis_date: Optional[str] = None,
    classification_system: str = DEFAULT_CLASSIFICATION_SYSTEM,
    benchmark: str = DEFAULT_BENCHMARK,
    history_days: int = DEFAULT_HISTORY_DAYS,
    company_sort: str = "combined_score",
    top: Optional[int] = None,
    source: Optional[Any] = None,
) -> SectorDetailResult:
    """§15.9: 按需同步指定板块的重量层数据并返回板块详情。

    1. 调用 ``sync_all(sector_ids=[sector_id])`` 同步该板块的成分股 + 个股数据
       （日线快照 + 估值 + 财务）。轻量层数据（板块列表 / 板块日线 / benchmark /
       股票池）由 ``sync_all`` 始终全量同步，此处不重复。
    2. 从 DB 加载快照（``SqliteFundamentalRepository.load_snapshot``）。
    3. 调用 ``build_sector_detail`` 构建公司排名 + 财务 + 估值。

    同步失败但有旧缓存时展示旧缓存和失败提示（``status="refresh_failed"``）。
    同步失败且无缓存时返回 ``status="no_cache"``。
    """

    result = fs_snapshot_service.refresh_sector_detail_snapshot(
        sector_id,
        db_path=_resolve_db_path(db_path),
        analysis_date=analysis_date,
        classification_system=classification_system,
        benchmark=benchmark,
        history_days=history_days,
        source=source,
    )

    detail = None
    if result.snapshot is not None:
        detail = build_sector_detail(
            result.snapshot,
            sector_id,
            company_sort=company_sort,
            top=top,
        )

    has_displayable_detail = detail is not None and bool(detail.companies)
    status = result.status
    if result.status in ("refresh_failed", "no_cache") and result.reason:
        status = "refresh_failed" if has_displayable_detail else "no_cache"

    if status == "refresh_failed":
        message = f"板块数据刷新失败，展示最近可用缓存：{result.reason}"
    elif status == "invalid":
        message = result.reason
    elif status == "no_cache" and result.reason:
        if detail is not None:
            message = f"板块详情刷新失败且无可用成分股数据。原因：{result.reason}"
        else:
            message = f"板块数据刷新失败且无可用缓存。原因：{result.reason}"
    elif status == "no_cache":
        message = "暂无本地数据，请先获取数据。"
    else:
        message = ""

    return SectorDetailResult(
        detail=detail,
        status=status,
        message=message,
        refresh_result=result.refresh_result,
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


__all__ = [
    "SectorBoardData",
    "SectorDetailData",
    "SectorDetailResult",
    "SnapshotLoadResult",
    "FrontendSnapshotResult",
    "DEFAULT_DB_PATH",
    "DEFAULT_CLASSIFICATION_SYSTEM",
    "DEFAULT_BENCHMARK",
    "DEFAULT_HISTORY_DAYS",
    "build_sector_board",
    "build_sector_detail",
    "collect_company_flags",
    "companies_to_rows",
    "financials_to_rows",
    "get_latest_cached_date",
    "has_sector_detail_cache",
    "load_latest_snapshot",
    "load_or_refresh_snapshot",
    "load_snapshot",
    "load_snapshot_from_db",
    "refresh_market_data",
    "refresh_sector_detail",
    "sectors_to_rows",
    "valuations_to_rows",
]
