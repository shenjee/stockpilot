# T+0 Cross-Process Contracts

This directory is the integration-owned boundary shared by Python, Electron,
preload, and the React renderer.

- `logical-v2.schema.json` freezes project-owned security, bar, quote,
  indicator, session, warning, CZSC, and workbench snapshot structures for
  T0-002. Issue #151: the `security` def uses `instrument_type`
  (stock|etf|index). The v1 file (`logical-schema.json`) is preserved
  unchanged with `security_type` (a_share|etf) for consumers that have not
  migrated.
- `app-v2.schema.json` freezes Live, **historical snapshot**, real/simulated
  trade, preference, service status, synchronous response, and ordered event
  structures for T0-003. Uses `schema_version: "t0_app_v2"` (issue #151).
  The v1 file (`app-v1.schema.json`) is preserved unchanged with
  `schema_version: "t0_app_v1"`.
- `replay-v2.schema.json` adds the Replay command/event state required by
  T0-056. Uses `schema_version: "t0_replay_v2"` (issue #151). The v1 file
  (`replay-v1.schema.json`) is preserved unchanged with
  `schema_version: "t0_replay_v1"`.
- `fixtures/` contains transport-neutral deterministic payloads intended for
  both Python and TypeScript compatibility tests.
  `fixtures/live-five-minute-merge-v1.json` (#155) locks Python
  `merge_five_minute_bars` and Renderer `mergeFiveMinuteBars` to the same
  step-wise `bars_5m` results. Both runtimes assert every step, not only the
  final state. It is a test vector only and does not change the public event
  contract.

The `*-v1.json` fixtures are preserved compatibility payloads. The matching
`*-v2.json` fixtures carry the v2 schema versions and are used by current v2
contract and Renderer tests. The workbench fixtures contain a complete Live
snapshot, typed incremental updates, a deterministic out-of-order delivery
sequence, an asynchronous operation error, and a synchronous rejection.
`tests/fake-safe-bridge.mjs` feeds the v2 files to Renderer tests without
Electron, Python, or network access.

These files describe logical JSON messages. They are **not SQLite schemas**,
HTTP route definitions, WebSocket framing, Electron IPC names, or generated
provider models. Storage and transport adapters must map into this boundary.

All public fields use `snake_case`. Incompatible evolution requires a new
schema identifier; providers, raw `czsc` objects, credentials, ports, file
paths, and SQLite implementation fields may not cross this boundary.

## App v2 behavior

- `t0_app_v2` owns Live, **historical snapshot**, trade and preference
  commands/events. Replay command names and payloads remain in `t0_replay_v2`;
  the app schema uses JSON Schema references to the Replay schema instead of
  copying its fields.
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
- `list_trades` is a fact-via-changed-event command. An accepted `list_trades`
  response carries `operation_id: null` and `data: null`; the renderer must not
  consume `command_response.data.trades` (that object shape is intentionally
  unfrozen). After an accepted `list_trades` request the backend MUST publish
  exactly one authoritative real `trades_changed` event (`session_id: null`),
  including when the repository is empty (`payload.trades: []`). The renderer
  treats that event as the sole source of the trade list and never reads the
  synchronous response data.
- A real `trades_changed.payload.trades` value is a **complete repository
  snapshot**: every persisted real trade for every symbol and trading date, in
  no required order. The payload carries no symbol/date scope fields, so a
  query-scoped subset would be ambiguous; consumers that need one symbol/date
  filter the snapshot themselves. `trade_revision` is monotonic within a
  `service_generation` and gates the snapshot (a consumer discards an event
  whose `(service_generation, trade_revision)` is not newer than its current
  state). A `service_generation` change resets the revision gate.
- Preference events report persisted copies and their own revision. React
  remains authoritative for current layout and chart interaction state.

## Historical snapshot command

- `get_historical_snapshot` is a synchronous App v1 command that returns a
  static `workbench_snapshot` for one past trading day. It carries
  `session_id: null` because it is not a managed Session: there is no
  lifecycle, no playback cursor, and no incremental events.
- The response `data` is a complete `workbench_snapshot` whose
  `session.session_type` is `"historical"`, `state` is `"ready"`, and
  `replay` is `null`; `operation_id` is also `null`. The reusable
  `historical_snapshot_success_response` definition enforces those semantics.
  The renderer treats the result as an authoritative full replacement of the
  current workbench view, exactly like a Live snapshot.
- Failures are delivered synchronously in `command_response.error` using the
  `historical_snapshot_error_response` definition. Both are retryable and
  affect `historical_chart`: `historical_data_unavailable` has category `data`
  and means the provider could not supply usable data for the requested date;
  `service_unavailable` has category `service` and means an unexpected failure
  inside the snapshot pipeline.

## Contract evolution

App v2 uses additive, backward-compatible evolution within the `t0_app_v2`
identifier. Issue #151 introduced `t0_app_v2` (from `t0_app_v1`) and
`t0_replay_v2` (from `t0_replay_v1`) because the `security` identity shape
changed incompatibly: `security_type: "a_share" | "etf"` was replaced by
`instrument_type: "stock" | "etf" | "index"` (objective securities-master
identity, separate from the fee layer's `FeeSecurityType`). Adding
`get_historical_snapshot`, the `historical_chart` capability, and the
`"historical"` logical `session_type` does not change existing command or
event semantics. Incompatible changes (removing commands, changing payload
shapes, or altering event delivery guarantees) still require a new schema
identifier.
