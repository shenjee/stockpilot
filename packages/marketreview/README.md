# marketreview

Daily market review persistence, patch semantics, and read-time metrics for
Stock Pilot.

This package is the single source of truth for the daily market review ledger
and daily price-limit events. It stores atomic review fields plus the currently
known events, applies patch semantics, and derives limit-up, limit-down,
first-board, and streak metrics from the stored timeline. Data acquisition is
owned by callers such as the daily-market-review Skill.

## Status

V1 target contract:

- atomic field storage
- date/direction `direction_replace` and unrestricted event-level `item_patch`;
  replacement is an event-list operation and does not create a completeness
  snapshot
- one event for every eligible stock that touched an upper or lower price limit
- optional `streak_height_anchor` on an effective limit-up event, representing
  that stock's actual streak height on the event date
- read-time limit-up/down counts, failure rate, first-board and streak
  aggregates, index change, and margin/turnover totals
- trading-day and market-close validation
- transactional persistence; the pre-launch database may be recreated and does
  not require migration compatibility

V1 does **not** include presentation formatting (yuan to 亿元, ratio to percent).
Skills and apps format `DailyMarketReviewView` for display.

## Public API

Preferred V1 entry points:

- `resolve_review_trade_date(...)` — resolve or validate a writable trade date
- `missing_atomic_fields(...)` — list fields still missing for a trade date

Repository and pure helpers:

- `MarketReviewRepository` — patch/get/delete reviews and price-limit events
- `compute_review_metrics(...)` — read-time derivations without SQLite
- `default_market_review_db_path()` — `<workspace>/stockpilot/db/market_review.sqlite3`

Skills and apps acquire data through `packages/marketdata`, APIs, network
search, user input, or other available means, then write through this package.
They must not access the market-review SQLite database directly. This package
does not expose `auto_patch_indices`, `fetch_index_atoms`, or `IndexFetchResult`.

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
