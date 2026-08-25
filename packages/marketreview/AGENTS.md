# packages/marketreview

Daily market review persistence, patch semantics, and read-time metrics.

## Ownership

- Owns SQLite schema, atomic field storage, ladder write modes, and derived
  metric calculation for the daily market review product.
- Reuses `packages/marketdata` for trading-calendar validation.
- Does not own external data acquisition, acquisition orchestration, or Skill
  presentation copy. Callers acquire data and write it through this package.

## Public entry points

- `resolve_review_trade_date` — resolve or validate a writable trade date.
- `missing_atomic_fields` — list fields still missing for a trade date.
- `MarketReviewRepository` — patch/get/delete reviews and ladder details.
- `compute_review_metrics` — pure read-time derivations for tests and callers.
- `default_market_review_db_path` — `<workspace>/stockpilot/db/market_review.sqlite3`.

Do not add `auto_patch_indices`, `fetch_index_atoms`, `IndexFetchResult`, or
other fetch-and-persist orchestration to this package. Index fetch failures are
handled by callers omitting failed fields from the field-level patch.

## Tests

```bash
python -m unittest discover -s packages/marketreview/tests -p 'test_*.py'
```
