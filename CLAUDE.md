# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`AGENTS.md` (root and per-subdirectory) carries the full, authoritative repo rules. This file summarizes the parts you need up front; when in doubt, defer to `AGENTS.md` and the package READMEs.

## Environment & Commands

This is a Python repo. There is no root `pyproject.toml`; some paths rely on `sys.path` insertion rather than package installation. Always activate the project virtualenv first:

```bash
source ~/.venvs/czsc/bin/activate
```

Run tests (each area is an independent `unittest` suite — run the one covering your change):

```bash
python -m unittest discover -s packages/chantheory/tests -p 'test_*.py'          # adapter layer
python -m unittest discover -s apps/chan-streamlit/tests -p 'test_*.py'          # debug app
python -m unittest discover -s skills/china-stock-analysis/tests -p 'test_*.py'  # skill scripts
```

Run a single test module / case:

```bash
python -m unittest packages.chantheory.tests.test_structure_mapping
python -m unittest packages.chantheory.tests.test_structure_mapping.TestX.test_y
```

Launch the debug app:

```bash
streamlit run apps/chan-streamlit/app.py
```

If Streamlit/Plotly are missing: `python -m pip install streamlit plotly`.

## Big-Picture Architecture

The repo is a **product codebase**, not just a skill collection. Three layers, with a strict dependency direction: `packages/` ← `apps/` and `skills/`; reusable logic lives in `packages/`, never duplicated into `apps/` or `skills/`.

### `packages/chantheory/` — Chan Theory adapter (Phase 2 core)
A thin, project-owned adapter over the `czsc` engine (`czsc==0.10.12`, Python 3.14 runtime). It is **not** a reimplementation of Chan Theory. Pipeline:
- `normalize.py` — standardizes heterogeneous OHLCV input (various timestamp/field names) into ascending bars with stable `symbol`/`timeframe`. Public entry points: `normalize_ohlcv_rows`, `normalize_tracker_klines`.
- `engine.py` — loads `czsc` and runs analysis. **Import-order quirk:** `numpy.typing` must be imported before `czsc` so `rs_czsc`-backed imports initialize; the loader also prefers the **pure-Python** `czsc.py.*` path because the `rs_czsc`-backed `RawBar` shifts date-only daily bars to the prior day 16:00 and breaks Chan structure alignment to trading dates.
- `structure_mapping.py` / `segments.py` — map `czsc` output into project schema (`fractals`, `strokes`, `segments`, `pivot_zones`, `divergences`).
- `candidate_points.py` — structure-only buy/sell candidates (NOT trading instructions).
- `plotting.py` — emits `plot_primitives` (markers/lines/boxes/labels) consumed by apps.
- `describe.py` — short summaries + warnings (unstable tail strokes, insufficient bars).
- `schema.py` / `config.py` — frozen public schema and pinned engine config.
- `adapters.py` / `multi_timeframe.py` / `signals.py` — public `analyze*` entry points.

All public fields are `snake_case`. Visual structure output is the **primary** output; text is only a supporting summary. Schema names are a stable contract — ask before changing public shape.

### `skills/china-stock-analysis/` — installable agent skill
Generates factual (no buy/sell advice) China A-share daily market reports. Installed by copying the directory into a client's skills dir; **runtime data must live outside the install dir** under a configurable `runtime_dir` (default `stockpilot/`) with `config/`, `db/`, `reports/` subdirs.
- `scripts/generate_report.py` is the compatible entry point; `cli.py` / `report_orchestrator.py` drive it.
- `scripts/market_data.py` — data provider layer. Default is the Tencent Finance public API (stdlib-only). **New providers must implement the same provider contract** — do not add HTTP code to `generate_report.py`.
- `scripts/services/` (indicator, kline_data, report_data, rule_evaluator), `scripts/repositories/kline_store.py` (SQLite cache), `scripts/renderers/` (markdown).
- Config templates live in `assets/config_templates/`; actual `config/*.yaml` are runtime/private (gitignored).

### `apps/chan-streamlit/` — Streamlit debug/validation app
A debug surface for `chantheory`, **not** the product UI. `app.py` uses `sys.path.insert` to import from `packages/` and `skills/.../scripts/`. Local structure: `charts/` (axis policy, figure builder, primitive renderer), `services/` (analysis + market), `chan_chart_widget/` (Plotly component), `ui_text.py` (bilingual `zh`/`en` labels — keep both in sync when adding strings). Reads analysis from `chantheory`; never re-implements it here.

### `docs/`
Product and phase design notes — treat as source-of-truth for intent and boundaries (`product_design.md`, `phase2_tasks.md`, `chan_theory_v0.1.md`, plus `fundamental_screener_*` for the screener phase).

## Change Conventions

- Keep reusable Python logic in `packages/`; don't let apps/skills depend on raw `czsc` objects when `chantheory` can provide the contract.
- Preserve Phase 2 boundary: visualization-ready structure output is primary; narrative text is supporting.
- When changing chart output, keep `plot_primitives` and human-readable summaries aligned; check nearby fixtures in `packages/chantheory/tests/fixtures/`.
- Keep public data fields `snake_case`; don't leak unstable engine details into skills/apps/user-facing output.
- User-facing copy in the app must stay bilingual (`zh` + `en`) — update `ui_text.py` for both.
- Ask before changing public schema shape, directory layout, cross-cutting architecture, or introducing new dependencies.
