"""SQLite repository for daily market review."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from typing import Any, Iterable, Mapping

from packages.marketdata.trading_calendar import TradingCalendar

from .computed import compute_review_metrics
from .db import review_transaction
from .errors import (
    InvalidFieldValueError,
    LadderStNotAllowedError,
)
from .schema import (
    ATOMIC_FIELD_NAMES,
    DailyMarketReviewAtoms,
    DailyMarketReviewView,
    LadderItemPatch,
    LadderOperation,
    LadderSnapshotReplace,
    LadderStockInput,
    LadderStockRecord,
)
from .sqlite_schema import connect, init_db, utc_now_iso
from .validation import assert_writable_trade_date, validate_atomic_field, validate_ladder_stock_input

_CODE_PATTERN = re.compile(r"^\d{6}$")


def _normalize_code(code: str) -> str:
    if type(code) is not str:
        raise InvalidFieldValueError(f"股票代码必须为字符串：{code!r}")
    normalized = code.strip()
    if not _CODE_PATTERN.fullmatch(normalized):
        raise InvalidFieldValueError(f"股票代码必须为 6 位数字：{code!r}")
    return normalized


def _validate_market(market: str) -> str:
    if type(market) is not str:
        raise InvalidFieldValueError(f"不支持的市场：{market!r}")
    if market not in {"sh", "sz", "bj"}:
        raise InvalidFieldValueError(f"不支持的市场：{market!r}")
    return market


def _row_to_atoms(row: sqlite3.Row) -> DailyMarketReviewAtoms:
    payload = {key: row[key] for key in row.keys() if key not in {"created_at", "updated_at"}}
    return DailyMarketReviewAtoms(**payload)


def _st_problem_identity(stock: LadderStockInput) -> tuple[str, str]:
    market = stock.market if type(stock.market) is str else str(stock.market)
    code = stock.code.strip() if type(stock.code) is str else str(stock.code)
    return market, code


def _collect_st_violations(stocks: Iterable[LadderStockInput]) -> list[tuple[str, str]]:
    return [
        _st_problem_identity(stock)
        for stock in stocks
        if type(stock.is_st) is bool and stock.is_st
    ]


def _snapshot_records(trade_date: str, stocks: Iterable[LadderStockInput]) -> list[LadderStockRecord]:
    records: list[LadderStockRecord] = []
    seen: set[tuple[str, str]] = set()
    for stock in stocks:
        record = _to_ladder_record(trade_date, stock)
        key = (record.market, record.code)
        if key in seen:
            raise InvalidFieldValueError(f"连板名单存在重复股票：{record.market}.{record.code}")
        seen.add(key)
        records.append(record)
    return records


def _to_ladder_record(trade_date: str, stock: LadderStockInput) -> LadderStockRecord:
    validate_ladder_stock_input(stock)
    market = _validate_market(stock.market)
    code = _normalize_code(stock.code)
    if stock.is_st:
        raise LadderStNotAllowedError([(market, code)])
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
        ladder_operation: LadderOperation | None = None,
        now: datetime | None = None,
    ) -> None:
        writable_date = assert_writable_trade_date(trade_date, self._calendar, now=now)
        payload = dict(fields or {})
        unknown = set(payload) - ATOMIC_FIELD_NAMES
        if unknown:
            raise InvalidFieldValueError(f"未知字段：{', '.join(sorted(unknown))}")

        with review_transaction(self._conn):
            self._ensure_review_row(writable_date)
            if payload:
                self._apply_field_patch(writable_date, payload)
            if ladder_operation is not None:
                self._apply_ladder_operation(writable_date, ladder_operation)
            self._touch_review(writable_date)

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
        return DailyMarketReviewView(
            atoms=atoms,
            ladder_stocks=ladder_stocks,
            computed=computed,
        )

    def list_reviews(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[DailyMarketReviewView]:
        dates = self.list_trade_dates(start_date, end_date)
        views: list[DailyMarketReviewView] = []
        for trade_date in dates:
            view = self.get_review(trade_date)
            if view is not None:
                views.append(view)
        return views

    def delete_review(self, trade_date: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM daily_market_review WHERE trade_date = ?",
            (trade_date,),
        ).fetchone()
        if row is None:
            return False
        with review_transaction(self._conn):
            self._conn.execute("DELETE FROM daily_market_review WHERE trade_date = ?", (trade_date,))
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
            INSERT INTO daily_market_review (trade_date, created_at, updated_at)
            VALUES (?, ?, ?)
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
            normalized = validate_atomic_field(key, value)
            assignments.append(f"{key} = ?")
            values.append(normalized)
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
        else:
            raise InvalidFieldValueError(f"未知连板写入模式：{operation!r}")

    def _snapshot_replace(self, trade_date: str, operation: LadderSnapshotReplace) -> None:
        violations = _collect_st_violations(operation.stocks)
        if violations:
            raise LadderStNotAllowedError(violations)
        records = _snapshot_records(trade_date, operation.stocks)
        self._conn.execute("DELETE FROM daily_ladder_stock WHERE trade_date = ?", (trade_date,))
        for record in records:
            self._insert_ladder_record(record)

    def _item_patch(self, trade_date: str, operation: LadderItemPatch) -> None:
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
