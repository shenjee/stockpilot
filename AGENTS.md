# AGENTS.md

This file gives coding agents the minimum repo-specific context needed to work safely and efficiently in `stockpilot`.

## Scope

- Root scope: applies to the entire repository unless a deeper directory adds its own `AGENTS.md`.
- Primary language: Python.
- Primary environment: use the project virtual environment at `~/.venvs/czsc`.
- Working directory for commands: run commands from the repository root unless a task clearly targets a subdirectory.

## Quick Start

Activate the validated environment before running Python commands:

```bash
source ~/.venvs/czsc/bin/activate
```

Check the interpreter if needed:

```bash
python --version
which python
```

## Repository Layout

- `packages/chantheory/`: project-owned adapter layer around `czsc` for Chan Theory analysis.
- `packages/marketdata/`: shared market-data provider, runtime path, K-line SQLite, and securities-store infrastructure used by the Chan app and stock-analysis skill.
- `packages/chantheory/tests/`: unit tests and JSON fixtures for the adapter layer.
- `packages/marketdata/tests/`: unit tests for shared market-data infrastructure and compatibility-sensitive storage behavior.
- `packages/fundamentalscreener/`: core package for Fundamental Screener sector/company ranking, financial quality, valuation, data quality, repositories, data sources, SQLite schema, and sync.
- `packages/fundamentalscreener/tests/`: unit tests and fixtures for Fundamental Screener core and CLI payloads.
- `apps/chan-streamlit/`: Streamlit debug app used to validate `chantheory` output and chart overlays.
- `apps/chan-streamlit/tests/`: app-focused tests.
- `apps/fundamental-screener/`: Streamlit validation/product app for browsing Fundamental Screener output.
- `apps/fundamental-screener/tests/`: app/service tests for the screener UI layer.
- `skills/china-stock-analysis/`: installable agent skill with scripts, config templates, and references.
- `skills/china-stock-analysis/tests/`: skill-focused tests kept outside the installable `scripts/` tree.
- `docs/`: product and phase design notes. Treat these as source-of-truth context for intent and boundaries.

## Architecture Rules

- Keep reusable Python logic in `packages/`, not in `apps/` or `skills/`.
- Treat `chantheory` as the stable project-facing interface for Chan Theory analysis.
- Treat `marketdata` as the shared infrastructure boundary for market providers, runtime paths, and cached K-line/securities storage.
- Do not make higher-level code depend directly on raw `czsc` objects when `chantheory` can provide the needed contract.
- Preserve the existing Phase 2 boundary: visualization-ready structure output is primary; narrative text is only supporting output.
- Treat `packages/fundamentalscreener/` as the stable core for Fundamental Screener calculations, data contracts, repositories, sync, and quality gates.
- Do not duplicate sector rotation, company ranking, financial quality, valuation, or anomaly/quality logic in Streamlit apps or skills.
- Fundamental Screener output is for measurable screening and comparison only; do not generate research reports, trading advice, or sector predictions from this layer.
- Prefer updating tests and fixtures close to the code being changed.

## Python And Imports

- The repo now exposes a root `pyproject.toml` for editable installs and dependency extras.
- Some code paths still rely on direct `sys.path` insertion; preserve that behavior unless the task explicitly refactors it.
- When running targeted tests, prefer commands that point directly at the relevant test modules or directories.
- If you introduce new shared logic, place it under `packages/` and keep imports consistent with the existing repo style.

## Common Commands

Activate the environment first:

```bash
source ~/.venvs/czsc/bin/activate
python -m pip install -e ".[dev]"
```

Run `chantheory` tests:

```bash
python -m unittest discover -s packages/chantheory/tests -p 'test_*.py'
```

Run Fundamental Screener core tests:

```bash
python -m unittest discover -s packages/fundamentalscreener/tests -p 'test_*.py'
```

Run shared market-data tests:

```bash
python -m unittest discover -s packages/marketdata/tests -p 'test_*.py'
```

Run the Chan Streamlit app smoke tests:

```bash
python -m unittest discover -s apps/chan-streamlit/tests -p 'test_*.py'
```

Run the Fundamental Screener app tests:

```bash
python -m unittest discover -s apps/fundamental-screener/tests -p 'test_*.py'
```

Run the stock analysis skill script tests:

```bash
python -m unittest discover -s skills/china-stock-analysis/tests -p 'test_*.py'
```

Start the debug app:

```bash
streamlit run apps/chan-streamlit/app.py
```

Start the Fundamental Screener app:

```bash
streamlit run apps/fundamental-screener/app.py
```

Run the Fundamental Screener CLI:

```bash
python -m packages.fundamentalscreener.cli sectors --format json
python -m packages.fundamentalscreener.cli screen --format json
```

If app dependencies are missing in the active environment:

```bash
python -m pip install -e ".[apps]"
```

## Change Guidelines

- Make focused changes. Avoid broad refactors unless the task requires them.
- Preserve stable schema names and public adapter outputs unless the task explicitly changes the contract.
- Keep all public data fields in `snake_case`, matching the existing `chantheory` contract.
- Avoid leaking unstable engine details into skills, apps, or user-facing output.
- When changing chart output, verify that `plot_primitives` and any human-readable summaries remain aligned.
- When changing structure mapping, check nearby fixtures in `packages/chantheory/tests/fixtures/`.
- When changing Fundamental Screener output, keep CLI JSON payloads, app tables/charts, warnings, and lineage metadata aligned.
- When changing shared market-data infrastructure, keep the app imports, skill compatibility wrappers, and bundled `securities_master.json` behavior aligned.
- When changing screener calculations or repositories, check nearby fixtures in `packages/fundamentalscreener/tests/fixtures/`.

## Testing Expectations

- For changes under `packages/chantheory/`, run the `chantheory` test suite first.
- For changes under `packages/fundamentalscreener/`, run the Fundamental Screener core test suite first.
- For changes under `packages/marketdata/`, run the shared market-data test suite first.
- For changes under `apps/chan-streamlit/`, run the relevant tests under `apps/chan-streamlit/tests/` and, if relevant, launch the app for a quick manual smoke check.
- For changes under `apps/fundamental-screener/`, run the relevant tests under `apps/fundamental-screener/tests/` and, if relevant, launch the app for a quick manual smoke check.
- For changes under `skills/china-stock-analysis/scripts/`, run the relevant tests under `skills/china-stock-analysis/tests/`.
- Add or update tests when behavior changes or regression risk is non-trivial.

## Data And Domain Notes

- Current validation is centered on China A-share day-bar data.
- `chantheory` currently uses `czsc==0.10.12` as the validated engine baseline according to the package README.
- The project intentionally keeps candidate buy/sell points as structure-only candidates, not trading instructions.
- The Streamlit app is a validation/debug tool, not the long-term product UI.
- Fundamental Screener uses A-share public market/fundamental data to rank sectors and companies for further research. It should remain a quantitative screening tool, not a report generator or investment adviser.
- Real-market Fundamental Screener ingestion belongs in `packages/fundamentalscreener/data_sources/` and sync/repository code; deterministic tests should use fixtures or injected fakes.
- `packages/fundamentalscreener/data_sources/base.py` defines the data source contract; `akshare_source.py` is the real public-data source; `fake_source.py` supports injected deterministic tests and smoke paths.
- `packages/fundamentalscreener/percentile.py`, `formatting.py`, and `sqlite_schema.py` are shared support modules for scoring helpers, CLI/app formatting, and SQLite schema setup.

## Agent Workflow

- Read `README.md` first for repo-level context.
- Read `packages/chantheory/README.md` before changing adapter behavior or schema expectations.
- Read `apps/chan-streamlit/README.md` before changing the debug app.
- Read `docs/fundamental_screener_mvp.md`, `docs/fundamental_screener_phase_plan.md`, and `docs/fundamental_screener_streamlit_frontend_plan.md` before changing Fundamental Screener product/core/app behavior.
- Read `apps/fundamental-screener/README.md` before changing the Fundamental Screener Streamlit app.
- Prefer small, reviewable patches.
- After edits, run the smallest meaningful test command that covers the change.

## When Unsure

- Ask before changing public schema shape, directory layout, or cross-cutting architecture.
- Ask before introducing new dependencies that are not already implied by the current environment.
- Call out any mismatch between repo docs and observed runtime behavior so it can be corrected explicitly.
