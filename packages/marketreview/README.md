# marketreview

Daily market review persistence, patch semantics, and read-time metrics for
Stock Pilot.

This package is the single source of truth for the daily market review ledger.
It stores atomic fields in SQLite, applies field-level patch semantics, manages
ladder snapshot modes, and computes derived metrics at read time.

## Status

V1 core is implemented:

- atomic field storage and provenance
- ladder snapshot modes: `snapshot_replace`, `item_patch`, `reset_missing`
- read-time derived metrics (limit-up failure rate, streak aggregates, index
  change, margin/turnover totals)
- trading-day and market-close validation
- three-index auto-fetch orchestration via `packages/marketdata`

V1 does **not** include presentation formatting (yuan to 亿元, ratio to percent).
Skills and apps format `DailyMarketReviewView` for display.

## Public API

Preferred V1 orchestration entry points:

- `resolve_review_trade_date(...)` — resolve or validate a writable trade date
- `auto_patch_indices(...)` — fetch and patch the three index atom pairs
- `missing_atomic_fields(...)` — list fields still missing for a trade date

Repository and pure helpers:

- `MarketReviewRepository` — patch/get/delete reviews and ladder snapshots
- `compute_review_metrics(...)` — read-time derivations without SQLite
- `default_market_review_db_path()` — `<workspace>/stockpilot/db/market_review.sqlite3`

Reuse `packages/marketdata` for trading calendar and index K-line providers.
Do not duplicate market data fetching inside skills.

## Documentation

- PRD: `docs/marketreview/daily_market_review_skill_prd.md`
- Metric appendix: `docs/marketreview/资本市场复盘指标说明与统计口径.md`
- Agent guidance: `packages/marketreview/AGENTS.md`

## Tests

```bash
source ~/.venvs/czsc/bin/activate
python -m unittest discover -s packages/marketreview/tests -p 'test_*.py'
```

Golden fixture: `tests/fixtures/golden_2026_08_21.json`
