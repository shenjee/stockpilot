# AGENT.md

This file adds package-specific guidance for work inside `packages/chantheory/`. It inherits the root `AGENT.md`.

## Scope

- Applies to `packages/chantheory/` and its `tests/` directory.
- This package is the project-owned adapter layer around `czsc`.

## Package Role

- Keep `chantheory` as the stable project-facing contract for Chan Theory analysis.
- Do not leak raw `czsc` objects into higher-level code when package schema objects can be used instead.
- Preserve the current responsibility split:
  - normalization and validation
  - engine loading and probing
  - mapping to stable project schema
  - plotting primitives, summaries, and warnings

## Public API

Current public entry points are exported from `__init__.py`:

- `analyze`
- `analyze_normalized`
- `analyze_tracker_klines`
- `normalize_ohlcv_rows`
- `normalize_tracker_klines`
- `build_symbol`
- `get_default_parameters`
- `get_engine_compatibility`

Prefer extending existing entry points over adding new top-level APIs unless the task clearly requires it.

## Change Rules

- Keep public data fields in `snake_case`.
- Preserve the stable result schema unless the task explicitly calls for a contract change.
- Prefer conservative degradation with warnings over hard failure where the existing package already follows that pattern.
- Maintain the validated engine assumptions documented in the package README unless explicitly updating the engine strategy.
- If touching engine import behavior, be careful not to regress the `numpy.typing` import shim behavior around `czsc`.

## File Guidance

- `normalize.py`: input normalization, timestamp handling, field mapping, and validation.
- `adapters.py`: `czsc` loading, execution, mapping, degradation behavior, and result assembly.
- `schema.py`: stable project schema definitions.
- `plotting.py`: visualization-ready plot primitive generation and style mapping.
- `describe.py`: short summaries and warning-facing descriptive output.
- `segments.py`: segment derivation logic and related structural rules.
- `config.py`: engine constants and default parameter helpers.

## Tests

Run the package suite from the repo root with the validated environment active:

```bash
source ~/.venvs/czsc/bin/activate
python -m unittest discover -s packages/chantheory/tests -p 'test_*.py'
```

When possible, update or add focused tests near the changed behavior:

- normalization changes: `test_normalize.py`
- engine or mapping changes: `test_adapters.py`
- plotting changes: `test_plotting.py`
- segment logic changes: `test_segments.py`

If a change affects stable fixture-backed behavior, check:

- `tests/fixtures/p2_sample_rows.json`
- `tests/fixtures/p2_sample_result.json`

## Review Checklist

- Public schema still matches package README expectations.
- Warnings remain informative and conservative.
- Plot primitives remain aligned with mapped structures.
- Tests cover the changed behavior at the smallest useful scope.
