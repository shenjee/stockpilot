from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .data_sources.fetch_report import CodeFetchReport
from .lineage import now_cn_isoformat
from .financial_pit import prepare_write, record_evidence, valid_date
from .sync_outcome import (
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_SUCCESS,
    classify_completed_task,
)


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
    if table == "financial_metrics" and not conn.in_transaction:
        # Start before the evidence read: a concurrent writer must not turn a
        # stale comparison into an UPSERT that backdates a revised value.
        conn.execute("BEGIN")
    for row in rows:
        evidence = None
        if table == "financial_metrics":
            row, evidence = prepare_write(conn, row, _FINANCIAL_VALUE_COLUMNS)
        values = tuple(row.get(c) for c in column_order)
        conn.execute(sql, values)
        if evidence is not None:
            record_evidence(conn, row, evidence)
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
# All value-bearing fields; audit/visibility/source fields are compared separately.
_FINANCIAL_VALUE_COLUMNS = tuple(
    column for column in _FINANCIAL_COLUMNS
    if column not in {
        "code", "report_period", "period_type", "disclosure_date", "as_of_date",
        "source", "fetch_run_id", "source_updated_at", "created_at", "updated_at",
    }
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
        elif table == "financial_metrics" and any(
            not valid_date(row.get(k)) for k in ("period_end_date", "disclosure_date", "as_of_date")
        ):
            rejected.append({"reason": "invalid_financial_date", "row": dict(row)})
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


def _task_error(
    status: str,
    *,
    report: Optional[CodeFetchReport],
    rejections: int,
) -> Optional[str]:
    if report is not None and report.failed_count > 0:
        prefix = "fetch_failed" if status == STATUS_FAILED else "partial_fetch_failed"
        message = (
            f"{prefix}: {report.failed_count}/{report.requested_count} code(s) failed"
        )
        if rejections > 0:
            message += f"; {rejections} row(s) failed validation"
        return message
    if status == STATUS_FAILED and rejections > 0:
        return f"all_rows_rejected: {rejections} row(s) failed validation"
    if status == STATUS_PARTIAL and rejections > 0:
        return f"partial_rows_rejected: {rejections} row(s) failed validation"
    return None


def _task_details(
    status: str,
    *,
    report: Optional[CodeFetchReport],
    rejections: int,
    rejected: Sequence[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    details: Dict[str, Any] = {}
    if report is not None:
        details.update(report.as_dict())
    if rejections > 0:
        details["rejected_count"] = rejections
        details["rejected_sample"] = [
            {"reason": item["reason"], "row": _summarize_row(item["row"])}
            for item in list(rejected)[:20]
        ]
    if not details and status == STATUS_SUCCESS:
        return None
    details["status"] = status
    return details


def _task_payload(
    *,
    task: str,
    status: str,
    row_count: int,
    error: Optional[str],
    rejections: int = 0,
    report: Optional[CodeFetchReport] = None,
) -> Dict[str, Any]:
    report_fields = report.as_dict() if report is not None else {
        "requested_count": None,
        "succeeded_count": None,
        "empty_count": None,
        "failed_count": None,
        "failures": [],
    }
    return {
        "task": task,
        "success": status == STATUS_SUCCESS,
        "status": status,
        "row_count": row_count,
        "error": error,
        "rejections": rejections,
        **report_fields,
    }


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
        fetched = fetch()
        report = getattr(fetched, "fetch_report", None)
        if report is not None and not isinstance(report, CodeFetchReport):
            report = None
        rows = list(fetched)
    except Exception as exc:  # noqa: BLE001
        error = f"fetch_failed: {exc}"
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
                error=error,
                details={"status": STATUS_FAILED},
            )
        return _task_payload(
            task=task,
            status=STATUS_FAILED,
            row_count=0,
            error=error,
        )
    try:
        with conn:
            result = persist(rows)
    except Exception as exc:  # noqa: BLE001
        error = f"persist_failed: {exc}"
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
                error=error,
                details=_task_details(
                    STATUS_FAILED, report=report, rejections=0, rejected=(),
                ),
            )
        return _task_payload(
            task=task,
            status=STATUS_FAILED,
            row_count=0,
            error=error,
            report=report,
        )

    written = int(result.written or 0)
    rejections = int(result.rejections or 0)
    status = classify_completed_task(
        report=report,
        written=written,
        rejections=rejections,
    )
    error = _task_error(status, report=report, rejections=rejections)
    details = _task_details(
        status,
        report=report,
        rejections=rejections,
        rejected=result.rejected,
    )

    finished_at = _ts()
    with conn:
        _log_fetch(
            conn,
            fetch_run_id=fetch_run_id,
            source=source_name,
            task=task,
            started_at=started_at,
            finished_at=finished_at,
            success=status == STATUS_SUCCESS,
            row_count=written,
            error=error,
            details=details,
        )
    return _task_payload(
        task=task,
        status=status,
        row_count=written,
        error=error,
        rejections=rejections,
        report=report,
    )
