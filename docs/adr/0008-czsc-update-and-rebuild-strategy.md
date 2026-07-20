# ADR 0008: Select The 5-Minute CZSC Update And Replay Rebuild Strategy

- Status: Accepted
- Date: 2026-07-20
- Accepted: 2026-07-21
- Owners: `packages/chantheory` and T+0 runtime
- Evidence: `docs/spikes/0008-czsc-update-and-rebuild-strategy.md`

## Context

T+0 Assistant initially loads at least 500 actual 5-minute bars across trading
days. During Live operation it adds official closed 5-minute bars. During Replay
it warms the analysis with bars before the target day, advances only with closed
bars available at the simulated time, and rebuilds from the warm state when the
user seeks backward.

`packages/chantheory` is the only project-owned CZSC boundary. Its current public
analysis functions can rebuild an `AnalysisResult` from a complete normalized
sequence, while the underlying engine exposes mutable update behavior internally.
The public adapter has not yet established a minute-bar incremental contract or
measured whether full rebuilds meet the desktop latency budget.

Choosing incremental mutation without evidence risks divergence between Live,
Replay, reconnect, and backward seek. Choosing unconditional full rebuilds without
measurement risks UI stalls, especially because signal replay and project mapping
may add work beyond the engine constructor itself.

## Decision Drivers

- Identical project-owned output for the same closed-bar prefix.
- No use of dynamic, unclosed 5-minute bars in CZSC.
- No future data after Replay seek or step.
- Acceptable latency for 500-bar initial load, one-bar advance, and backward seek.
- Cancellation or isolation of obsolete Replay calculations so Live remains
  responsive.
- No raw `czsc` mutable object leakage into T+0 runtime or API payloads.
- A small, explicit state model that can be rebuilt after failure.
- Stable plot primitives and candidate-point lifecycle behavior.

## Options

### Full Project-Level Rebuild On Every Closed 5-Minute Bar

Call the stable `chantheory` analysis path with the full loaded prefix and publish
the resulting complete CZSC structure. This gives the simplest equivalence model
but may repeat normalization, engine work, signal replay, and mapping.

### Incremental Analyzer Behind `packages/chantheory`

Add a project-owned stateful adapter that accepts one official closed bar at a
time and returns the same stable schema as a rebuild. Recreate it on reconnect or
backward seek. This may reduce steady-state work but expands the public contract,
state ownership, cancellation, and compatibility testing.

### Hybrid Rebuild And Incremental Strategy

Use a full rebuild for initial load, reconnect, stock switch, and Replay backward
seek; use a project-owned incremental adapter for forward Live/Replay advance;
periodically or diagnostically compare against a full rebuild.

### Persist Engine State Or Replay Checkpoints

Serialize mutable engine or pipeline state and resume from it. This may accelerate
seek, but creates compatibility and corruption risks. The architecture baseline
does not require persisted derived state for MVP.

## Decision

The MVP uses **full project-level rebuild** for every official closed 5-minute
bar. The runtime passes the complete available closed-bar prefix through the
stable `packages/chantheory` API and publishes the resulting complete project
schema snapshot.

Initial load, disconnect recovery, stock switch, analysis recovery, and Replay
backward seek use the same full-rebuild path. Replay backward seek discards the
old derived state and rebuilds from the pre-open warm input through the selected
closed-bar prefix.

The MVP does not implement an incremental adapter and does not persist mutable
CZSC engine state or promise checkpoint compatibility. Dynamic unclosed 5-minute
bars remain display-only and never enter CZSC analysis.

`packages/chantheory` remains the only project-owned CZSC boundary. T+0 runtime
must not import, inspect, or mutate raw `czsc` engine objects.

## Evidence

- The same closed-bar prefix produces a deterministic full-rebuild result across
  the complete project schema.
- Replay backward seek leaves no future structure, signal, metadata, or plot
  primitive in the rebuilt result.
- A dynamic unclosed 5-minute bar does not change CZSC output and only an official
  closed bar is admitted to analysis.
- A safe incremental experiment can match the rebuild oracle, but it has no
  observable end-to-end advantage after signal replay and project mapping.
- A naive incremental experiment that shares engine-owned `RawBar` instances
  with signal replay diverges at the second target-day prefix because signal
  functions mutate bar caches.
- Independent reviewer measurements put full-rebuild p95 at approximately
  188–195 ms. This meets the accepted MVP scenario of one CZSC update per official
  five-minute close and does not justify incremental state complexity.

Reproducible details, raw benchmark rounds, fixture identity, comparator rules,
minimal divergence, tests, memory proxy, and environment limitations are recorded
in `docs/spikes/0008-czsc-update-and-rebuild-strategy.md` and Draft PR #29.

## Accepted Limitations

- The first lazy calculation is approximately one second and is handled by the
  existing Loading state.
- Absolute performance varies materially with the execution environment.
- The documented `~/.venvs/czsc` Python 3.14.5 environment has not yet completed
  the benchmark because its Homebrew framework signature is damaged.
- The production computation queue, cancellation, priority, instance
  serialization, shutdown, and failure-recovery design are separate decisions.

## Follow-up

Repair `~/.venvs/czsc` and run the five-sweep performance regression before
release. This regression is a release-quality follow-up, not a prerequisite for
accepting this ADR or beginning the full-rebuild MVP implementation.

## Consequences

With full rebuild accepted for MVP:

- implementation and recovery semantics remain simple and deterministic;
- complete CZSC snapshots can be atomically replaced by React;
- computation scheduling must prevent rebuild work from blocking Live, API, or
  shutdown paths;
- later incremental optimization has a permanent rebuild oracle and regression
  suite.

If a hybrid is accepted later:

- `packages/chantheory` gains an explicit stateful adapter and equivalence tests;
- every stateful instance is owned by exactly one Live or Replay pipeline;
- backward seek and fault recovery still discard state and rebuild cleanly;
- engine-version compatibility becomes part of the adapter test matrix.

## Related Documents

- [ADR 0002](./0002-packages-own-domain-logic.md)
- [ADR 0004](./0004-schema-first-analysis-contracts.md)
- `packages/chantheory/README.md`
- `docs/t0assistant/t0_assistant_prd.md`
- `docs/t0assistant/architecture.md`
- `docs/t0assistant/module_design.md`
