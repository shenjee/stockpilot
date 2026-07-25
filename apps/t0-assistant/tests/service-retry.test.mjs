import assert from "node:assert/strict";
import test from "node:test";

import { retryDesktopService } from "../electron/service-retry.mjs";

test("manual retry reconnects the Gateway when Python is already ready", async () => {
  const connection = {
    host: "127.0.0.1",
    port: 43123,
    token: "secret",
    service_generation: 2,
  };
  const starts = [];
  const serviceHost = {
    state: "ready",
    connectionInfo: () => connection,
    rendererStatus: (message) => ({
      state: "ready",
      service_generation: 2,
      message,
    }),
    start: async () => {
      throw new Error("must not restart Python");
    },
  };
  const gateway = { start: (next) => starts.push(next) };

  const status = await retryDesktopService(serviceHost, gateway);
  assert.deepEqual(starts, [connection]);
  assert.equal(status.state, "ready");
  assert.match(status.message, /重连事件通道/);
});

test("manual retry starts Python when the host is failed", async () => {
  let startCount = 0;
  const expected = {
    state: "ready",
    service_generation: 3,
    message: "本地服务已就绪",
  };
  const serviceHost = {
    state: "failed",
    start: async () => {
      startCount += 1;
      return expected;
    },
  };

  assert.equal(
    await retryDesktopService(serviceHost, { start: () => {} }),
    expected,
  );
  assert.equal(startCount, 1);
});
