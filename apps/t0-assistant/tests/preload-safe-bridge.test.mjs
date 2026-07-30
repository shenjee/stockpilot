import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

import { SAFE_BRIDGE_METHODS } from "../electron/safe-bridge.mjs";


const testDir = dirname(fileURLToPath(import.meta.url));
const preloadPath = resolve(testDir, "../electron/preload.cjs");

async function loadPreloadBridge() {
  const source = await readFile(preloadPath, "utf8");
  const invocations = [];
  const subscriptions = [];
  let exposed = null;
  const ipcRenderer = {
    invoke: (...args) => {
      invocations.push(args);
      return Promise.resolve({ accepted: true });
    },
    on: (...args) => subscriptions.push(["on", ...args]),
    removeListener: (...args) => subscriptions.push(["off", ...args]),
  };
  const contextBridge = {
    exposeInMainWorld: (name, bridge) => {
      exposed = { name, bridge };
    },
  };
  vm.runInNewContext(source, {
    Object,
    TypeError,
    require: (specifier) => {
      assert.equal(specifier, "electron");
      return { contextBridge, ipcRenderer };
    },
  }, { filename: preloadPath });
  return { exposed, invocations, subscriptions };
}

test("sandbox-compatible preload exposes exactly the frozen Safe Bridge surface", async () => {
  const { exposed } = await loadPreloadBridge();

  assert.equal(exposed.name, "stockpilot");
  assert.deepEqual(
    Object.keys(exposed.bridge).sort(),
    [...SAFE_BRIDGE_METHODS].sort(),
  );
  assert.equal(Object.isFrozen(exposed.bridge), true);
});

test("sandbox-compatible preload keeps transport details inside IPC", async () => {
  const { exposed, invocations, subscriptions } = await loadPreloadBridge();
  const request = { schema_version: "t0_app_v1", request_id: "preload-test" };

  await exposed.bridge.selectSecurity(request);
  assert.deepEqual(invocations, [
    ["bridge:invoke", "select_security", request],
  ]);

  const received = [];
  const unsubscribe = exposed.bridge.onAppEvent((event) => received.push(event));
  assert.equal(subscriptions[0][0], "on");
  assert.equal(subscriptions[0][1], "bridge:app-event");
  subscriptions[0][2](null, { event_type: "workbench_snapshot" });
  assert.deepEqual(received, [{ event_type: "workbench_snapshot" }]);
  unsubscribe();
  assert.equal(subscriptions[1][0], "off");
  assert.equal(subscriptions[1][1], "bridge:app-event");
});
