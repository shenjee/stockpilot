from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .lineage import now_cn_isoformat


def _ts() -> str:
    return now_cn_isoformat()


def _lineage_columns(row: Dict[str, Any], source: str, fetch_run_id: str) -> Dict[str, Any]:
    """Fill lineage fields for a row while preserving explicit upstream values."""

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
    """Generic UPSERT that updates non-PK columns on conflict."""

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
    """Persist-phase summary for a single task."""

    written: int = 0
    rejected: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def rejections(self) -> int:
        return len(self.rejected)


def _validate_required(
    table: str, rows: Iterable[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Filter rows based on REQUIRED_PK_FIELDS[table]."""

    required = REQUIRED_PK_FIELDS.get(table, ())
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for row in rows:
        missing = [
            field_name
            for field_name in required
            if row.get(field_name) in (None, "")
            or (isinstance(row.get(field_name), str) and not row.get(field_name).strip())
        ]
        if missing:
            rejected.append({"reason": f"missing_pk: {','.join(missing)}", "row": dict(row)})
        else:
            accepted.append(row)
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
    """Generic persist flow: enrich, validate, then UPSERT."""

    enriched = [enrich(row) for row in rows]
    accepted, rejected = _validate_required(table, enriched)
    written = _upsert(
        conn,
        table,
        accepted,
        pk_columns=pk_columns,
        column_order=column_order,
    )
    return _PersistResult(written=written, rejected=rejected)


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


def _summarize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only small PK-related fields for rejected-row diagnostics."""

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
    return {key: row.get(key) for key in keep if key in row}


def _run_task(
    conn,
    *,
    fetch_run_id: str,
    source_name: str,
    task: str,
    fetch: Callable[[], List[Dict[str, Any]]],
    persist: Callable[[List[Dict[str, Any]]], _PersistResult],
) -> Dict[str, Any]:
    """Run a single sync subtask and append a data_fetch_log row."""

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
    success = not (written == 0 and rejections > 0)
    error: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    if rejections > 0:
        sample = [
            {"reason": item["reason"], "row": _summarize_row(item["row"])}
            for item in result.rejected[:20]
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
