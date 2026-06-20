# AGENT.md

This file gives coding agents the minimum repo-specific context needed to work safely and efficiently in `stockpilot`.

## Scope

- Root scope: applies to the entire repository unless a deeper directory adds its own `AGENT.md`.
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

- `packages/chantheory/`: project-owned adapter layer around `czsc`. This is the main reusable Python package in the repo.
- `packages/chantheory/tests/`: unit tests and JSON fixtures for the adapter layer.
- `apps/chan-streamlit/`: Streamlit debug app used to validate `chantheory` output and chart overlays.
- `apps/chan-streamlit/tests/`: app-focused tests.
- `skills/china-stock-analysis/`: installable agent skill with scripts, config templates, and references.
- `skills/china-stock-analysis/tests/`: skill-focused tests kept outside the installable `scripts/` tree.
- `docs/`: product and phase design notes. Treat these as source-of-truth context for intent and boundaries.

## Architecture Rules

- Keep reusable Python logic in `packages/`, not in `apps/` or `skills/`.
- Treat `chantheory` as the stable project-facing interface for Chan Theory analysis.
- Do not make higher-level code depend directly on raw `czsc` objects when `chantheory` can provide the needed contract.
- Preserve the existing Phase 2 boundary: visualization-ready structure output is primary; narrative text is only supporting output.
- Prefer updating tests and fixtures close to the code being changed.

## Python And Imports

- The repo does not currently expose a root packaging file such as `pyproject.toml`.
- Some code paths rely on direct `sys.path` insertion instead of installation as a package.
- When running targeted tests, prefer commands that point directly at the relevant test modules or directories.
- If you introduce new shared logic, place it under `packages/` and keep imports consistent with the existing repo style.

## Common Commands

Activate the environment first:

```bash
source ~/.venvs/czsc/bin/activate
```

Run `chantheory` tests:

```bash
python -m unittest discover -s packages/chantheory/tests -p 'test_*.py'
```

Run the Streamlit app smoke tests:

```bash
python -m unittest discover -s apps/chan-streamlit/tests -p 'test_*.py'
```

Run the stock analysis skill script tests:

```bash
python -m unittest discover -s skills/china-stock-analysis/tests -p 'test_*.py'
```

Start the debug app:

```bash
streamlit run apps/chan-streamlit/app.py
```

If Streamlit or Plotly are missing in the active environment:

```bash
python -m pip install streamlit plotly
```

## Change Guidelines

- Make focused changes. Avoid broad refactors unless the task requires them.
- Preserve stable schema names and public adapter outputs unless the task explicitly changes the contract.
- Keep all public data fields in `snake_case`, matching the existing `chantheory` contract.
- Avoid leaking unstable engine details into skills, apps, or user-facing output.
- When changing chart output, verify that `plot_primitives` and any human-readable summaries remain aligned.
- When changing structure mapping, check nearby fixtures in `packages/chantheory/tests/fixtures/`.

## Testing Expectations

- For changes under `packages/chantheory/`, run the `chantheory` test suite first.
- For changes under `apps/chan-streamlit/`, run the relevant tests under `apps/chan-streamlit/tests/` and, if relevant, launch the app for a quick manual smoke check.
- For changes under `skills/china-stock-analysis/scripts/`, run the relevant tests under `skills/china-stock-analysis/tests/`.
- Add or update tests when behavior changes or regression risk is non-trivial.

## Data And Domain Notes

- Current validation is centered on China A-share day-bar data.
- `chantheory` currently uses `czsc==0.10.12` as the validated engine baseline according to the package README.
- The project intentionally keeps candidate buy/sell points as structure-only candidates, not trading instructions.
- The Streamlit app is a validation/debug tool, not the long-term product UI.

## Agent Workflow

- Read `README.md` first for repo-level context.
- Read `packages/chantheory/README.md` before changing adapter behavior or schema expectations.
- Read `apps/chan-streamlit/README.md` before changing the debug app.
- Prefer small, reviewable patches.
- After edits, run the smallest meaningful test command that covers the change.

## When Unsure

- Ask before changing public schema shape, directory layout, or cross-cutting architecture.
- Ask before introducing new dependencies that are not already implied by the current environment.
- Call out any mismatch between repo docs and observed runtime behavior so it can be corrected explicitly.
