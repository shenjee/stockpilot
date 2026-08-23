"""SQLite repository for daily market review."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import asdict
from typing import Any, Iterable, Mapping

from packages.marketdata.trading_calendar import TradingCalendar

from .computed import compute_review_metrics
from .errors import (
    LadderInvalidTransitionError,
    LadderSnapshotRequiredError,
    LadderStNotAllowedError,
)
from .schema import (
    ATOMIC_FIELD_NAMES,
    DailyMarketReviewAtoms,
    DailyMarketReviewView,
    LadderItemPatch,
    LadderOperation,
    LadderResetMissing,
    LadderSnapshotReplace,
    LadderStatus,
    LadderStockInput,
    LadderStockRecord,
    MetricProvenance,
)
from .sqlite_schema import connect, init_db, utc_now_iso

_CODE_PATTERN = re.compile(r"^\d{6}$")
_LADDER_SNAPSHOT_METRIC = "ladder_snapshot"


def _normalize_code(code: str) -> str:
    normalized = code.strip()
    if not _CODE_PATTERN.fullmatch(normalized):
        raise LadderInvalidTransitionError(f"股票代码必须为 6 位数字：{code!r}")
    return normalized


def _validate_market(market: str) -> str:
    if market not in {"sh", "sz", "bj"}:
        raise LadderInvalidTransitionError(f"不支持的市场：{market!r}")
    return market


def _row_to_atoms(row: sqlite3.Row) -> DailyMarketReviewAtoms:
    payload = {key: row[key] for key in row.keys() if key not in {"created_at", "updated_at"}}
    return DailyMarketReviewAtoms(**payload)


def _collect_st_violations(stocks: Iterable[LadderStockInput]) -> list[tuple[str, str]]:
    violations: list[tuple[str, str]] = []
    for stock in stocks:
        if stock.is_st:
            violations.append((stock.market, _normalize_code(stock.code)))
    return violations


def _to_ladder_record(trade_date: str, stock: LadderStockInput) -> LadderStockRecord:
    market = _validate_market(stock.market)
    code = _normalize_code(stock.code)
    if stock.streak_height < 2:
        raise LadderInvalidTransitionError(
            f"连板高度必须 >= 2：{market}.{code} streak_height={stock.streak_height}"
        )
    if stock.is_st:
        raise LadderInvalidTransitionError(f"ST 股票不得写入连板明细：{market}.{code}")
    return LadderStockRecord(
        trade_date=trade_date,
        market=market,  # type: ignore[arg-type]
        code=code,
        name=stock.name.strip(),
        streak_height=stock.streak_height,
        is_st=False,
    )


class MarketReviewRepository:
    def __init__(
        self,
        db_path: str | sqlite3.Connection,
        *,
        calendar: TradingCalendar | None = None,
    ) -> None:
        self._owns_connection = not isinstance(db_path, sqlite3.Connection)
        self._conn = connect(db_path) if self._owns_connection else db_path
        init_db(self._conn)
        self._calendar = calendar or TradingCalendar()

    def close(self) -> None:
        if self._owns_connection:
            self._conn.close()

    def __enter__(self) -> "MarketReviewRepository":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def patch_review(
        self,
        trade_date: str,
        fields: Mapping[str, Any] | None = None,
        *,
        provenance: Mapping[str, MetricProvenance] | None = None,
        ladder_operation: LadderOperation | None = None,
    ) -> None:
        payload = dict(fields or {})
        unknown = set(payload) - ATOMIC_FIELD_NAMES
        if unknown:
            raise LadderInvalidTransitionError(f"未知字段：{', '.join(sorted(unknown))}")

        try:
            self._conn.execute("BEGIN")
            self._ensure_review_row(trade_date)
            if payload:
                self._apply_field_patch(trade_date, payload)
            if provenance:
                self._upsert_provenance(trade_date, provenance)
            if ladder_operation is not None:
                self._apply_ladder_operation(trade_date, ladder_operation)
            self._touch_review(trade_date)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def get_review(self, trade_date: str) -> DailyMarketReviewView | None:
        row = self._conn.execute(
            "SELECT * FROM daily_market_review WHERE trade_date = ?",
            (trade_date,),
        ).fetchone()
        if row is None:
            return None
        atoms = _row_to_atoms(row)
        ladder_stocks = self._load_ladder_stocks(trade_date)
        previous_effective_limit_up = self._previous_effective_limit_up(trade_date)
        computed = compute_review_metrics(
            atoms,
            ladder_stocks,
            previous_effective_limit_up=previous_effective_limit_up,
        )
        provenance = self._load_provenance(trade_date)
        return DailyMarketReviewView(
            atoms=atoms,
            ladder_stocks=ladder_stocks,
            computed=computed,
            provenance=provenance,
        )

    def delete_review(self, trade_date: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM daily_market_review WHERE trade_date = ?",
            (trade_date,),
        ).fetchone()
        if row is None:
            return False
        self._conn.execute("DELETE FROM daily_ladder_stock WHERE trade_date = ?", (trade_date,))
        self._conn.execute(
            "DELETE FROM market_review_metric_provenance WHERE trade_date = ?",
            (trade_date,),
        )
        self._conn.execute("DELETE FROM daily_market_review WHERE trade_date = ?", (trade_date,))
        self._conn.commit()
        return True

    def list_trade_dates(self, start_date: str | None = None, end_date: str | None = None) -> list[str]:
        query = "SELECT trade_date FROM daily_market_review"
        params: list[str] = []
        clauses: list[str] = []
        if start_date:
            clauses.append("trade_date >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("trade_date <= ?")
            params.append(end_date)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY trade_date"
        rows = self._conn.execute(query, params).fetchall()
        return [row["trade_date"] for row in rows]

    def _ensure_review_row(self, trade_date: str) -> None:
        now = utc_now_iso()
        self._conn.execute(
            """
            INSERT INTO daily_market_review (trade_date, ladder_status, created_at, updated_at)
            VALUES (?, 'missing', ?, ?)
            ON CONFLICT(trade_date) DO NOTHING
            """,
            (trade_date, now, now),
        )

    def _apply_field_patch(self, trade_date: str, fields: Mapping[str, Any]) -> None:
        assignments: list[str] = []
        values: list[Any] = []
        for key, value in fields.items():
            if key not in ATOMIC_FIELD_NAMES:
                continue
            if value is not None and key in {
                "effective_limit_up",
                "limit_up_20pct",
                "opened_limit_down",
                "closed_limit_down",
                "limit_up_failed",
                "pullback_count",
                "advancing_count",
                "declining_count",
            }:
                if not isinstance(value, int) or value < 0:
                    raise LadderInvalidTransitionError(f"{key} 必须为非负整数")
            assignments.append(f"{key} = ?")
            values.append(value)
        if not assignments:
            return
        values.append(trade_date)
        self._conn.execute(
            f"UPDATE daily_market_review SET {', '.join(assignments)} WHERE trade_date = ?",
            values,
        )

    def _touch_review(self, trade_date: str) -> None:
        self._conn.execute(
            "UPDATE daily_market_review SET updated_at = ? WHERE trade_date = ?",
            (utc_now_iso(), trade_date),
        )

    def _apply_ladder_operation(self, trade_date: str, operation: LadderOperation) -> None:
        if isinstance(operation, LadderSnapshotReplace):
            self._snapshot_replace(trade_date, operation)
        elif isinstance(operation, LadderItemPatch):
            self._item_patch(trade_date, operation)
        elif isinstance(operation, LadderResetMissing):
            self._reset_missing(trade_date)
        else:
            raise LadderInvalidTransitionError(f"未知连板写入模式：{operation!r}")

    def _current_ladder_status(self, trade_date: str) -> LadderStatus:
        row = self._conn.execute(
            "SELECT ladder_status FROM daily_market_review WHERE trade_date = ?",
            (trade_date,),
        ).fetchone()
        if row is None:
            return "missing"
        return row["ladder_status"]

    def _snapshot_replace(self, trade_date: str, operation: LadderSnapshotReplace) -> None:
        if operation.ladder_status != "complete":
            raise LadderInvalidTransitionError("snapshot_replace 必须提交 ladder_status=complete")
        violations = _collect_st_violations(operation.stocks)
        if violations:
            raise LadderStNotAllowedError(violations)
        records = [_to_ladder_record(trade_date, stock) for stock in operation.stocks]
        self._conn.execute("DELETE FROM daily_ladder_stock WHERE trade_date = ?", (trade_date,))
        for record in records:
            self._insert_ladder_record(record)
        self._conn.execute(
            "UPDATE daily_market_review SET ladder_status = 'complete' WHERE trade_date = ?",
            (trade_date,),
        )
        self._upsert_provenance(
            trade_date,
            {
                _LADDER_SNAPSHOT_METRIC: MetricProvenance(
                    source="manual_batch",
                    source_as_of=trade_date,
                    retrieved_at=utc_now_iso(),
                    acquisition_mode="manual",
                )
            },
        )

    def _item_patch(self, trade_date: str, operation: LadderItemPatch) -> None:
        if self._current_ladder_status(trade_date) != "complete":
            raise LadderSnapshotRequiredError()
        violations = _collect_st_violations(operation.upserts)
        if violations:
            raise LadderStNotAllowedError(violations)
        for stock in operation.upserts:
            record = _to_ladder_record(trade_date, stock)
            self._conn.execute(
                """
                INSERT INTO daily_ladder_stock (
                    trade_date, market, code, name, streak_height, is_st
                ) VALUES (?, ?, ?, ?, ?, 0)
                ON CONFLICT(trade_date, market, code) DO UPDATE SET
                    name = excluded.name,
                    streak_height = excluded.streak_height,
                    is_st = excluded.is_st
                """,
                (
                    record.trade_date,
                    record.market,
                    record.code,
                    record.name,
                    record.streak_height,
                ),
            )
        for market, code in operation.deletes:
            self._conn.execute(
                """
                DELETE FROM daily_ladder_stock
                WHERE trade_date = ? AND market = ? AND code = ?
                """,
                (trade_date, _validate_market(market), _normalize_code(code)),
            )
        self._upsert_provenance(
            trade_date,
            {
                _LADDER_SNAPSHOT_METRIC: MetricProvenance(
                    source="manual_item_patch",
                    source_as_of=trade_date,
                    retrieved_at=utc_now_iso(),
                    acquisition_mode="manual",
                )
            },
        )

    def _reset_missing(self, trade_date: str) -> None:
        self._conn.execute("DELETE FROM daily_ladder_stock WHERE trade_date = ?", (trade_date,))
        self._conn.execute(
            """
            DELETE FROM market_review_metric_provenance
            WHERE trade_date = ? AND metric_name = ?
            """,
            (trade_date, _LADDER_SNAPSHOT_METRIC),
        )
        self._conn.execute(
            "UPDATE daily_market_review SET ladder_status = 'missing' WHERE trade_date = ?",
            (trade_date,),
        )

    def _insert_ladder_record(self, record: LadderStockRecord) -> None:
        self._conn.execute(
            """
            INSERT INTO daily_ladder_stock (
                trade_date, market, code, name, streak_height, is_st
            ) VALUES (?, ?, ?, ?, ?, 0)
            """,
            (
                record.trade_date,
                record.market,
                record.code,
                record.name,
                record.streak_height,
            ),
        )

    def _load_ladder_stocks(self, trade_date: str) -> list[LadderStockRecord]:
        rows = self._conn.execute(
            """
            SELECT trade_date, market, code, name, streak_height, is_st
            FROM daily_ladder_stock
            WHERE trade_date = ?
            ORDER BY streak_height DESC, market, code
            """,
            (trade_date,),
        ).fetchall()
        return [
            LadderStockRecord(
                trade_date=row["trade_date"],
                market=row["market"],
                code=row["code"],
                name=row["name"],
                streak_height=row["streak_height"],
                is_st=bool(row["is_st"]),
            )
            for row in rows
        ]

    def _load_provenance(self, trade_date: str) -> dict[str, MetricProvenance]:
        rows = self._conn.execute(
            """
            SELECT metric_name, source, source_as_of, retrieved_at, acquisition_mode
            FROM market_review_metric_provenance
            WHERE trade_date = ?
            """,
            (trade_date,),
        ).fetchall()
        return {
            row["metric_name"]: MetricProvenance(
                source=row["source"],
                source_as_of=row["source_as_of"],
                retrieved_at=row["retrieved_at"],
                acquisition_mode=row["acquisition_mode"],
            )
            for row in rows
        }

    def _upsert_provenance(
        self,
        trade_date: str,
        provenance: Mapping[str, MetricProvenance],
    ) -> None:
        for metric_name, record in provenance.items():
            self._conn.execute(
                """
                INSERT INTO market_review_metric_provenance (
                    trade_date, metric_name, source, source_as_of, retrieved_at, acquisition_mode
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_date, metric_name) DO UPDATE SET
                    source = excluded.source,
                    source_as_of = excluded.source_as_of,
                    retrieved_at = excluded.retrieved_at,
                    acquisition_mode = excluded.acquisition_mode
                """,
                (
                    trade_date,
                    metric_name,
                    record.source,
                    record.source_as_of,
                    record.retrieved_at,
                    record.acquisition_mode,
                ),
            )

    def _previous_effective_limit_up(self, trade_date: str) -> int | None:
        previous_day = self._calendar.previous_trading_day(trade_date, "sh")
        if previous_day is None:
            return None
        row = self._conn.execute(
            "SELECT effective_limit_up FROM daily_market_review WHERE trade_date = ?",
            (previous_day.isoformat(),),
        ).fetchone()
        if row is None:
            return None
        return row["effective_limit_up"]
