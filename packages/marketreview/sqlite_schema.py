"""SQLite schema for daily market review."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Union

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
        PRIMARY KEY (trade_date, market, code)
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
        PRIMARY KEY (trade_date, metric_name)
    )
    """,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(db_path: PathLike) -> sqlite3.Connection:
    if isinstance(db_path, sqlite3.Connection):
        db_path.row_factory = sqlite3.Row
        return db_path
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: PathLike) -> sqlite3.Connection:
    owns_connection = not isinstance(db_path, sqlite3.Connection)
    conn = connect(db_path)
    for statement in DDL_STATEMENTS:
        conn.execute(statement)
    conn.commit()
    if owns_connection:
        return conn
    return conn
