import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { applyTradesChanged } from "../renderer/src/trading/trade-state.mjs";

const testDir = dirname(fileURLToPath(import.meta.url));
const fixturePath = resolve(
  testDir,
  "../contracts/fixtures/list-trades-flow-v2.json",
);

test("list_trades is a fact-via-changed-event command (contract flow)", async () => {
  const fixture = JSON.parse(await readFile(fixturePath, "utf8"));
  assert.equal(fixture.schema_version, "t0_app_v2");

  for (const scenarioName of ["existing_trades", "empty_repository"]) {
    const scenario = fixture[scenarioName];
    // The renderer must not consume the sync response data: the accepted
    // list_trades response carries operation_id:null and data:null.
    const response = scenario.list_trades_response;
    assert.equal(response.accepted, true);
    assert.equal(response.operation_id, null);
    assert.equal(response.data, null);

    // After an accepted list_trades the backend publishes one authoritative
    // scoped real trades_changed event (session_id null), even when empty.
    const event = scenario.trades_changed_event;
    assert.equal(event.event_type, "trades_changed");
    assert.equal(event.session_id, null);
    assert.equal(event.payload.symbol, "sh.600584");
    assert.equal(event.payload.trade_date, "2026-07-24");
    assert.equal(
      Number.isInteger(event.payload.trade_revision),
      true,
      `${scenarioName}: trade_revision must be an integer`,
    );
  }
});

test("real trades_changed.payload is already scoped to symbol + trade_date", async () => {
  const fixture = JSON.parse(await readFile(fixturePath, "utf8"));
  const request = fixture.existing_trades.list_trades_request;
  const event = fixture.existing_trades.trades_changed_event;
  const trades = event.payload.trades;

  assert.equal(event.payload.symbol, request.payload.symbol);
  assert.equal(event.payload.trade_date, request.payload.trade_date);
  assert.equal(trades.length, 1);
  assert.equal(trades[0].trade_id, "trade-1");
  assert.equal(trades[0].symbol, request.payload.symbol);
  assert.equal(trades[0].executed_at.slice(0, 10), request.payload.trade_date);
});

test("the renderer treats the scoped trades_changed event as authoritative", async () => {
  const fixture = JSON.parse(await readFile(fixturePath, "utf8"));
  const event = fixture.existing_trades.trades_changed_event;
  const request = fixture.existing_trades.list_trades_request;

  // The renderer does NOT read command_response.data.trades; it consumes the
  // frozen scoped trades_changed event. applyTradesChanged is the real reducer.
  const state = applyTradesChanged(null, event, {
    symbol: request.payload.symbol,
    tradeDate: request.payload.trade_date,
  });
  assert.equal(state.tradeRevision, event.payload.trade_revision);
  assert.deepEqual(
    state.trades.map((t) => t.trade_id),
    ["trade-1"],
    "renderer should accept the already-scoped payload",
  );
});

test("an empty repository still publishes a scoped trades_changed with trades:[]", async () => {
  const fixture = JSON.parse(await readFile(fixturePath, "utf8"));
  const event = fixture.empty_repository.trades_changed_event;
  assert.equal(event.payload.symbol, "sh.600584");
  assert.equal(event.payload.trade_date, "2026-07-24");
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
