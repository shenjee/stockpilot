"""Snapshot service orchestration for Fundamental Screener.

This module owns reusable load/refresh orchestration and neutral result
contracts. It deliberately avoids Streamlit-facing DTO names and UI text so the
same logic can be reused by apps, CLI adapters, or future services.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .lineage import SnapshotMetadata, now_cn
from .quality import QualityReport
from .repositories import MarketSnapshot
from .sqlite_repository import QualityInvalidError, SqliteFundamentalRepository
from .sqlite_schema import connect, init_db
from .sync import DETAIL_REQUIRED_TASKS, LIGHT_REQUIRED_TASKS, sync_all


@dataclass
class SnapshotLoadResult:
    """Result of loading a snapshot from SQLite."""

    snapshot: Optional[MarketSnapshot] = None
    metadata: Optional[SnapshotMetadata] = None
    quality_report: Optional[QualityReport] = None
    quality_error: Optional[str] = None


@dataclass
class SnapshotResult:
    """Package-level snapshot load/refresh result."""

    snapshot: Optional[MarketSnapshot] = None
    metadata: Optional[SnapshotMetadata] = None
    quality_report: Optional[QualityReport] = None
    status: str = "ok"
    reason_code: str = ""
    reason: str = ""
    refresh_result: Optional[Dict[str, Any]] = None


@dataclass
class SectorDetailSnapshotResult:
    """Package-level detail refresh result before UI shaping."""

    sector_id: str = ""
    snapshot: Optional[MarketSnapshot] = None
    metadata: Optional[SnapshotMetadata] = None
    quality_report: Optional[QualityReport] = None
    status: str = "ok"
    reason_code: str = ""
    reason: str = ""
    refresh_result: Optional[Dict[str, Any]] = None
    has_company_data: bool = False


def _as_path(db_path: Path | str) -> Path:
    return Path(db_path)


def build_default_source() -> Any:
    """Build the default real data source lazily."""

    from .data_sources.akshare_source import AkShareFundamentalDataSource

    return AkShareFundamentalDataSource()


def _find_latest_analysis_date(
    db_path: Path | str,
    classification_system: Optional[str] = None,
) -> Optional[str]:
    """Return the latest cached trade date for the given classification."""

    db = _as_path(db_path)
    if not db.exists():
        return None
    try:
        conn = connect(db)
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
    db_path: Path | str,
    analysis_date: str,
    classification_system: str,
) -> bool:
    """Return whether there is any sector bar at or before the analysis date."""

    db = _as_path(db_path)
    if not db.exists():
        return False
    try:
        conn = connect(db)
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


def get_latest_cached_date(
    db_path: Path | str,
    classification_system: str,
) -> Optional[str]:
    """Return the latest cached analysis date for UI defaults."""

    return _find_latest_analysis_date(db_path, classification_system)


def load_snapshot_from_db(
    db_path: Path | str,
    analysis_date: str,
    classification_system: str,
    benchmark: str,
) -> SnapshotLoadResult:
    """Load a snapshot and quality metadata from SQLite."""

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


def load_latest_snapshot(
    *,
    db_path: Path | str,
    analysis_date: Optional[str],
    classification_system: str,
    benchmark: str,
) -> SnapshotResult:
    """Load the latest usable snapshot without any UI wording."""

    resolved_date = analysis_date
    if resolved_date is None:
        resolved_date = _find_latest_analysis_date(db_path, classification_system)
    if resolved_date is None:
        return SnapshotResult(status="no_cache", reason_code="no_cache")

    if not _has_cache_for_date(db_path, resolved_date, classification_system):
        return SnapshotResult(status="no_cache", reason_code="no_cache")

    load_result = load_snapshot_from_db(
        db_path,
        resolved_date,
        classification_system,
        benchmark,
    )

    if load_result.quality_error:
        return SnapshotResult(
            status="invalid",
            reason_code="quality_invalid",
            reason=load_result.quality_error,
            quality_report=load_result.quality_report,
        )

    status = "ok"
    if load_result.metadata:
        status = load_result.metadata.data_quality_status or "ok"

    return SnapshotResult(
        snapshot=load_result.snapshot,
        metadata=load_result.metadata,
        quality_report=load_result.quality_report,
        status=status,
    )


def _build_task_failure_reason(
    tasks: Sequence[Dict[str, Any]],
    required_tasks: Sequence[str],
    prefix: str,
    empty_label: str,
) -> str:
    by_task = {task["task"]: task for task in tasks}
    failed = [
        f"{task}: {by_task.get(task, {}).get('error', 'unknown')}"
        for task in required_tasks
        if not by_task.get(task, {}).get("success")
    ]
    empty = [
        task
        for task in required_tasks
        if by_task.get(task, {}).get("success")
        and int(by_task.get(task, {}).get("row_count", 0) or 0) == 0
    ]
    if not failed and not empty:
        return ""
    reason = prefix
    if failed:
        reason += f": failed=[{', '.join(failed)}]"
    if empty:
        reason += f": {empty_label}={empty}"
    return reason


def refresh_market_data(
    *,
    db_path: Path | str,
    analysis_date: Optional[str],
    classification_system: str,
    benchmark: str,
    history_days: int,
    codes: Optional[Sequence[str]] = None,
    source: Optional[Any] = None,
) -> SnapshotResult:
    """Refresh market data and return a package-level result contract."""

    db = _as_path(db_path)
    resolved_date = analysis_date or now_cn().date().isoformat()

    refresh_result_dict: Optional[Dict[str, Any]] = None
    sync_error = ""
    try:
        active_source = source if source is not None else build_default_source()
        conn = connect(db)
        try:
            sync_sector_ids: Optional[Sequence[str]] = [] if codes is None else None
            result = sync_all(
                conn,
                active_source,
                analysis_date=resolved_date,
                classification_system=classification_system,
                benchmark=benchmark,
                history_days=history_days,
                codes=codes,
                sector_ids=sync_sector_ids,
            )
            refresh_result_dict = result.to_dict()
            sync_error = _build_task_failure_reason(
                result.tasks,
                LIGHT_REQUIRED_TASKS,
                prefix="sync did not satisfy required tasks",
                empty_label="empty_required",
            )
        finally:
            conn.close()
    except Exception as exc:
        sync_error = str(exc)

    load_result = load_snapshot_from_db(
        db,
        resolved_date,
        classification_system,
        benchmark,
    )

    if load_result.snapshot is not None:
        status = "ok"
        if load_result.metadata:
            status = load_result.metadata.data_quality_status or "ok"
        if sync_error:
            status = "refresh_failed"
        return SnapshotResult(
            snapshot=load_result.snapshot,
            metadata=load_result.metadata,
            quality_report=load_result.quality_report,
            status=status,
            reason_code="sync_failed" if sync_error else "",
            reason=sync_error,
            refresh_result=refresh_result_dict,
        )

    if load_result.quality_error:
        if sync_error:
            return SnapshotResult(
                status="no_cache",
                reason_code="sync_failed",
                reason=sync_error,
                refresh_result=refresh_result_dict,
            )
        return SnapshotResult(
            status="invalid",
            reason_code="quality_invalid",
            reason=load_result.quality_error,
            quality_report=load_result.quality_report,
            refresh_result=refresh_result_dict,
        )

    return SnapshotResult(
        status="no_cache",
        reason_code="sync_failed" if sync_error else "no_cache",
        reason=sync_error,
        refresh_result=refresh_result_dict,
    )


def has_sector_detail_cache(
    sector_id: str,
    *,
    db_path: Path | str,
    analysis_date: Optional[str],
    classification_system: str,
) -> bool:
    """Return whether cached constituents exist for the sector."""

    db = _as_path(db_path)
    resolved_date = analysis_date
    if not db.exists():
        return False
    if resolved_date is None:
        resolved_date = _find_latest_analysis_date(db, classification_system)
    if resolved_date is None:
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
            (sector_id, classification_system, resolved_date),
        ).fetchone()
        return row is not None
    except Exception:
        return False
    finally:
        conn.close()


def _has_company_data_for_sector(
    snapshot: Optional[MarketSnapshot],
    sector_id: str,
) -> bool:
    if snapshot is None:
        return False
    return any(company.sector_id == sector_id for company in snapshot.companies)


def refresh_sector_detail_snapshot(
    sector_id: str,
    *,
    db_path: Path | str,
    analysis_date: Optional[str],
    classification_system: str,
    benchmark: str,
    history_days: int,
    source: Optional[Any] = None,
) -> SectorDetailSnapshotResult:
    """Refresh the heavy detail path and keep detail availability semantics."""

    db = _as_path(db_path)
    resolved_date = analysis_date
    if resolved_date is None:
        resolved_date = _find_latest_analysis_date(db, classification_system)
    if resolved_date is None:
        return SectorDetailSnapshotResult(
            sector_id=sector_id,
            status="no_cache",
            reason_code="no_cache",
        )

    refresh_result_dict: Optional[Dict[str, Any]] = None
    sync_error = ""
    try:
        active_source = source if source is not None else build_default_source()
        conn = connect(db)
        try:
            result = sync_all(
                conn,
                active_source,
                analysis_date=resolved_date,
                classification_system=classification_system,
                benchmark=benchmark,
                history_days=history_days,
                sector_ids=[sector_id],
            )
            refresh_result_dict = result.to_dict()
            sync_error = _build_task_failure_reason(
                result.tasks,
                DETAIL_REQUIRED_TASKS,
                prefix="sync did not satisfy detail required tasks",
                empty_label="empty",
            )
        finally:
            conn.close()
    except Exception as exc:
        sync_error = str(exc)

    load_result = load_snapshot_from_db(
        db,
        resolved_date,
        classification_system,
        benchmark,
    )
    has_company_data = _has_company_data_for_sector(load_result.snapshot, sector_id)

    if load_result.snapshot is not None:
        status = "ok"
        reason_code = ""
        reason = ""
        if sync_error:
            if not has_company_data:
                status = "no_cache"
                reason_code = "detail_sync_failed"
                reason = sync_error
            else:
                status = "refresh_failed"
                reason_code = "detail_sync_failed"
                reason = sync_error
        elif load_result.metadata:
            quality_status = load_result.metadata.data_quality_status or "ok"
            if quality_status in ("degraded", "stale"):
                status = quality_status
        return SectorDetailSnapshotResult(
            sector_id=sector_id,
            snapshot=load_result.snapshot,
            metadata=load_result.metadata,
            quality_report=load_result.quality_report,
            status=status,
            reason_code=reason_code,
            reason=reason,
            refresh_result=refresh_result_dict,
            has_company_data=has_company_data,
        )

    if load_result.quality_error:
        if sync_error:
            return SectorDetailSnapshotResult(
                sector_id=sector_id,
                status="no_cache",
                reason_code="detail_sync_failed",
                reason=sync_error,
                refresh_result=refresh_result_dict,
            )
        return SectorDetailSnapshotResult(
            sector_id=sector_id,
            status="invalid",
            reason_code="quality_invalid",
            reason=load_result.quality_error,
            refresh_result=refresh_result_dict,
        )

    return SectorDetailSnapshotResult(
        sector_id=sector_id,
        status="no_cache",
        reason_code="detail_sync_failed" if sync_error else "no_cache",
        reason=sync_error,
        refresh_result=refresh_result_dict,
    )


__all__ = [
    "SnapshotLoadResult",
    "SnapshotResult",
    "SectorDetailSnapshotResult",
    "_find_latest_analysis_date",
    "_has_cache_for_date",
    "build_default_source",
    "get_latest_cached_date",
    "has_sector_detail_cache",
    "load_latest_snapshot",
    "load_snapshot_from_db",
    "refresh_market_data",
    "refresh_sector_detail_snapshot",
]
