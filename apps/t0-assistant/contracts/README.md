# T+0 Cross-Process Contracts

This directory is the integration-owned boundary shared by Python, Electron,
preload, and the React renderer.

- `logical-schema.json` freezes project-owned security, bar, quote, indicator,
  session, warning, CZSC, and workbench snapshot structures for T0-002.
- `app-v1.schema.json` freezes Live, real/simulated trade, preference, service
  status, synchronous response, and ordered event structures for T0-003.
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

## App v1 behavior

- `t0_app_v1` owns only Live, trade and preference commands/events. Replay
  command names and payloads remain in `t0_replay_v1`; the app schema uses
  JSON Schema references to Replay v1.0 instead of copying its fields.
- Every command has an opaque `request_id`. A command either fails
  synchronously once in `command_response.error`, or is accepted and may later
  fail once through `operation_failed`; the same failure is not delivered on
  both paths. An accepted response with `operation_id: null` has no asynchronous
  operation-failure path: it either completed synchronously or publishes facts
  through ordinary changed events. Only a non-null `operation_id` authorizes a
  later `operation_failed` event, which must carry that same identifier.
- Events carry `service_generation`, `session_id` (or explicit `null` for
  service/preference scope), and a monotonic `revision`. Consumers discard an
  older generation, wrong Session, or `revision <= current_revision`. A jump
  greater than one triggers `get_live_snapshot`; there is no inferred `gap`
  event.
- Workbench and CZSC events are authoritative full replacements. Market and
  ordinary indicator events are typed updates. Failed refreshes do not publish
  empty facts over the last successful state.
- Real trades and Replay-simulated trades share the transport value shape but
  retain an explicit `trade_scope`. Simulated trades never enter the real
  trade repository. Trade validation and 5-minute bucketing remain owned by
  T0-037 rather than this transport contract. A real `trades_changed` event is
  repository-scoped and therefore has `session_id: null`; a simulated event is
  Replay-Session-scoped, has a non-null `session_id`, and may only contain
  `trade_scope: simulated` records.
- Preference events report persisted copies and their own revision. React
  remains authoritative for current layout and chart interaction state.
