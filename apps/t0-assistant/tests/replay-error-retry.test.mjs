import assert from "node:assert/strict";
import test from "node:test";

import {
  asReplayOwnedError,
  isReplayOwnedError,
} from "../renderer/src/replay-controls.mjs";

/**
 * Contract for the App feedback banner: Replay-owned errors must never enter
 * the generic retry path that calls `retry_live`.
 */

function shouldShowFeedbackRetry(error) {
  if (!error?.retryable) return false;
  if (error.affected_capability === "market_calendar") return false;
  if (!isReplayOwnedError(error)) return true;
  return error.affected_capability === "service";
}

function routeRetry(error) {
  if (isReplayOwnedError(error)) {
    if (error.affected_capability === "service") {
      return "retry_service";
    }
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
  assert.equal(shouldShowFeedbackRetry(replayError), false);
  assert.equal(routeRetry(replayError), "dismiss_replay");
});

test("replay_busy follows the same Replay-owned recovery path", () => {
  const busy = {
    error_code: "replay_busy",
    retryable: true,
    affected_capability: "replay",
    message: "回放正在处理其他游标操作",
  };
  assert.equal(isReplayOwnedError(busy), true);
  assert.equal(routeRetry(busy), "dismiss_replay");
});

test("Replay calculation_failed is owned by source, not affected_capability", () => {
  const calcFailed = asReplayOwnedError({
    error_code: "calculation_failed",
    retryable: true,
    affected_capability: "five_minute_chart",
    message: "指标或缠论计算失败",
  });
  assert.equal(calcFailed.source, "replay");
  assert.equal(isReplayOwnedError(calcFailed), true);
  assert.equal(shouldShowFeedbackRetry(calcFailed), false);
  assert.equal(routeRetry(calcFailed), "dismiss_replay");
});

test("Replay service_unavailable retries service, never retry_live", () => {
  const serviceError = asReplayOwnedError({
    error_code: "service_unavailable",
    retryable: true,
    affected_capability: "service",
    message: "本地服务暂时不可用",
  });
  assert.equal(isReplayOwnedError(serviceError), true);
  assert.equal(shouldShowFeedbackRetry(serviceError), true);
  assert.equal(routeRetry(serviceError), "retry_service");
});

test("any onReplayEvent operation_failed is tagged Replay-owned", () => {
  // Mirrors App.tsx: asReplayOwnedError(applicationErrorFrom(payload))
  const fromReplayChannel = asReplayOwnedError({
    error_code: "operation_superseded",
    retryable: false,
    affected_capability: "replay",
    message: "操作已被更新的定位取代",
  });
  assert.equal(routeRetry(fromReplayChannel), "dismiss_replay");
});

test("calendar errors do not show the generic Live retry button", () => {
  assert.equal(
    shouldShowFeedbackRetry({
      retryable: true,
      affected_capability: "market_calendar",
    }),
    false,
  );
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
    shouldShowFeedbackRetry({
      retryable: true,
      affected_capability: "live",
    }),
    true,
  );
});
