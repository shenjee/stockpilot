"""Internal financial visibility evidence, stored in existing fetch-log details.

This is deliberately not a financial version store: superseded values are lost.
Keep evidence and writes in the caller's transaction.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any, Mapping, Sequence

from .lineage import now_cn_isoformat

_PK = ("code", "report_period", "period_type", "disclosure_date")
_SELECTION_COLUMNS = ("period_end_date", "period_type", "disclosure_date", "source_updated_at")
FINANCIAL_DEDUP_ORDER = (
    "period_end_date DESC,"
    " CASE period_type"
    "   WHEN 'annual' THEN 1 WHEN 'semiannual' THEN 2"
    "   WHEN 'quarterly' THEN 3 WHEN 'quarter' THEN 3"
    "   WHEN 'first_quarter' THEN 4 ELSE 5 END,"
    " disclosure_date DESC, source_updated_at DESC"
)


def valid_date(value: Any) -> bool:
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _task(row: Mapping[str, Any]) -> str:
    return "financial_pit:" + json.dumps([row[k] for k in _PK], separators=(",", ":"))


def read_evidence(conn: sqlite3.Connection, row: Mapping[str, Any]) -> dict | None:
    result = conn.execute(
        "SELECT details FROM data_fetch_log WHERE task = ? AND fetch_run_id = ? "
        "ORDER BY id DESC LIMIT 1", (_task(row), row["fetch_run_id"]),
    ).fetchone()
    return json.loads(result[0]) if result and result[0] else None


def _audit_date(row: Mapping[str, Any]) -> str:
    candidates = [str(row.get(k) or "")[:10]
                  for k in ("as_of_date", "source_updated_at", "updated_at")]
    return max([d for d in candidates if valid_date(d)] or [now_cn_isoformat()[:10]])


def prepare_write(
    conn: sqlite3.Connection, row: dict, value_columns: Sequence[str],
) -> tuple[dict, dict]:
    """Preserve visibility for equal values; revisions never travel backwards."""
    row = dict(row)
    cur = conn.execute(
        "SELECT * FROM financial_metrics WHERE " + " AND ".join(f"{k} = ?" for k in _PK),
        tuple(row[k] for k in _PK),
    )
    result = cur.fetchone()
    old = dict(zip([d[0] for d in cur.description], result)) if result else None
    evidence = read_evidence(conn, old) if old else None
    details = dict(evidence or {})
    details.update(
        key={k: row[k] for k in _PK},
        disclosure_basis=(row.get("_disclosure_date_basis")
                          or details.get("disclosure_basis", "source_provided")),
    )
    if old:
        previous_date = old["as_of_date"]
        old_date = previous_date if evidence else _audit_date(old)
        if not evidence:
            details["legacy_unverified"] = True
        same = all(old[k] == row.get(k) for k in value_columns)
        if old["source"] != row["source"]:
            details["source_change"] = {
                "from_source": old["source"], "to_source": row["source"],
                "observed_at": row["updated_at"],
            }
        if same:
            row["as_of_date"] = old_date
        else:
            row["as_of_date"] = max(old_date, row["as_of_date"], row["updated_at"][:10])
            details["revised"] = True
        if previous_date < row["as_of_date"]:
            # Keep only the lost version's ordering metadata, not its values.
            # A correction to period_end_date must not change whether the old
            # version would have outranked the available report at that time.
            intervals = details.setdefault("unavailable_selections", [])
            if not intervals and details.get("unavailable_from"):
                intervals.append({
                    "from_date": details["unavailable_from"],
                    "before_date": details["unavailable_before"],
                    **{k: old[k] for k in _SELECTION_COLUMNS},
                })
            intervals.append({
                "from_date": previous_date, "before_date": row["as_of_date"],
                **{k: old[k] for k in _SELECTION_COLUMNS},
            })
            details["unavailable_from"] = min(
                details.get("unavailable_from", previous_date), previous_date,
            )
            details["unavailable_before"] = row["as_of_date"]
    details["visible_from"] = row["as_of_date"]
    return row, details


def record_evidence(conn: sqlite3.Connection, row: dict, details: dict) -> None:
    conn.execute(
        "INSERT INTO data_fetch_log "
        "(fetch_run_id, source, task, started_at, finished_at, success, row_count, details) "
        "VALUES (?, ?, ?, ?, ?, 1, 1, ?)",
        (row["fetch_run_id"], row["source"], _task(row), row["updated_at"],
         row["updated_at"], json.dumps(details, ensure_ascii=False)),
    )


def _missing_selection_matters(conn, row, evidence, selected, analysis_date):
    intervals = evidence.get("unavailable_selections")
    if not intervals:
        # Compatibility with evidence recorded before selection metadata was
        # saved: use the retained report fields, without inventing old values.
        intervals = [{"from_date": evidence.get("unavailable_from", "9999-12-31"),
                      "before_date": evidence.get("unavailable_before", "0001-01-01"),
                      **{k: row[k] for k in _SELECTION_COLUMNS}}]
    current = selected.get(row["code"])
    columns = ", ".join(_SELECTION_COLUMNS)
    placeholders = ", ".join("?" for _ in _SELECTION_COLUMNS)
    for lost in intervals:
        if not (lost["from_date"] <= analysis_date < lost["before_date"]
                and lost["disclosure_date"] <= analysis_date):
            continue
        if current is None:
            return True
        # Reuse exactly the repository order, including report-type priority
        # and disclosure/source-update ties, rather than comparing dates alone.
        winner = conn.execute(
            f"WITH candidates ({columns}, missing) AS ("
            f"SELECT {placeholders}, 1 UNION ALL SELECT {placeholders}, 0) "
            f"SELECT missing FROM candidates ORDER BY {FINANCIAL_DEDUP_ORDER} LIMIT 1",
            tuple(lost[k] for k in _SELECTION_COLUMNS)
            + tuple(current[k] for k in _SELECTION_COLUMNS),
        ).fetchone()[0]
        if winner:
            return True
    return False


def add_quality_issues(conn, report, analysis_date: str, classification_system: str) -> None:
    """Describe selected rows and lost history within the requested universe."""
    cur = conn.execute(
        "SELECT f.* FROM financial_metrics f WHERE EXISTS ("
        " SELECT 1 FROM sector_constituents sc WHERE sc.code = f.code"
        " AND sc.classification_system = ? AND sc.as_of_date = ("
        " SELECT MAX(as_of_date) FROM sector_constituents WHERE sector_id = sc.sector_id"
        " AND classification_system = ? AND as_of_date <= ?))",
        (classification_system, classification_system, analysis_date),
    )
    columns = [d[0] for d in cur.description]
    rows = [dict(zip(columns, r)) for r in cur.fetchall()]
    cur = conn.execute(
        "SELECT * FROM ("
        " SELECT *, ROW_NUMBER() OVER (PARTITION BY code ORDER BY "
        + FINANCIAL_DEDUP_ORDER + ") AS rn FROM financial_metrics"
        " WHERE disclosure_date <= ? AND as_of_date <= ?) WHERE rn = 1",
        (analysis_date, analysis_date),
    )
    selected_columns = [d[0] for d in cur.description]
    selected = {r[0]: dict(zip(selected_columns, r)) for r in cur.fetchall()}
    for row in rows:
        evidence = read_evidence(conn, row)
        details = dict(evidence or {"disclosure_basis": "unknown", "legacy_unverified": True})
        details.update(source=row["source"], fetch_run_id=row["fetch_run_id"],
                       report_period=row["report_period"], as_of_date=row["as_of_date"])
        if evidence and _missing_selection_matters(conn, row, evidence, selected, analysis_date):
            report.add_issue(
                "financial_pit_history_unavailable", "warning",
                f"{row['code']} {row['report_period']}: 该时点的旧财务值无法恢复；"
                "修订值未用于此历史日期，可能仅能使用更早报告期。",
                entity_type="company", entity_id=row["code"], details=details,
            )
        current = selected.get(row["code"])
        if current is None or any(row[k] != current[k] for k in _PK):
            continue
        basis = details["disclosure_basis"]
        code = "financial_pit_disclosure_estimated" if basis == "estimated" else "financial_pit_disclosure_unverified"
        report.add_issue(
            code, "warning" if details.get("legacy_unverified") else "info",
            f"{row['code']} {row['report_period']}: "
            + ("披露日期由报告期估算，并非已核实公告日。" if basis == "estimated"
               else "披露日期尚无已核实公告证据。")
            + ("旧库缺少版本可见性证据，历史结果不保证可复现。"
               if details.get("legacy_unverified") else "仅按记录可见日使用，不保证完整修订历史。"),
            entity_type="company", entity_id=row["code"], details=details,
        )
