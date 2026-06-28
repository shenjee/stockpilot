# chan-streamlit

`apps/chan-streamlit/` is the Phase 2 Streamlit debug app for validating `chantheory` output.

It is intended for:

- checking mapped `fractals`, `strokes`, `segments`, and `pivot_zones`
- overlaying `plot_primitives` on top of K-line data
- comparing warnings, summaries, and raw JSON in one place
- verifying that UI output matches the stable project schema

## Features

- symbol, market, timeframe, and date-range controls
- `max_bi_num` and `min_bars` parameter controls
- layer toggles for Fractal, Stroke, Segment, Pivot Zone, Divergence, and Alerts
- K-line chart with `plot_primitives` overlay
- warnings and diagnostics panels
- raw JSON inspection view

## Run

Install the app dependencies from the repo root:

```bash
python -m pip install -e ".[apps]"
```

This extra includes:

- `streamlit`
- `plotly`
- `streamlit-searchbox`

Start the app from the repo root:

```bash
streamlit run apps/chan-streamlit/app.py
```

## Notes

- The app imports `packages/` and `skills/china-stock-analysis/scripts/` directly from the repo.
- The current data provider path uses `TencentStockDataProvider`.
- `divergences` now surface as `plot_primitives` overlays when the project stroke-and-pivot divergence rule is satisfied.
