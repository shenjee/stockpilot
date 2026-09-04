import assert from "node:assert/strict";
import test from "node:test";

import {
  applyWorkbenchPreferences,
  createWorkbenchState,
  selectWorkbenchLayout,
  selectWorkbenchMode,
  selectWorkbenchSecondaryChart,
  selectWorkbenchSecurity,
  toggleWorkbenchLayer,
  workbenchPreferences,
  WorkbenchLayer,
  WorkbenchLayoutMode,
  WorkbenchMode,
  WorkbenchSecondaryChart,
  workbenchLayoutMode,
} from "../renderer/src/workbench-layout.mjs";


test("workbench defaults to a visible secondary pane on the intraday chart", () => {
  const state = createWorkbenchState();

  assert.deepEqual(state.layout, { showIntraday: true });
  assert.equal(state.secondaryChart, WorkbenchSecondaryChart.INTRADAY);
  assert.equal(workbenchLayoutMode(state), WorkbenchLayoutMode.SHOW_INTRADAY);
  assert.deepEqual(state.chartViews, {
    fiveMinute: null,
    intraday: null,
    thirtyMinute: null,
  });
});

test("show and hide secondary layout retain chart view state", () => {
  const fiveMinute = { range: { from: 120, to: 180 }, followState: "manual" };
  const intraday = { range: { from: 0, to: 60 }, followState: "following" };
  const thirtyMinute = { range: { from: 8, to: 80 }, followState: "manual" };
  const initial = {
    ...createWorkbenchState(),
    chartViews: { fiveMinute, intraday, thirtyMinute },
  };

  const hidden = selectWorkbenchLayout(initial, WorkbenchLayoutMode.HIDE_INTRADAY);
  const restored = selectWorkbenchLayout(hidden, WorkbenchLayoutMode.SHOW_INTRADAY);

  assert.deepEqual(hidden.layout, { showIntraday: false });
  assert.deepEqual(restored.layout, { showIntraday: true });
  assert.strictEqual(hidden.chartViews.fiveMinute, fiveMinute);
  assert.strictEqual(hidden.chartViews.intraday, intraday);
  assert.strictEqual(restored.chartViews.thirtyMinute, thirtyMinute);
});

test("secondary chart type is session-only and is not persisted", () => {
  const state = selectWorkbenchSecondaryChart(
    createWorkbenchState(),
    WorkbenchSecondaryChart.THIRTY_MINUTE,
  );
  assert.equal(state.secondaryChart, WorkbenchSecondaryChart.THIRTY_MINUTE);
  assert.equal(state.layout.showIntraday, true);
  assert.equal(workbenchPreferences(state).layout.show_intraday, true);
  assert.equal("secondaryChart" in workbenchPreferences(state), false);
});

test("an unsupported layout cannot silently corrupt state", () => {
  assert.throws(
    () => selectWorkbenchLayout(createWorkbenchState(), "wide"),
    /Unsupported workbench layout/,
  );
});

test("selecting a new security clears chart view state to avoid inheriting the prior stock", () => {
  const security = {
    symbol: "sh.600519",
    code: "600519",
    market: "sh",
    name: "贵州茅台",
    instrument_type: "stock",
  };
  const withViews = {
    ...createWorkbenchState(),
    chartViews: {
      fiveMinute: { range: { from: 10, to: 80 }, followState: "manual" },
      intraday: { range: { from: 0, to: 40 }, followState: "following" },
      thirtyMinute: { range: { from: 4, to: 40 }, followState: "manual" },
    },
  };
  const selected = selectWorkbenchSecurity(withViews, security);
  assert.deepEqual(selected.chartViews, {
    fiveMinute: null,
    intraday: null,
    thirtyMinute: null,
  });
});

test("mode and layout changes preserve layers, security, and chart view state", () => {
  const security = {
    symbol: "sh.600519",
    code: "600519",
    market: "sh",
    name: "贵州茅台",
    instrument_type: "stock",
  };
  const snapshot = { range: { from: 10, to: 80 }, followState: "manual" };
  let state = {
    ...selectWorkbenchSecurity(createWorkbenchState(), security),
    chartViews: { fiveMinute: snapshot, intraday: null, thirtyMinute: null },
  };
  state = toggleWorkbenchLayer(state, WorkbenchLayer.MA20);
  state = selectWorkbenchMode(state, WorkbenchMode.REPLAY);
  state = selectWorkbenchLayout(state, WorkbenchLayoutMode.HIDE_INTRADAY);

  assert.equal(state.mode, "replay");
  assert.equal(state.layers.ma20, true);
  assert.deepEqual(state.security, security);
  assert.strictEqual(state.chartViews.fiveMinute, snapshot);
});

test("persisted preferences are copies and never own current React state", () => {
  const state = toggleWorkbenchLayer(
    createWorkbenchState(),
    WorkbenchLayer.MA5,
  );
  const persistedCopy = workbenchPreferences(state);
  const newerState = toggleWorkbenchLayer(state, WorkbenchLayer.MA10);

  assert.equal(persistedCopy.layers.ma5, true);
  assert.equal(persistedCopy.layers.ma10, false);
  assert.equal(newerState.layers.ma10, true);
  assert.notStrictEqual(persistedCopy.layers, newerState.layers);

  const restored = applyWorkbenchPreferences(createWorkbenchState(), {
    ...persistedCopy,
    layout: { show_intraday: false },
  });
  assert.deepEqual(restored.layout, {
    showIntraday: false,
  });
  assert.equal(restored.layers.ma5, true);
  assert.equal(restored.mode, "live");
  assert.equal(restored.secondaryChart, WorkbenchSecondaryChart.INTRADAY);
});
