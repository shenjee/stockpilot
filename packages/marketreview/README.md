# marketreview

Daily market review persistence and queries for Stock Pilot.

This package stores the daily market review ledger and daily price-limit
events. It behaves like a structured workbook: callers submit values, the
package persists them, and callers can later read, revise, or replace them.
Data acquisition, business validation, derived statistics, and presentation
belong to callers such as the daily-market-review Skill.

## Status

V1 target contract:

- atomic field storage with field-level save
- simple save/update/delete/query operations for price-limit event rows
- one event row per `trade_date + market + code + direction`
- required `streak_height` integer on every event
- structural persistence constraints only: STRICT column types, nullability,
  required fields, uniqueness, and transaction safety
- submitted numeric values are stored as-is; display rounding belongs to callers
- the pre-launch database may be recreated and does not require migration
  compatibility

The package does not judge whether submitted market facts are correct. It does
not calculate streak height, combine reviews with events, or produce display
summaries. Tables are `STRICT` so INTEGER/REAL/TEXT storage classes are
enforced. SQLite does not add business `CHECK` constraints for market,
direction, or limit-rate enumerations.

## Public API

- `MarketReviewRepository.save_review(trade_date, fields)`
- `MarketReviewRepository.get_review(trade_date)`
- `MarketReviewRepository.list_reviews(start, end)`
- `MarketReviewRepository.delete_review(trade_date)`
- `MarketReviewRepository.save_price_limit_events(trade_date, events)`
- `MarketReviewRepository.get_price_limit_events(trade_date)`
- `MarketReviewRepository.list_price_limit_events(start, end)`
- `MarketReviewRepository.delete_price_limit_events(trade_date)`
- `MarketReviewRepository.delete_price_limit_event(trade_date, market, code, direction)`
- `MarketReviewRepository.replace_price_limit_event_direction(trade_date, market, code, old_direction, event)`
- `missing_atomic_fields(...)` — list atomic fields still stored as `None`
- `default_market_review_db_path()` — `<workspace>/stockpilot/db/market_review.sqlite3`

`get_review` returns atomic fields only. `get_price_limit_events` returns stored
event rows. Callers combine and summarize those results.

`save_price_limit_events([])` is a no-op and does not clear the day. Missing
deletes succeed. Same-identity saves overwrite. Records returned by
`get_price_limit_events` can be passed back to `save_price_limit_events`;
`trade_date` on an event mapping is ignored in favor of the call argument.
Duplicate identities in one batch are rejected. Batch saves run in one
transaction so a failure cannot leave a partial batch.
`replace_price_limit_event_direction` deletes the old direction and saves the
replacement event in one transaction; use it when direction must change.

Skills and apps must not access the market-review SQLite database directly.
This package does not expose `auto_patch_indices`, `fetch_index_atoms`,
`IndexFetchResult`, or `compute_review_metrics`.

Conceptually, callers may view event storage as
`limit[trade_date][direction][market + code]`. SQLite stores that structure as
one flat `daily_price_limit_event` row per date, stock, and touched direction.

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
