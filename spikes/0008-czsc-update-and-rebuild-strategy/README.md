# ADR 0008 CZSC rebuild/update Spike

This directory contains evidence code for [GitHub Issue #27](https://github.com/shenjee/stockpilot/issues/27). It is not a production API. The only application-facing boundary remains `packages/chantheory`.

## Contents

- `fixtures/a_share_5m_548.json`: 500 deterministic warm bars plus 48 closed bars for 2026-07-14.
- `fixture.py` / `generate_fixture.py`: fixture generator, identity hash, and session validation.
- `comparator.py`: exact project-schema semantic comparator.
- `experiment.py`: full rebuild, isolated incremental experiment, dynamic-bar gate, replay seek, and fake generation executor.
- `benchmark.py`: repeatable latency, stage-cost, and Python-allocation benchmark.
- `benchmark_results.json`: measured result used by the validation report.
- `packages/chantheory/tests/test_czsc_5m_spike.py`: executable acceptance coverage.

The fixture timestamps are closed-bar **end times**: `09:35` through `11:30`, then `13:05` through `15:00`. It contains no lunch, overnight, weekend, holiday placeholder, duplicate, or out-of-order bar. The first warm date is intentionally a 20-bar partial history boundary; the next ten warm dates and target date each contain 48 bars.

## Run

Preferred project environment:

```bash
source ~/.venvs/czsc/bin/activate
python --version
which python
python spikes/0008-czsc-update-and-rebuild-strategy/generate_fixture.py --check
PYTHONPATH=packages python -m unittest packages.chantheory.tests.test_czsc_5m_spike
PYTHONPATH=packages python spikes/0008-czsc-update-and-rebuild-strategy/benchmark.py \
  --output spikes/0008-czsc-update-and-rebuild-strategy/benchmark_results.json --quiet
PYTHONPATH=packages python -m unittest discover -s packages/chantheory/tests -p 'test_*.py'
```

The 2026-07-21 run could not use `~/.venvs/czsc` because its Homebrew Python framework failed code-signature verification before NumPy import. To avoid a system-wide repair, the recorded run used an isolated Python 3.13.14 `python-build-standalone` runtime under `/private/tmp`, with the same pinned `czsc==0.10.12`. The report records this limitation explicitly.

## Interpretation

The safe incremental experiment owns the raw analyzer entirely inside this Spike and only returns `AnalysisResult`. It clones raw bars before signal replay because CZSC signal functions write caches onto `RawBar`; sharing those bars creates a reproducible prefix-2 semantic mismatch. This isolation is evidence about a possible future adapter, not code to copy into T+0 runtime.

Five complete 48-prefix sweeps show no material end-to-end advantage for the safe incremental path once signal replay and project mapping are included. Independent review runs measured full-rebuild p95 at approximately 188–195 ms. ADR 0008 accepts full project-level rebuild for the product's five-minute update cadence and defers the repaired-project-environment benchmark to a pre-release performance regression.
