import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from market_data import get_market_prefix


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
                    source TEXT NOT NULL DEFAULT 'tencent',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (symbol, timeframe, timestamp)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_klines_timeframe_ts ON klines(symbol, timeframe, timestamp)")
            conn.execute(
                """
                INSERT OR IGNORE INTO klines (
                    symbol, code, market, timeframe, timestamp, open, close, high, low, volume, source, updated_at
                )
                SELECT symbol, code, market, 'day', trade_date, open, close, high, low, volume, source, updated_at
                FROM daily_klines
                """
            )

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
                    volume, source, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, timeframe, timestamp) DO UPDATE SET
                    open = excluded.open,
                    close = excluded.close,
                    high = excluded.high,
                    low = excluded.low,
                    volume = excluded.volume,
                    updated_at = excluded.updated_at
                """,
                rows,
            )
            if timeframe == "day":
                conn.executemany(
                    """
                    INSERT INTO daily_klines (
                        symbol, code, market, trade_date, open, close, high, low,
                        volume, source, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, trade_date) DO UPDATE SET
                        open = excluded.open,
                        close = excluded.close,
                        high = excluded.high,
                        low = excluded.low,
                        volume = excluded.volume,
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
                    SELECT timestamp, open, close, high, low, volume
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
                    SELECT timestamp, open, close, high, low, volume
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
            }
            for row in rows
        ]
