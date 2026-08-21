import assert from "node:assert/strict";
import test from "node:test";

import {
  replayCursorChangeRequiresRelist,
  resolveTradeUiPolicy,
} from "../renderer/src/trading/trade-ui-policy.mjs";

const stock = {
  symbol: "sh.600584",
  instrument_type: "stock",
};
const index = {
  symbol: "sz.399001",
  instrument_type: "index",
};

test("index visible security does not mount trade UI or list trades", () => {
  const policy = resolveTradeUiPolicy({
    session: {
      symbol: "sz.399001",
      trade_date: "2026-07-24",
      session_type: "live",
    },
    workbenchSecurity: index,
    mode: "live",
    today: "2026-07-24",
  });
  assert.equal(policy.shouldMountTradeDrawer, false);
  assert.equal(policy.shouldListTrades, false);
  assert.equal(policy.isKnownNonTradableVisible, true);
  assert.equal(policy.tradeDrawerReadOnly, false);
});

test("Replay pending selection does not list the newly selected code", () => {
  const policy = resolveTradeUiPolicy({
    session: {
      symbol: "sh.600584",
      trade_date: "2026-07-20",
      session_type: "replay",
    },
    workbenchSecurity: {
      symbol: "sz.000001",
      instrument_type: "stock",
    },
    mode: "replay",
    today: "2026-07-24",
  });
  assert.equal(policy.visibleSymbol, "sh.600584");
  assert.equal(policy.isTradableSecurity, false);
  assert.equal(policy.shouldListTrades, false);
  assert.equal(policy.tradeDrawerReadOnly, true);
});

test("historical day chart is read-only and lists the visible trade date", () => {
  const policy = resolveTradeUiPolicy({
    session: {
      symbol: "sh.600584",
      trade_date: "2026-07-20",
      session_type: "historical",
    },
    workbenchSecurity: stock,
    mode: "live",
    today: "2026-07-24",
  });
  assert.equal(policy.historicalChartVisible, true);
  assert.equal(policy.tradeDrawerReadOnly, true);
  assert.equal(policy.shouldMountTradeDrawer, true);
  assert.equal(policy.shouldListTrades, true);
  assert.equal(policy.visibleTradeDate, "2026-07-20");
});

test("Replay seek/step/play does not require a trade relist", () => {
  const scope = {
    visibleSymbol: "sh.600584",
    visibleTradeDate: "2026-07-20",
  };
  assert.equal(replayCursorChangeRequiresRelist(scope, scope), false);
  assert.equal(
    replayCursorChangeRequiresRelist(scope, {
      visibleSymbol: "sz.000001",
      visibleTradeDate: "2026-07-20",
    }),
    true,
  );
});
