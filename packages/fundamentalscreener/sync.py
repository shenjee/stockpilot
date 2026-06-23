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

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

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
    now_cn,
    now_cn_isoformat,
)
from .quality import QualityReport
from .sqlite_schema import connect, init_db, list_tables

# ---------------------------------------------------------------------------
# 同步任务定义
# ---------------------------------------------------------------------------

# Phase 6B 必须成功的板块层任务。CLI ``sync`` 的 rc=0 要求这 4 个任务全部成功
# 且写入行数 > 0，避免断网/空返回被自动化误判为成功（公司层任务不在必需集内：
# per-code 任务未传 --codes 时跳过，batch 任务可能 0 行成功）。
REQUIRED_PHASE_6B_TASKS: Tuple[str, ...] = (
    "list_sectors",
    "get_sector_constituents",
    "get_sector_daily",
    "get_benchmark_daily",
)


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
# 写入工具
# ---------------------------------------------------------------------------


def _ts() -> str:
    return now_cn_isoformat()


def _lineage_columns(row: Dict[str, Any], source: str, fetch_run_id: str) -> Dict[str, Any]:
    """填充任意采集行的血缘列，保留外部已显式给出的字段。"""

    now = _ts()
    enriched = dict(row)
    enriched.setdefault("source", source)
    enriched["fetch_run_id"] = fetch_run_id
    enriched.setdefault("source_updated_at", None)
    enriched.setdefault("created_at", now)
    enriched["updated_at"] = now
    return enriched


def _upsert(
    conn,
    table: str,
    rows: Iterable[Dict[str, Any]],
    *,
    pk_columns: Sequence[str],
    column_order: Sequence[str],
) -> int:
    """通用 UPSERT：在主键冲突时更新非主键列。"""

    count = 0
    placeholders = ", ".join("?" for _ in column_order)
    columns = ", ".join(column_order)
    pk = ", ".join(pk_columns)
    update_cols = [c for c in column_order if c not in pk_columns and c != "created_at"]
    update_sql = ", ".join(f"{c}=excluded.{c}" for c in update_cols)
    sql = (
        f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) "
        f"ON CONFLICT({pk}) DO UPDATE SET {update_sql}"
    )
    for row in rows:
        values = tuple(row.get(c) for c in column_order)
        conn.execute(sql, values)
        count += 1
    return count


# 各表写入器：返回 (rows 写入数, raw rows 数)。
# 入参 ``raw_rows`` 已是单条 dict 的列表；血缘列在写入时自动补齐。


_SECTOR_COLUMNS = (
    "sector_id",
    "classification_system",
    "sector_name",
    "source",
    "fetch_run_id",
    "source_updated_at",
    "created_at",
    "updated_at",
)
_SECTOR_CONSTITUENTS_COLUMNS = (
    "sector_id",
    "classification_system",
    "code",
    "as_of_date",
    "source",
    "fetch_run_id",
    "source_updated_at",
    "created_at",
    "updated_at",
)
_SECTOR_DAILY_COLUMNS = (
    "sector_id",
    "classification_system",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "turnover_amount",
    "rising_count",
    "total_count",
    "source",
    "fetch_run_id",
    "source_updated_at",
    "created_at",
    "updated_at",
)
_STOCKS_COLUMNS = (
    "code",
    "name",
    "market",
    "listing_status",
    "delisted_at",
    "as_of_date",
    "source",
    "fetch_run_id",
    "source_updated_at",
    "created_at",
    "updated_at",
)
_COMPANY_DAILY_COLUMNS = (
    "code",
    "trade_date",
    "close",
    "turnover_amount",
    "turnover_rate",
    "market_cap",
    "change_pct",
    "source",
    "fetch_run_id",
    "source_updated_at",
    "created_at",
    "updated_at",
)
_COMPANY_VAL_COLUMNS = (
    "code",
    "trade_date",
    "market",
    "pe",
    "pb",
    "ps",
    "dividend_yield",
    "source",
    "fetch_run_id",
    "source_updated_at",
    "created_at",
    "updated_at",
)
_FINANCIAL_COLUMNS = (
    "code",
    "report_period",
    "period_end_date",
    "disclosure_date",
    "period_type",
    "as_of_date",
    "revenue_yoy",
    "net_profit_yoy",
    "deducted_net_profit_yoy",
    "gross_margin",
    "net_margin",
    "roe",
    "operating_cashflow_to_profit",
    "free_cashflow",
    "debt_to_asset",
    "interest_bearing_debt_ratio",
    "accounts_receivable_yoy",
    "inventory_yoy",
    "gross_margin_yoy_change",
    "source",
    "fetch_run_id",
    "source_updated_at",
    "created_at",
    "updated_at",
)
_BENCHMARK_COLUMNS = (
    "benchmark",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "turnover_amount",
    "source",
    "fetch_run_id",
    "source_updated_at",
    "created_at",
    "updated_at",
)


# 各表的必填主键字段（不能为空字符串 / None）。在 ``_upsert`` 前会先做这层校验，
# 避免上游返回的畸形行被静默写入成 PK="" 的"成功缓存"。
REQUIRED_PK_FIELDS: Dict[str, Tuple[str, ...]] = {
    "sectors": ("sector_id", "classification_system"),
    "sector_constituents": ("sector_id", "classification_system", "code", "as_of_date"),
    "sector_daily_bars": ("sector_id", "classification_system", "trade_date"),
    "benchmark_daily_bars": ("benchmark", "trade_date"),
    "stocks": ("code",),
    "company_daily_snapshot": ("code", "trade_date"),
    "company_valuation_history": ("code", "trade_date"),
    "financial_metrics": (
        "code",
        "report_period",
        "period_type",
        "disclosure_date",
    ),
}


@dataclass
class _PersistResult:
    """单任务 persist 阶段的汇总：写入数 + 被拒行（带原因）。

    - ``written`` 实际 UPSERT 的行数。
    - ``rejected`` 被校验拒绝的行：每条 ``{"reason": ..., "row": <原 dict>}``。
    - ``rejections`` 便捷计数。
    若 ``rejected`` 非空，``_run_task`` 会把摘要写入 ``data_fetch_log.details``，
    并在"所有行都被拒"时把整个任务标记为失败。
    """

    written: int = 0
    rejected: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def rejections(self) -> int:
        return len(self.rejected)


def _validate_required(
    table: str, rows: Iterable[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """按 ``REQUIRED_PK_FIELDS[table]`` 过滤 rows。

    返回 ``(accepted, rejected)``。``rejected`` 每条形如
    ``{"reason": "missing_pk: sector_id", "row": <原 dict>}``，便于审计。
    空字符串和 ``None`` 都视为 missing。
    """

    required = REQUIRED_PK_FIELDS.get(table, ())
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for r in rows:
        missing = [
            f for f in required if r.get(f) in (None, "") or (isinstance(r.get(f), str) and not r.get(f).strip())
        ]
        if missing:
            rejected.append({"reason": f"missing_pk: {','.join(missing)}", "row": dict(r)})
        else:
            accepted.append(r)
    return accepted, rejected


def _persist_with_validation(
    conn,
    *,
    table: str,
    rows: List[Dict[str, Any]],
    pk_columns: Sequence[str],
    column_order: Sequence[str],
    enrich: Callable[[Dict[str, Any]], Dict[str, Any]],
) -> _PersistResult:
    """通用 persist：enrich → 校验 → UPSERT，返回 ``_PersistResult``。

    顺序很重要：先 enrich 再校验。enricher 会填入 sync 层上下文派生的 PK 字段
    （例如 ``classification_system`` / ``as_of_date`` / ``benchmark``），这些字段
    在源数据里可以缺省。如果先校验再 enrich，这些合法的缺省行会被误拒。

    被拒行不会被写入，但会出现在结果里，供 ``_run_task`` 记录到
    ``data_fetch_log.details.rejected``。``rejected`` 里的 ``row`` 是 enriched
    后的行，便于审计最终 PK 字段状态。
    """

    enriched = [enrich(r) for r in rows]
    accepted, rejected = _validate_required(table, enriched)
    written = _upsert(
        conn,
        table,
        accepted,
        pk_columns=pk_columns,
        column_order=column_order,
    )
    return _PersistResult(written=written, rejected=rejected)


# ---------------------------------------------------------------------------
# 任务运行器
# ---------------------------------------------------------------------------


def _log_fetch(
    conn,
    *,
    fetch_run_id: str,
    source: str,
    task: str,
    started_at: str,
    finished_at: str,
    success: bool,
    row_count: int,
    used_cache: bool = False,
    error: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    conn.execute(
        "INSERT INTO data_fetch_log "
        "(fetch_run_id, source, task, started_at, finished_at, success, "
        "row_count, used_cache, error, details) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            fetch_run_id,
            source,
            task,
            started_at,
            finished_at,
            1 if success else 0,
            int(row_count or 0),
            1 if used_cache else 0,
            error,
            json.dumps(details, ensure_ascii=False) if details is not None else None,
        ),
    )


def _run_task(
    conn,
    *,
    fetch_run_id: str,
    source_name: str,
    task: str,
    fetch: Callable[[], List[Dict[str, Any]]],
    persist: Callable[[List[Dict[str, Any]]], _PersistResult],
) -> Dict[str, Any]:
    """运行单个同步子任务并写入 data_fetch_log。

    fetch 失败时返回失败记录；persist 在单独的事务内执行，失败时回滚不会破坏
    其他任务已写入的数据。persist 返回 ``_PersistResult``：
    - 若所有行都被 PK 校验拒绝（``written == 0 and rejections > 0``），任务标记
      为失败，错误码 ``all_rows_rejected``，被拒行摘要写入 ``details.rejected``。
    - 若部分行被拒但仍有写入，任务仍记为成功，但 ``details.rejected`` 会带上
      被拒行摘要，便于后续质量检查。
    """

    started_at = _ts()
    try:
        rows = list(fetch())
    except Exception as exc:  # noqa: BLE001
        finished_at = _ts()
        with conn:
            _log_fetch(
                conn,
                fetch_run_id=fetch_run_id,
                source=source_name,
                task=task,
                started_at=started_at,
                finished_at=finished_at,
                success=False,
                row_count=0,
                error=f"fetch_failed: {exc}",
            )
        return {
            "task": task,
            "success": False,
            "row_count": 0,
            "error": f"fetch_failed: {exc}",
        }
    try:
        with conn:
            result = persist(rows)
    except Exception as exc:  # noqa: BLE001
        finished_at = _ts()
        with conn:
            _log_fetch(
                conn,
                fetch_run_id=fetch_run_id,
                source=source_name,
                task=task,
                started_at=started_at,
                finished_at=finished_at,
                success=False,
                row_count=0,
                error=f"persist_failed: {exc}",
            )
        return {
            "task": task,
            "success": False,
            "row_count": 0,
            "error": f"persist_failed: {exc}",
        }

    written = int(result.written or 0)
    rejections = int(result.rejections or 0)
    # 全部行被 PK 校验拒绝 → 任务失败，不能记成"成功 0 行"。
    success = not (written == 0 and rejections > 0)
    error: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    if rejections > 0:
        # 截断被拒行摘要，避免 details 字段过大；每条只保留 reason + 关键 PK 字段。
        sample = [
            {"reason": r["reason"], "row": _summarize_row(r["row"])}
            for r in result.rejected[:20]
        ]
        details = {
            "rejected_count": rejections,
            "rejected_sample": sample,
        }
        if not success:
            error = f"all_rows_rejected: {rejections} row(s) missing required PK fields"

    finished_at = _ts()
    with conn:
        _log_fetch(
            conn,
            fetch_run_id=fetch_run_id,
            source=source_name,
            task=task,
            started_at=started_at,
            finished_at=finished_at,
            success=success,
            row_count=written,
            error=error,
            details=details,
        )
    return {
        "task": task,
        "success": success,
        "row_count": written,
        "error": error,
        "rejections": rejections,
    }


def _summarize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """压缩被拒行，只保留小体量字段，避免 details 字段爆炸。"""

    keep = (
        "sector_id",
        "classification_system",
        "code",
        "trade_date",
        "benchmark",
        "report_period",
        "period_type",
        "disclosure_date",
        "as_of_date",
    )
    return {k: row.get(k) for k in keep if k in row}


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
    fetch_run_id: Optional[str] = None,
) -> SyncResult:
    """运行一次完整同步。

    Phase 6A 实现支持：板块列表、板块成分、板块行情、基准行情、股票池、公司日度
    快照、公司估值历史、财务指标。

    ``codes`` 用于限制公司层 per-code 抓取范围（估值历史 + 财务指标）。未提供
    ``codes`` 时，这两个 per-code 任务会被跳过，避免对全量股票池逐只发起网络
    请求（P2: 默认全量 fanout 会导致数千次顺序请求）。股票池和公司日度快照是
    batch 接口，始终运行。
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

    # 板块列表
    def _enrich_sector(r: Dict[str, Any]) -> Dict[str, Any]:
        return _lineage_columns(
            {
                "sector_id": str(r.get("sector_id", "")),
                "classification_system": str(
                    r.get("classification_system", classification_system)
                ),
                "sector_name": r.get("sector_name"),
                "source_updated_at": r.get("source_updated_at"),
            },
            source_name,
            fetch_run_id,
        )

    def _persist_sectors(rows: List[Dict[str, Any]]) -> _PersistResult:
        return _persist_with_validation(
            conn,
            table="sectors",
            rows=rows,
            pk_columns=("sector_id", "classification_system"),
            column_order=_SECTOR_COLUMNS,
            enrich=_enrich_sector,
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
            persist=_persist_sectors,
        )
    )

    # 板块成分 & 板块行情：以 sectors_rows 为驱动
    def _fetch_constituents() -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for s in sectors_rows:
            sid = str(s.get("sector_id", ""))
            if not sid:
                continue
            out.extend(
                source.get_sector_constituents(sid, classification_system, analysis_date)
            )
        # 板块列表非空但成分股总数为 0 → 几乎必然是数据源故障（反爬 403、空页、
        # API 结构变更），不能记成"成功写入 0 行"。抛错让 _run_task 标记 fetch_failed。
        if sectors_rows and not out:
            raise RuntimeError(
                f"get_sector_constituents: {len(sectors_rows)} sector(s) found but "
                f"0 constituents returned — likely a data source failure "
                f"(anti-crawl, HTTP error, or API structure change)."
            )
        return out

    def _persist_constituents(rows: List[Dict[str, Any]]) -> _PersistResult:
        def _enrich(r: Dict[str, Any]) -> Dict[str, Any]:
            return _lineage_columns(
                {
                    "sector_id": str(r.get("sector_id", "")),
                    "classification_system": str(
                        r.get("classification_system", classification_system)
                    ),
                    "code": str(r.get("code", "")),
                    "as_of_date": str(r.get("as_of_date", analysis_date)),
                    "source_updated_at": r.get("source_updated_at"),
                },
                source_name,
                fetch_run_id,
            )

        return _persist_with_validation(
            conn,
            table="sector_constituents",
            rows=rows,
            pk_columns=("sector_id", "classification_system", "code", "as_of_date"),
            column_order=_SECTOR_CONSTITUENTS_COLUMNS,
            enrich=_enrich,
        )

    result.tasks.append(
        _run_task(
            conn,
            fetch_run_id=fetch_run_id,
            source_name=source_name,
            task="get_sector_constituents",
            fetch=_fetch_constituents,
            persist=_persist_constituents,
        )
    )

    def _fetch_sector_daily() -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for s in sectors_rows:
            sid = str(s.get("sector_id", ""))
            if not sid:
                continue
            out.extend(
                source.get_sector_daily(
                    sid, classification_system, start_date, analysis_date
                )
            )
        return out

    def _persist_sector_daily(rows: List[Dict[str, Any]]) -> _PersistResult:
        def _enrich(r: Dict[str, Any]) -> Dict[str, Any]:
            return _lineage_columns(
                {
                    "sector_id": str(r.get("sector_id", "")),
                    "classification_system": str(
                        r.get("classification_system", classification_system)
                    ),
                    "trade_date": str(r.get("trade_date", "")),
                    "open": r.get("open"),
                    "high": r.get("high"),
                    "low": r.get("low"),
                    "close": r.get("close"),
                    "turnover_amount": r.get("turnover_amount"),
                    "rising_count": r.get("rising_count"),
                    "total_count": r.get("total_count"),
                    "source_updated_at": r.get("source_updated_at"),
                },
                source_name,
                fetch_run_id,
            )

        return _persist_with_validation(
            conn,
            table="sector_daily_bars",
            rows=rows,
            pk_columns=("sector_id", "classification_system", "trade_date"),
            column_order=_SECTOR_DAILY_COLUMNS,
            enrich=_enrich,
        )

    result.tasks.append(
        _run_task(
            conn,
            fetch_run_id=fetch_run_id,
            source_name=source_name,
            task="get_sector_daily",
            fetch=_fetch_sector_daily,
            persist=_persist_sector_daily,
        )
    )

    # 基准日线：独立写入 ``benchmark_daily_bars``，不污染 sector_daily_bars。
    # 详见 docs §18：benchmark 与 sector 是不同实体，schema 上必须区分。
    def _fetch_benchmark() -> List[Dict[str, Any]]:
        return source.get_benchmark_daily(benchmark, start_date, analysis_date)

    def _persist_benchmark(rows: List[Dict[str, Any]]) -> _PersistResult:
        def _enrich(r: Dict[str, Any]) -> Dict[str, Any]:
            return _lineage_columns(
                {
                    "benchmark": str(r.get("benchmark", benchmark)),
                    "trade_date": str(r.get("trade_date", "")),
                    "open": r.get("open"),
                    "high": r.get("high"),
                    "low": r.get("low"),
                    "close": r.get("close"),
                    "turnover_amount": r.get("turnover_amount"),
                    "source_updated_at": r.get("source_updated_at"),
                },
                source_name,
                fetch_run_id,
            )

        return _persist_with_validation(
            conn,
            table="benchmark_daily_bars",
            rows=rows,
            pk_columns=("benchmark", "trade_date"),
            column_order=_BENCHMARK_COLUMNS,
            enrich=_enrich,
        )

    result.tasks.append(
        _run_task(
            conn,
            fetch_run_id=fetch_run_id,
            source_name=source_name,
            task="get_benchmark_daily",
            fetch=_fetch_benchmark,
            persist=_persist_benchmark,
        )
    )

    # 公司层
    def _fetch_universe() -> List[Dict[str, Any]]:
        return source.get_stock_universe(analysis_date)

    def _persist_universe(rows: List[Dict[str, Any]]) -> _PersistResult:
        def _enrich(r: Dict[str, Any]) -> Dict[str, Any]:
            return _lineage_columns(
                {
                    "code": str(r.get("code", "")),
                    "name": r.get("name"),
                    "market": r.get("market"),
                    "listing_status": r.get("listing_status"),
                    "delisted_at": r.get("delisted_at"),
                    "as_of_date": str(r.get("as_of_date", analysis_date)),
                    "source_updated_at": r.get("source_updated_at"),
                },
                source_name,
                fetch_run_id,
            )

        return _persist_with_validation(
            conn,
            table="stocks",
            rows=rows,
            pk_columns=("code",),
            column_order=_STOCKS_COLUMNS,
            enrich=_enrich,
        )

    result.tasks.append(
        _run_task(
            conn,
            fetch_run_id=fetch_run_id,
            source_name=source_name,
            task="get_stock_universe",
            fetch=_fetch_universe,
            persist=_persist_universe,
        )
    )

    def _fetch_company_daily() -> List[Dict[str, Any]]:
        return source.get_company_daily_snapshot(analysis_date)

    def _persist_company_daily(rows: List[Dict[str, Any]]) -> _PersistResult:
        def _enrich(r: Dict[str, Any]) -> Dict[str, Any]:
            return _lineage_columns(
                {
                    "code": str(r.get("code", "")),
                    "trade_date": str(r.get("trade_date", analysis_date)),
                    "close": r.get("close"),
                    "turnover_amount": r.get("turnover_amount"),
                    "turnover_rate": r.get("turnover_rate"),
                    "market_cap": r.get("market_cap"),
                    "change_pct": r.get("change_pct"),
                    "source_updated_at": r.get("source_updated_at"),
                },
                source_name,
                fetch_run_id,
            )

        return _persist_with_validation(
            conn,
            table="company_daily_snapshot",
            rows=rows,
            pk_columns=("code", "trade_date"),
            column_order=_COMPANY_DAILY_COLUMNS,
            enrich=_enrich,
        )

    result.tasks.append(
        _run_task(
            conn,
            fetch_run_id=fetch_run_id,
            source_name=source_name,
            task="get_company_daily_snapshot",
            fetch=_fetch_company_daily,
            persist=_persist_company_daily,
        )
    )

    def _company_codes() -> List[str]:
        # Per-code 公司层任务仅在 codes 显式提供时运行。调用方（sync_all）已通过
        # if codes 守卫保证此处 codes 非空。
        return [c for c in (codes or []) if c]

    if codes:
        # --- 估值历史（per-code：每只股票 2 次百度接口调用）---
        def _fetch_valuation_history() -> List[Dict[str, Any]]:
            return source.get_company_valuation_history(
                _company_codes(), start_date, analysis_date
            )

        def _persist_valuation_history(rows: List[Dict[str, Any]]) -> _PersistResult:
            def _enrich(r: Dict[str, Any]) -> Dict[str, Any]:
                return _lineage_columns(
                    {
                        "code": str(r.get("code", "")),
                        "trade_date": str(r.get("trade_date", "")),
                        "market": r.get("market"),
                        "pe": r.get("pe"),
                        "pb": r.get("pb"),
                        "ps": r.get("ps"),
                        "dividend_yield": r.get("dividend_yield"),
                        "source_updated_at": r.get("source_updated_at"),
                    },
                    source_name,
                    fetch_run_id,
                )

            return _persist_with_validation(
                conn,
                table="company_valuation_history",
                rows=rows,
                pk_columns=("code", "trade_date"),
                column_order=_COMPANY_VAL_COLUMNS,
                enrich=_enrich,
            )

        result.tasks.append(
            _run_task(
                conn,
                fetch_run_id=fetch_run_id,
                source_name=source_name,
                task="get_company_valuation_history",
                fetch=_fetch_valuation_history,
                persist=_persist_valuation_history,
            )
        )

        # --- 财务指标（per-code：每只股票 1 次新浪接口调用）---
        def _fetch_financial() -> List[Dict[str, Any]]:
            return source.get_financial_metrics(_company_codes(), analysis_date)

        def _persist_financial(rows: List[Dict[str, Any]]) -> _PersistResult:
            def _enrich(r: Dict[str, Any]) -> Dict[str, Any]:
                return _lineage_columns(
                    {
                        "code": str(r.get("code", "")),
                        "report_period": str(r.get("report_period", "")),
                        "period_end_date": str(r.get("period_end_date", "")),
                        "disclosure_date": str(r.get("disclosure_date", "")),
                        "period_type": str(r.get("period_type", "annual")),
                        "as_of_date": str(r.get("as_of_date", analysis_date)),
                        "revenue_yoy": r.get("revenue_yoy"),
                        "net_profit_yoy": r.get("net_profit_yoy"),
                        "deducted_net_profit_yoy": r.get("deducted_net_profit_yoy"),
                        "gross_margin": r.get("gross_margin"),
                        "net_margin": r.get("net_margin"),
                        "roe": r.get("roe"),
                        "operating_cashflow_to_profit": r.get(
                            "operating_cashflow_to_profit"
                        ),
                        "free_cashflow": r.get("free_cashflow"),
                        "debt_to_asset": r.get("debt_to_asset"),
                        "interest_bearing_debt_ratio": r.get(
                            "interest_bearing_debt_ratio"
                        ),
                        "accounts_receivable_yoy": r.get("accounts_receivable_yoy"),
                        "inventory_yoy": r.get("inventory_yoy"),
                        "gross_margin_yoy_change": r.get("gross_margin_yoy_change"),
                        "source_updated_at": r.get("source_updated_at"),
                    },
                    source_name,
                    fetch_run_id,
                )

            return _persist_with_validation(
                conn,
                table="financial_metrics",
                rows=rows,
                pk_columns=("code", "report_period", "period_type", "disclosure_date"),
                column_order=_FINANCIAL_COLUMNS,
                enrich=_enrich,
            )

        result.tasks.append(
            _run_task(
                conn,
                fetch_run_id=fetch_run_id,
                source_name=source_name,
                task="get_financial_metrics",
                fetch=_fetch_financial,
                persist=_persist_financial,
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m packages.fundamentalscreener.sync",
        description="Fundamental Screener 数据治理同步入口（Phase 6A）。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-db", help="幂等初始化 SQLite schema。")
    p_init.add_argument("--db", required=True, help="SQLite 路径。")

    p_sync = sub.add_parser(
        "sync",
        help="运行同步任务。默认接入同花顺行业板块（ths_industry），东方财富（em_industry）为对照源。",
    )
    p_sync.add_argument("--db", required=True)
    p_sync.add_argument("--date", required=True, help="分析日期 YYYY-MM-DD。")
    p_sync.add_argument(
        "--classification-system",
        dest="classification_system",
        default="ths_industry",
        help="板块分类口径，默认 ths_industry（同花顺）。em_industry 为东方财富对照源。",
    )
    p_sync.add_argument("--benchmark", default="hs300")
    p_sync.add_argument(
        "--history-days",
        dest="history_days",
        type=int,
        default=90,
        help="回采历史天数（自然日），需覆盖 60 个交易日以支持 60 日收益。",
    )
    p_sync.add_argument(
        "--codes",
        default="",
        help="逗号分隔的股票代码。未提供时跳过 per-code 公司层任务（估值历史 + "
        "财务指标），仅运行 batch 任务（股票池 + 日度快照）。",
    )

    p_quality = sub.add_parser(
        "quality", help="读取 SQLite 并输出结构化质量报告（Phase 6D）。"
    )
    p_quality.add_argument("--db", required=True)
    p_quality.add_argument("--date", required=True)
    p_quality.add_argument(
        "--classification-system",
        dest="classification_system",
        default="ths_industry",
        help="板块分类口径，默认 ths_industry（同花顺）。",
    )
    p_quality.add_argument("--benchmark", default="hs300")

    return parser


def _akshare_available() -> bool:
    """探测 akshare 是否可导入。真实同步需要 akshare；未安装时 CLI 给出明确错误。"""

    try:
        import akshare  # noqa: F401, type: ignore[import-not-found]
    except ImportError:
        return False
    return True


def _parse_codes(raw: str) -> Optional[List[str]]:
    """解析 ``--codes`` 参数：逗号分隔的股票代码列表，空串返回 ``None``。"""

    if not raw:
        return None
    codes = [c.strip() for c in raw.split(",") if c.strip()]
    return codes or None


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
        if not parsed_codes:
            sys.stderr.write(
                "sync: --codes not provided; skipping per-code company tasks "
                "(valuation history + financial metrics). Pass --codes A,B,C "
                "to sync these for specific stocks.\n"
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
        # rc=0 要求 Phase 6B 必需的板块层任务全部成功且写入行数 > 0，且无任何子任务
        # 失败。这样断网/空返回不会被自动化误判为成功：公司层 Phase 6C 桩以 0 行成功
        # 但不在必需集内；list_sectors 失败后下游板块任务虽以 0 行"成功"但必需集仍
        # 不满足。JSON 始终输出便于排查。akshare 缺失/口径不支持在前面已返回 rc=2。
        by_task = {t["task"]: t for t in result.tasks}
        required_ok = all(
            by_task.get(t, {}).get("success")
            and int(by_task.get(t, {}).get("row_count", 0) or 0) > 0
            for t in REQUIRED_PHASE_6B_TASKS
        )
        return 0 if (result.failure_count == 0 and required_ok) else 1

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
