# packages/marketreview

Daily market review persistence and queries.

## Ownership

- Owns the SQLite schema plus simple save, update, delete, and query operations
  for atomic review fields and daily price-limit events.
- Persists caller-provided values as data. It does not verify whether a stock
  really touched a price limit, whether a streak height is factually correct,
  or whether caller-provided events are complete.
- Does not own external data acquisition, business validation, acquisition
  orchestration, derived presentation metrics, or Skill presentation copy.
  The Skill validates acquired data before writing and formats stored data
  after reading.
- Database constraints should remain structural: column types/nullability,
  required row identity, uniqueness, and transaction safety. Do not duplicate
  Skill-level market, security-universe, trading-date, price-limit-rate, or
  streak-consistency checks in the repository.

## Public entry points

- `missing_atomic_fields` — list fields still missing for a trade date.
- `MarketReviewRepository` — patch/get/delete reviews and price-limit events.
- `default_market_review_db_path` — `<workspace>/stockpilot/db/market_review.sqlite3`.

Do not add `auto_patch_indices`, `fetch_index_atoms`, `IndexFetchResult`, or
other fetch-and-persist orchestration to this package. Index fetch failures are
handled by callers omitting failed fields from the field-level patch.

## Tests

```bash
python -m unittest discover -s packages/marketreview/tests -p 'test_*.py'
```
