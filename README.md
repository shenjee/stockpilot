# StockPilot

StockPilot is a Python repository for stock-focused analysis workflows. It
contains reusable analysis packages, Streamlit apps for local validation and
product exploration, installable agent skills, and supporting product docs.

The repository is no longer only a Chan Theory skill prototype. The current
codebase also includes a broader Fundamental Screener core, app, CLI, SQLite
support, and product planning docs.

## Current Components

| Area | Path | Role |
| --- | --- | --- |
| Reusable package | `packages/chantheory/` | Project-owned adapter layer around `czsc` for Chan Theory structure analysis. |
| Reusable package | `packages/fundamentalscreener/` | Fundamental Screener core for sector rotation, company ranking, financial quality, valuation, repositories, lineage, CLI payloads, and SQLite sync/schema support. |
| Reusable package | `packages/marketdata/` | Shared market-data provider, runtime path, K-line store, and securities-store infrastructure used by the Chan Streamlit app and China stock analysis skill. |
| Local app | `apps/chan-streamlit/` | Streamlit debug app for validating `chantheory` chart overlays and structure output. |
| Local app | `apps/fundamental-screener/` | Streamlit frontend for browsing Fundamental Screener outputs and validations. |
| Installable skill | `skills/china-stock-analysis/` | Agent skill that generates factual China A-share daily reports using installable scripts, templates, and references. |
| Product docs | `docs/` | Chan Theory design docs, Fundamental Screener MVP/phase plans, and supporting technical notes. |

## Repository Layout

Top-level repository roles:

- `packages/`: shared Python logic that can be reused by apps, skills, and CLIs.
- `apps/`: local Streamlit apps for validation, debugging, and product iteration.
- `skills/`: installable skill bundles with scripts, references, and config templates.
- `docs/`: product design notes, plans, and technical documentation.
- `pyproject.toml`: editable install metadata, dependency extras, and CLI entry points.
- `CHANGELOG.md` and `CHANGELOG.zh.md`: change history.
- `AGENTS.md`: repo-specific development and test guidance.

Key subdirectories today:

```text
stockpilot/
|-- apps/
|   |-- chan-streamlit/
|   `-- fundamental-screener/
|-- docs/
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
|   |-- marketdata/
|   `-- fundamentalscreener/
`-- skills/
    `-- china-stock-analysis/
```

## Development Setup

Create or activate a Python environment, then install the repository in editable
mode from the repo root:

```bash
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
- `packages/marketdata/` is the shared market-data and runtime infrastructure for the Chan app and stock-analysis skill.
- Apps should render and orchestrate shared logic, not duplicate screening or structure-analysis rules.
- Skills should keep runtime-specific scripting inside `skills/`, while shared analysis logic stays in `packages/`.

## Development Entry Points

Use the validated environment from `AGENTS.md` before running Python commands:

```bash
source ~/.venvs/czsc/bin/activate
```

Common entry points:

```bash
streamlit run apps/chan-streamlit/app.py
streamlit run apps/fundamental-screener/app.py
python -m packages.fundamentalscreener.cli sectors --format json
python -m packages.fundamentalscreener.cli screen --format json
```

Common targeted tests:

```bash
python -m unittest discover -s packages/chantheory/tests -p 'test_*.py'
python -m unittest discover -s packages/fundamentalscreener/tests -p 'test_*.py'
python -m unittest discover -s packages/marketdata/tests -p 'test_*.py'
python -m unittest discover -s apps/chan-streamlit/tests -p 'test_*.py'
python -m unittest discover -s apps/fundamental-screener/tests -p 'test_*.py'
python -m unittest discover -s skills/china-stock-analysis/tests -p 'test_*.py'
```

For the fuller command matrix and repo-specific workflow guidance, see
[AGENTS.md](AGENTS.md).

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

- Chan Theory: [docs/chan_theory_v0.1.md](docs/chan_theory_v0.1.md)
- Chan Theory product notes: [docs/product_design.md](docs/product_design.md)
- Fundamental Screener MVP: [docs/fundamental_screener_mvp.md](docs/fundamental_screener_mvp.md)
- Fundamental Screener phase plan: [docs/fundamental_screener_phase_plan.md](docs/fundamental_screener_phase_plan.md)
- Fundamental Screener app plan: [docs/fundamental_screener_streamlit_frontend_plan.md](docs/fundamental_screener_streamlit_frontend_plan.md)

## Version History

See [CHANGELOG.md](CHANGELOG.md).
