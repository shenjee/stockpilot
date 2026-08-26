# packages/marketreview

Daily market review persistence and queries.

## Ownership

- Owns the SQLite schema plus simple save, update, delete, and query operations
  for atomic review fields and daily price-limit events.
- Persists caller-provided values as data. Do not rewrite submitted numbers
  (for example, do not round money fields). It does not verify whether a stock
  really touched a price limit, whether a streak height is factually correct,
  or whether caller-provided events are complete.
- Does not own external data acquisition, business validation, acquisition
  orchestration, derived presentation metrics, or Skill presentation copy.
  The Skill validates acquired data before writing and formats stored data
  after reading.
- Database constraints should remain structural: STRICT column types,
  nullability, required row identity, uniqueness, and transaction safety. Do
  not add business `CHECK` constraints for market, direction, limit-rate,
  trading day, ST status, or streak-height calculation.

## Public entry points

- `MarketReviewRepository` — save/get/delete reviews and price-limit events.
- `missing_atomic_fields` — list atomic fields still stored as `None`.
- `default_market_review_db_path` — `<workspace>/stockpilot/db/market_review.sqlite3`.

Do not add `auto_patch_indices`, `fetch_index_atoms`, `IndexFetchResult`,
`compute_review_metrics`, or other fetch/stat orchestration to this package.

## Tests

```bash
python -m unittest discover -s packages/marketreview/tests -p 'test_*.py'
```
