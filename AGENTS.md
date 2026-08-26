# AGENTS.md

Repository-wide instructions for coding agents working on `stockpilot`. A deeper
`AGENTS.md` may add or override guidance for its directory.

## Environment

- Primary language: Python.
- Use the validated project environment at `~/.venvs/czsc`.
- Run commands from the repository root unless the task clearly targets a
  subdirectory.
- Activate the environment before running Python commands:

```bash
source ~/.venvs/czsc/bin/activate
```

## Architecture

- Reusable domain and infrastructure logic belongs in `packages/`, not in
  `apps/` or `skills/`.
- Apps are UI, orchestration, and validation layers; do not duplicate package
  business logic in them.
- Skills should use stable package interfaces instead of copying package logic.
- `packages/chantheory/` owns the stable project-facing Chan Theory contract.
  Do not expose raw `czsc` objects when its schema can represent the result.
- `packages/marketdata/` owns shared market providers, runtime paths, calendars,
  K-line storage, and securities storage.
- `packages/indicators/` owns reusable, timestamp-aligned technical indicators.
- `packages/fundamentalscreener/` owns screening calculations, scoring,
  valuation, repositories, sync, lineage, and data-quality rules.
- `packages/marketreview/` owns daily market review persistence, patch
  semantics, complete daily price-limit event snapshots, first-board/streak
  derivation, and read-time metrics. Callers acquire data and write it through
  this package.
- `packages/t0assistant/` owns reusable T+0 domain logic, contracts, replay,
  runtime, preferences, repositories, and trading abstractions.
- Preserve public schemas and package contracts unless the task explicitly
  changes them. Keep public data fields in `snake_case`.

## Domain Boundaries

- Chan Theory output is visualization-ready structure data; narrative text is
  supporting output. Candidate buy/sell points are structural candidates, not
  trading instructions.
- Fundamental Screener is for measurable screening and comparison. Do not turn
  it into a research-report generator, investment adviser, or sector predictor.
- Daily Market Review is a factual daily ledger for market-wide metrics. Do not
  turn it into trading advice, sector forecasts, or a research-report generator.
- Keep Live and Replay consumers aligned with shared market-data, indicator, and
  T+0 contracts rather than creating parallel calculations in an app.

## Change Rules

- Prefer focused, reviewable changes; avoid broad refactors unless required.
- Preserve compatibility-sensitive behavior unless the task explicitly changes
  it, including existing direct `sys.path` setup where still used.
- Keep CLI payloads, app presentation, warnings, and lineage metadata aligned
  when changing shared output contracts.
- Add or update tests when behavior changes or regression risk is non-trivial.
- Keep tests and fixtures close to the code they cover.
- After edits, run the smallest relevant test target under the nearest `tests/`
  directory; expand coverage when the change crosses package or app boundaries.
- Call out conflicts between documentation and observed runtime behavior.

## Documentation

Read documentation only when the task touches that area:

- Repository context: `README.md`
- Architecture decisions: `docs/adr/README.md`
- Chan Theory: `packages/chantheory/README.md` and `docs/chan_theory_v0.1.md`
- Fundamental Screener: `docs/fundamental_screener_*.md` and
  `apps/fundamental-screener/README.md`
- Daily Market Review: `docs/marketreview/` and
  `packages/marketreview/README.md`
- Indicators: `packages/indicators/README.md`
- T+0 Assistant: `docs/t0assistant/` and `apps/t0-assistant/README.md`
- Area-specific guidance: the nearest nested `AGENTS.md`

## Ask Before

Ask before changing:

- public schema or package contract shape
- repository directory layout
- cross-package architecture or ownership
- compatibility-sensitive behavior relied on by multiple consumers
- project dependencies not already implied by the current environment
