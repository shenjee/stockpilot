"""T+0 Assistant-owned persistence adapters."""

from .app_database import (
    AppDatabase,
    AppDatabaseCompatibilityError,
    AppDatabaseUnavailableError,
    AppDatabaseWriteBoundary,
    DDL_STATEMENTS,
    SCHEMA_VERSION,
    SqlitePreferenceRepository,
    connect,
    init_db,
    open_app_database,
    validate_schema,
)

__all__ = [
    "AppDatabase",
    "AppDatabaseCompatibilityError",
    "AppDatabaseUnavailableError",
    "AppDatabaseWriteBoundary",
    "DDL_STATEMENTS",
    "SCHEMA_VERSION",
    "SqlitePreferenceRepository",
    "connect",
    "init_db",
    "open_app_database",
    "validate_schema",
]
