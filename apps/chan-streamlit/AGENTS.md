# AGENT.md

This file adds app-specific guidance for work inside `apps/chan-streamlit/`. It inherits the root `AGENT.md`.

## Scope

- Applies to the Streamlit debug app, its tests, sample data, and local widget assets.
- This app is a validation and debugging surface for `chantheory`, not the long-term product UI.

## App Role

- Keep the app focused on validating mapped structure output, warnings, summaries, and chart overlays.
- Treat `chantheory` as the source of analysis truth.
- Avoid moving core analysis logic into the app when it belongs in `packages/chantheory/`.

## Local Architecture

- `app.py` inserts repo-local import paths for `packages/` and the app directory itself.
- The app depends on:
  - `chantheory` for analysis
  - `marketdata` for shared market data, runtime paths, and K-line/securities storage
  - Plotly for chart rendering
  - `streamlit-searchbox` for symbol lookup
  - the local `chan_chart_widget/` component for chart interaction

Keep those boundaries clear when editing.

## Change Rules

- Preserve bilingual behavior for user-facing copy in `TEXT` unless the task intentionally changes localization coverage.
- Keep English and Chinese labels aligned when adding new UI strings.
- Prefer small helper functions over large inline blocks when UI formatting logic grows.
- Do not duplicate normalization or structure-mapping logic in the app.
- If chart behavior changes, verify both rendering and test expectations.

## Testing

Run the app test from the repo root with the validated environment active:

```bash
source ~/.venvs/czsc/bin/activate
python -m unittest discover -s apps/chan-streamlit/tests -p 'test_*.py'
```

For manual validation, launch the app:

```bash
source ~/.venvs/czsc/bin/activate
streamlit run apps/chan-streamlit/app.py
```

If app dependencies are missing:

```bash
python -m pip install -e ".[apps]"
```

## Test Focus

Current tests cover chart-axis and row-order behavior. If your change affects:

- axis selection or tick rendering, update `test_app.py`
- row ordering or chart keys, update `test_app.py`
- localized copy, manually verify both `zh` and `en`
- widget integration or overlay behavior, prefer a small focused test when possible and do a manual smoke check

## Data Notes

- Sample input lives under `sample_data/`.
- The app currently supports day and selected minute timeframes in the UI.
- `divergences` depend on the project stroke-and-pivot divergence rule and should appear as overlays when the structure matches.

## Review Checklist

- App still reads analysis from `chantheory` rather than re-implementing it.
- Chinese and English UI text stay in sync.
- Chart behavior remains consistent for day and minute timeframes.
- Automated test coverage still matches the changed behavior.
