# ADR 0008: Select The 5-Minute CZSC Update And Replay Rebuild Strategy

- Status: Proposed
- Date: 2026-07-20
- Owners: `packages/chantheory` and T+0 runtime
- Evidence target: `docs/spikes/t0assistant/czsc-rebuild-validation.md`

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

## Current Direction

Use **full project-level rebuild as the correctness oracle and initial MVP
baseline**. Adopt the hybrid strategy only if measurements show a user-visible
latency or resource problem and an incremental adapter proves byte-for-byte or
semantically equivalent project output for every tested prefix.

If incremental capability is needed, it must be implemented behind
`packages/chantheory`; T+0 runtime may own an adapter instance but must not import,
inspect, or mutate raw `czsc` engine objects. Initial load, stock switch, reconnect,
analysis recovery, and Replay backward seek always retain a clean rebuild path.

MVP will not persist mutable CZSC engine state or promise checkpoint compatibility.

## Validation Required

The CZSC Spike must use deterministic A-share 5-minute fixtures with at least 500
warm bars plus a target-day sequence. It must produce executable tests and a
measurement report covering:

1. validation that timestamps map to the intended CZSC 5-minute frequency and
   preserve ascending, duplicate, and session-boundary semantics;
2. full rebuild output for 500 bars and for each target-day closed-bar prefix;
3. step-by-step engine/update output, if explored, compared with a clean rebuild
   of the same prefix;
4. equality rules for strokes, pending stroke, pivot zones, CZSC buy/sell points,
   signal events/snapshots, warnings, metadata, and `plot_primitives`;
5. confirmation that an unclosed dynamic 5-minute bar never affects CZSC output;
6. Replay backward seek rebuilt from the pre-open warm prefix, with no structure,
   signal, or metadata from the abandoned future prefix;
7. repeated runs producing the same normalized project payload after excluding
   only fields explicitly documented as non-deterministic;
8. cold 500-bar rebuild, one-bar full rebuild, forward incremental update (if
   explored), and backward-seek rebuild timings over repeated runs;
9. peak memory observations and the cost attributable to signal replay/mapping,
   not only the raw CZSC constructor;
10. behavior under cancellation or stale-task isolation while Live and Replay
    work are both queued.

The report must include environment details, fixture identity, sample count,
median and tail latency, equivalence failures with minimal reproductions, and a
recommendation. If the existing adapter is not equivalent under incremental
advance, the Spike must not patch around the difference inside T+0 runtime.

## Decision Outcome

Pending. Accept full rebuild if it satisfies the measured interaction needs.
Accept a hybrid only when equivalence tests pass and the full-rebuild measurements
show a concrete reason to accept additional state complexity. If neither is true,
leave this ADR `Proposed` and investigate the adapter boundary explicitly.

## Consequences

If the current direction is accepted without incremental optimization:

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
