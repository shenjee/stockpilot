# StockPilot

StockPilot is a Python repository for stock-focused analysis workflows. It
contains reusable analysis packages, Streamlit apps for local validation, an
Electron T+0 desktop app, installable agent skills, and supporting product docs.

The repository is no longer only a Chan Theory skill prototype. The current
codebase includes Fundamental Screener, shared market data and indicators, and
a T+0 desktop workbench with Live and Replay workflows.

## Current Components

| Area | Path | Role |
| --- | --- | --- |
| Reusable package | `packages/chantheory/` | Project-owned adapter layer around `czsc` for Chan Theory structure analysis. |
| Reusable package | `packages/fundamentalscreener/` | Fundamental Screener core for sector rotation, company ranking, financial quality, valuation, repositories, lineage, CLI payloads, and SQLite sync/schema support. |
| Reusable package | `packages/marketdata/` | Shared market-data provider, runtime path, K-line store, and securities-store infrastructure used by the Chan app, T+0 Live/Replay, and China stock analysis skill. |
| Reusable package | `packages/indicators/` | Schema-aligned MA, BOLL, MACD, volume-average, and intraday VWAP calculations shared by Live and Replay pipelines. |
| Reusable package | `packages/t0assistant/` | T+0 contracts, Live/Replay runtime, shared pipeline, trade records, preferences, and repositories. |
| Local app | `apps/t0-assistant/` | Electron desktop app with a React renderer and an Electron-managed Python service. |
| Local app | `apps/chan-viewer/` | Streamlit debug app for validating `chantheory` chart overlays and structure output. |
| Local app | `apps/fundamental-screener/` | Streamlit frontend for browsing Fundamental Screener outputs and validations. |
| Installable skill | `skills/china-stock-analysis/` | Agent skill that generates factual China A-share daily reports using installable scripts, templates, and references. |
| Product docs | `docs/` | Chan Theory design docs, Fundamental Screener MVP/phase plans, and supporting technical notes. |

## Repository Layout

Top-level repository roles:

- `packages/`: shared Python logic that can be reused by apps, skills, and CLIs.
- `apps/`: Streamlit validation apps and the Electron/React/Python T+0 desktop app.
- `skills/`: installable skill bundles with scripts, references, and config templates.
- `docs/`: product design notes, plans, and technical documentation.
- `pyproject.toml`: editable install metadata, dependency extras, and CLI entry points.
- `CHANGELOG.md` and `CHANGELOG.zh.md`: change history.
- `AGENTS.md`: repo-specific development and test guidance.

Key subdirectories today:

```text
stockpilot/
|-- apps/
|   |-- chan-viewer/
|   |-- fundamental-screener/
|   `-- t0-assistant/
|-- docs/
|   |-- t0assistant/
|   |-- chan_theory_v0.1.md
|   |-- fundamental_screener_mvp.md
|   |-- fundamental_screener_phase_plan.md
|   |-- fundamental_screener_streamlit_frontend_plan.md
|   |-- product_design.md
|   |-- product_design.zh.md
|   |-- software_technical_document.zh.md
|   `-- stock_technical_concepts.zh.md
|-- packages/
|   |-- chantheory/
|   |-- indicators/
|   |-- marketdata/
|   |-- fundamentalscreener/
|   `-- t0assistant/
`-- skills/
    `-- china-stock-analysis/
```

## Development Setup

Use the validated project environment, then install in editable mode from the
repository root:

```bash
source ~/.venvs/czsc/bin/activate
python -m pip install -e ".[dev]"
```

Common dependency sets:

- `python -m pip install -e .` for core packages and the China stock analysis skill runtime
- `python -m pip install -e ".[apps]"` for both Streamlit apps, including `streamlit-searchbox`
- `python -m pip install -e ".[akshare]"` for AkShare-backed sync and master-data build helpers
- `python -m pip install -e ".[dev]"` for the full local development environment

Stable CLI entry points after installation:

```bash
stockpilot-fundamentalscreener sectors --format json
stockpilot-fundamentalscreener screen --format json
```

The existing module invocation also remains valid:

```bash
python -m packages.fundamentalscreener.cli sectors --format json
python -m packages.fundamentalscreener.cli screen --format json
```

## Architecture Notes

- `packages/chantheory/` is the stable project-facing Chan Theory adapter layer.
- `packages/fundamentalscreener/` is the stable core for screening, scoring, quality checks, repositories, CLI output, and sync.
- `packages/marketdata/` is the shared market-data and runtime infrastructure for the Chan app, T+0 Live/Replay, and stock-analysis skill.
- `packages/indicators/` owns reusable, timestamp-aligned technical indicators for standard market bars.
- `packages/t0assistant/` owns T+0 runtime, Replay, trading records, preferences, and repositories.
- Apps should render and orchestrate shared logic, not duplicate screening or structure-analysis rules.
- Skills should keep runtime-specific scripting inside `skills/`, while shared analysis logic stays in `packages/`.

### Compatible Python imports

`marketdata`, `chantheory`, and `fundamentalscreener` are compatibility names for
their respective `packages.*` implementations. Each pair, including lazily
imported submodules, shares module objects, classes, exceptions, and process
state. The canonical class `__module__` uses `packages.*`. Both import orders,
root wrappers, and existing `sys.path` setups that put `packages/` first remain
supported. The neutral `packages/_import_aliases.py` mechanism runs at package
initialization boundaries; it does not make Chan Theory or Fundamental Screener
depend on market data.

The skill's `_standalone_marketdata` copy is independent. Without StockPilot
installed, its wrappers use that copy; with an editable or regular installation,
they use the shared implementation. Standalone tests run in fresh `-I -S`
interpreters against temporary skill directories, without host editable finders.
See [import compatibility verification](docs/import_compatibility.md) for the
entry-point inventory, installation matrix, and regression results.

## Development Entry Points

Use the validated environment from `AGENTS.md` before running Python commands:

```bash
source ~/.venvs/czsc/bin/activate
```

Common entry points:

```bash
streamlit run apps/chan-viewer/app.py
streamlit run apps/fundamental-screener/app.py
python -m packages.fundamentalscreener.cli sectors --format json
python -m packages.fundamentalscreener.cli screen --format json
```

For the T+0 desktop app (after activating Python above):

```bash
cd apps/t0-assistant
npm ci
npm start
```

Electron builds the renderer and owns the local Python service lifecycle.
See the [T+0 app README](apps/t0-assistant/README.md) for renderer-only development,
process permissions, and manual viewport acceptance. The Python `apps` extra
covers Streamlit; desktop dependencies come from the app's npm lockfile.

Common targeted tests (from the repository root):

```bash
python -m unittest discover -s packages/chantheory/tests -p 'test_*.py'
python -m unittest discover -s packages/fundamentalscreener/tests -p 'test_*.py'
python -m unittest discover -s packages/marketdata/tests -p 'test_*.py'
python -m unittest discover -s packages/indicators/tests -p 'test_*.py'
python apps/t0-assistant/tests/run_required_python.py packages/t0assistant/tests
python apps/t0-assistant/tests/run_required_python.py apps/t0-assistant/tests
python -m unittest discover -s apps/chan-viewer/tests -p 'test_*.py'
python -m unittest discover -s apps/fundamental-screener/tests -p 'test_*.py'
python -m unittest discover -s skills/china-stock-analysis/tests -p 'test_*.py'
```

T+0 renderer, Electron host, and contract checks:

```bash
cd apps/t0-assistant
npm test
npm run smoke
```

`npm run smoke` covers renderer typecheck/build, Electron host, and Node contracts;
it does not run the Python suites or App Vitest suite (`npm test` includes the
latter). See the [CI coverage matrix](docs/t0assistant/ci_coverage.md) for the
separate workflow tracks and their exact targets. Commands here are statically
checked against scripts; they are not a claim of live-market or GUI acceptance.
Development rules are in [AGENTS.md](AGENTS.md).

## Runtime Data Boundary

The repository stores source code, docs, and committed test fixtures. Private or
generated runtime data should stay outside the installed skill directory and, in
general, outside the source repository.

Expected runtime layout:

```text
<workspace-or-project-dir>/
`-- stockpilot/
    |-- config/
    |-- db/
    `-- reports/
```

Expected installed skill layout:

```text
<target-skills-dir>/
`-- china-stock-analysis/
    |-- SKILL.md
    |-- scripts/
    |-- references/
    `-- assets/
```

This keeps skill installs immutable and prevents private state from being mixed
into the repo.

## Related Docs

Start with the [documentation authority and evidence guide](docs/documentation_guide.md)
for current contracts, historical handoffs, acceptance evidence, and the repeatable
local-link check. The [container view](docs/architecture/c4-container.md) maps
application processes and shared packages.

- Architecture decisions: [docs/adr/README.md](docs/adr/README.md)
- T+0 Assistant: [docs/t0assistant/t0_assistant_prd.md](docs/t0assistant/t0_assistant_prd.md)
- Chan Theory: [docs/chan_theory_v0.1.md](docs/chan_theory_v0.1.md)
- Chan Theory product notes: [docs/product_design.md](docs/product_design.md)
- Fundamental Screener MVP: [docs/fundamental_screener_mvp.md](docs/fundamental_screener_mvp.md)
- Fundamental Screener phase plan: [docs/fundamental_screener_phase_plan.md](docs/fundamental_screener_phase_plan.md)
- Fundamental Screener app plan: [docs/fundamental_screener_streamlit_frontend_plan.md](docs/fundamental_screener_streamlit_frontend_plan.md)

## Version History

See [CHANGELOG.md](CHANGELOG.md).
