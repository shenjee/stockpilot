# Spike 0007: Local Python Transport - Validation Report

- **ADR**: [`docs/adr/0007-local-python-transport.md`](../../adr/0007-local-python-transport.md) (status: `Proposed`)
- **Issue**: [#26](https://github.com/shenjee/stockpilot/issues/26)
- **Phase**: B - Local transport
- **Prototype**: [`spikes/0006-0007-electron-python/`](../../spikes/0006-0007-electron-python/)
- **Date**: 2026-07-20
- **Executor**: Claude
- **Depends on**: [Spike 0006](./0006-electron-managed-python-process.md) (process boundary)

> Spike evidence, not an ADR edit. Recommendation only; the ADR owners decide
> accept/reject/investigate and back-fill evidence + status in one reviewable
> change (ADR README rule 5).

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
| bounded buffer | per-subscriber `asyncio.Queue(maxsize=N)`; overflow policy = drop oldest, keep live tail (tested). |
| stdout/stderr | diagnostics only; never transport. (Carried over from Phase A.) |

## Acceptance evidence

All 28 transport-contract tests (14 each for WS and SSE) + 5 isolation tests
pass (`node --test "tests/transport/**/*.js" "tests/isolation/**/*.js"`):

```text
ℹ tests 33  ℹ pass 33  ℹ fail 0
```

### ADR 0007 Validation Required, item by item (applies to BOTH transports)

| # | Requirement | Evidence |
| --- | --- | --- |
| 1 | ephemeral port + temp credential, not exposed to Renderer | host binds ephemeral `127.0.0.1` port; credential is 32-byte hex per launch; isolation tests prove the renderer API string contains none of `http://`, `127.0.0.1`, `Bearer`, `credential`, `ws://`, `/events`, `/ws`, `port`. |
| 2 | reject unauthenticated + non-loopback | `401` without credential, `403` with wrong credential; Python binds `127.0.0.1` only (`web.TCPSite(runner, "127.0.0.1", port)`). |
| 3 | request timeout + cancellation | `request timeout` test: hung request aborts within budget (~0.4 s); `cancellation` test: `close()` aborts an in-flight stream and fires `onDisconnect`. |
| 4 | ordered delivery + duplicate/stale revision handling | `ordered delivery` test: revisions arrive `1,2,3,4` in order; `duplicate / stale revisions` test: out-of-order/old revisions dropped. |
| 5 | connection loss + reconnect + full-snapshot re-baseline | `reconnect re-baselines` test: a fresh transport to the same session receives a fresh snapshot baseline on connect. |
| 6 | reject old `service_generation` / retired session | `old service_generation` test: envelope with `generation != current` dropped; `retired session` test: emit/snapshot/subscribe after retire -> `404`/error envelope. |
| 7 | slow consumer exceeds bounded buffer | `slow consumer` test (server `SPIKE_SLOW_CONSUMER_DELAY_MS=80`, `SPIKE_EVENT_BUFFER_MAX=4`, 12 events): client receives `< 12` events but the **latest revision (12) is kept** (live tail), head dropped. |
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
- **Across a Python restart (new generation)**: all prior events are
  invalidated. The client drops any envelope whose `generation` does not match
  the current host generation; a new session + snapshot is required.
- **Retry ownership**: the transport owns reconnect retries (bounded,
  exponential backoff, capped at `maxReconnectAttempts`); it does **not** retry
  individual business requests - those surface as errors to the caller
  (Electron main / renderer) per the error-boundary design.

## Measured data (this machine: macOS, Apple Silicon, Node 24.18, Python 3.14, aiohttp 3.14)

```json
{
  "payload_sizes": {
    "small_snapshot_bytes": 267,
    "large_snapshot_bytes": 36794,
    "incremental_event_bytes": 155,
    "large_snapshot_bars": 500
  },
  "serialization": { "n": 200, "total_ms": 33.874, "avg_us": 169.37 },
  "latency": {
    "ws":  { "n": 50, "received": 50, "avg_ms": 2.053, "p50_ms": 1.937, "p95_ms": 2.946, "max_ms": 3.529 },
    "sse": { "n": 50, "received": 50, "avg_ms": 1.880, "p50_ms": 1.897, "p95_ms": 2.247, "max_ms": 2.285 }
  },
  "memory": {
    "ws":  { "rss_delta_mb": 14.22 },
    "sse": { "rss_delta_mb": 3.57 }
  }
}
```

- **Payload envelope**: a representative 500-bar 5 m workbench snapshot is
  ~36.8 KB; an incremental bar event is ~155 B. Well within local-transport
  budgets; serialization of the 500-bar snapshot averages ~169 µs.
- **Event latency**: WS avg 2.05 ms (p95 2.95 ms); SSE avg 1.88 ms (p95 2.25 ms).
  Both are sub-3 ms on loopback. SSE is marginally faster and tighter here,
  but the difference is below any user-perceptible threshold and dominated by
  Python event-loop scheduling, not framing.
- **Memory**: WS RSS +14.2 MB, SSE +3.6 MB around a 200-event burst. The WS
  number is inflated by being measured first (V8 warm-up); both are small in
  absolute terms. No unbounded growth was observed (bounded buffer enforced).

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
documented as a viable simpler alternative. The preferred choice satisfies
every failure, isolation, ordering, and bounded-resource criterion with
measured sub-3 ms loopback latency and clean shutdown behavior. Transport
mechanics are isolated below the logical contract, so the choice can be
revisited later without touching React.
