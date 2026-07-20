# ADR 0006: Let Electron Manage One Python Service Process Per App Instance

- Status: Proposed
- Date: 2026-07-20
- Owners: T+0 Assistant desktop runtime
- Evidence target: `docs/spikes/t0assistant/python-process-validation.md`

## Context

T+0 Assistant needs Python for shared market-data, indicator, repository, replay,
and CZSC capabilities, while Electron owns the desktop window and React renderer.
The Python runtime must start and stop with the desktop application, fail without
crashing Electron, and be recoverable without pretending that memory-only Replay
state survived.

The architecture baseline assigns Python lifecycle ownership to Electron main,
but process startup, readiness, restart, shutdown, logging, and packaged-runtime
behavior have not yet been proven in a minimal application.

This ADR decides the process ownership and lifecycle model. ADR 0007 separately
decides the local request/event transport so that transport can evolve without
changing process ownership.

## Decision Drivers

- Renderer isolation from process and filesystem capabilities.
- Predictable startup, health checking, shutdown, and crash recovery.
- One clear owner for ports, credentials, child-process handles, and diagnostic
  streams.
- No orphan Python process after normal application shutdown.
- Explicit behavior when Python fails during startup or crashes at runtime.
- Support for development and packaged desktop execution on the target macOS
  devices.
- Testability with a fake Python service before the production backend exists.

## Options

### One Electron-Managed Child Process Per App Instance

Electron main starts one Python local service, waits for an explicit readiness
signal, monitors it, performs bounded restarts, and terminates it during app
shutdown. Live and Replay Sessions remain inside that Python process.

### A Separately Installed Long-Running Local Daemon

Electron connects to a Python service managed outside the application lifecycle.
This could share resources among desktop apps, but adds installation, version
coordination, discovery, and stale-daemon problems that the MVP does not require.

### Run Python Per Request Or Per Session

Electron launches short-lived workers for operations or launches separate Live
and Replay processes. This improves some isolation but makes warm state, shared
cache coordination, startup latency, cancellation, and resource limits harder.

### Embed Python In The Electron Process

Load an embedded interpreter through a native integration. This reduces the
number of operating-system processes but increases packaging complexity and
weakens the desired failure boundary.

## Current Direction

Prefer **one Electron-main-managed Python child process per application
instance**. Electron main owns:

- executable resolution and child startup;
- per-launch connection parameters and temporary credentials;
- readiness and health state;
- stdout/stderr capture for diagnostics only;
- bounded restart policy and generation changes;
- graceful shutdown followed by a time-bounded forced termination if required.

React receives a narrow service-status projection through preload. It never
receives a child-process handle, executable path, credential, or unrestricted
IPC method.

Python process restart creates a new service generation. Persisted inputs can be
used to reconstruct Live state, but Replay progress and simulated trades are
reported as lost and are not silently reconstructed.

## Validation Required

The Electron/Python Spike must provide a minimal packaged-or-package-equivalent
prototype and evidence for:

1. cold start, readiness timeout, startup failure, and clear renderer status;
2. normal app quit with no remaining child process;
3. Python crash detection without Electron main or renderer termination;
4. bounded automatic restart with a new service generation;
5. restart exhaustion and a user-visible retry path without a restart loop;
6. graceful shutdown of an idle service and a service with active work;
7. invalidation of pre-crash requests/events after a restart;
8. preservation of persisted Live inputs and explicit loss of memory-only Replay
   state;
9. stdout/stderr capture without interpreting either stream as business data;
10. executable and resource discovery in both development and packaged layouts;
11. focused automated tests using a fake service for lifecycle state transitions.

The report must record process identifiers before and after restart, measured
startup/shutdown timings, tested failure injection, relevant macOS packaging or
signing constraints, and any assumptions not yet tested on both target machines.

## Decision Outcome

Pending. Accept only after the prototype demonstrates deterministic ownership,
bounded recovery, and clean shutdown. Packaging uncertainty that can prevent the
app from launching is decision-blocking, not a detail to defer silently.

## Consequences

If the current direction is accepted:

- Electron main becomes responsible for a small but critical lifecycle state
  machine;
- Python remains an independently failing local backend;
- multiple App instances create multiple Python processes, while shared SQLite
  correctness must not depend on a single process;
- Replay state is intentionally disposable across Python crashes;
- Python runtime packaging becomes a release concern that needs its own later ADR
  once the Spike establishes viable choices.

## Related Documents

- [ADR 0001](./0001-modular-monolith.md)
- [ADR 0007](./0007-local-python-transport.md)
- `docs/t0assistant/architecture.md`
- `docs/t0assistant/module_design.md`
