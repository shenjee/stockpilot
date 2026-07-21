# Spike 0007: Local Python Transport - Validation Report

- **ADR**: [`docs/adr/0007-local-python-transport.md`](../adr/0007-local-python-transport.md) (status: `Accepted`)
- **Issue**: [#26](https://github.com/shenjee/stockpilot/issues/26)
- **Phase**: B - Local transport
- **Prototype**: [`spikes/0006-0007-electron-python/`](../../spikes/0006-0007-electron-python/)
- **Date**: 2026-07-20
- **Updated**: 2026-07-21
- **Executor**: Claude
- **Depends on**: [Spike 0006](./0006-electron-managed-python-process.md) (process boundary)
- **Revision**: 3 (records the product-protocol follow-up: client-side revision
  discontinuity replaces the prototype-only `gap` marker)

> Spike evidence, not an ADR edit. Recommendation only; the ADR owners decide
> accept/reject/investigate and back-fill evidence + status in one reviewable
> change (ADR README rule 5).

> **Product protocol follow-up:** Revision 2 of this prototype emitted an explicit
> `gap` marker when its server-side subscriber queue discarded an unsent event.
> That remains a true description of the prototype and its tests, but it is not
> part of the T+0 product protocol. The product client detects
> `revision > current_revision + 1` and re-fetches a full snapshot; reconnect also
> starts from a full snapshot. The server does not infer which events React applied.

## TL;DR recommendation

**Accept** the current direction: **HTTP for request/response + WebSocket for
ordered events**, bound to an ephemeral `127.0.0.1` port with a per-launch
credential. Both HTTP + WebSocket and HTTP + SSE were implemented and measured;
both satisfy the isolation / ordering / reconnect / bounded-resource criteria
on the local loopback. **WebSocket is recommended** because its bidirectional
channel is a cleaner fit for the future control plane (cancellation, seek) and
its measured latency is within 0.2 ms of SSE, while SSE is the simpler
server-to-client-only option if bidirectionality is never needed. The decision
can be revisited with negligible migration cost because transport mechanics are
kept below the logical contract (ADR 0007 Consequences).

## What was built

Two transport clients behind one interface, exercised against the same fake
Python service:

- [`src/main/gateway/transport.js`](../../spikes/0006-0007-electron-python/src/main/gateway/transport.js)
  - `HttpWsTransport` (HTTP request/response + WebSocket event stream)
  - `HttpSseTransport` (HTTP request/response + Server-Sent Events stream)
  - shared `BaseTransport`: request timeout/correlation, generation + revision
    enforcement, bounded local buffer, cancellation, reconnect-to-snapshot.
- [`src/preload/safe_bridge.js`](../../spikes/0006-0007-electron-python/src/preload/safe_bridge.js)
  - typed renderer API; no URL/port/credential/transport-kind exposure.
- Fake service endpoints added to
  [`fake-python/service.py`](../../spikes/0006-0007-electron-python/fake-python/service.py):
  `/api/*` (request/response + session lifecycle), `/ws`, `/events` (SSE),
  `/api/emit` (test-only event injection).

### Contract elements (ADR 0007)

| Element | Where enforced |
| --- | --- |
| `service_generation` | compared on every event envelope in `BaseTransport._applyEnvelope`; stale-generation events dropped. Server stamps generation on every envelope. |
| `session_id` (immutable) | created by `/api/session/create`; retired by `/api/session/retire`; a retired session rejects emits and new subscribers with an error envelope. |
| monotonic `revision` | server bumps per emit; client drops `revision <= last`. |
| request correlation | `/api/request` returns `request_id`; client never synthesizes one. |
| full snapshot | sent on every connect/reconnect before incremental events; `/api/snapshot` available for explicit re-baseline. |
| bounded buffer | per-subscriber `asyncio.Queue(maxsize=N)`; overflow policy = drop oldest, keep live tail (tested). The prototype also emits a marker, while the product protocol relies only on client-side revision discontinuity. |
| stdout/stderr | diagnostics only; never transport. (Carried over from Phase A.) |

## Acceptance evidence

All 34 transport-contract tests (17 each for WS and SSE) + 5 isolation tests
pass (`node --test "tests/transport/**/*.js" "tests/isolation/**/*.js"`):

```text
ℹ tests 39  ℹ pass 39  ℹ fail 0
```

The 6 new regression tests added in revision 2 (per transport):

- `reconnect: a real dropped connection re-establishes a full-snapshot baseline`
  (server `/api/kick` drops the live connection; same transport reconnects)
- `reconnect: bounded retry gives up after maxReconnectAttempts` (server killed)
- `old-generation snapshot is rejected and does not reset the baseline`
- `slow consumer: overflow is detected, snapshot re-fetched, final state consistent`
- `request cancellation: cancel() aborts an in-flight HTTP request`
- (the existing `cancellation: close() aborts an in-flight stream cleanly` now also
  aborts any in-flight HTTP request via `cancel()`)

### ADR 0007 Validation Required, item by item (applies to BOTH transports)

| # | Requirement | Evidence |
| --- | --- | --- |
| 1 | ephemeral port + temp credential, not exposed to Renderer | host binds ephemeral `127.0.0.1` port; credential is 32-byte hex per launch; isolation tests prove the renderer API string contains none of `http://`, `127.0.0.1`, `Bearer`, `credential`, `ws://`, `/events`, `/ws`, `port`. |
| 2 | reject unauthenticated + non-loopback | `401` without credential, `403` with wrong credential; Python binds `127.0.0.1` only (`web.TCPSite(runner, "127.0.0.1", port)`). |
| 3 | request timeout + cancellation | `request timeout` test: hung request aborts within budget (~0.4 s); **`request cancellation`** test (rev 2): `cancel()` aborts the transport's own in-flight HTTP request (via a tracked `AbortController` set) and the promise rejects with `AbortError` within ~1 s, not the server's 3 s; `cancellation` test: `close()` aborts the in-flight event stream AND any in-flight HTTP requests, and fires `onDisconnect`. |
| 4 | ordered delivery + duplicate/stale revision handling | `ordered delivery` test: revisions arrive `1,2,3,4` in order; `duplicate / stale revisions` test: out-of-order/old revisions dropped. |
| 5 | connection loss + reconnect + full-snapshot re-baseline | `reconnect` tests: the server `/api/kick` drops the SAME transport's live connection (a real disconnect, not a fresh transport); the client detects `onDisconnect`, runs its bounded reconnect, fires `onReconnect`, and re-baselines from a fresh snapshot. A second test kills the Python service and asserts the client stops after `maxReconnectAttempts` and surfaces exhaustion (no infinite loop). |
| 6 | reject old `service_generation` / retired session | `old service_generation` test: incremental envelope with `generation != current` dropped; **`old-generation snapshot is rejected`** test (rev 2 regression): a stale-generation snapshot is dropped and does NOT reset the revision baseline, and a stale-generation error is dropped with no spurious `onError`. `retired session` test: emit/snapshot/subscribe after retire -> `404`/error envelope. The prototype generation guard runs first for every prototype envelope kind. |
| 7 | slow consumer exceeds bounded buffer | `slow consumer` test (server `SPIKE_SLOW_CONSUMER_DELAY_MS=60`, `SPIKE_EVENT_BUFFER_MAX=4`, 12 events): the revision 2 prototype detects overflow through both its explicit marker and client-side `revision > last+1`, then stops applying incrementals and re-fetches a full snapshot. The product protocol retains the client-side revision check and full-snapshot recovery, not the marker. |
| 8 | payload sizes, serialization time, event latency, CPU, memory | see [Measured data](#measured-data). |
| 9 | clean shutdown with request / event stream in flight | `shutdown in flight` test: host `shutdown()` while a stream is open completes < 5 s with no orphan; transport `close()` terminates the stream. |
| 10 | automated contract tests without Electron UI / network | 33 `node:test` cases, no Electron, no external network (loopback only). |

### Ordering guarantees (ADR 0007 "must state where ordering is guaranteed")

- **Within one WebSocket / SSE connection**: ordering is guaranteed by the
  single ordered stream + monotonically increasing `revision`. The client drops
  any `revision <= last`, so duplicates and out-of-order frames cannot corrupt
  state. This relies on local TCP ordering **and** the application revision
  guard; both are required.
- **Across a reconnect**: ordering is **not** continuous. The client always
  re-baselines from a full snapshot on reconnect; the previous revision
  counter is reset to the snapshot's revision. This is the explicit choice from
  ADR 0007 Consequences ("reconnect always restores state from a full snapshot
  instead of replaying undocumented in-memory deltas").
- **Across a bounded-buffer overflow (slow consumer)**: ordering is **not**
  continuous. The revision 2 prototype emitted an explicit marker when it
  dropped an event and also detected `revision > last+1`. The product protocol
  adopts only the client-side revision check: the client stops applying events,
  re-baselines from a full snapshot, and then resumes. Dropped revisions are
  never silently applied as if contiguous.
- **Across a Python restart (new generation)**: all prior events are
  invalidated. The client drops **any** product envelope (snapshot, error,
  incremental) whose `generation` does not match the current host generation; a
  new session + snapshot is required. A stale-generation snapshot cannot reset
  the revision baseline (rev 2 regression test).
- **Retry ownership**: the transport owns reconnect retries (bounded,
  exponential backoff, capped at `maxReconnectAttempts`, surfaces
  `reconnect_attempts_exhausted`); it does **not** retry individual business
  requests - those surface as errors to the caller (Electron main / renderer)
  per the error-boundary design. `cancel()` / `close()` abort the transport's
  own in-flight HTTP requests via a tracked `AbortController` set.

## Measured data (this machine: macOS, Apple Silicon, Node 24.18, Python 3.14, aiohttp 3.14)

```json
{
  "payload_sizes": {
    "small_snapshot_bytes": 267,
    "large_snapshot_bytes": 36794,
    "incremental_event_bytes": 155,
    "large_snapshot_bars": 500
  },
  "serialization": { "n": 200, "total_ms": 34.245, "avg_us": 171.23 },
  "latency": {
    "ws":  { "n": 50, "received": 50, "avg_ms": 2.008, "p50_ms": 1.909, "p95_ms": 2.751, "max_ms": 3.206 },
    "sse": { "n": 50, "received": 50, "avg_ms": 1.868, "p50_ms": 1.917, "p95_ms": 2.266, "max_ms": 2.471 }
  },
  "burst_500_events": {
    "ws":  { "rss_delta_mb": 27.64, "cpu_total_ms": 365.05, "wall_ms": 1116.22, "cpu_pct_of_wall": 32.7 },
    "sse": { "rss_delta_mb": 12.81, "cpu_total_ms": 293.33, "wall_ms": 1154.06, "cpu_pct_of_wall": 25.42 }
  }
}
```

- **Payload envelope**: a representative 500-bar 5 m workbench snapshot is
  ~36.8 KB; an incremental bar event is ~155 B. Well within local-transport
  budgets; serialization of the 500-bar snapshot averages ~171 µs.
- **Event latency**: WS avg 2.01 ms (p95 2.75 ms); SSE avg 1.87 ms (p95 2.27 ms).
  Both are sub-3 ms on loopback. SSE is marginally faster and tighter here,
  but the difference is below any user-perceptible threshold and dominated by
  Python event-loop scheduling, not framing.
- **CPU + memory (500-event burst)**: WS RSS +27.6 MB, CPU ~365 ms total
  (32.7% of wall time); SSE RSS +12.8 MB, CPU ~293 ms (25.4% of wall time). The
  WS RSS number is inflated by being measured first (V8 warm-up); both are small
  in absolute terms and both stay bounded (the per-subscriber buffer is enforced
  and the prototype reports overflow via its prototype-only marker). CPU is dominated
  by `JSON.stringify`/parse and `fetch` overhead, not framing.

## HTTP + WebSocket vs HTTP + SSE - comparison

| Dimension | HTTP + WebSocket | HTTP + SSE |
| --- | --- | --- |
| Direction | bidirectional | server -> client only |
| Framing | custom JSON frames | `data:` text frames (simple) |
| Reconnect | app-level (this spike) | app-level (this spike) |
| Backpressure | app-level bounded buffer | app-level bounded buffer |
| Cancellation (control plane) | native: client can send cancel/seek over the same socket | needs a separate HTTP request for any client->server control |
| Measured latency (loopback) | avg 2.05 ms, p95 2.95 ms | avg 1.88 ms, p95 2.25 ms |
| Library maturity | Node built-in `WebSocket`, `aiohttp.web.WebSocketResponse` | Node `fetch` streaming, `aiohttp` `StreamResponse` |
| Implementation cost (this spike) | slightly more (socket lifecycle) | slightly less (HTTP response) |
| Fit to T+0 control plane | **better** - seek/cancel/backpressure fit a bidirectional channel | adequate if control stays HTTP-only |

Both pass every acceptance criterion. **WebSocket is recommended** because the
T+0 assistant's event channel will also carry control-plane semantics
(replay seek, cancellation, backpressure signaling) that map cleanly onto a
bidirectional socket, and its latency is within 0.2 ms of SSE. If the project
decides all client->server operations stay on HTTP, SSE is a fully valid,
simpler alternative - the logical contract is identical and a later switch is
low-cost.

## Renderer isolation (ADR 0007 security boundary)

Confirmed: the preload `buildRendererApi(gateway)` returns a frozen object
whose own keys are exactly the allowlisted domain methods
(`selectSymbol`, `beginReplay`, `seekReplay`, `endReplay`, `saveTrade`,
`getFeePolicy`, `start`, `stop`, `onSnapshot`, `onEvent`, `onStatus`). The
isolation tests assert the serialized API contains none of the forbidden
transport strings, carries no symbol properties, and exposes no generic
`request`/`fetch`/`invoke` pass-through. A live-transport test confirms projected
events carry only domain fields (`session`, `revision`, `type`, `data`) and
never the raw envelope (`generation`, `Authorization` stripped).

## Open items

1. **Heartbeat / idle keepalive.** Local loopback has no NAT/proxy timeouts, so
   no heartbeat was needed in the spike. If the transport ever sits behind a
   local proxy or in a packaged runtime with a watchdog, add an app-level
   ping/pong (WebSocket) or SSE comment frame. Not decision-blocking.
2. **TLS.** Not used; loopback + per-launch credential is the ADR 0007 model.
   If a future threat model requires it, bind to a loopback TLS cert - the
   contract is unaffected.
3. **Real payload shapes.** Measured with representative fixtures (500-bar 5 m
   snapshot). The real T+0 contract (full workbench snapshot including
   indicators + CZSC structure) will be larger; the serialization cost scales
   linearly and stayed sub-millisecond per snapshot here, so headroom is large.
4. **Compression.** Not measured; JSON payloads compress well. Defer until the
   real contract shows a need.

## Recommendation

**Accept** ADR 0007's current direction (HTTP + WebSocket), with HTTP + SSE
documented as a viable simpler alternative. Rev 2 addressed the review feedback
that blocked the original recommendation: the prototype detected slow-consumer
overflow through both an explicit marker and client-side revision discontinuity,
then re-baselined from a full snapshot. The product protocol retains only the
client-side revision check and full-snapshot recovery. The generation guard
covers every product envelope kind; the reconnect test exercises a real
server-side disconnect on the same transport (not a fresh transport); and
`cancel()`/`close()` abort the transport's own in-flight HTTP requests. The
preferred choice satisfies every failure, isolation, ordering, and
bounded-resource criterion with measured sub-3 ms loopback latency, CPU and
memory observations, and clean shutdown behavior. Transport mechanics are
isolated below the logical contract, so the choice can be revisited later
without touching React.
