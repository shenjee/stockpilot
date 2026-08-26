# marketreview

Daily market review persistence and queries for Stock Pilot.

This package stores the daily market review ledger and daily price-limit
events. It behaves like a structured workbook: callers submit values, the
package persists them, and callers can later read, revise, or replace them.
Data acquisition, business validation, derived statistics, and presentation
belong to callers such as the daily-market-review Skill.

## Status

V1 target contract:

- atomic field storage
- simple save/update/delete/query operations for price-limit event rows
- one event for every eligible stock that touched an upper or lower price limit
- required `streak_height` on every event: the event-date streak count for an
  effective limit-up, otherwise `0`
- structural persistence constraints only: types/nullability, required row
  identity, uniqueness, and transaction safety
- the pre-launch database may be recreated and does not require migration
  compatibility

The package does not judge whether submitted market facts are correct. The
Skill validates market, code, trading date, security universe, price-limit
rate, and other acquisition rules before writing. If the user supplies a streak
count, the Skill uses it; otherwise the Skill calculates it from the previous
trading day's stored event before writing. SQLite does not repeat those checks
or calculate streak height. Skills and apps read stored rows, calculate the
required summaries, and format them for display.

## Public API

Preferred V1 entry points:

- `missing_atomic_fields(...)` — list fields still missing for a trade date

Repository and pure helpers:

- `MarketReviewRepository` — patch/get/delete reviews and price-limit events
- `default_market_review_db_path()` — `<workspace>/stockpilot/db/market_review.sqlite3`

Skills and apps acquire and validate data through `packages/marketdata`, APIs,
network search, user input, or other available means, then write through this
package. They must not access the market-review SQLite database directly. This
package does not expose `auto_patch_indices`, `fetch_index_atoms`, or
`IndexFetchResult`.

Conceptually, callers may view event storage as
`limit[trade_date][direction][market + code]`. SQLite stores that structure as
one flat `daily_price_limit_event` row per date, stock, and touched direction;
there is no separate snapshot or completeness row.

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
