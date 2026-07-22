import assert from "node:assert/strict";
import test from "node:test";

import {
  createWorkbenchState,
  selectWorkbenchLayout,
  WorkbenchLayoutMode,
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
