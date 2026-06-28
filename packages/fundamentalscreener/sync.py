"""Fundamental Screener 数据治理同步入口（Phase 6B+6C）。

稳定调用方式：

    python -m packages.fundamentalscreener.sync init-db --db <path>
    python -m packages.fundamentalscreener.sync sync --db <path> --date <YYYY-MM-DD>
        --classification-system ths_industry
    python -m packages.fundamentalscreener.sync quality --db <path> --date <YYYY-MM-DD>

Phase 6B+6C 实现：
- ``init-db``：幂等初始化 SQLite schema。
- ``sync``：通过 ``AkShareFundamentalDataSource`` 或注入式 fake 数据源，将板块列表/
  成分股/板块行情/基准行情/股票池/公司日度快照/公司估值历史/财务指标写入 SQLite，
  并在 ``data_fetch_log`` 中留下来源血缘。默认使用同花顺行业板块（``ths_industry``），
  东方财富（``em_industry``）作为对照源。未安装 akshare 时返回 rc=2。
- ``quality``：占位输出空的 ``QualityReport``，真实规则在 Phase 6D 落地。

设计原则：
- 单个数据源方法失败不破坏已有缓存：``UPSERT``（``INSERT ... ON CONFLICT``）+ 单
  任务事务；任务失败时写入 ``data_fetch_log`` 的失败行并继续下一任务。
- 所有采集写入必须带 ``source`` / ``fetch_run_id`` / ``source_updated_at`` /
  ``created_at`` / ``updated_at``。
- rc=0 要求 Phase 6B 必需的板块层任务全部成功且写入行数 > 0；公司层任务失败也会
  导致 rc=1，但公司层 0 行成功不阻塞板块层判定。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .data_sources import (
    AkShareFundamentalDataSource,
    FakeFundamentalDataSource,
    FundamentalDataSource,
)
from .lineage import (
    DEFAULT_CONFIG_VERSION,
    DEFAULT_FORMULA_VERSION,
    SnapshotMetadata,
    SourceSet,
    new_fetch_run_id,
)
from .quality import QualityReport
from .sqlite_schema import connect, init_db, list_tables
from .sync_cli import (
    _parse_codes,
    _parse_sector_ids,
    build_parser,
    compute_sync_exit_code,
)
from .sync_persistence import (
    _run_task,
    _ts,
)
from .sync_task_builders import (
    build_benchmark_persist,
    build_company_daily_persist,
    build_company_valuation_persist,
    build_financial_metrics_persist,
    build_sector_constituents_persist,
    build_sector_daily_persist,
    build_sectors_persist,
    build_stock_universe_persist,
)

# ---------------------------------------------------------------------------
# 同步任务定义
# ---------------------------------------------------------------------------

# Phase 6B 必需的板块层任务（§15.9.4a 后已拆为轻量 + 重量两层）。
# 此常量保留为轻量必需集的别名，供 CLI rc 判定使用。成分股已移至
# ``DETAIL_REQUIRED_TASKS``，不再阻塞首屏/CLI 的轻量必需判定。
REQUIRED_PHASE_6B_TASKS: Tuple[str, ...] = (
    "list_sectors",
    "get_sector_daily",
    "get_benchmark_daily",
)

# §15.9.4a 必需任务分层：按需加载模式下成分股属重量层，不应阻塞首屏。
# - 轻量必需：首屏校验（板块列表 + 全部板块日线 + benchmark），任一失败或 0 行
#   → refresh_failed / no_cache。
# - 重量必需：仅在用户进入板块详情时触发；失败 → 该板块 degraded，不阻塞首屏。
LIGHT_REQUIRED_TASKS: Tuple[str, ...] = (
    "list_sectors",
    "get_sector_daily",
    "get_benchmark_daily",
)
DETAIL_REQUIRED_TASKS: Tuple[str, ...] = ("get_sector_constituents",)


@dataclass
class SyncResult:
    """一次 ``sync`` 调用的汇总结果。"""

    fetch_run_id: str
    started_at: str
    finished_at: str
    tasks: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def success_count(self) -> int:
        return sum(1 for t in self.tasks if t.get("success"))

    @property
    def failure_count(self) -> int:
        return sum(1 for t in self.tasks if not t.get("success"))

    @property
    def row_count(self) -> int:
        return sum(int(t.get("row_count", 0) or 0) for t in self.tasks)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fetch_run_id": self.fetch_run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "row_count": self.row_count,
            "tasks": list(self.tasks),
        }


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------


def sync_all(
    conn,
    source: FundamentalDataSource,
    *,
    analysis_date: str,
    classification_system: str,
    benchmark: str = "hs300",
    history_days: int = 90,
    codes: Optional[Sequence[str]] = None,
    sector_ids: Optional[Sequence[str]] = None,
    fetch_run_id: Optional[str] = None,
) -> SyncResult:
    """运行一次完整同步。

    Phase 6A 实现支持：板块列表、板块成分、板块行情、基准行情、股票池、公司日度
    快照、公司估值历史、财务指标。

    ``codes`` 用于限制公司层 per-code 抓取范围（估值历史 + 财务指标）。未提供
    ``codes`` 时，这两个 per-code 任务会被跳过，避免对全量股票池逐只发起网络
    请求（P2: 默认全量 fanout 会导致数千次顺序请求）。股票池和公司日度快照是
    batch 接口，始终运行。

    ``sector_ids``（§15.9.5 按需加载）：非空时将成分股抓取从"遍历全部板块"收窄
    到指定板块，并从已抓取成分股派生 ``codes`` 驱动个股层任务（日线快照 + 估值 +
    财务）。``sector_ids=None`` 时回退当前全量行为（向后兼容）。``sector_ids`` 非
    空时，``codes`` 参数可选：显式传入则与派生 codes 取交集，未传入则直接用派生
    codes。轻量层（板块列表 / 板块日线 / benchmark / 股票池）始终全量同步。
    """

    init_db(conn)

    fetch_run_id = fetch_run_id or new_fetch_run_id()
    # source_name 必须反映实际使用的 classification_system，而非对象级固定值。
    # AkShareFundamentalDataSource 同时支持 ths_industry / em_industry，直接用
    # source.name 可能导致 EM 数据被标记为 akshare_ths（lineage 误标）。
    if isinstance(source, AkShareFundamentalDataSource):
        source_name = (
            "akshare_ths" if classification_system == "ths_industry" else "akshare_em"
        )
    else:
        source_name = getattr(source, "name", "unknown")
    started_at = _ts()

    start_date = (
        datetime.fromisoformat(analysis_date) - timedelta(days=history_days)
    ).date().isoformat()

    result = SyncResult(
        fetch_run_id=fetch_run_id,
        started_at=started_at,
        finished_at="",
        tasks=[],
    )

    sectors_rows: List[Dict[str, Any]] = []

    def _fetch_sectors() -> List[Dict[str, Any]]:
        nonlocal sectors_rows
        sectors_rows = source.list_sectors(classification_system)
        return sectors_rows

    result.tasks.append(
        _run_task(
            conn,
            fetch_run_id=fetch_run_id,
            source_name=source_name,
            task="list_sectors",
            fetch=_fetch_sectors,
            persist=build_sectors_persist(
                conn,
                source_name=source_name,
                fetch_run_id=fetch_run_id,
                classification_system=classification_system,
            ),
        )
    )

    # 板块成分 & 板块行情：以 sectors_rows 为驱动
    constituents_rows: List[Dict[str, Any]] = []

    def _fetch_constituents() -> List[Dict[str, Any]]:
        nonlocal constituents_rows
        # §15.9.5：sector_ids 非空时只遍历指定板块（按需加载），否则遍历全部
        # sectors_rows（向后兼容）。轻量层（list_sectors）始终全量，此处成分股属
        # 重量层。
        if sector_ids is not None:
            wanted = set(str(s) for s in sector_ids)
            target_sectors = [
                s for s in sectors_rows if str(s.get("sector_id", "")) in wanted
            ]
        else:
            target_sectors = sectors_rows
        out: List[Dict[str, Any]] = []
        for s in target_sectors:
            sid = str(s.get("sector_id", ""))
            if not sid:
                continue
            try:
                out.extend(
                    source.get_sector_constituents(sid, classification_system, analysis_date)
                )
            except Exception:
                continue
        # 目标板块非空但成分股总数为 0 → 几乎必然是数据源故障（反爬 403、空页、
        # API 结构变更），不能记成"成功写入 0 行"。抛错让 _run_task 标记 fetch_failed。
        # 注意：sector_ids 未命中任何板块时 target_sectors 为空，不抛错（graceful
        # no-op），避免按需加载指定了尚未出现在 sectors 表的板块时误判为故障。
        if target_sectors and not out:
            raise RuntimeError(
                f"get_sector_constituents: {len(target_sectors)} sector(s) targeted but "
                f"0 constituents returned — likely a data source failure "
                f"(anti-crawl, HTTP error, or API structure change)."
            )
        constituents_rows = out
        return out

    result.tasks.append(
        _run_task(
            conn,
            fetch_run_id=fetch_run_id,
            source_name=source_name,
            task="get_sector_constituents",
            fetch=_fetch_constituents,
            persist=build_sector_constituents_persist(
                conn,
                source_name=source_name,
                fetch_run_id=fetch_run_id,
                classification_system=classification_system,
                analysis_date=analysis_date,
            ),
        )
    )

    def _fetch_sector_daily() -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for s in sectors_rows:
            sid = str(s.get("sector_id", ""))
            if not sid:
                continue
            try:
                out.extend(
                    source.get_sector_daily(
                        sid, classification_system, start_date, analysis_date
                    )
                )
            except Exception:
                continue
        return out

    result.tasks.append(
        _run_task(
            conn,
            fetch_run_id=fetch_run_id,
            source_name=source_name,
            task="get_sector_daily",
            fetch=_fetch_sector_daily,
            persist=build_sector_daily_persist(
                conn,
                source_name=source_name,
                fetch_run_id=fetch_run_id,
                classification_system=classification_system,
            ),
        )
    )

    # 基准日线：独立写入 ``benchmark_daily_bars``，不污染 sector_daily_bars。
    # 详见 docs §18：benchmark 与 sector 是不同实体，schema 上必须区分。
    def _fetch_benchmark() -> List[Dict[str, Any]]:
        return source.get_benchmark_daily(benchmark, start_date, analysis_date)

    result.tasks.append(
        _run_task(
            conn,
            fetch_run_id=fetch_run_id,
            source_name=source_name,
            task="get_benchmark_daily",
            fetch=_fetch_benchmark,
            persist=build_benchmark_persist(
                conn,
                source_name=source_name,
                fetch_run_id=fetch_run_id,
                benchmark=benchmark,
            ),
        )
    )

    # 公司层
    def _fetch_universe() -> List[Dict[str, Any]]:
        return source.get_stock_universe(analysis_date)

    result.tasks.append(
        _run_task(
            conn,
            fetch_run_id=fetch_run_id,
            source_name=source_name,
            task="get_stock_universe",
            fetch=_fetch_universe,
            persist=build_stock_universe_persist(
                conn,
                source_name=source_name,
                fetch_run_id=fetch_run_id,
                analysis_date=analysis_date,
            ),
        )
    )

    def _effective_company_codes() -> List[str]:
        # §15.9.5：确定 per-code 公司层任务（日线快照 + 估值 + 财务）的 code 集合。
        # - sector_ids 非空（按需加载）：从已抓取成分股派生 distinct codes；若 codes
        #   显式传入则取交集，否则直接用派生 codes。
        # - sector_ids=None（向后兼容）：codes 参数驱动 per-code 任务；未传则跳过。
        if sector_ids is not None:
            derived = sorted(
                {str(r.get("code", "")) for r in constituents_rows if r.get("code")}
            )
            if codes is not None:
                wanted = set(str(c) for c in codes)
                return [c for c in derived if c in wanted]
            return derived
        return [c for c in (codes or []) if c]

    def _fetch_company_daily() -> List[Dict[str, Any]]:
        # §15.9.5：sector_ids 非空时用派生 codes 驱动 per-code 日线快照；
        # sector_ids=None 时回退全市场（codes=None），保持向后兼容。
        if sector_ids is not None:
            return source.get_company_daily_snapshot(
                analysis_date, codes=_effective_company_codes()
            )
        return source.get_company_daily_snapshot(analysis_date)

    result.tasks.append(
        _run_task(
            conn,
            fetch_run_id=fetch_run_id,
            source_name=source_name,
            task="get_company_daily_snapshot",
            fetch=_fetch_company_daily,
            persist=build_company_daily_persist(
                conn,
                source_name=source_name,
                fetch_run_id=fetch_run_id,
                analysis_date=analysis_date,
            ),
        )
    )

    effective_codes = _effective_company_codes()
    if effective_codes:
        # --- 估值历史（per-code：每只股票 2 次百度接口调用）---
        def _fetch_valuation_history() -> List[Dict[str, Any]]:
            return source.get_company_valuation_history(
                effective_codes, start_date, analysis_date
            )

        result.tasks.append(
            _run_task(
                conn,
                fetch_run_id=fetch_run_id,
                source_name=source_name,
                task="get_company_valuation_history",
                fetch=_fetch_valuation_history,
                persist=build_company_valuation_persist(
                    conn,
                    source_name=source_name,
                    fetch_run_id=fetch_run_id,
                ),
            )
        )

        # --- 财务指标（per-code：每只股票 1 次新浪接口调用）---
        def _fetch_financial() -> List[Dict[str, Any]]:
            return source.get_financial_metrics(effective_codes, analysis_date)

        result.tasks.append(
            _run_task(
                conn,
                fetch_run_id=fetch_run_id,
                source_name=source_name,
                task="get_financial_metrics",
                fetch=_fetch_financial,
                persist=build_financial_metrics_persist(
                    conn,
                    source_name=source_name,
                    fetch_run_id=fetch_run_id,
                    analysis_date=analysis_date,
                ),
            )
        )

    result.finished_at = _ts()
    return result


def build_snapshot_metadata(
    *,
    analysis_date: str,
    fetch_run_id: str,
    sources: Dict[str, str],
    data_quality_status: str = "ok",
) -> SnapshotMetadata:
    """构造 ``SnapshotMetadata``。Phase 6A 仅供脚本和测试使用。"""

    return SnapshotMetadata.create(
        analysis_date=analysis_date,
        source_set=sources,
        fetch_run_id=fetch_run_id,
        data_quality_status=data_quality_status,
    )


def _akshare_available() -> bool:
    """探测 akshare 是否可导入。真实同步需要 akshare；未安装时 CLI 给出明确错误。"""

    try:
        import akshare  # noqa: F401, type: ignore[import-not-found]
    except ImportError:
        return False
    return True


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    source: Optional[FundamentalDataSource] = None,
) -> int:
    """CLI 入口。

    ``source`` 仅用于 ``sync`` 子命令的内部/测试注入：传入时跳过 akshare 可用性
    探测并直接使用该数据源；为 ``None`` 时构造 ``AkShareFundamentalDataSource()``
    并要求 akshare 已安装。其他子命令忽略该参数。
    """

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init-db":
        conn = connect(args.db)
        try:
            init_db(conn)
            payload = {
                "command": "init-db",
                "db": str(Path(args.db).resolve()) if args.db != ":memory:" else ":memory:",
                "tables": list(list_tables(conn)),
                "config_version": DEFAULT_CONFIG_VERSION,
                "formula_version": DEFAULT_FORMULA_VERSION,
            }
            sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
            sys.stdout.write("\n")
        finally:
            conn.close()
        return 0

    if args.command == "sync":
        # Phase 6B+6C：CLI ``sync`` 接入 AkShare 行业板块（默认 ``ths_industry``，
        # ``em_industry`` 为对照源）+ 公司层（股票池 / 日度快照 / 估值历史 /
        # 财务指标）。per-code 公司层任务（估值历史 + 财务指标）需要 ``--codes``
        # 显式指定，未提供时跳过。``source`` 注入仅供测试使用，跳过 akshare 探测。
        if args.classification_system not in ("ths_industry", "em_industry"):
            sys.stderr.write(
                f"sync: classification system {args.classification_system!r} is not "
                "supported. Use 'ths_industry' (default) or 'em_industry'.\n"
            )
            return 2

        if source is None:
            if not _akshare_available():
                sys.stderr.write(
                    "sync: akshare is not installed. Install it with "
                    "`pip install akshare` to enable real sector sync "
                    "(ths_industry / em_industry). "
                    "(Real AkShare sync is a manual smoke, not a unit "
                    "test dependency.)\n"
                )
                return 2
            # sync_all 会按 classification_system 派生 source name，无需在此设置。
            source = AkShareFundamentalDataSource()

        parsed_codes = _parse_codes(args.codes)
        parsed_sector_ids = _parse_sector_ids(args.sector_ids)
        if not parsed_codes and not parsed_sector_ids:
            sys.stderr.write(
                "sync: --codes not provided; skipping per-code company tasks "
                "(valuation history + financial metrics). Pass --codes A,B,C "
                "to sync these for specific stocks, or --sector-ids X,Y to "
                "derive codes from sector constituents (§15.9.5).\n"
            )

        conn = connect(args.db)
        try:
            result = sync_all(
                conn,
                source,
                analysis_date=args.date,
                classification_system=args.classification_system,
                benchmark=args.benchmark,
                history_days=args.history_days,
                codes=parsed_codes,
                sector_ids=parsed_sector_ids,
            )
        finally:
            conn.close()

        payload = result.to_dict()
        payload["command"] = "sync"
        payload["db"] = str(Path(args.db).resolve()) if args.db != ":memory:" else ":memory:"
        payload["date"] = args.date
        payload["classification_system"] = args.classification_system
        payload["benchmark"] = args.benchmark
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
        sys.stdout.write("\n")
        # rc=0 要求轻量必需任务全部成功且写入行数 > 0，且无任何子任务失败。
        # §15.9.4a: 成分股属重量层，从 REQUIRED_PHASE_6B_TASKS 移至独立校验。
        # 全量同步（sector_ids is None）时仍要求成分股成功且有行；按需加载
        # （sector_ids 非空）时成分股为用户显式请求的板块，同样要求成功。
        # JSON 始终输出便于排查。akshare 缺失/口径不支持在前面已返回 rc=2。
        return compute_sync_exit_code(
            result.tasks,
            result.failure_count,
            LIGHT_REQUIRED_TASKS,
            DETAIL_REQUIRED_TASKS,
        )

    if args.command == "quality":
        # Phase 6D：读取 SQLite 并输出结构化质量报告。
        from .quality import run_quality_checks

        conn = connect(args.db)
        try:
            init_db(conn)
            report = run_quality_checks(
                conn,
                analysis_date=args.date,
                classification_system=args.classification_system,
                benchmark=args.benchmark,
            )
        finally:
            conn.close()
        sys.stdout.write(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        sys.stdout.write("\n")
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "SyncResult",
    "build_parser",
    "build_snapshot_metadata",
    "main",
    "sync_all",
]
