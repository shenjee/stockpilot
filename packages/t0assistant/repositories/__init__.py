"""T+0 Assistant-owned persistence adapters."""

from .app_database import (
    AppDatabase,
    AppDatabaseCompatibilityError,
    AppDatabaseUnavailableError,
    AppDatabaseWriteBoundary,
    DDL_STATEMENTS,
    INDEX_STATEMENTS,
    SCHEMA_VERSION,
    SqlitePreferenceRepository,
    connect,
    init_db,
    open_app_database,
    validate_schema,
)
from .trading import (
    FeePlanRecord,
    RepositoryConflictError,
    RepositoryNotFoundError,
    RepositoryPersistenceError,
    RepositoryReadOnlyError,
    SqliteFeePlanRepository,
    SqliteTradeRepository,
    TransferFeeSide,
)

__all__ = [
    "AppDatabase",
    "AppDatabaseCompatibilityError",
    "AppDatabaseUnavailableError",
    "AppDatabaseWriteBoundary",
    "DDL_STATEMENTS",
    "FeePlanRecord",
    "INDEX_STATEMENTS",
    "RepositoryConflictError",
    "RepositoryNotFoundError",
    "RepositoryPersistenceError",
    "RepositoryReadOnlyError",
    "SCHEMA_VERSION",
    "SqliteFeePlanRepository",
    "SqlitePreferenceRepository",
    "SqliteTradeRepository",
    "TransferFeeSide",
    "connect",
    "init_db",
    "open_app_database",
    "validate_schema",
]
