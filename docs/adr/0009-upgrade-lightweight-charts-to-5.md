# ADR 0009: Upgrade T+0 Chart Engine To Lightweight Charts 5.2.0

- Status: Accepted
- Date: 2026-08-01
- Owners: T+0 Assistant frontend
- Supersedes: the Lightweight Charts **4.x version choice** in
  [`0005-t0-chart-engine-and-logical-time-axis.md`](./0005-t0-chart-engine-and-logical-time-axis.md)
- Related issues: #124 (blocks #123 dual-format acceptance)
- Evidence: `apps/t0-assistant` typecheck/build/tests; real LC tests under
  `apps/t0-assistant/tests/chart-*-lc.test.mjs`

## Context

ADR 0005 selected Lightweight Charts with project-owned logical indices. The
production app then pinned `lightweight-charts 4.2.3`.

Issue #123 requires two formatting roles for the same numeric value:

- ordinary Y-axis tick labels: `< 100` → two decimals, `>= 100` → integer;
- current-price and crosshair labels: always two decimals.

LC 4.2.3 exposes only one custom price formatter path for ticks, current price,
and crosshair labels. Meeting both rules therefore requires LC 5.x
`tickmarksFormatter` / `tickmarksPriceFormatter`.

## Decision

Upgrade and lock T+0 Assistant to **`lightweight-charts 5.2.0`** (stable
`latest`, not RC/preview), and migrate all v4-only APIs without compatibility
shims:

- series creation: `addSeries(CandlestickSeries|LineSeries|HistogramSeries, …)`;
- trade markers: `createSeriesMarkers(series, markers)`;
- project primitives: `IPrimitivePaneView` / `IPrimitivePaneRenderer` /
  `PrimitivePaneViewZOrder`;
- price/MACD dual format through series `priceFormat.formatter` +
  `priceFormat.tickmarksFormatter`, with matching chart
  `localization.priceFormatter` + `localization.tickmarksPriceFormatter`;
- when a price/MACD scale range reaches `abs >= 100`, set series
  `priceFormat.minMove = 1` so LC generates integer tick positions (step ≥ 1)
  that stay aligned with integer tick labels; ranges below 100 keep
  `minMove = 0.01`.

ADR 0005's non-version decisions remain in force: project-owned logical indices,
chart-group follow/manual state machine, and viewport ownership outside the
library.

## Consequences

- Bundle and API surface follow LC 5.x; renderer/tests must not call v4
  `add*Series` or `series.setMarkers()`.
- #123 can close only after real LC tests prove tick `100` and exact `100.00`
  coexist on the LC formatter path.
- Spike/ADR docs that still say “LC 4.x baseline” must point here for the
  current engine version.
- LC 5.x enables `layout.attributionLogo` (default `true`). T+0 Assistant keeps
  it enabled so the TradingView attribution link remains on the chart pane;
  headless LC tests must stub `getComputedStyle(...).color` as `rgb`/`rgba`
  because ColorParser now resolves theme colors through the browser.
- Production renderer build after the upgrade
  (`apps/t0-assistant`, Vite production): `dist/assets/index-*.js` ≈ 485.5 kB
  (gzip ≈ 148.0 kB). The increase versus the earlier 4.x chart spike observation
  is expected for the full app bundle that now includes LC 5.2.0.
