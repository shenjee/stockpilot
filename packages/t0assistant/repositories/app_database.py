"""Private SQLite foundation owned only by the T+0 Assistant.

The schema intentionally starts with preferences.  Later real-trade and fee
tables share this file and migration boundary, but not the market-data DB.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Callable, TypeVar
from urllib.parse import quote

from packages.t0assistant.preferences import (
    LayerPreference,
    LayoutPreference,
    PreferenceCapability,
    PreferencePersistenceError,
    PreferenceSnapshot,
    PreferenceValues,
    PreferencesReadOnlyError,
)


PathLike = str | Path
_T = TypeVar("_T")
SCHEMA_VERSION = 1

DDL_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS app_schema (
        singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
        schema_version INTEGER NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS preferences (
        singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
        preference_revision INTEGER NOT NULL CHECK (preference_revision >= 0),
        last_symbol TEXT,
        chart_split TEXT NOT NULL CHECK (chart_split IN ('64_36', '50_50')),
        show_intraday INTEGER NOT NULL CHECK (show_intraday IN (0, 1)),
        ma5 INTEGER NOT NULL CHECK (ma5 IN (0, 1)),
        ma10 INTEGER NOT NULL CHECK (ma10 IN (0, 1)),
        ma20 INTEGER NOT NULL CHECK (ma20 IN (0, 1)),
        ma30 INTEGER NOT NULL CHECK (ma30 IN (0, 1)),
        ma60 INTEGER NOT NULL CHECK (ma60 IN (0, 1)),
        strokes INTEGER NOT NULL CHECK (strokes IN (0, 1)),
        pivot_zones INTEGER NOT NULL CHECK (pivot_zones IN (0, 1)),
        updated_at TEXT NOT NULL,
        CHECK (last_symbol IS NULL OR last_symbol GLOB 'sh.[0-9][0-9][0-9][0-9][0-9][0-9]' OR last_symbol GLOB 'sz.[0-9][0-9][0-9][0-9][0-9][0-9]')
    )
    """,
)

_REQUIRED_COLUMNS = {
    "app_schema": {"singleton_id", "schema_version", "updated_at"},
    "preferences": {
        "singleton_id",
        "preference_revision",
        "last_symbol",
        "chart_split",
        "show_intraday",
        "ma5",
        "ma10",
        "ma20",
        "ma30",
        "ma60",
        "strokes",
        "pivot_zones",
        "updated_at",
    },
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class AppDatabaseCompatibilityError(RuntimeError):
    """An existing DB is incompatible and must never be silently replaced."""


class AppDatabaseUnavailableError(RuntimeError):
    """The private App database cannot be opened even for reading."""


@dataclass(slots=True)
class AppDatabase:
    connection: sqlite3.Connection
    capability: PreferenceCapability
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def __enter__(self) -> AppDatabase:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _configure(connection: sqlite3.Connection, *, query_only: bool) -> None:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA query_only = {1 if query_only else 0}")


def connect(db_path: PathLike, *, read_only: bool = False) -> sqlite3.Connection:
    """Open the App DB without creating or migrating its schema."""

    path = Path(db_path).expanduser()
    if read_only:
        uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, check_same_thread=False)
    _configure(connection, query_only=read_only)
    return connection


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _validate_columns(connection: sqlite3.Connection) -> None:
    for table, required in _REQUIRED_COLUMNS.items():
        present = _table_columns(connection, table)
        if not required.issubset(present):
            missing = ", ".join(sorted(required - present))
            raise AppDatabaseCompatibilityError(
                f"incompatible {table} table; missing columns: {missing}"
            )


def validate_schema(connection: sqlite3.Connection) -> None:
    _validate_columns(connection)
    row = connection.execute(
        "SELECT schema_version FROM app_schema WHERE singleton_id = 1"
    ).fetchone()
    if row is None or row["schema_version"] != SCHEMA_VERSION:
        observed = None if row is None else row["schema_version"]
        raise AppDatabaseCompatibilityError(
            f"unsupported App database schema version: {observed!r}"
        )


def init_db(connection: sqlite3.Connection) -> None:
    """Idempotently initialize version 1 in one transaction."""

    now = _utc_now()
    defaults = PreferenceValues()
    savepoint = "t0_app_schema_init"
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        for statement in DDL_STATEMENTS:
            connection.execute(statement)
        # Fail closed before any seed write when an existing table happens to
        # reuse one of our names with an incompatible shape.
        _validate_columns(connection)
        existing = connection.execute(
            "SELECT schema_version FROM app_schema WHERE singleton_id = 1"
        ).fetchone()
        if existing is not None and existing["schema_version"] != SCHEMA_VERSION:
            raise AppDatabaseCompatibilityError(
                f"unsupported App database schema version: {existing['schema_version']!r}"
            )
        connection.execute(
            """
            INSERT INTO app_schema(singleton_id, schema_version, updated_at)
            VALUES (1, ?, ?)
            ON CONFLICT(singleton_id) DO NOTHING
            """,
            (SCHEMA_VERSION, now),
        )
        connection.execute(
            """
            INSERT INTO preferences(
                singleton_id, preference_revision, last_symbol, chart_split,
                show_intraday, ma5, ma10, ma20, ma30, ma60, strokes,
                pivot_zones, updated_at
            ) VALUES (1, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(singleton_id) DO NOTHING
            """,
            (
                defaults.last_symbol,
                defaults.layout.chart_split,
                int(defaults.layout.show_intraday),
                int(defaults.layers.ma5),
                int(defaults.layers.ma10),
                int(defaults.layers.ma20),
                int(defaults.layers.ma30),
                int(defaults.layers.ma60),
                int(defaults.layers.strokes),
                int(defaults.layers.pivot_zones),
                now,
            ),
        )
        validate_schema(connection)
    except BaseException:
        connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    else:
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")


def open_app_database(
    db_path: PathLike, *, force_read_only: bool = False
) -> AppDatabase:
    """Open, initialize and capability-probe the private App database.

    If a pre-existing DB cannot be opened for writes, it is reopened read-only
    and remains useful for startup restoration.  Incompatible/corrupt files
    fail closed and are never deleted or recreated.
    """

    path = Path(db_path).expanduser()
    if force_read_only:
        connection = connect(path, read_only=True)
        validate_schema(connection)
        return AppDatabase(
            connection,
            PreferenceCapability(
                readable=True,
                writable=False,
                reason="本地成交与设置文件为只读；偏好和设置不能保存",
            ),
        )

    connection: sqlite3.Connection | None = None
    try:
        connection = connect(path)
        init_db(connection)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("ROLLBACK")
        return AppDatabase(
            connection,
            PreferenceCapability(readable=True, writable=True),
        )
    except AppDatabaseCompatibilityError:
        if connection is not None:
            connection.close()
        raise
    except (OSError, sqlite3.Error) as write_error:
        if connection is not None:
            connection.close()
        if not path.exists():
            raise AppDatabaseUnavailableError(
                f"App database cannot be created: {write_error}"
            ) from write_error
        try:
            read_connection = connect(path, read_only=True)
            validate_schema(read_connection)
        except (OSError, sqlite3.Error, AppDatabaseCompatibilityError) as read_error:
            raise AppDatabaseUnavailableError(
                f"App database cannot be read: {read_error}"
            ) from read_error
        return AppDatabase(
            read_connection,
            PreferenceCapability(
                readable=True,
                writable=False,
                reason=f"本地成交与设置文件不可写；偏好和设置不能保存：{write_error}",
            ),
        )


class AppDatabaseWriteBoundary:
    """Serialize App DB access to a shared cross-thread SQLite connection."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        writable: bool,
        lock: RLock,
    ) -> None:
        self._connection = connection
        self._writable = writable
        self._lock = lock

    def read(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        with self._lock:
            try:
                return operation(self._connection)
            except sqlite3.Error as exc:
                raise PreferencePersistenceError(f"偏好读取失败：{exc}") from exc

    def run(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        if not self._writable:
            raise PreferencesReadOnlyError(
                "本地成交与设置文件为只读；偏好和设置不能保存"
            )
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                result = operation(self._connection)
                self._connection.commit()
                return result
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise PreferencePersistenceError(
                    f"偏好保存失败，未确认持久化：{exc}"
                ) from exc
            except BaseException:
                self._connection.rollback()
                raise


class SqlitePreferenceRepository:
    def __init__(self, database: AppDatabase) -> None:
        self._database = database
        self._writer = AppDatabaseWriteBoundary(
            database.connection,
            writable=database.capability.writable,
            lock=database._lock,
        )

    @property
    def capability(self) -> PreferenceCapability:
        return self._database.capability

    def load(self) -> PreferenceSnapshot:
        def fetch(connection: sqlite3.Connection) -> sqlite3.Row | None:
            return connection.execute(
                "SELECT * FROM preferences WHERE singleton_id = 1"
            ).fetchone()

        row = self._writer.read(fetch)
        if row is None:
            raise PreferencePersistenceError(
                "偏好读取失败：缺少 singleton preference row"
            )
        return self._snapshot_from_row(row)

    @staticmethod
    def _snapshot_from_row(row: sqlite3.Row) -> PreferenceSnapshot:
        return PreferenceSnapshot(
            preference_revision=row["preference_revision"],
            preferences=PreferenceValues(
                last_symbol=row["last_symbol"],
                layout=LayoutPreference(
                    chart_split=row["chart_split"],
                    show_intraday=bool(row["show_intraday"]),
                ),
                layers=LayerPreference(
                    ma5=bool(row["ma5"]),
                    ma10=bool(row["ma10"]),
                    ma20=bool(row["ma20"]),
                    ma30=bool(row["ma30"]),
                    ma60=bool(row["ma60"]),
                    strokes=bool(row["strokes"]),
                    pivot_zones=bool(row["pivot_zones"]),
                ),
            ),
        )

    def save(self, preferences: PreferenceValues) -> PreferenceSnapshot:
        if not self.capability.writable:
            raise PreferencesReadOnlyError(
                self.capability.reason or "本地成交与设置文件为只读"
        )

        def persist(connection: sqlite3.Connection) -> PreferenceSnapshot:
            row = connection.execute(
                "SELECT * FROM preferences WHERE singleton_id = 1"
            ).fetchone()
            if row is None:
                raise PreferencePersistenceError(
                    "偏好保存失败：缺少 singleton preference row"
                )
            current = self._snapshot_from_row(row)
            if current.preferences == preferences:
                return current
            cursor = connection.execute(
                """
                UPDATE preferences
                SET preference_revision = preference_revision + 1,
                    last_symbol = ?, chart_split = ?, show_intraday = ?,
                    ma5 = ?, ma10 = ?, ma20 = ?, ma30 = ?, ma60 = ?,
                    strokes = ?, pivot_zones = ?, updated_at = ?
                WHERE singleton_id = ?
                  AND preference_revision = ?
                """,
                (
                    preferences.last_symbol,
                    preferences.layout.chart_split,
                    int(preferences.layout.show_intraday),
                    int(preferences.layers.ma5),
                    int(preferences.layers.ma10),
                    int(preferences.layers.ma20),
                    int(preferences.layers.ma30),
                    int(preferences.layers.ma60),
                    int(preferences.layers.strokes),
                    int(preferences.layers.pivot_zones),
                    _utc_now(),
                    1,
                    current.preference_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise PreferencePersistenceError(
                    "偏好保存冲突；持久化副本已被其他操作修改"
                )
            return PreferenceSnapshot(current.preference_revision + 1, preferences)

        return self._writer.run(persist)
