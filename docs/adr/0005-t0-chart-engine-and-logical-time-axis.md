# ADR 0005: Select The T+0 Chart Engine And Logical Time-Axis Approach

- Status: Proposed
- Date: 2026-07-20
- Owners: T+0 Assistant frontend
- Evidence target: `docs/spikes/0005-t0-chart-engine-and-logical-time-axis.md`

## Context

T+0 Assistant must render a synchronized 5-minute price/VOL/MACD chart group and
an independent 1-minute intraday chart group. The 5-minute group has requirements
that are not proven by a library name alone:

- display only actual bars, with no empty slots for lunch, overnight, weekends,
  holidays, or suspensions;
- fill the available width with the latest `N` bars, including earlier trading
  days when the current day has too few bars;
- synchronize the logical range and crosshair across price, VOL, and MACD;
- preserve the visible range when the user manually browses;
- recompute `N` after a width or layout change only while following the latest
  edge;
- preserve viewport state when the intraday panel is hidden and restored;
- draw BOLL, MA, strokes, pivot zones, CZSC points, and manual trades without
  turning the visible window into an analysis input;
- never display replay bars after the simulated time.

The current UI specification mentions Lightweight Charts, but its suitability
for the complete interaction model has not yet been demonstrated. Treating that
mention as an accepted architecture decision would bypass the required technical
evidence.

## Decision Drivers

- Correct logical-axis behavior across discontinuous A-share trading sessions.
- Reliable synchronization of three vertically aligned charts.
- Explicit, testable follow-latest versus manual-browse state.
- Viewport preservation during resize, layout switching, and incremental updates.
- Ability to render all required overlays with acceptable interaction latency on
  the two target MacBook classes.
- A maintainable React/TypeScript integration with deterministic automated tests.
- Reasonable bundle size and licensing for a local desktop application.

## Options

### Lightweight Charts With Project-Owned Logical Indices

Map every actual 5-minute bar to a contiguous logical index and keep timestamp
metadata for labels and hover. Coordinate the three chart instances through a
project-owned chart-group adapter and state machine.

Expected advantages are a focused financial-chart API and a relatively small
runtime. Risks include custom primitives for complex CZSC overlays and subtle
range feedback loops among synchronized chart instances.

### ECharts With A Category Axis

Use an explicit category for every actual bar and coordinate multiple grids or
instances through a project-owned adapter.

Expected advantages are rich overlays and mature general-purpose interaction.
Risks include a larger surface area, more configuration, and the need to prove
financial-chart behavior and viewport stability under frequent updates.

### Plotly With A Category Axis Or Range Breaks

Use Plotly financial series and categorical/range-break time handling.

Expected advantages are broad plotting capability. Risks include desktop bundle
weight, React update cost, and less direct control over the required synchronized
follow/manual state model.

## Current Direction

Prefer **Lightweight Charts with project-owned logical indices and an explicit
chart-group state machine**, subject to the validation below. This is a working
hypothesis, not an accepted decision.

Regardless of library choice, the project owns these abstractions:

- mapping between logical index and market timestamp;
- visible logical range shared by a chart group;
- `following_latest` / `manual_browse` state transitions;
- viewport capture and restore across component/layout lifecycle changes;
- conversion from backend drawing contracts to library-specific primitives.

This prevents the rest of the application contract from depending directly on
one chart library's internal range or series objects.

## Validation Required

The chart Spike must use a deterministic fixture containing at least 500 actual
5-minute bars across multiple trading days, including lunch and overnight gaps.
It must provide a runnable prototype and an evidence report covering:

1. The latest `N` bars fill the actual plot width at each tested layout width.
2. When the current trading day has fewer than `N` bars, earlier trading days
   fill the left side without creating non-trading slots.
3. Price, VOL, MACD, overlays, and crosshair use the same logical range.
4. Incremental append/update does not reset a manually selected range.
5. Moving back to the latest edge restores follow-latest behavior.
6. The 64/36, 50/50, and hidden-intraday layouts preserve the required state;
   restoring the intraday panel restores its prior viewport.
7. Replay input truncated at time `T` cannot render a point after `T`.
8. CZSC boxes, lines, and markers whose geometry intersects the viewport remain
   correctly clipped and aligned, including primitives that start off-screen.
9. CPU, memory, update latency, resize latency, and bundle-size observations are
   recorded on representative target hardware or a clearly documented proxy.
10. Automated tests cover the logical-index mapping and follow/manual state
    transitions independently of the visual demo.

The report must compare all three options at least at the capability and risk
level. A full prototype is required for the current preferred option; a rejected
option needs a focused prototype only if documentation or a minimal experiment
cannot resolve a material uncertainty.

## Decision Outcome

Pending. Move this ADR to `Accepted` only after the evidence report identifies a
library and shows that all correctness criteria pass. Record any accepted
limitations and link the exact prototype revision. If no candidate passes, keep
this ADR `Proposed` and create a follow-up task rather than weakening the product
requirements silently.

## Consequences

If the current direction is accepted:

- React code will depend on a project-owned chart adapter, not raw chart objects
  outside the chart module;
- backend payloads will contain stable timestamps and ordered series data, while
  logical indices remain a frontend rendering concern;
- chart viewport state becomes explicit application state;
- complex CZSC overlays may require maintained custom primitives.

## Related Documents

- `docs/t0assistant/t0_assistant_prd.md`
- `docs/t0assistant/ui_layout_spec.md`
- `docs/t0assistant/architecture.md`
- `docs/t0assistant/module_design.md`
