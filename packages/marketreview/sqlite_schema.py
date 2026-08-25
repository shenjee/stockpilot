"""SQLite schema for daily market review."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Union

from .db import review_transaction
from .errors import ForeignKeysUnavailableError

PathLike = Union[str, Path, sqlite3.Connection]

_REVIEW_TABLE = "daily_market_review"
_LADDER_TABLE = "daily_ladder_stock"
_REVIEW_NEW_TABLE = "daily_market_review_new"
_LADDER_NEW_TABLE = "daily_ladder_stock_new"
_PROVENANCE_TABLE = "market_review_metric_provenance"


def _ddl_daily_market_review(table_name: str, *, if_not_exists: bool = False) -> str:
    exists = "IF NOT EXISTS " if if_not_exists else ""
    return f"""
    CREATE TABLE {exists}{table_name} (
        trade_date TEXT NOT NULL PRIMARY KEY,
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
    """


def _ddl_daily_ladder_stock(
    table_name: str,
    *,
    parent_table: str,
    if_not_exists: bool = False,
) -> str:
    exists = "IF NOT EXISTS " if if_not_exists else ""
    return f"""
    CREATE TABLE {exists}{table_name} (
        trade_date TEXT NOT NULL,
        market TEXT NOT NULL CHECK (market IN ('sh', 'sz', 'bj')),
        code TEXT NOT NULL,
        name TEXT NOT NULL,
        streak_height INTEGER NOT NULL CHECK (streak_height >= 2),
        is_st INTEGER NOT NULL CHECK (is_st = 0),
        PRIMARY KEY (trade_date, market, code),
        FOREIGN KEY (trade_date) REFERENCES {parent_table}(trade_date) ON DELETE CASCADE
    )
    """


DDL_DAILY_MARKET_REVIEW = _ddl_daily_market_review(_REVIEW_TABLE, if_not_exists=True)
DDL_DAILY_LADDER_STOCK = _ddl_daily_ladder_stock(
    _LADDER_TABLE,
    parent_table=_REVIEW_TABLE,
    if_not_exists=True,
)
DDL_STATEMENTS: tuple[str, ...] = (
    DDL_DAILY_MARKET_REVIEW,
    DDL_DAILY_LADDER_STOCK,
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


def _table_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]


def _row_count(conn: sqlite3.Connection, table_name: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


def _trade_dates(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[0] for row in conn.execute(f"SELECT trade_date FROM {table_name}").fetchall()}


def _has_delete_cascade_fk(
    conn: sqlite3.Connection,
    table_name: str,
    *,
    parent_table: str,
) -> bool:
    if not _table_exists(conn, table_name):
        return False
    for row in conn.execute(f"PRAGMA foreign_key_list({table_name})").fetchall():
        parent = row[2]
        from_col = row[3]
        to_col = row[4]
        on_delete = row[6]
        if (
            parent == parent_table
            and from_col == "trade_date"
            and to_col == "trade_date"
            and str(on_delete).upper() == "CASCADE"
        ):
            return True
    return False


def _drop_table_if_exists(conn: sqlite3.Connection, table_name: str) -> None:
    conn.execute(f"DROP TABLE IF EXISTS {table_name}")


def _discard_temp_rebuild_tables(conn: sqlite3.Connection) -> None:
    # Drop the child first. daily_ladder_stock_new references
    # daily_market_review_new with ON DELETE CASCADE.
    _drop_table_if_exists(conn, _LADDER_NEW_TABLE)
    _drop_table_if_exists(conn, _REVIEW_NEW_TABLE)


def _copy_matching_columns(conn: sqlite3.Connection, source: str, dest: str) -> None:
    dest_columns = _table_columns(conn, dest)
    source_columns = set(_table_columns(conn, source))
    columns = [column for column in dest_columns if column in source_columns]
    if not columns:
        return
    column_sql = ", ".join(columns)
    conn.execute(f"INSERT INTO {dest} ({column_sql}) SELECT {column_sql} FROM {source}")


def _temp_rebuild_pair_is_complete(conn: sqlite3.Connection) -> bool:
    if not _table_exists(conn, _REVIEW_NEW_TABLE) or not _table_exists(conn, _LADDER_NEW_TABLE):
        return False
    if not _has_delete_cascade_fk(
        conn,
        _LADDER_NEW_TABLE,
        parent_table=_REVIEW_NEW_TABLE,
    ):
        return False
    orphan = conn.execute(
        f"""
        SELECT 1 FROM {_LADDER_NEW_TABLE} AS ladder
        WHERE NOT EXISTS (
            SELECT 1 FROM {_REVIEW_NEW_TABLE} AS review
            WHERE review.trade_date = ladder.trade_date
        )
        LIMIT 1
        """
    ).fetchone()
    if orphan is not None:
        return False
    if _table_exists(conn, _REVIEW_TABLE) and (
        _row_count(conn, _REVIEW_TABLE) != _row_count(conn, _REVIEW_NEW_TABLE)
        or _trade_dates(conn, _REVIEW_TABLE) != _trade_dates(conn, _REVIEW_NEW_TABLE)
    ):
        return False
    return True


def _rehome_ladder_new(conn: sqlite3.Connection, *, parent_table: str) -> None:
    if not _table_exists(conn, _LADDER_NEW_TABLE):
        _discard_temp_rebuild_tables(conn)
        return
    if not _table_exists(conn, _LADDER_TABLE):
        conn.execute(_ddl_daily_ladder_stock(_LADDER_TABLE, parent_table=parent_table))
    _copy_matching_columns(conn, _LADDER_NEW_TABLE, _LADDER_TABLE)
    _discard_temp_rebuild_tables(conn)


def _adopt_unfinished_rebuild(conn: sqlite3.Connection) -> None:
    has_review = _table_exists(conn, _REVIEW_TABLE)
    has_ladder = _table_exists(conn, _LADDER_TABLE)
    has_review_new = _table_exists(conn, _REVIEW_NEW_TABLE)
    has_ladder_new = _table_exists(conn, _LADDER_NEW_TABLE)

    if not has_review_new and not has_ladder_new:
        return

    if has_review and has_ladder:
        _discard_temp_rebuild_tables(conn)
        return

    if has_review_new and has_ladder_new and _temp_rebuild_pair_is_complete(conn):
        if has_ladder:
            conn.execute(f"DROP TABLE {_LADDER_TABLE}")
        if has_review:
            conn.execute(f"DROP TABLE {_REVIEW_TABLE}")
        conn.execute(f"ALTER TABLE {_REVIEW_NEW_TABLE} RENAME TO {_REVIEW_TABLE}")
        conn.execute(f"ALTER TABLE {_LADDER_NEW_TABLE} RENAME TO {_LADDER_TABLE}")
        return

    if (
        has_ladder_new
        and not has_review_new
        and has_review
        and not has_ladder
        and _has_delete_cascade_fk(conn, _LADDER_NEW_TABLE, parent_table=_REVIEW_TABLE)
    ):
        conn.execute(f"ALTER TABLE {_LADDER_NEW_TABLE} RENAME TO {_LADDER_TABLE}")
        return

    if has_review:
        if has_ladder_new and not has_ladder:
            _rehome_ladder_new(conn, parent_table=_REVIEW_TABLE)
        else:
            _discard_temp_rebuild_tables(conn)
        return

    if has_review_new:
        conn.execute(f"ALTER TABLE {_REVIEW_NEW_TABLE} RENAME TO {_REVIEW_TABLE}")
        if has_ladder_new and not has_ladder:
            _rehome_ladder_new(conn, parent_table=_REVIEW_TABLE)
        else:
            _drop_table_if_exists(conn, _LADDER_NEW_TABLE)
        return

    _discard_temp_rebuild_tables(conn)


def _rebuild_review_and_ladder(conn: sqlite3.Connection) -> None:
    _drop_table_if_exists(conn, _LADDER_NEW_TABLE)
    _drop_table_if_exists(conn, _REVIEW_NEW_TABLE)
    conn.execute(_ddl_daily_market_review(_REVIEW_NEW_TABLE))
    _copy_matching_columns(conn, _REVIEW_TABLE, _REVIEW_NEW_TABLE)
    conn.execute(_ddl_daily_ladder_stock(_LADDER_NEW_TABLE, parent_table=_REVIEW_NEW_TABLE))
    if _table_exists(conn, _LADDER_TABLE):
        _copy_matching_columns(conn, _LADDER_TABLE, _LADDER_NEW_TABLE)
        conn.execute(f"DROP TABLE {_LADDER_TABLE}")
    conn.execute(f"DROP TABLE {_REVIEW_TABLE}")
    conn.execute(f"ALTER TABLE {_REVIEW_NEW_TABLE} RENAME TO {_REVIEW_TABLE}")
    conn.execute(f"ALTER TABLE {_LADDER_NEW_TABLE} RENAME TO {_LADDER_TABLE}")


def _rebuild_ladder_foreign_key(conn: sqlite3.Connection) -> None:
    _drop_table_if_exists(conn, _LADDER_NEW_TABLE)
    conn.execute(_ddl_daily_ladder_stock(_LADDER_NEW_TABLE, parent_table=_REVIEW_TABLE))
    _copy_matching_columns(conn, _LADDER_TABLE, _LADDER_NEW_TABLE)
    conn.execute(f"DROP TABLE {_LADDER_TABLE}")
    conn.execute(f"ALTER TABLE {_LADDER_NEW_TABLE} RENAME TO {_LADDER_TABLE}")


def _apply_legacy_migration(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, _PROVENANCE_TABLE):
        conn.execute(f"DROP TABLE {_PROVENANCE_TABLE}")

    rebuild_review = _table_exists(conn, _REVIEW_TABLE) and (
        "ladder_status" in _table_columns(conn, _REVIEW_TABLE)
    )
    rebuild_ladder = _table_exists(conn, _LADDER_TABLE) and not _has_delete_cascade_fk(
        conn, _LADDER_TABLE, parent_table=_REVIEW_TABLE
    )
    if rebuild_review:
        _rebuild_review_and_ladder(conn)
        return
    if rebuild_ladder:
        _rebuild_ladder_foreign_key(conn)


def migrate_legacy_schema(conn: sqlite3.Connection) -> None:
    with review_transaction(conn):
        _adopt_unfinished_rebuild(conn)
        _apply_legacy_migration(conn)


def init_db(db_path: PathLike) -> sqlite3.Connection:
    conn = connect(db_path)
    with review_transaction(conn):
        _adopt_unfinished_rebuild(conn)
        for statement in DDL_STATEMENTS:
            conn.execute(statement)
        _apply_legacy_migration(conn)
    return conn
