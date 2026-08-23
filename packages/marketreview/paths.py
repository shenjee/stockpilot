"""Runtime paths for market review data."""

from __future__ import annotations

from pathlib import Path

from packages.marketdata.runtime_paths import RuntimePaths


def default_market_review_db_path(runtime: RuntimePaths | None = None) -> Path:
    paths = runtime or RuntimePaths()
    paths.ensure_dirs()
    return paths.db_dir / "market_review.sqlite3"
