# T+0 Cross-Process Contracts

This directory is the integration-owned boundary shared by Python, Electron,
preload, and the React renderer.

- `logical-v2.schema.json` freezes project-owned security, bar, quote,
  indicator, session, warning, CZSC, and workbench snapshot structures for
  T0-002. Issue #151: the `security` def uses `instrument_type`
  (stock|etf|index). The v1 file (`logical-schema.json`) is preserved
  unchanged with `security_type` (a_share|etf) for consumers that have not
  migrated.
- `app-v2.schema.json` freezes Live, **historical snapshot**, persisted real
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
- The runtime supports persisted real trades only. `trade_scope: real` remains
  a required compatibility field in this change; removing the field is a
  separate public-schema migration. Legacy `simulated` definitions may remain
  temporarily in v1/v2 schemas for wire compatibility, but commands using
  `trade_scope: simulated` MUST fail with `error_code: unsupported_trade_scope`,
  `category: invalid_request`, and `retryable: false`; the backend MUST NOT
  publish simulated trade records or Session-scoped `trades_changed` events. Trade
  validation and 5-minute bucketing remain owned by T0-037 rather than this
  transport contract. Trade commands and events use `session_id: null`.
- `list_trades` is a fact-via-changed-event command. An accepted `list_trades`
  response carries `operation_id: null` and `data: null`; the renderer must not
  consume `command_response.data.trades` (that object shape is intentionally
  unfrozen). The request supplies `symbol + trade_date`; after acceptance the
  backend MUST publish exactly one authoritative scoped `trades_changed` event
  (`session_id: null`). In the Issue #163 implementation PR,
  `real_trades_changed_payload` in both App v1 and App v2 MUST add required
  `symbol` and `trade_date` fields. The payload contains only records from that
  explicit scope, including an empty `trades: []`; consumers MUST NOT infer
  scope from `trades[]`. This is an additive contract change. The renderer
  treats the event as the sole source of the scoped trade list and never reads
  the synchronous response data.
- Chart-overlay reads are scoped by `symbol + trade_date` and use the trade
  repository's scoped query; Replay cursor filtering remains Renderer-local.
  `trades_changed` no longer broadcasts the complete repository; mutations
  publish the resulting fact for each affected scope. An update that moves a
  trade between scopes publishes both the old (without the moved record) and
  new scope. The all-history dialog uses a separate history read rather than
  abusing a chart-overlay query. `trade_revision` remains monotonic within one
  `service_generation`; each published event receives a newer revision and a
  generation change resets the gate. Consumers first apply the ordinary global
  `service_generation`/event `revision` gate, then route the accepted event by
  scope. An event for an unobserved scope advances the global revision gate but
  does not replace the current scoped list. `trade_revision` is a monotonic
  staleness marker, not a per-scope contiguous delivery sequence: a numeric
  jump MUST NOT by itself trigger snapshot recovery. Only a gap in the outer
  event revision uses the ordinary transport recovery rule.
- Issue #163 also owns the minimum compatibility path for the existing
  all-history dialog. App v1/v2 add a synchronous `list_trade_history` command
  with `session_id: null` and payload `{ "trade_scope": "real" }`. Its accepted
  response has `operation_id: null` and
  `data: { "trade_revision": integer, "trades": [...] }`; it does not publish
  an all-history event. The response is one repository snapshot and consumers
  discard a late response whose `trade_revision` is older than the history
  state already accepted. No filtering, pagination, or history-page redesign
  belongs to Issue #163.
- The all-history dialog MUST NOT merge scoped `trades_changed` payloads into
  its repository-wide list. While open, it treats any accepted real
  `trades_changed` as invalidation, coalesces repeated invalidations, and calls
  `list_trade_history` again. While closed it keeps no authoritative hidden
  history state and reads again on the next open. This preserves T0-043 while
  keeping `trades_changed` strictly scoped.
- `list_trades` remains a harmless scoped read. If an index request reaches the
  backend, it is accepted and publishes an explicit empty fact for that
  `symbol + trade_date`. Create/update eligibility is fail-closed and currently
  allows only securities-master `instrument_type` values `stock` and `etf`;
  `index` or an identity whose trade eligibility cannot be established fails
  with `error_code: trade_not_allowed`, `category: invalid_request`, and
  `retryable: false`. Delete resolves an existing record by `trade_id` and is
  mode-independent at the service boundary so legacy invalid rows can be
  cleaned up. Replay exposes no create/update/delete UI, including from a
  history dialog opened while Replay is active.
- Replay hides the `录入成交` action but keeps the `历史交易记录` entry. The
  history dialog is read-only in Replay; viewing a historical day remains
  read-only navigation and does not write the trade repository or add trade
  state to Replay Session.
- For a tradable Replay security, Renderer calls `list_trades` once when the
  target `symbol + trade_date` becomes ready or changes. Play, step, and seek
  only reapply the local `executed_at <= replay.current_time` filter and MUST
  NOT issue another list request. Renderer re-reads only after a matching
  scoped `trades_changed`, service-generation change, or reconnect. Index
  Replay never calls `list_trades`.
- The checked-in `list-trades-flow-v1/v2.json` and
  `list-trade-history-flow-v1/v2.json` fixtures describe the Issue #163 scoped
  and synchronous-history contracts and are the source of truth for contract
  tests.
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
- The historical chart independently requests persisted real trades for the
  snapshot's `symbol + trade_date` and renders the same no-autoscale trade
  overlay. The chart is read-only: create/update/delete remain in the Live trade
  drawer or the dedicated all-history entry and are not inline chart actions.
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
