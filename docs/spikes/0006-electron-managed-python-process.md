# Spike 0006: Electron-Managed Python Process - Validation Report

- **ADR**: [`docs/adr/0006-electron-managed-python-process.md`](../../adr/0006-electron-managed-python-process.md) (status: `Proposed`)
- **Issue**: [#26](https://github.com/shenjee/stockpilot/issues/26)
- **Phase**: A - Process lifecycle
- **Prototype**: [`spikes/0006-0007-electron-python/`](../../spikes/0006-0007-electron-python/)
- **Date**: 2026-07-20
- **Executor**: Claude

> This is Spike evidence, not an ADR edit. The ADR owners decide whether to
> accept, reject, or continue investigating; this report only records findings
> and a recommendation. Per the Issue constraints and the ADR README rule 5,
> ADR status changes happen in the same reviewable change as evidence
> back-fill, owned by the ADR maintainer.

## TL;DR recommendation

**Accept** the current direction (one Electron-main-managed Python child
process per app instance), **conditional on** one open item: real Electron
packaging of the Python interpreter has not been exercised end-to-end on the
target macOS devices (only a *package-equivalent* directory layout was
validated). The process lifecycle itself is deterministic, recoverable, and
leaves no orphans. See [Open items](#open-items).

## What was built

A pure-Node process host (`PythonServiceHost`) plus an `aiohttp` fake Python
service with controllable failure modes. The host is Electron-agnostic so the
lifecycle state machine runs under `node:test` headlessly; the real Electron
main will wrap the same class (see [Electron integration](#electron-integration)).

- Host: [`src/main/python_service_host.js`](../../spikes/0006-0007-electron-python/src/main/python_service_host.js)
- Discovery: [`src/main/executable_discovery.js`](../../spikes/0006-0007-electron-python/src/main/executable_discovery.js)
- Fake service: [`fake-python/service.py`](../../spikes/0006-0007-electron-python/fake-python/service.py)
- Tests: [`tests/lifecycle/test-lifecycle.js`](../../spikes/0006-0007-electron-python/tests/lifecycle/test-lifecycle.js)
- Measurements: [`tests/benchmark/lifecycle-measure.js`](../../spikes/0006-0007-electron-python/tests/benchmark/lifecycle-measure.js)

### stdout / stderr discipline

Confirmed per ADR 0006 and Issue constraints: stdout carries **only** the
bootstrap/lifecycle handshake (`SPIKE_LISTENING`, `SPIKE_READY`, `SPIKE_FAILED`,
`SPIKE_STOPPING`) carrying process metadata (port/pid/generation). All other
diagnostics are `SPIKE_LOG <json>` lines on **stderr**. Business traffic never
uses stdout/stderr. Tested in
[`stdout/stderr are captured as diagnostics`](../../spikes/0006-0007-electron-python/tests/lifecycle/test-lifecycle.js).

## Acceptance evidence

All 15 lifecycle tests pass (`node --test "tests/lifecycle/**/*.js"`):

```text
✔ cold start reaches READY ...
✔ readiness timeout moves to FAILED ...
✔ startup failure surfaces as a clear FAILED state ...
✔ runtime crash keeps the host alive ... new generation
✔ restart exhaustion stops the loop ... user-retry path
✔ graceful shutdown of an idle service leaves no orphan process
✔ graceful shutdown with work in flight ... forced kill fallback
✔ forced kill (ignoring /shutdown) still terminates the child
✔ after full shutdown, no python process ... remains
✔ stdout/stderr are captured as diagnostics ...
✔ executable discovery ... (env / packaged / dev)
✔ script discovery ...
✔ discoverPackagedRoot ...
✔ after a crash+restart, the old port/credential no longer authenticate
✔ a crash loses memory-only Replay state explicitly ...
```

### ADR 0006 Validation Required, item by item

| # | Requirement | Evidence |
| --- | --- | --- |
| 1 | cold start, readiness timeout, startup failure, clear renderer status | `test-lifecycle.js`: cold-start reaches READY with deterministic status; readiness timeout → `FAILED(readiness_timeout)` + child killed; startup failure → `FAILED(startup_fail_mode|exit_before_ready)`. Status projection (`state`, `generation`, `port`, `pid`, `credential`, `restartCount`) is the only thing a renderer sees. |
| 2 | normal app quit with no remaining child process | idle graceful shutdown: `orphan_after_shutdown=false`; `pgrep` cross-check finds no added python process. |
| 3 | Python crash without Electron main/renderer termination | `crash-after-ready` mode: host object stays usable, state `ready→restarting→ready`, renderer-visible state only changes via the status projection. |
| 4 | bounded automatic restart with new generation | `restart` measurement: generation `1→2`, pid `80311→80312`, port rotated, `generation_advanced=true`. Bounded by `maxRestarts` (default 3). |
| 5 | restart exhaustion + user-visible retry | `restart exhaustion` test: after `maxRestarts` attempts state is `FAILED(restart_exhausted)`; calling `start()` again (the user-retry path) succeeds with a newer generation. No infinite loop. |
| 6 | graceful shutdown idle and with active work | idle: graceful exit (no forced kill). with work: service ignores `/shutdown` + slow in-flight request → host falls back to `forced: true` SIGKILL within `gracefulShutdownTimeoutMs`, no orphan. |
| 7 | invalidation of pre-crash requests/events | `old port/credential no longer authenticate` test: after restart the prior port+credential do not authenticate to the new service; new generation authenticates. (Full event invalidation is exercised in Phase B.) |
| 8 | persisted Live inputs preserved, memory-only Replay lost | The host expresses the contract: generation advances on restart (retiring the prior Replay generation), and the state trace includes the explicit `restarting` transition the renderer uses to tell the user "Replay progress was lost, restoring Live." The fake service has no real Live/Replay split; persistence-level reconstruction is out of scope for this Spike. |
| 9 | stdout/stderr capture without business interpretation | Only lifecycle handshake kinds are read; everything else is opaque diagnostics. Verified by the diagnostics test. |
| 10 | executable + resource discovery, dev and packaged | `executable_discovery.js`: env override → packaged `<root>/python/bin/python3` → dev `python3`; script discovery mirrors the layout; `discoverPackagedRoot` returns null when no candidate contains an interpreter. |
| 11 | focused automated tests with a fake service | 15 `node:test` cases, all green, repeatable, no Electron UI / no network. |

## Measured data (this machine: macOS, Apple Silicon, Node 24.18, Python 3.14, aiohttp 3.14)

```json
{
  "N": 5,
  "cold_start_ms": [193, 138, 141, 138, 143],
  "avg_cold_start_ms": 151,
  "max_cold_start_ms": 193,
  "graceful_shutdown_ms": [70, 54, 54, 53, 54],
  "avg_graceful_shutdown_ms": 57,
  "restart": {
    "generation_before": 1, "generation_after": 2,
    "pid_before": 80311, "pid_after": 80312,
    "port_changed": true, "pid_changed": true,
    "generation_advanced": true,
    "state_trace": ["starting","ready","restarting","starting","ready","stopping","stopped"]
  },
  "orphan_after_shutdown": false
}
```

- **Cold start to READY**: ~150 ms (worst 193 ms) - dominated by Python
  interpreter + `aiohttp` startup, well under any realistic readiness budget.
- **Graceful shutdown**: ~57 ms via the `/shutdown` endpoint.
- **Crash → new READY**: sub-second with the 40-80 ms backoff used in tests.
- **Generation/pid/port** all rotate on restart, confirming isolation of the
  old generation.

## Failure injection modes (fake service)

| `SPIKE_MODE` / env | What it simulates | Host reaction |
| --- | --- | --- |
| `normal` | healthy service | READY |
| `startup-fail` | initialization failure | `FAILED(startup_fail_mode)`, no restart |
| `slow-ready` + `SPIKE_INIT_DELAY_MS` | slow init / hung startup | readiness timeout → `FAILED`, child SIGKILLed |
| `crash-after-ready` + `SPIKE_CRASH_DELAY_MS` | runtime crash | bounded restart, new generation |
| `SPIKE_IGNORE_SHUTDOWN=1` | service won't honor graceful stop | forced-kill fallback |
| `SPIKE_SLOW_WORK_MS` | active work during shutdown | in-flight request survives host stop (forced-kill path) |

## Electron integration

The host is intentionally Electron-free. In the real app, Electron main would:

1. instantiate `PythonServiceHost` and subscribe to `onStateChange`;
2. project a **narrow** status (state + generation only, never port/cred/path)
   through the preload allowlist to React;
3. forward domain requests/events to Python (Phase B transport), keeping
   `port`/`credential` inside main only.

This matches ADR 0006 "React receives a narrow service-status projection
through preload. It never receives a child-process handle, executable path,
credential, or unrestricted IPC method." The preload isolation is validated in
Phase B.

## Open items

1. **Packaging on target macOS devices (decision-blocking per ADR 0006).**
   Only a *package-equivalent* directory layout was validated via
   `executable_discovery.js`. Real `electron-builder` / notarized bundling of
   the Python interpreter and its resources on the target Mac models has not
   been run. This should be a follow-up Spike or the Python-runtime-packaging
   ADR noted in ADR 0006 Consequences.
2. **Multi-instance concurrency.** Multiple app instances correctly spawn
   multiple processes (one host = one child). Cross-instance SQLite correctness
   is owned by the SQLite ADR (ADR 0003), not this Spike.
3. **Real Live/Replay reconstruction.** The host expresses the generation +
   `restarting` contract the renderer needs; actual reconstruction of Live
   from persisted inputs is a backend concern, out of scope here.

## Recommendation

**Accept** ADR 0006's current direction, conditional on resolving open item 1
(real Python runtime packaging) in a follow-up. The lifecycle state machine is
deterministic, bounded, recoverable, and orphan-free on this platform.
