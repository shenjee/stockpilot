"""SQLite schema for daily market review."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Union

from .errors import ForeignKeysUnavailableError

PathLike = Union[str, Path, sqlite3.Connection]

DDL_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS daily_market_review (
        trade_date TEXT NOT NULL PRIMARY KEY,
        ladder_status TEXT NOT NULL DEFAULT 'missing' CHECK (ladder_status IN ('missing', 'complete')),
        effective_limit_up INTEGER,
        limit_up_20pct INTEGER,
        opened_limit_down INTEGER,
        closed_limit_down INTEGER,
        limit_up_failed INTEGER,
        pullback_count INTEGER,
        median_change_pct REAL,
        advancing_count INTEGER,
        declining_count INTEGER,
        margin_balance_sh REAL,
        margin_balance_sz REAL,
        margin_balance_bj REAL,
        sh_index_close REAL,
        sh_index_prev_close REAL,
        sz_index_close REAL,
        sz_index_prev_close REAL,
        cy_index_close REAL,
        cy_index_prev_close REAL,
        turnover_amount_sh REAL,
        turnover_amount_sz REAL,
        turnover_amount_cy REAL,
        turnover_amount_bj REAL,
        total_market_cap REAL,
        float_market_cap REAL,
        pe_sh REAL,
        pe_sz REAL,
        pe_cy REAL,
        pe_all REAL,
        avg_stock_price REAL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_ladder_stock (
        trade_date TEXT NOT NULL,
        market TEXT NOT NULL CHECK (market IN ('sh', 'sz', 'bj')),
        code TEXT NOT NULL,
        name TEXT NOT NULL,
        streak_height INTEGER NOT NULL CHECK (streak_height >= 2),
        is_st INTEGER NOT NULL CHECK (is_st = 0),
        PRIMARY KEY (trade_date, market, code),
        FOREIGN KEY (trade_date) REFERENCES daily_market_review(trade_date) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS market_review_metric_provenance (
        trade_date TEXT NOT NULL,
        metric_name TEXT NOT NULL,
        source TEXT NOT NULL,
        source_as_of TEXT,
        retrieved_at TEXT NOT NULL,
        acquisition_mode TEXT NOT NULL,
        PRIMARY KEY (trade_date, metric_name),
        FOREIGN KEY (trade_date) REFERENCES daily_market_review(trade_date) ON DELETE CASCADE
    )
    """,
)

LEGACY_LADDER_DDL = """
    CREATE TABLE daily_ladder_stock (
        trade_date TEXT NOT NULL,
        market TEXT NOT NULL CHECK (market IN ('sh', 'sz', 'bj')),
        code TEXT NOT NULL,
        name TEXT NOT NULL,
        streak_height INTEGER NOT NULL CHECK (streak_height >= 2),
        is_st INTEGER NOT NULL CHECK (is_st = 0),
        PRIMARY KEY (trade_date, market, code)
    )
"""

LEGACY_PROVENANCE_DDL = """
    CREATE TABLE market_review_metric_provenance (
        trade_date TEXT NOT NULL,
        metric_name TEXT NOT NULL,
        source TEXT NOT NULL,
        source_as_of TEXT,
        retrieved_at TEXT NOT NULL,
        acquisition_mode TEXT NOT NULL,
        PRIMARY KEY (trade_date, metric_name)
    )
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def configure_connection(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    fk_enabled = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    if fk_enabled != 1:
        raise ForeignKeysUnavailableError()
    return conn


def connect(db_path: PathLike) -> sqlite3.Connection:
    if isinstance(db_path, sqlite3.Connection):
        return configure_connection(db_path)
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return configure_connection(sqlite3.connect(path))


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _child_table_has_foreign_key(conn: sqlite3.Connection, table_name: str) -> bool:
    if not _table_exists(conn, table_name):
        return False
    rows = conn.execute(f"PRAGMA foreign_key_list({table_name})").fetchall()
    return len(rows) > 0


def _migrate_table_add_foreign_key(conn: sqlite3.Connection, table_name: str, create_sql: str) -> None:
    if not _table_exists(conn, table_name):
        return
    if _child_table_has_foreign_key(conn, table_name):
        return
    temp_name = f"{table_name}_legacy"
    conn.execute(f"ALTER TABLE {table_name} RENAME TO {temp_name}")
    conn.execute(create_sql)
    columns = ", ".join(row[1] for row in conn.execute(f"PRAGMA table_info({temp_name})").fetchall())
    conn.execute(f"INSERT INTO {table_name} ({columns}) SELECT {columns} FROM {temp_name}")
    conn.execute(f"DROP TABLE {temp_name}")


def migrate_legacy_schema(conn: sqlite3.Connection) -> None:
    _migrate_table_add_foreign_key(conn, "daily_ladder_stock", DDL_STATEMENTS[1])
    _migrate_table_add_foreign_key(conn, "market_review_metric_provenance", DDL_STATEMENTS[2])


def init_db(db_path: PathLike) -> sqlite3.Connection:
    owns_connection = not isinstance(db_path, sqlite3.Connection)
    conn = connect(db_path)
    started_in_transaction = conn.in_transaction
    for statement in DDL_STATEMENTS:
        conn.execute(statement)
    migrate_legacy_schema(conn)
    if owns_connection or not started_in_transaction:
        conn.commit()
    return conn
