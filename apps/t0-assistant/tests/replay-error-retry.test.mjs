import assert from "node:assert/strict";
import test from "node:test";

import { isReplayScopedError } from "../renderer/src/replay-controls.mjs";

/**
 * Contract for the App feedback banner: Replay-scoped errors must never enter
 * the generic retry path that calls `retry_live` / `retryService`.
 */

function shouldShowGenericRetry(error) {
  return Boolean(error?.retryable) && !isReplayScopedError(error);
}

function routeRetry(error) {
  if (isReplayScopedError(error)) {
    return "dismiss_replay";
  }
  if (error?.affected_capability === "preferences") {
    return "retry_preferences";
  }
  if (error?.affected_capability === "service") {
    return "retry_service";
  }
  return "retry_live";
}

test("Replay banner errors do not show the Live retry button", () => {
  const replayError = {
    error_code: "invalid_replay_state",
    category: "session",
    severity: "error",
    retryable: true,
    affected_capability: "replay",
    message: "当前回放状态不允许此操作",
  };
  assert.equal(shouldShowGenericRetry(replayError), false);
  assert.equal(routeRetry(replayError), "dismiss_replay");
});

test("replay_busy follows the same Replay-owned recovery path", () => {
  const busy = {
    error_code: "replay_busy",
    retryable: true,
    affected_capability: "replay",
    message: "回放正在处理其他游标操作",
  };
  assert.equal(isReplayScopedError(busy), true);
  assert.equal(routeRetry(busy), "dismiss_replay");
});

test("Live and service errors still use their own retry actions", () => {
  assert.equal(
    routeRetry({
      retryable: true,
      affected_capability: "live",
    }),
    "retry_live",
  );
  assert.equal(
    routeRetry({
      retryable: true,
      affected_capability: "service",
    }),
    "retry_service",
  );
  assert.equal(
    shouldShowGenericRetry({
      retryable: true,
      affected_capability: "live",
    }),
    true,
  );
});
