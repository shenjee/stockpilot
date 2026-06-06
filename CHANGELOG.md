# Changelog

All notable changes to Stock Pilot Skills are documented here.

## [1.0.0] - 2026-06-06

Initial Stock Pilot skill release.

### Added

- Added installable `china-stock-daily-tracker` skill structure.
- Added product design documents in English and Chinese.
- Added factual A-share daily report workflow.
- Added watchlist and portfolio configuration support.
- Added local SQLite daily K-line cache.
- Added basic index, watchlist, portfolio, indicator, strategy-rule, and sector-placeholder report sections.
- Added basic portfolio fields including broker and buy-order records.
- Added runtime directory configuration for private workspace data.
- Added full technical indicator output for the daily report:
  - MA5, MA10, MA20, MA60
  - MACD
  - KDJ
  - RSI
  - BOLL
  - volume versus 20-day average volume
  - volume ratio based on 5-day average volume
  - turnover-rate state
  - amplitude
  - ATR14
- Added structured indicator status descriptions for every tracked stock instead of only the first eight rows.
- Added richer MACD wording: zero-axis position, red/green bar, expansion/shrinkage, and momentum direction.
- Added BOLL position and volatility-width descriptions.
- Added explicit "float shares not configured" turnover-rate status when turnover cannot be calculated.
- Added optional `float_shares` examples to watchlist and portfolio config templates.
- Added `scripts/market_data.py` with a pluggable market data provider boundary.
- Added Tencent Finance as the default market data provider.
- Added `data_source.provider` runtime configuration support.

### Changed

- Refactored `generate_report.py` so report generation no longer owns Tencent-specific HTTP request code.
- Updated documentation to describe provider-based market data architecture.
- Updated the suggested script layout from `fetch_kline.py` to `market_data.py`.
