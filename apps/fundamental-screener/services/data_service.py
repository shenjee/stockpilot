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
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fundamentalscreener.company_ranking import compute_company_ranking, sort_companies
from fundamentalscreener.config import DEFAULT_PERIODS, DEFAULT_SECTOR_SORT
from fundamentalscreener.financial_quality import compute_financial_quality, sort_financials
from fundamentalscreener.lineage import SnapshotMetadata, now_cn
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
from fundamentalscreener.sqlite_schema import connect, init_db
from fundamentalscreener.valuation import compute_valuation, sort_valuations


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
# 产品级入口（前端计划 Step 2）
# ---------------------------------------------------------------------------


def _find_latest_analysis_date(
    db_path: Path,
    classification_system: Optional[str] = None,
) -> Optional[str]:
    """查询 SQLite 缓存中指定口径的最新可用分析日期。

    返回 ``sector_daily_bars`` 的 ``MAX(trade_date)``。当 ``classification_system``
    非空时，按该口径过滤，避免 ``em_industry`` 缓存的较晚日期使 ``ths_industry``
    路径误选到不存在的分析日期。如果数据库文件不存在、表不存在或表为空，返回
    ``None``。
    """

    if not db_path.exists():
        return None
    try:
        conn = connect(db_path)
    except Exception:
        return None
    try:
        init_db(conn)
        if classification_system:
            row = conn.execute(
                "SELECT MAX(trade_date) FROM sector_daily_bars "
                "WHERE classification_system = ?",
                (classification_system,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT MAX(trade_date) FROM sector_daily_bars"
            ).fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None
    finally:
        conn.close()


def _has_cache_for_date(
    db_path: Path,
    analysis_date: str,
    classification_system: str,
) -> bool:
    """检查缓存中是否存在该口径下 ``trade_date <= analysis_date`` 的板块行情。

    匹配 repository 的 point-in-time 语义（``sqlite_repository.py``：行情按
    ``trade_date <= analysis_date`` 截断），因此用户选择非交易日或晚于最新缓存
    交易日的日期时，只要存在更早的缓存行，repository 仍能组装快照。

    用于在 ``load_latest_snapshot()`` 显式传入 ``analysis_date`` 时，先验证缓存
    是否存在，避免空 DB 被质量检查判为 ``invalid``（违反 no-cache 契约）。
    """

    if not db_path.exists():
        return False
    try:
        conn = connect(db_path)
    except Exception:
        return False
    try:
        init_db(conn)
        row = conn.execute(
            "SELECT 1 FROM sector_daily_bars "
            "WHERE trade_date <= ? AND classification_system = ? LIMIT 1",
            (analysis_date, classification_system),
        ).fetchone()
        return row is not None
    except Exception:
        return False
    finally:
        conn.close()


def load_latest_snapshot(
    db_path: Optional[Path | str] = None,
    analysis_date: Optional[str] = None,
    classification_system: str = DEFAULT_CLASSIFICATION_SYSTEM,
    benchmark: str = DEFAULT_BENCHMARK,
) -> FrontendSnapshotResult:
    """读取最新可用真实数据缓存（前端计划 §2.8）。

    ``analysis_date`` 为 ``None`` 时自动发现该口径下最新可用分析日期；显式传入
    时直接使用该日期（用于前端"指定分析日期"控件）。无缓存时返回
    ``status="no_cache"`` + ``snapshot=None``，**不抛异常**。
    以下情况均返回 ``no_cache``：
    - 默认 SQLite 缓存文件不存在。
    - 缓存表尚未初始化。
    - 缓存表为空（按口径过滤后）。
    - 找不到可用分析日期。
    - 显式传入的 ``analysis_date`` 在缓存中无对应数据。

    有缓存时通过 ``SqliteFundamentalRepository`` 组装 ``MarketSnapshot``。
    """

    db = Path(db_path) if db_path else DEFAULT_DB_PATH

    if analysis_date is None:
        analysis_date = _find_latest_analysis_date(db, classification_system)
    if analysis_date is None:
        return FrontendSnapshotResult(
            status="no_cache",
            message="暂无本地数据，请点击获取数据。",
        )

    # 显式传入 analysis_date 时仍需验证缓存是否存在，避免空 DB 被判为 invalid。
    if not _has_cache_for_date(db, analysis_date, classification_system):
        return FrontendSnapshotResult(
            status="no_cache",
            message="暂无本地数据，请点击获取数据。",
        )

    load_result = load_snapshot_from_db(
        db, analysis_date, classification_system, benchmark
    )

    if load_result.quality_error:
        return FrontendSnapshotResult(
            status="invalid",
            message=load_result.quality_error,
            quality_report=load_result.quality_report,
        )

    status = "ok"
    if load_result.metadata:
        status = load_result.metadata.data_quality_status or "ok"

    return FrontendSnapshotResult(
        snapshot=load_result.snapshot,
        metadata=load_result.metadata,
        quality_report=load_result.quality_report,
        status=status,
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
    """同步数据并写入内部缓存，然后读取最新快照。

    内部调用 ``sync_all()`` 和同花顺行业板块数据源（``ths_industry``）。
    刷新失败但有旧缓存时展示旧缓存和失败提示（``status="refresh_failed"``）。
    刷新失败且无缓存时返回 ``status="no_cache"``。

    ``source`` 参数仅供测试注入 fake 数据源；产品路径不传，默认构造
    ``AkShareFundamentalDataSource()``（惰性导入 akshare）。
    """

    db = Path(db_path) if db_path else DEFAULT_DB_PATH
    if analysis_date is None:
        analysis_date = now_cn().date().isoformat()

    # 构造数据源（默认 AkShare THS，可注入用于测试）
    if source is None:
        from fundamentalscreener.data_sources.akshare_source import (
            AkShareFundamentalDataSource,
        )
        source = AkShareFundamentalDataSource()

    from fundamentalscreener.sync import LIGHT_REQUIRED_TASKS, sync_all

    refresh_result_dict: Optional[Dict[str, Any]] = None
    sync_error: Optional[str] = None
    try:
        conn = connect(db)
        try:
            # §15.9: codes=None 时执行轻量层同步（sector_ids=[] 跳过成分股和
            # 个股层），避免全市场 per-code 抓取阻塞首屏。codes 非空时回退
            # 全量行为（向后兼容）。
            sync_sector_ids: Optional[Sequence[str]] = [] if codes is None else None
            result = sync_all(
                conn,
                source,
                analysis_date=analysis_date,
                classification_system=classification_system,
                benchmark=benchmark,
                history_days=history_days,
                codes=codes,
                sector_ids=sync_sector_ids,
            )
            refresh_result_dict = result.to_dict()
            # §15.9.4a: 首屏只校验轻量必需集（板块列表 + 板块日线 + benchmark）。
            # 成分股属重量层，失败不阻塞首屏——用户点进板块详情时才触发同步。
            # 非轻量任务（get_stock_universe / get_company_daily_snapshot 等）失败
            # 也不阻塞首屏：sync_all 的 _run_task 会捕获异常写进 result.tasks，
            # 此处只按 LIGHT_REQUIRED_TASKS 判定，其它失败最多由质量检查降级。
            by_task = {t["task"]: t for t in result.tasks}
            required_ok = all(
                by_task.get(t, {}).get("success")
                and int(by_task.get(t, {}).get("row_count", 0) or 0) > 0
                for t in LIGHT_REQUIRED_TASKS
            )
            if not required_ok:
                failed_details = [
                    f"{t}: {by_task.get(t, {}).get('error', 'unknown')}"
                    for t in LIGHT_REQUIRED_TASKS
                    if not by_task.get(t, {}).get("success")
                ]
                empty_required = [
                    t
                    for t in LIGHT_REQUIRED_TASKS
                    if by_task.get(t, {}).get("success")
                    and int(by_task.get(t, {}).get("row_count", 0) or 0) == 0
                ]
                sync_error = (
                    "sync did not satisfy required tasks"
                    + (f": failed=[{', '.join(failed_details)}]" if failed_details else "")
                    + (
                        f": empty_required={empty_required}"
                        if empty_required
                        else ""
                    )
                )
        finally:
            conn.close()
    except Exception as exc:
        sync_error = str(exc)

    # 同步后读取快照（无论成功失败都尝试读取缓存）
    load_result = load_snapshot_from_db(
        db, analysis_date, classification_system, benchmark
    )

    if load_result.snapshot is not None:
        status = "ok"
        if load_result.metadata:
            status = load_result.metadata.data_quality_status or "ok"
        message = ""
        if sync_error:
            status = "refresh_failed"
            message = f"数据刷新失败，展示最近可用缓存：{sync_error}"
        return FrontendSnapshotResult(
            snapshot=load_result.snapshot,
            metadata=load_result.metadata,
            quality_report=load_result.quality_report,
            status=status,
            message=message,
            refresh_result=refresh_result_dict,
        )

    # 无缓存可用
    if load_result.quality_error:
        # sync 失败导致 DB 为空 → no_cache；sync 成功但质量检查阻断 → invalid
        if sync_error:
            return FrontendSnapshotResult(
                status="no_cache",
                message=(
                    "数据刷新失败且无可用缓存。"
                    + (f"原因：{sync_error}" if sync_error else "")
                ),
                refresh_result=refresh_result_dict,
            )
        return FrontendSnapshotResult(
            status="invalid",
            message=load_result.quality_error,
            quality_report=load_result.quality_report,
            refresh_result=refresh_result_dict,
        )

    return FrontendSnapshotResult(
        status="no_cache",
        message=(
            "数据刷新失败且无可用缓存。"
            + (f"原因：{sync_error}" if sync_error else "")
        ),
        refresh_result=refresh_result_dict,
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

    db = Path(db_path) if db_path else DEFAULT_DB_PATH
    if not db.exists():
        return False
    if analysis_date is None:
        analysis_date = _find_latest_analysis_date(db, classification_system)
    if analysis_date is None:
        return False
    try:
        conn = connect(db)
    except Exception:
        return False
    try:
        init_db(conn)
        row = conn.execute(
            "SELECT 1 FROM sector_constituents "
            "WHERE sector_id = ? AND classification_system = ? "
            "AND as_of_date <= ? LIMIT 1",
            (sector_id, classification_system, analysis_date),
        ).fetchone()
        return row is not None
    except Exception:
        return False
    finally:
        conn.close()


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

    db = Path(db_path) if db_path else DEFAULT_DB_PATH
    if analysis_date is None:
        analysis_date = _find_latest_analysis_date(db, classification_system)
    if analysis_date is None:
        return SectorDetailResult(
            status="no_cache",
            message="暂无本地数据，请先获取数据。",
        )

    # 构造数据源（默认 AkShare THS，可注入用于测试）
    if source is None:
        from fundamentalscreener.data_sources.akshare_source import (
            AkShareFundamentalDataSource,
        )
        source = AkShareFundamentalDataSource()

    from fundamentalscreener.sync import DETAIL_REQUIRED_TASKS, sync_all

    refresh_result_dict: Optional[Dict[str, Any]] = None
    sync_error: Optional[str] = None
    try:
        conn = connect(db)
        try:
            result = sync_all(
                conn,
                source,
                analysis_date=analysis_date,
                classification_system=classification_system,
                benchmark=benchmark,
                history_days=history_days,
                sector_ids=[sector_id],
            )
            refresh_result_dict = result.to_dict()
            # §15.9.4b: sync_all 的 _run_task 捕获异常后写进 result.tasks，
            # 不会向上抛出，必须显式检查重量层必需任务（成分股）是否成功且有行。
            # 失败或 0 行 → 视为 detail 层刷新失败，提示用户而非静默展示空详情。
            by_task = {t["task"]: t for t in result.tasks}
            failed_details = [
                f"{t}: {by_task.get(t, {}).get('error', 'unknown')}"
                for t in DETAIL_REQUIRED_TASKS
                if not by_task.get(t, {}).get("success")
            ]
            empty_details = [
                t
                for t in DETAIL_REQUIRED_TASKS
                if by_task.get(t, {}).get("success")
                and int(by_task.get(t, {}).get("row_count", 0) or 0) == 0
            ]
            if failed_details or empty_details:
                sync_error = (
                    "sync did not satisfy detail required tasks"
                    + (f": failed=[{', '.join(failed_details)}]" if failed_details else "")
                    + (f": empty={empty_details}" if empty_details else "")
                )
        finally:
            conn.close()
    except Exception as exc:
        sync_error = str(exc)

    # 从 DB 加载快照（无论同步成功失败都尝试读取缓存）
    load_result = load_snapshot_from_db(
        db, analysis_date, classification_system, benchmark
    )

    if load_result.snapshot is not None:
        detail = build_sector_detail(
            load_result.snapshot, sector_id,
            company_sort=company_sort, top=top,
        )
        status = "ok"
        message = ""
        if sync_error:
            # 重量层失败但有旧缓存 → refresh_failed；若详情仍无公司则升级为 no_cache
            # 语义（用户点进未加载板块，刷新失败且无可用详情）。
            if not detail.companies:
                return SectorDetailResult(
                    detail=detail,
                    status="no_cache",
                    message=f"板块详情刷新失败且无可用成分股数据。原因：{sync_error}",
                    refresh_result=refresh_result_dict,
                )
            status = "refresh_failed"
            message = f"板块数据刷新失败，展示最近可用缓存：{sync_error}"
        elif load_result.metadata:
            qs = load_result.metadata.data_quality_status or "ok"
            if qs in ("degraded", "stale"):
                status = qs
        return SectorDetailResult(
            detail=detail,
            status=status,
            message=message,
            refresh_result=refresh_result_dict,
        )

    # 无缓存可用
    if load_result.quality_error:
        if sync_error:
            return SectorDetailResult(
                status="no_cache",
                message=f"板块数据刷新失败且无可用缓存。原因：{sync_error}",
                refresh_result=refresh_result_dict,
            )
        return SectorDetailResult(
            status="invalid",
            message=load_result.quality_error,
            refresh_result=refresh_result_dict,
        )

    return SectorDetailResult(
        status="no_cache",
        message=(
            f"板块数据刷新失败且无可用缓存。原因：{sync_error}"
            if sync_error
            else "暂无本地数据。"
        ),
        refresh_result=refresh_result_dict,
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
