# ADR 0007: Select The Local Request And Event Transport To Python

- Status: Proposed
- Date: 2026-07-20
- Owners: T+0 Assistant desktop runtime and backend API
- Depends on: ADR 0006 process boundary
- Evidence target: `docs/spikes/t0assistant/local-transport-validation.md`

## Context

React needs request/response operations such as selecting a stock, saving a
trade, and seeking Replay, plus ordered events such as loading state, snapshots,
market updates, CZSC replacements, operation results, and backend status.

The security boundary is already constrained:

```text
Renderer -> preload allowlist -> Electron main -> Python local service
```

Renderer must not discover or call the Python listener directly. Python binds
only to the local machine, and stdout/stderr are diagnostics rather than a
business protocol. The exact Main-to-Python transport remains undecided.

## Decision Drivers

- Separate, clear request and event semantics.
- Ordered Session events and straightforward reconnect to a full snapshot.
- Local-only exposure with per-launch authentication.
- Backpressure, bounded buffering, cancellation, and stale-generation isolation.
- Simple Python and Electron implementations using mature libraries.
- Testability without a real market provider or full UI.
- Adequate throughput for snapshots and incremental market/indicator updates.
- Diagnosable failure modes during startup, restart, and shutdown.

## Options

### HTTP For Requests Plus WebSocket For Events

Electron main issues authenticated HTTP requests and maintains an authenticated
WebSocket connection for server events. This gives bidirectional control if later
needed, but requires heartbeat, reconnect, buffering, and socket lifecycle code.

### HTTP For Requests Plus Server-Sent Events For Events

Electron main issues HTTP requests and consumes a unidirectional SSE stream.
This matches the current server-to-client event direction and has simple framing,
but cancellation and library behavior in Electron main must be verified.

### One WebSocket For Requests And Events

Multiplex commands, results, and events over a single connection. This reduces
listeners but forces the project to define more correlation, timeout, and
backpressure behavior in its own protocol.

### Framed JSON Over Standard Input/Output

Use child stdin/stdout as the protocol. This avoids a listening socket but mixes
process lifecycle and business transport, complicates logging discipline, and
can be fragile under malformed or blocked streams.

### Unix Domain Socket

Use a filesystem-scoped local socket with a project-defined framed protocol or
HTTP. This improves local exposure boundaries but adds path lifecycle and
cross-platform considerations without removing the need for authentication and
event semantics.

## Current Direction

Prefer **HTTP for request/response plus WebSocket for ordered events**, bound to
an ephemeral `127.0.0.1` port and protected by a high-entropy per-launch
credential known only to Electron main and Python. This remains a hypothesis
until measured against HTTP + SSE.

Transport details must remain below stable logical contracts. The contract must
include at least:

- `service_generation` to isolate events across Python restarts;
- immutable `session_id` or session generation;
- monotonically increasing `revision` within its documented scope;
- request correlation and structured error payloads;
- a full-snapshot operation used to establish or recover the event baseline;
- explicit maximum message and buffer behavior.

The renderer-facing preload bridge exposes domain-oriented methods and events,
not URLs, tokens, raw HTTP verbs, or a generic IPC pass-through.

## Validation Required

The Electron/Python Spike must compare HTTP + WebSocket with HTTP + SSE and
provide a minimal round-trip prototype for the preferred choice. Evidence must
cover:

1. ephemeral port and temporary credential handoff without exposing either to
   Renderer JavaScript;
2. rejection of unauthenticated requests, invalid origins/hosts where applicable,
   and non-loopback binding;
3. request timeout and cancellation behavior;
4. ordered delivery of fixture events and explicit handling of duplicates or
   stale revisions;
5. connection loss, reconnect, and full-snapshot re-baselining;
6. rejection of events from an old `service_generation` or retired Session;
7. behavior when a slow consumer exceeds the bounded event buffer;
8. maximum and representative initial-snapshot sizes, serialization time, event
   latency, CPU, and memory observations;
9. clean shutdown with requests or an event stream in flight;
10. automated contract tests that run without Electron UI and market network
    access.

The evidence report must state where ordering is guaranteed, where it is not,
and which layer owns retries. It must not claim correctness based only on local
TCP ordering while ignoring application revisions, reconnects, and restarts.

## Decision Outcome

Pending. Accept only when one transport passes the failure, isolation, ordering,
and bounded-resource criteria. The ADR should then record the selected libraries,
measured payload envelope, and any limits that the interface contract must expose.

## Consequences

If the current direction is accepted:

- Electron main hosts an HTTP client and one managed WebSocket connection;
- Python hosts a loopback-only local API and event endpoint;
- reconnect always restores state from a full snapshot instead of replaying
  undocumented in-memory deltas;
- revisions and generations remain required even though transport is local;
- logical API/event schemas can be developed after this ADR without exposing
  transport mechanics to React components.

## Related Documents

- [ADR 0004](./0004-schema-first-analysis-contracts.md)
- [ADR 0006](./0006-electron-managed-python-process.md)
- `docs/t0assistant/architecture.md`
- `docs/t0assistant/module_design.md`
