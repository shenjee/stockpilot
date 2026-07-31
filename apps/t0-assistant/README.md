# StockPilot T+0 Assistant

This directory contains the Electron T+0 desktop app, React renderer, and the
Electron-managed Python service. The local service provides Live and historical
market data, Replay sessions and playback, real and simulated trades,
preferences, authenticated loopback transport, bounded restart, and graceful
shutdown.

W0 integration coordinator: **Codex**, acting in the repository's integration
owner role for T0-001, T0-002, and T0-056. Public contract changes remain
integration-owned after W0.

## Directory ownership

```text
apps/t0-assistant/
├── contracts/   # process-neutral logical schemas and fixtures
├── electron/    # main, preload, window, and Python process host
├── renderer/    # React/TypeScript delivery layer
├── backend/     # formal Python API/bootstrap delivery adapter
└── tests/       # app and contract smoke tests
```

Reusable domain behavior must go to `packages/`; Electron, React, HTTP, and
WebSocket adapters stay here. No source from `spikes/` is copied into this app.
`backend/service.py` is the sole Electron-managed Python entry point.

## Validated Python environment

The project reuses `~/.venvs/czsc`. Activate it before install, tests, or launch
so Electron resolves the same interpreter from `PATH`:

```bash
source ~/.venvs/czsc/bin/activate
python --version
which python
python -m pip install -e ".[dev]"
```

`which python` should resolve to `~/.venvs/czsc/bin/python`. To select an
equivalent validated interpreter explicitly, set `T0_PYTHON` to its executable
path for the launch command.

## Install and run

```bash
cd apps/t0-assistant
npm install
npm start
```

`npm start` builds the renderer, opens Electron, starts the authenticated
Python service on an ephemeral `127.0.0.1` port, waits for `/health`,
connects the main-process WebSocket event gateway, and stops the child during
normal app quit. The renderer receives only the frozen domain Safe Bridge and
project-owned payloads—never the port, credential, HTTP/WebSocket primitives,
process handle, or executable path.

For renderer-only development:

```bash
npm run dev:renderer
```

## Verification

From the repository root:

```bash
source ~/.venvs/czsc/bin/activate
python -m unittest discover -s apps/t0-assistant/tests -p 'test_*.py'
cd apps/t0-assistant
npm run smoke
npm run acceptance:target-viewports
```

The smoke suite is offline with respect to market services. CI reports four
independent tracks so failures are attributable without reading unrelated logs:

```text
Python smoke     contracts plus the formal loopback service bootstrap
Renderer smoke   TypeScript checking plus the production Vite build
Electron smoke   Python service host lifecycle and bounded shutdown
Contract smoke   Python JSON Schema validation plus Node fixture consumption
```

Electron GUI launch remains a manual smoke; the automated Electron track tests
the headless process-host lifecycle without opening a window.

`acceptance:target-viewports` builds the production Renderer and opens hidden
sandboxed Electron windows at the 13-inch and 14-inch target logical viewports.
It verifies the three workbench layouts, fixed market sidebar, aligned chart
rows, Replay controls, overlay trade drawer, preference-preserving layout
changes, and absence of horizontal scrolling. Physical-device readability and
Canvas crosshair behavior remain manual acceptance items documented in
`docs/t0assistant/t0_054_acceptance.md`.

The service-host tests require permission to spawn the configured Python
interpreter and bind an ephemeral `127.0.0.1` port. The target-viewport command
additionally requires a macOS runner that permits Electron sandbox, GPU, and
renderer child processes. A restricted runner may therefore report a Python
readiness timeout or `sandbox initialization failed: Operation not permitted`
before product behavior is exercised. Such a run is "not executed", not a
viewport pass. On restricted CI, run `npm run smoke:renderer`,
`node --test tests/preload-safe-bridge.test.mjs`, and the Python regression
suite; reserve the target-viewport gate for a compatible macOS runner.

## Current boundary

- `contracts/logical-schema.json` is a logical JSON boundary, not a SQLite
  schema.
- Replay only exposes market data at or before its current cursor.
- The app emits no trading advice, positions, P&L, automatic trades, or future
  Replay data.
- Packaging, signing, notarization, and installed-App acceptance are tracked by
  issue #87 and are not performed by `npm start`.
