"""Fundamental Screener SQLite schema 与初始化入口（Phase 6A）。

本模块只负责声明 SQLite 表结构和提供幂等的 ``init_db()``。它不依赖任何
真实数据源，也不实现 ``MarketSnapshot`` 组装（属于 Phase 6D）。

Phase 6A 要求：
- 覆盖 ``stocks``、``sectors``、``sector_constituents``、``sector_daily_bars``、
  ``benchmark_daily_bars``、``company_daily_snapshot``、
  ``company_valuation_history``、``financial_metrics``、``data_fetch_log`` 9 张表。
- 关键采集表带 ``source`` / ``fetch_run_id`` / ``source_updated_at`` /
  ``created_at`` / ``updated_at`` 血缘字段。
- ``init_db()`` 可幂等调用，已有数据不会被破坏。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Tuple, Union

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

# 注意：所有 ``CREATE TABLE`` 都使用 ``IF NOT EXISTS``，保证 ``init_db`` 幂等。

DDL_STATEMENTS: Tuple[str, ...] = (
    # 股票池：code/name/market/上市状态/退市状态等。
    """
    CREATE TABLE IF NOT EXISTS stocks (
        code TEXT NOT NULL,
        name TEXT,
        market TEXT,
        listing_status TEXT,
        delisted_at TEXT,
        as_of_date TEXT,
        source TEXT NOT NULL,
        fetch_run_id TEXT NOT NULL,
        source_updated_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (code)
    )
    """,
    # 板块：sector_id/sector_name/classification_system/source。
    """
    CREATE TABLE IF NOT EXISTS sectors (
        sector_id TEXT NOT NULL,
        classification_system TEXT NOT NULL,
        sector_name TEXT,
        source TEXT NOT NULL,
        fetch_run_id TEXT NOT NULL,
        source_updated_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (sector_id, classification_system)
    )
    """,
    # 板块-公司关系，带 ``source`` 和 ``as_of_date``。
    """
    CREATE TABLE IF NOT EXISTS sector_constituents (
        sector_id TEXT NOT NULL,
        classification_system TEXT NOT NULL,
        code TEXT NOT NULL,
        as_of_date TEXT NOT NULL,
        source TEXT NOT NULL,
        fetch_run_id TEXT NOT NULL,
        source_updated_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (sector_id, classification_system, code, as_of_date)
    )
    """,
    # 板块历史行情，支撑 1/5/20/60 日收益与 chart series。
    """
    CREATE TABLE IF NOT EXISTS sector_daily_bars (
        sector_id TEXT NOT NULL,
        classification_system TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        turnover_amount REAL,
        rising_count INTEGER,
        total_count INTEGER,
        source TEXT NOT NULL,
        fetch_run_id TEXT NOT NULL,
        source_updated_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (sector_id, classification_system, trade_date)
    )
    """,
    # 公司日度行情快照：市值/收盘价/成交额/换手率/涨跌幅。
    """
    CREATE TABLE IF NOT EXISTS company_daily_snapshot (
        code TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        close REAL,
        turnover_amount REAL,
        turnover_rate REAL,
        market_cap REAL,
        change_pct REAL,
        source TEXT NOT NULL,
        fetch_run_id TEXT NOT NULL,
        source_updated_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (code, trade_date)
    )
    """,
    # 公司日度估值历史，长期保存 PE/PB/PS/股息率，用于本地分位计算。
    """
    CREATE TABLE IF NOT EXISTS company_valuation_history (
        code TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        market TEXT,
        pe REAL,
        pb REAL,
        ps REAL,
        dividend_yield REAL,
        source TEXT NOT NULL,
        fetch_run_id TEXT NOT NULL,
        source_updated_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (code, trade_date)
    )
    """,
    # 公司财务指标，按报告期和披露日保存，支持 point-in-time 过滤。
    """
    CREATE TABLE IF NOT EXISTS financial_metrics (
        code TEXT NOT NULL,
        report_period TEXT NOT NULL,
        period_end_date TEXT NOT NULL,
        disclosure_date TEXT NOT NULL,
        period_type TEXT NOT NULL,
        as_of_date TEXT NOT NULL,
        revenue_yoy REAL,
        net_profit_yoy REAL,
        deducted_net_profit_yoy REAL,
        gross_margin REAL,
        net_margin REAL,
        roe REAL,
        operating_cashflow_to_profit REAL,
        free_cashflow REAL,
        debt_to_asset REAL,
        interest_bearing_debt_ratio REAL,
        accounts_receivable_yoy REAL,
        inventory_yoy REAL,
        gross_margin_yoy_change REAL,
        source TEXT NOT NULL,
        fetch_run_id TEXT NOT NULL,
        source_updated_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (code, report_period, period_type, disclosure_date)
    )
    """,
    # 基准指数（hs300 等）历史行情。独立于板块表，避免 sector_daily_bars 同时承
    # 担"板块"和"基准"两种实体角色（详见 docs §18 数据治理边界）。
    """
    CREATE TABLE IF NOT EXISTS benchmark_daily_bars (
        benchmark TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        turnover_amount REAL,
        source TEXT NOT NULL,
        fetch_run_id TEXT NOT NULL,
        source_updated_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (benchmark, trade_date)
    )
    """,
    # 同步日志：来源、任务、时间、成功/失败、错误信息、行数、fetch_run_id。
    """
    CREATE TABLE IF NOT EXISTS data_fetch_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fetch_run_id TEXT NOT NULL,
        source TEXT NOT NULL,
        task TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        success INTEGER NOT NULL,
        row_count INTEGER,
        used_cache INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        details TEXT
    )
    """,
)

# 常用索引（不属于 PK，但能加速常见查询）。
INDEX_STATEMENTS: Tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS ix_sector_daily_bars_trade_date "
    "ON sector_daily_bars (trade_date)",
    "CREATE INDEX IF NOT EXISTS ix_benchmark_daily_bars_trade_date "
    "ON benchmark_daily_bars (trade_date)",
    "CREATE INDEX IF NOT EXISTS ix_company_daily_snapshot_trade_date "
    "ON company_daily_snapshot (trade_date)",
    "CREATE INDEX IF NOT EXISTS ix_company_valuation_history_trade_date "
    "ON company_valuation_history (trade_date)",
    "CREATE INDEX IF NOT EXISTS ix_financial_metrics_disclosure "
    "ON financial_metrics (code, disclosure_date)",
    "CREATE INDEX IF NOT EXISTS ix_data_fetch_log_run "
    "ON data_fetch_log (fetch_run_id)",
)

# 9 张表名，测试和质量检查会按名称遍历。
TABLE_NAMES: Tuple[str, ...] = (
    "stocks",
    "sectors",
    "sector_constituents",
    "sector_daily_bars",
    "benchmark_daily_bars",
    "company_daily_snapshot",
    "company_valuation_history",
    "financial_metrics",
    "data_fetch_log",
)


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------


PathLike = Union[str, Path]


def connect(db_path: PathLike) -> sqlite3.Connection:
    """打开 SQLite 连接，启用外键和 ``Row`` 工厂。

    ``db_path`` 可以是磁盘路径或 ``:memory:``。我们不在这里做任何 schema
    创建动作，保持 ``connect`` 行为纯粹。
    """

    path = str(db_path)
    if path != ":memory:":
        Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """幂等初始化 schema。

    使用 ``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS``，
    重复调用不会破坏已有数据。所有语句在单事务内执行；任意一条失败都会回滚。
    """

    with conn:
        for stmt in DDL_STATEMENTS:
            conn.execute(stmt)
        for stmt in INDEX_STATEMENTS:
            conn.execute(stmt)


def list_tables(conn: sqlite3.Connection) -> Tuple[str, ...]:
    """返回当前数据库中的所有 user 表名（用于测试断言）。"""

    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return tuple(row[0] for row in cur.fetchall())


def table_columns(conn: sqlite3.Connection, table: str) -> Tuple[str, ...]:
    """返回指定表的列名（用于测试断言血缘字段是否齐全）。"""

    cur = conn.execute(f"PRAGMA table_info({table})")
    return tuple(row[1] for row in cur.fetchall())


def required_lineage_columns(table: str) -> Tuple[str, ...]:
    """返回采集表必须有的血缘列。

    ``data_fetch_log`` 自身就是血缘表，使用单独的列集合。其余采集表共享一组
    标准血缘列。
    """

    if table == "data_fetch_log":
        return ("fetch_run_id", "source", "task", "started_at")
    return (
        "source",
        "fetch_run_id",
        "source_updated_at",
        "created_at",
        "updated_at",
    )


__all__ = [
    "DDL_STATEMENTS",
    "INDEX_STATEMENTS",
    "TABLE_NAMES",
    "connect",
    "init_db",
    "list_tables",
    "required_lineage_columns",
    "table_columns",
]
