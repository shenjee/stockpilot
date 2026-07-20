# Spike 0006 / 0007 — Electron-Managed Python Process & Local Transport

Evidence spike for two related ADRs. It is **not** production code and does **not**
implement the T+0 backend or freeze any business payload.

| Phase | ADR | Question | Report |
| --- | --- | --- | --- |
| A — Process lifecycle | [ADR 0006](../../../docs/adr/0006-electron-managed-python-process.md) | Can Electron reliably own one Python child process: readiness, crash, bounded restart, clean shutdown, generation, disposable Replay state? | [`docs/spikes/0006-electron-managed-python-process.md`](../../../docs/spikes/0006-electron-managed-python-process.md) |
| B — Local transport | [ADR 0007](../../../docs/adr/0007-local-python-transport.md) | HTTP + WebSocket vs HTTP + SSE; ephemeral loopback port + per-launch credential; generation/revision/snapshot; reconnect; bounded buffer; cancellation; shutdown in-flight. | [`docs/spikes/0007-local-python-transport.md`](../../../docs/spikes/0007-local-python-transport.md) |

## What is here

```text
spikes/0006-0007-electron-python/
├── src/main/
│   ├── python_service_host.js     # process FSM: spawn, readiness, restart, generation, shutdown
│   ├── executable_discovery.js    # resolve python executable (dev + package-equivalent)
│   └── gateway/                  # transport clients (Phase B): http_ws.js, http_sse.js
├── src/preload/
│   └── safe_bridge.js            # typed allowlist: domain methods/events only, no url/port/cred
├── fake-python/
│   └── service.py                # aiohttp fake service with controllable failure modes
├── tests/
│   ├── lifecycle/               # ADR 0006 acceptance: cold start, timeout, crash, restart, shutdown, orphans, Replay loss
│   ├── transport/               # ADR 0007 acceptance: auth, ordering, reconnect, slow consumer, cancel, shutdown in-flight (WS + SSE)
│   ├── isolation/               # Renderer isolation: no url/port/cred leaks through preload
│   ├── benchmark/               # payload size / latency / CPU / memory for WS vs SSE
│   └── helpers/                 # shared spawn + assertions
└── fixtures/                    # deterministic snapshot/event fixtures
```

## Design constraints (from the ADRs)

- Python binds only to `127.0.0.1` on an **ephemeral** port with a **per-launch** credential.
- The Renderer never receives URL, port, credential, executable path, child handle, or a
  generic IPC pass-through — only typed domain methods/events through the preload allowlist.
- `stdout`/`stderr` are **diagnostics only**, never a business message protocol.
- A restart creates a new `service_generation`; pre-crash requests/events are invalidated.
- Reconnect always re-baselines from a **full snapshot**; revisions/generations are required
  even though transport is local.

## Run

```bash
# tests use Node's built-in test runner (Node >= 22; developed on Node 24)
node --test tests/lifecycle/ tests/transport/ tests/isolation/

# one area
node --test tests/lifecycle/

# transport benchmark
node tests/benchmark/transport-bench.js

# run the fake service standalone (manual exploration)
python3 fake-python/service.py
```

No `npm install` is required for the Node side: the host and tests use only the Node
standard library (global `fetch`, `WebSocket`, `node:child_process`, `node:test`).
The fake Python service needs `aiohttp` (`pip install aiohttp`).

## Out of scope

- The real T+0 backend, market data, indicators, CZSC, SQLite, or any business payload.
- Electron packaging/signing/auto-update (a later ADR per ADR 0006 Consequences).
- Modifying `docs/adr/**`, `apps/**`, or `packages/**`.
