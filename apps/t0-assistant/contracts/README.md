# T+0 Cross-Process Contracts

This directory is the integration-owned boundary shared by Python, Electron,
preload, and the React renderer.

- `logical-schema.json` freezes project-owned security, bar, quote, indicator,
  session, warning, CZSC, and workbench snapshot structures for T0-002.
- `replay-v1.schema.json` adds the Replay v1.0 command/event state required by
  T0-056.
- `fixtures/` contains transport-neutral deterministic payloads intended for
  both Python and TypeScript compatibility tests.

These files describe logical JSON messages. They are **not SQLite schemas**,
HTTP route definitions, WebSocket framing, Electron IPC names, or generated
provider models. Storage and transport adapters must map into this boundary.

All public fields use `snake_case`. Incompatible evolution requires a new
schema identifier; providers, raw `czsc` objects, credentials, ports, file
paths, and SQLite implementation fields may not cross this boundary.
