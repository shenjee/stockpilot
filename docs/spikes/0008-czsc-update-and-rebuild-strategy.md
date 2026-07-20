# ADR 0008 CZSC update and rebuild validation

## Result

- Validation date: 2026-07-21 (Asia/Shanghai)
- Issue: [StockPilot #27](https://github.com/shenjee/stockpilot/issues/27)
- Draft PR: [#29](https://github.com/shenjee/stockpilot/pull/29)
- Related decision: `docs/adr/0008-czsc-update-and-rebuild-strategy.md` (`Proposed`, unchanged)
- Related Epic: [#28](https://github.com/shenjee/stockpilot/issues/28)
- Baseline commit: `b37ec9d20c3569daa04dff1f62f5d209a577cfd9`
- Evidence implementation commit: `54c607fcad796722d463e963da5cc474e5af8faa`
- Prototype classification: **Reference only**
- Recommendation: **Accept full rebuild** for MVP

The full project-level rebuild is deterministic, safely supports backward seek, and has acceptable measured latency on the target-class Apple Silicon machine. A safe incremental experiment is semantically equivalent across every target-day prefix, but it provides no end-to-end latency advantage and requires explicit isolation from signal cache mutation. The added mutable-state complexity is therefore unjustified for MVP.

## Test environment

| Item | Value |
| --- | --- |
| Hardware | Apple M3 Pro, arm64 |
| OS | macOS 26.5.2 arm64 |
| Recorded Python | 3.13.14, isolated `python-build-standalone` runtime under `/private/tmp` |
| Required project environment | `~/.venvs/czsc`, documented Python 3.14.5 |
| CZSC | 0.10.12 |
| Clock | `time.perf_counter_ns` |
| Memory tool | `tracemalloc` peak Python allocations |

The required `~/.venvs/czsc` environment was checked first. Its interpreter was Python 3.14.5, but NumPy import failed because the Homebrew Python framework failed macOS code-signature verification (`library load denied by system policy`; `codesign --verify --deep --strict` also failed). A system-wide Homebrew reinstall was deliberately not performed. Instead, the experiment used an isolated Python 3.13.14 runtime with the same `czsc==0.10.12`. Results establish behavior for the pinned engine on arm64 but should be rerun on the repaired project Python before release qualification.

## Fixture identity and input semantics

- File: `spikes/0008-czsc-update-and-rebuild-strategy/fixtures/a_share_5m_548.json`
- SHA-256 over canonical sorted compact JSON plus newline: `3d01a0b19633ca42ab49df72903a3b4ea93b9ddc9d59eff24db1d5a51ef4e79f`
- Symbol/source/timeframe: `600584.SH` / `adr-0008-deterministic` / `5m`
- Warm input: exactly 500 bars across 11 weekdays; the first day has the newest 20 bars and ten later days have 48 bars each
- Target input: all 48 bars on 2026-07-14
- Timestamp meaning: closed-bar **end time**
- Session endpoints: `09:35..11:30` and `13:05..15:00`, five-minute spacing
- Volume: non-negative deterministic share quantity
- Amount: deterministic `volume * (open + close) / 2`, rounded to cents; it is supplied, not derived by normalization

Automated self-checks cover total and daily counts, strict ordering, uniqueness, weekends, the [official SSE 2026 closure schedule](https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20251222_10802510.shtml), lunch/session exclusion, OHLC geometry, volume/amount non-negativity, generator byte equality, and project `5m` to CZSC `Freq.F5` mapping. A separate normalization test proves sorting and keep-last duplicate semantics.

## Method

### Full rebuild oracle

The oracle calls the public project normalization API and `analyze_normalized` for the 500-bar warm state and all 48 prefixes from 501 through 548 bars. Every prefix is rebuilt twice from fresh input and compared semantically. No raw CZSC object crosses the experiment boundary or participates in equality.

The semantic comparator includes normalized symbol/timeframe/source, every normalized bar and bar/time metadata, normalization warnings/input fields/gaps, and every stable `AnalysisResult` field:

- identity, engine/version, and parameters;
- fractals, strokes, `meta.pending_stroke`, segments, pivot zones, divergences, and structure alerts;
- candidate point events plus buy/sell points;
- signal series, events, and snapshots;
- warnings and summaries;
- plot primitives;
- all `meta`, including engine probe, mapping counts, signal configuration/counts, and deterministic engine assumptions.

No field is excluded. Exact values, list order, types, and keys are compared. The comparator reports the smallest JSON-style path, values, and difference reason.

### Incremental experiment

`CZSC.update(RawBar)` exists in the pinned pure-Python engine. The experimental stateful wrapper constructs the engine from 500 bars, advances one target-day bar at a time, and then reuses the existing project mapping by temporarily supplying the owned analyzer inside the Spike. Only stable `AnalysisResult` values leave the wrapper.

A naive version reused the analyzer-owned `RawBar` objects for project signal replay. CZSC signal functions write cached calculations to those bars, so repeated publication contaminated later signal replay. Minimal reproduction:

| Prefix | Field | Clean rebuild | Unsafe shared-bar incremental |
| --- | --- | --- | --- |
| target +1 | all semantic fields | equal | equal |
| target +2 | `signal_series[3].points[0].status` | `not_ready` | `inactive` |
| target +2 | `signal_series[3].points[0].value` | empty | `其他_任意_任意_0` |

The safe experiment clones normalized bars for signal replay so replay caches never mutate engine-owned input. With that state-ownership rule, all 48 prefixes are semantically equal. This is a candidate technique only if a future project adapter is justified; it is not a T+0 runtime workaround.

### Dynamic unclosed bar

A minimal projection keeps closed analysis input separate from an optional dynamic display bar. Adding or revising the dynamic row leaves the entire semantic analysis unchanged, including strokes, pending stroke, pivot zones, candidate points, signals, warnings, metadata, and plot primitives. Closing the official row increases analysis input from 500 to 501 and only then permits a new result.

### Replay backward seek

The test advances/rebuilds to T2 prefix 40, seeks backward by discarding derived state and rebuilding warm + T1 prefix 16, compares against a direct clean T1 oracle, and scans the canonical T1 payload for every abandoned T1..T2 timestamp. It then creates a new safe incremental instance at T1, advances to T2, and compares with a clean T2 rebuild. Both comparisons are exact; no future timestamp remains at T1.

### Stale task isolation

The fake generation executor gives Live and Replay distinct pipeline objects and per-session generations. Completing an older seek after a newer seek is rejected; completing a task after Replay retirement is rejected; Live continues to publish from its own pipeline. This proves stale-result discard without claiming real computation cancellation or implementing a production queue.

## Performance results

Imports are excluded. “Cold” is the first analysis call after module import and includes lazy first-use initialization; warm runs follow one explicit unmeasured rebuild. All end-to-end rows include normalization, signal replay, project mapping, plot primitives, summaries, warnings, and `AnalysisResult` assembly.

| Scenario | Samples | Median | p95 | Max |
| --- | ---: | ---: | ---: | ---: |
| Cold 500-bar full rebuild | 1 | 1010.255 ms | 1010.255 ms | 1010.255 ms |
| Warm/repeated 500-bar full rebuild | 11 | 44.220 ms | 85.646 ms | 85.646 ms |
| Full rebuild after each target-day closed bar | 48 | 47.411 ms | 77.911 ms | 83.048 ms |
| Safe forward incremental + complete project result | 48 | 45.702 ms | 82.657 ms | 86.726 ms |
| Backward seek rebuild to prefix 16 | 9 | 47.054 ms | 48.596 ms | 48.596 ms |

The product needs an initial 500-bar loading calculation, an update after each five-minute close, and an interactive backward seek. The first lazy call is about one second and occurs inside an already explicit loading state. Subsequent update and seek medians are about 45–47 ms, with observed tails below 100 ms. Incremental does not improve tail latency and improves the median by only 1.709 ms in this run, well below the complexity cost.

Single-run stage instrumentation at 548 bars:

| Stage | Time | Share of 51.580 ms total |
| --- | ---: | ---: |
| Raw CZSC constructor | 4.317 ms | 8.4% |
| Signal replay | 34.362 ms | 66.6% |
| Structure mapping (including candidates) | 1.307 ms | 2.5% |
| Plot primitive generation | 0.086 ms | 0.2% |
| Normalization/orchestration/remaining assembly | 11.507 ms | 22.3% |

Peak 548-bar Python allocation was **4,727,675 bytes (4.51 MiB)**. This is a reproducible `tracemalloc` proxy and excludes some native-library allocations; process RSS was not isolated from imported CZSC dependencies and is therefore not claimed.

## Test commands and results

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=packages \
  /private/tmp/stockpilot-czsc-runtime/venv/bin/python -B \
  -m unittest packages.chantheory.tests.test_czsc_5m_spike

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=packages \
  /private/tmp/stockpilot-czsc-runtime/venv/bin/python -B \
  spikes/0008-czsc-update-and-rebuild-strategy/benchmark.py \
  --output spikes/0008-czsc-update-and-rebuild-strategy/benchmark_results.json

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=packages \
  /private/tmp/stockpilot-czsc-runtime/venv/bin/python -B \
  -m unittest discover -s packages/chantheory/tests -p 'test_*.py'
```

Results:

- focused ADR 0008 acceptance: **14 tests passed in 50.918 s**;
- complete `packages/chantheory` regression: **94 tests passed in 49.693 s**;
- fixture regeneration check: canonical SHA-256 matched;
- full benchmark: completed and wrote `benchmark_results.json`.

## Verified and not verified

Verified:

- deterministic fixture generation, session rules, normalization, duplicate policy, and CZSC F5 mapping;
- 500-bar rebuild and all 48 closed-bar prefixes, repeated determinism, and complete semantic comparison;
- safe incremental equivalence plus the unsafe shared-cache minimal failure;
- dynamic unclosed-bar exclusion;
- T2 → T1 rebuild with no future data and T1 → T2 equivalence;
- stale and retired Replay publication isolation plus separate Live/Replay mutable pipeline identities;
- cold/warm/update/incremental/seek latency, stage contribution, and Python allocation proxy.

Not verified:

- production cancellation, queue capacity, priorities, threads/processes, shutdown, or API event ordering;
- real provider data, suspensions, exchange holiday calendar, timezone-aware timestamps, or revised historical bars;
- process RSS/native peak allocation;
- Intel macOS, Apple M1, repaired project Python 3.14.5, or other CZSC versions;
- real Live/Replay runtime, because this Spike must not implement it.

## Known limitations and recommendation

The synthetic fixture is deterministic and structurally active but not a claim about real-market price distribution. Timestamp semantics are explicitly end-time and naive local exchange time because the public normalized schema currently stores naive strings. The incremental experiment uses a test-only patch point to reuse existing project mapping; extracting it would require a reviewed `packages/chantheory` API and long-term engine compatibility tests.

**Recommendation: Accept full rebuild for MVP.** Keep it for initial load, each closed 5-minute update, reconnect/recovery, and every Replay backward seek. Publish complete CZSC snapshots and isolate stale tasks with session generations. Do not accept hybrid now: equivalent safe incremental operation exists, but it has no demonstrated end-to-end performance benefit and adds RawBar cache ownership, mutable analyzer lifetime, cancellation, and compatibility risk.

Before formal development:

1. repair and rerun the suite in the documented `~/.venvs/czsc` Python 3.14.5 environment;
2. define the production computation executor, Live priority, per-instance serialization, generation checks, shutdown, and failure recovery;
3. freeze closed-bar timestamp/timezone and provider revision semantics;
4. add a small real-market anonymized 5-minute fixture if licensing permits;
5. retain this full-rebuild comparator suite as the oracle for any future incremental adapter.
