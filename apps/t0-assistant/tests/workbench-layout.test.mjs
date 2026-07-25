import assert from "node:assert/strict";
import test from "node:test";

import {
  applyWorkbenchPreferences,
  createWorkbenchState,
  selectWorkbenchLayout,
  selectWorkbenchMode,
  selectWorkbenchSecurity,
  toggleWorkbenchLayer,
  workbenchPreferences,
  WorkbenchLayer,
  WorkbenchLayoutMode,
  WorkbenchMode,
  workbenchLayoutMode,
} from "../renderer/src/workbench-layout.mjs";


test("workbench defaults to the 64/36 split with a visible intraday group", () => {
  const state = createWorkbenchState();

  assert.deepEqual(state.layout, {
    chartSplit: "64_36",
    showIntraday: true,
  });
  assert.equal(workbenchLayoutMode(state), WorkbenchLayoutMode.MAIN_PRIORITY);
});

test("all three layout choices retain chart view state", () => {
  const fiveMinute = { from: 120, to: 180 };
  const intraday = { from: 0, to: 60 };
  const initial = {
    ...createWorkbenchState(),
    chartViews: { fiveMinute, intraday },
  };

  const equal = selectWorkbenchLayout(initial, WorkbenchLayoutMode.EQUAL);
  const hidden = selectWorkbenchLayout(equal, WorkbenchLayoutMode.HIDE_INTRADAY);
  const restored = selectWorkbenchLayout(hidden, WorkbenchLayoutMode.MAIN_PRIORITY);

  assert.deepEqual(equal.layout, { chartSplit: "50_50", showIntraday: true });
  assert.deepEqual(hidden.layout, { chartSplit: "50_50", showIntraday: false });
  assert.deepEqual(restored.layout, { chartSplit: "64_36", showIntraday: true });
  assert.strictEqual(equal.chartViews.fiveMinute, fiveMinute);
  assert.strictEqual(hidden.chartViews.intraday, intraday);
  assert.strictEqual(restored.chartViews.intraday, intraday);
});

test("an unsupported layout cannot silently corrupt state", () => {
  assert.throws(
    () => selectWorkbenchLayout(createWorkbenchState(), "wide"),
    /Unsupported workbench layout/,
  );
});

test("mode and layout changes preserve layers, security, and chart view state", () => {
  const security = {
    symbol: "sh.600519",
    code: "600519",
    market: "sh",
    name: "贵州茅台",
    security_type: "a_share",
  };
  const range = { from: 10, to: 80 };
  let state = {
    ...selectWorkbenchSecurity(createWorkbenchState(), security),
    chartViews: { fiveMinute: range, intraday: null },
  };
  state = toggleWorkbenchLayer(state, WorkbenchLayer.MA20);
  state = selectWorkbenchMode(state, WorkbenchMode.REPLAY);
  state = selectWorkbenchLayout(state, WorkbenchLayoutMode.HIDE_INTRADAY);

  assert.equal(state.mode, "replay");
  assert.equal(state.layers.ma20, true);
  assert.deepEqual(state.security, security);
  assert.strictEqual(state.chartViews.fiveMinute, range);
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
    layout: { chart_split: "50_50", show_intraday: false },
  });
  assert.deepEqual(restored.layout, {
    chartSplit: "50_50",
    showIntraday: false,
  });
  assert.equal(restored.layers.ma5, true);
  assert.equal(restored.mode, "live");
});
