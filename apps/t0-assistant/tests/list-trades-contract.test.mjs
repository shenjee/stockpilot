import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { applyTradesChanged } from "../renderer/src/trading/trade-state.mjs";

const testDir = dirname(fileURLToPath(import.meta.url));
const fixturePath = resolve(
  testDir,
  "../contracts/fixtures/list-trades-flow-v1.json",
);

test("list_trades is a fact-via-changed-event command (contract flow)", async () => {
  const fixture = JSON.parse(await readFile(fixturePath, "utf8"));
  assert.equal(fixture.schema_version, "t0_app_v1");

  for (const scenarioName of ["existing_trades", "empty_repository"]) {
    const scenario = fixture[scenarioName];
    // The renderer must not consume the sync response data: the accepted
    // list_trades response carries operation_id:null and data:null.
    const response = scenario.list_trades_response;
    assert.equal(response.accepted, true);
    assert.equal(response.operation_id, null);
    assert.equal(response.data, null);

    // After an accepted list_trades the backend publishes one authoritative
    // real trades_changed event (session_id null), even when empty.
    const event = scenario.trades_changed_event;
    assert.equal(event.event_type, "trades_changed");
    assert.equal(event.session_id, null);
    assert.equal(
      Number.isInteger(event.payload.trade_revision),
      true,
      `${scenarioName}: trade_revision must be an integer`,
    );
  }
});

test("real trades_changed.payload.trades is a complete repository snapshot, not a query-scoped subset", async () => {
  const fixture = JSON.parse(await readFile(fixturePath, "utf8"));
  const event = fixture.existing_trades.trades_changed_event;
  const trades = event.payload.trades;

  // The snapshot contains trades for multiple symbols and trading dates, even
  // though the list_trades request asked for one symbol/date. The payload has
  // no scope fields, so the snapshot must be the full repository.
  const symbols = new Set(trades.map((t) => t.symbol));
  const dates = new Set(trades.map((t) => t.executed_at.slice(0, 10)));
  assert.ok(symbols.size > 1, "snapshot should span multiple symbols");
  assert.ok(dates.size > 1, "snapshot should span multiple trading dates");
});

test("the renderer treats the trades_changed event as authoritative and filters the snapshot itself", async () => {
  const fixture = JSON.parse(await readFile(fixturePath, "utf8"));
  const event = fixture.existing_trades.trades_changed_event;
  const scope = fixture.existing_trades.expected_scope_filter;

  // The renderer does NOT read command_response.data.trades; it consumes the
  // frozen trades_changed event and filters the full snapshot to the
  // symbol/date it currently shows. applyTradesChanged is the real reducer.
  const state = applyTradesChanged(null, event, {
    symbol: scope.symbol,
    tradeDate: scope.trade_date,
  });
  assert.equal(state.tradeRevision, event.payload.trade_revision);
  assert.deepEqual(
    state.trades.map((t) => t.trade_id),
    scope.matched_trade_ids,
    "renderer should keep only the symbol/date slice of the full snapshot",
  );
});

test("an empty repository still publishes a trades_changed event with an empty snapshot", async () => {
  const fixture = JSON.parse(await readFile(fixturePath, "utf8"));
  const event = fixture.empty_repository.trades_changed_event;
  assert.deepEqual(event.payload.trades, []);

  // applyTradesChanged accepts the empty authoritative snapshot (revision 0
  // over the null initial state) without dropping it.
  const state = applyTradesChanged(null, event, {
    symbol: "sh.600584",
    tradeDate: "2026-07-24",
  });
  assert.deepEqual(state.trades, []);
  assert.equal(state.tradeRevision, 0);
});
