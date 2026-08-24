# packages/marketreview

Daily market review persistence, patch semantics, and read-time metrics.

## Ownership

- Owns SQLite schema, atomic field storage, ladder snapshot modes, and derived
  metric calculation for the daily market review product.
- Reuses `packages/marketdata` for trading calendar and future quote providers.
- Does not own Skill presentation copy or external HTTP ingestion beyond what
  `marketdata` exposes.

## Public entry points

- `resolve_review_trade_date` — resolve or validate a writable trade date.
- `auto_patch_indices` — fetch and patch the three index atom pairs.
- `missing_atomic_fields` — list fields still missing for a trade date.
- `MarketReviewRepository` — patch/get/delete reviews and ladder snapshots.
- `compute_review_metrics` — pure read-time derivations for tests and callers.
- `default_market_review_db_path` — `<workspace>/stockpilot/db/market_review.sqlite3`.

## Tests

```bash
python -m unittest discover -s packages/marketreview/tests -p 'test_*.py'
```
