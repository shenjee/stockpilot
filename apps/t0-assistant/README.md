# StockPilot T+0 Assistant

This directory is the formal desktop-delivery skeleton created by T0-001 and
extended by T0-004/T0-028. It starts an Electron shell, a React renderer, and
one Electron-managed Python service. The service proves process
ownership, authenticated loopback health and commands, ordered WebSocket event
delivery, Renderer isolation, bounded restart, and graceful shutdown; it does
not implement market data or Replay domain behavior.

W0 integration coordinator: **Codex**, acting in the repository's integration
owner role for T0-001, T0-002, and T0-056. Public contract changes remain
integration-owned after W0.

## Directory ownership

```text
apps/t0-assistant/
├── contracts/   # process-neutral logical schemas and fixtures
├── electron/    # main, preload, window, and Python process host
├── renderer/    # React/TypeScript delivery layer
├── backend/     # Python API/bootstrap delivery adapters and lifecycle fake
└── tests/       # app and contract smoke tests
```

Reusable domain behavior must go to `packages/`; Electron, React, HTTP, and
WebSocket adapters stay here. No source from `spikes/` is copied into this app.

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
```

The smoke suite is offline with respect to market services. CI reports four
independent tracks so failures are attributable without reading unrelated logs:

```text
Python smoke     marketdata plus the loopback fake backend
Renderer smoke   TypeScript checking plus the production Vite build
Electron smoke   Python service host lifecycle and bounded shutdown
Contract smoke   Python JSON Schema validation plus Node fixture consumption
```

Electron GUI launch remains a manual smoke; the automated Electron track tests
the headless process-host lifecycle without opening a window.

## W0 boundary

- No market provider, SQLite repository, indicator, CZSC, Session, or playback
  implementation is present. Until their handlers are integrated, the formal
  service rejects those domain commands with `service_unavailable`.
- `contracts/logical-schema.json` is a logical JSON boundary, not a SQLite
  schema.
- Replay v1.0 freezes playback-speed intent and state only; runtime playback and
  UI controls remain T0-046 and T0-049.
- FR-06 is unaffected: the skeleton and contracts emit no trading advice,
  positions, P&L, automatic trades, or future Replay data.
