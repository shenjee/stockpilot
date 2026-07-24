import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from ..market_data import get_market_prefix


def resolve_market_data_db_path(db_dir: Path) -> Path:
    """Prefer the generic DB name but migrate from known legacy names when possible."""
    generic_db_path = db_dir / "market_data.sqlite"
    legacy_db_paths = [
        db_dir / "china_stock_analysis.sqlite",
        db_dir / "china_stock_daily_tracker.sqlite",
    ]

    if not generic_db_path.exists():
        for legacy_path in legacy_db_paths:
            if legacy_path.exists():
                try:
                    legacy_path.rename(generic_db_path)
                    break
                except Exception as exc:
                    print(f"[WARN] 无法重命名旧版数据库文件 {legacy_path}: {exc}")
                    generic_db_path = legacy_path
                    break
    return generic_db_path


class KLineStore:
    """本地 SQLite K 线仓储。"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_klines (
                    symbol TEXT NOT NULL,
                    code TEXT NOT NULL,
                    market TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    open REAL NOT NULL,
                    close REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    volume INTEGER NOT NULL,
                    amount REAL,
                    source TEXT NOT NULL DEFAULT 'tencent',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (symbol, trade_date)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_klines_date ON daily_klines(trade_date)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS klines (
                    symbol TEXT NOT NULL,
                    code TEXT NOT NULL,
                    market TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    open REAL NOT NULL,
                    close REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    volume INTEGER NOT NULL,
                    amount REAL,
                    source TEXT NOT NULL DEFAULT 'tencent',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (symbol, timeframe, timestamp)
                )
                """
            )
            self._ensure_column(conn, "daily_klines", "amount", "REAL")
            self._ensure_column(conn, "klines", "amount", "REAL")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_klines_timeframe_ts ON klines(symbol, timeframe, timestamp)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS kline_coverage (
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'unknown',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (symbol, timeframe, start_date, end_date)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_kline_coverage_range
                ON kline_coverage(symbol, timeframe, start_date, end_date)
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO klines (
                    symbol, code, market, timeframe, timestamp, open, close, high, low,
                    volume, amount, source, updated_at
                )
                SELECT symbol, code, market, 'day', trade_date, open, close, high, low,
                       volume, amount, source, updated_at
                FROM daily_klines
                """
            )

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def symbol(code: str, market: str | None = None) -> str:
        prefix = market or get_market_prefix(code)
        return f"{prefix}{code}"

    def latest_timestamp(self, code: str, market: str | None = None, timeframe: str = "day") -> str | None:
        symbol = self.symbol(code, market)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(timestamp) FROM klines WHERE symbol = ? AND timeframe = ?",
                (symbol, timeframe),
            ).fetchone()
        return row[0] if row and row[0] else None

    def earliest_timestamp(self, code: str, market: str | None = None, timeframe: str = "day") -> str | None:
        symbol = self.symbol(code, market)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MIN(timestamp) FROM klines WHERE symbol = ? AND timeframe = ?",
                (symbol, timeframe),
            ).fetchone()
        return row[0] if row and row[0] else None

    def latest_date(self, code: str, market: str | None = None, timeframe: str = "day") -> str | None:
        return self.latest_timestamp(code, market=market, timeframe=timeframe)

    def count_since(self, code: str, start_date: str, market: str | None = None, timeframe: str = "day", end_date: str | None = None) -> int:
        symbol = self.symbol(code, market)
        with self._connect() as conn:
            if end_date:
                row = conn.execute(
                    "SELECT COUNT(*) FROM klines WHERE symbol = ? AND timeframe = ? AND timestamp >= ? AND timestamp <= ?",
                    (symbol, timeframe, start_date, end_date),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM klines WHERE symbol = ? AND timeframe = ? AND timestamp >= ?",
                    (symbol, timeframe, start_date),
                ).fetchone()
        return int(row[0] or 0)

    def has_negative_prices(
        self,
        code: str,
        start_date: str,
        market: str | None = None,
        timeframe: str = "day",
        end_date: str | None = None,
    ) -> bool:
        symbol = self.symbol(code, market)
        query_end = end_date or "9999-12-31 23:59:59"
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM klines
                WHERE symbol = ? AND timeframe = ? AND timestamp >= ? AND timestamp <= ?
                  AND (open < 0 OR close < 0 OR high < 0 OR low < 0)
                LIMIT 1
                """,
                (symbol, timeframe, start_date, query_end),
            ).fetchone()
        return bool(row)

    def negative_price_dates(
        self,
        code: str,
        start_date: str,
        market: str | None = None,
        timeframe: str = "day",
        end_date: str | None = None,
    ) -> set[str]:
        """Return only dates containing invalid negative OHLC values."""

        symbol = self.symbol(code, market)
        query_end = end_date or "9999-12-31 23:59:59"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT substr(timestamp, 1, 10)
                FROM klines
                WHERE symbol = ? AND timeframe = ?
                  AND timestamp >= ? AND timestamp <= ?
                  AND (open < 0 OR close < 0 OR high < 0 OR low < 0)
                ORDER BY 1
                """,
                (symbol, timeframe, start_date, query_end),
            ).fetchall()
        return {str(row[0]) for row in rows}

    def timestamps_between(
        self,
        code: str,
        start_date: str,
        end_date: str,
        market: str | None = None,
        timeframe: str = "day",
    ) -> list[str]:
        """Return cached timestamps in one requested range, oldest first."""

        symbol = self.symbol(code, market)
        query_end = end_date if timeframe == "day" else f"{end_date} 23:59:59"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT timestamp
                FROM klines
                WHERE symbol = ? AND timeframe = ?
                  AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp
                """,
                (symbol, timeframe, start_date, query_end),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def coverage_ranges(
        self,
        code: str,
        start_date: str,
        end_date: str,
        market: str | None = None,
        timeframe: str = "day",
    ) -> list[tuple[str, str]]:
        """Return successful provider coverage overlapping a date range."""

        symbol = self.symbol(code, market)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT start_date, end_date
                FROM kline_coverage
                WHERE symbol = ? AND timeframe = ?
                  AND end_date >= ? AND start_date <= ?
                ORDER BY start_date, end_date
                """,
                (symbol, timeframe, start_date, end_date),
            ).fetchall()
        return [(str(row[0]), str(row[1])) for row in rows]

    def mark_coverage(
        self,
        code: str,
        market: str | None,
        start_date: str,
        end_date: str,
        *,
        source: str = "unknown",
        timeframe: str = "day",
    ) -> None:
        """Record that a provider successfully answered the inclusive range."""

        if start_date > end_date:
            raise ValueError("coverage start_date must not exceed end_date")
        symbol = self.symbol(code, market)
        updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO kline_coverage (
                    symbol, timeframe, start_date, end_date, source, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, timeframe, start_date, end_date) DO UPDATE SET
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (
                    symbol,
                    timeframe,
                    start_date,
                    end_date,
                    source,
                    updated_at,
                ),
            )

    def upsert_many(self, code: str, market: str | None, klines: list, source: str = "unknown", timeframe: str = "day") -> None:
        if not klines:
            return

        symbol = self.symbol(code, market)
        updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = [
            (
                symbol,
                code,
                market or get_market_prefix(code),
                timeframe,
                item["date"],
                item["open"],
                item["close"],
                item["high"],
                item["low"],
                item["volume"],
                item.get("amount"),
                source,
                updated_at,
            )
            for item in klines
        ]
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO klines (
                    symbol, code, market, timeframe, timestamp, open, close, high, low,
                    volume, amount, source, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, timeframe, timestamp) DO UPDATE SET
                    open = excluded.open,
                    close = excluded.close,
                    high = excluded.high,
                    low = excluded.low,
                    volume = excluded.volume,
                    amount = excluded.amount,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                rows,
            )
            if timeframe == "day":
                conn.executemany(
                    """
                    INSERT INTO daily_klines (
                        symbol, code, market, trade_date, open, close, high, low,
                        volume, amount, source, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, trade_date) DO UPDATE SET
                        open = excluded.open,
                        close = excluded.close,
                        high = excluded.high,
                        low = excluded.low,
                        volume = excluded.volume,
                        amount = excluded.amount,
                        source = excluded.source,
                        updated_at = excluded.updated_at
                    """,
                    [
                        (
                            row[0],
                            row[1],
                            row[2],
                            row[4],
                            row[5],
                            row[6],
                            row[7],
                            row[8],
                            row[9],
                            row[10],
                            row[11],
                            row[12],
                        )
                        for row in rows
                    ],
                )

    def get_klines(
        self,
        code: str,
        end_date: str,
        market: str | None = None,
        limit: int = 120,
        timeframe: str = "day",
        start_date: str | None = None,
    ) -> list:
        symbol = self.symbol(code, market)
        with self._connect() as conn:
            if start_date:
                rows = conn.execute(
                    """
                    SELECT timestamp, open, close, high, low, volume, amount
                    FROM klines
                    WHERE symbol = ? AND timeframe = ? AND timestamp >= ? AND timestamp <= ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (symbol, timeframe, start_date, end_date, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT timestamp, open, close, high, low, volume, amount
                    FROM klines
                    WHERE symbol = ? AND timeframe = ? AND timestamp <= ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (symbol, timeframe, end_date, limit),
                ).fetchall()

        rows.reverse()
        return [
            {
                "date": row[0],
                "open": row[1],
                "close": row[2],
                "high": row[3],
                "low": row[4],
                "volume": row[5],
                **({"amount": row[6]} if row[6] is not None else {}),
            }
            for row in rows
        ]
