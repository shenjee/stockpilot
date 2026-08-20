const APP_COMMANDS = Object.freeze({
  searchSecurities: "search_securities",
  resolveSecurityIdentity: "resolve_security_identity",
  selectSecurity: "select_security",
  saveLastSymbol: "save_last_symbol",
  getLiveSnapshot: "get_live_snapshot",
  retryLive: "retry_live",
  listTrades: "list_trades",
  createTrade: "create_trade",
  updateTrade: "update_trade",
  deleteTrade: "delete_trade",
  listFeePlans: "list_fee_plans",
  createFeePlan: "create_fee_plan",
  updateFeePlan: "update_fee_plan",
  deleteFeePlan: "delete_fee_plan",
  calculateTradeFee: "calculate_trade_fee",
  getPreferences: "get_preferences",
  savePreferences: "save_preferences",
  getHistoricalSnapshot: "get_historical_snapshot",
});

const REPLAY_COMMANDS = Object.freeze({
  selectSymbol: "select_symbol",
  beginReplay: "begin_replay",
  setReplayPlayback: "set_replay_playback",
  setReplaySpeed: "set_replay_speed",
  stepReplay: "step_replay",
  seekReplay: "seek_replay",
  endReplay: "end_replay",
  getReplaySnapshot: "get_replay_snapshot",
});

const SUBSCRIPTIONS = Object.freeze({
  onServiceStatus: "service_status",
  onAppEvent: "app_event",
  onReplayEvent: "replay_event",
  onReplaySnapshot: "replay_snapshot",
  onWindowLifecycle: "window_lifecycle",
});

export const SAFE_BRIDGE_METHODS = Object.freeze([
  "getServiceStatus",
  "retryService",
  ...Object.keys(APP_COMMANDS),
  ...Object.keys(REPLAY_COMMANDS),
  ...Object.keys(SUBSCRIPTIONS),
]);

export const SAFE_BRIDGE_COMMANDS = Object.freeze([
  ...new Set(Object.values({ ...APP_COMMANDS, ...REPLAY_COMMANDS })),
]);

export function buildSafeBridge({ invoke, subscribe }) {
  if (typeof invoke !== "function" || typeof subscribe !== "function") {
    throw new TypeError("Safe Bridge requires invoke and subscribe adapters");
  }

  const bridge = {
    getServiceStatus: () => invoke("get_service_status", undefined),
    retryService: () => invoke("retry_service", undefined),
  };
  for (const [method, command] of Object.entries({...APP_COMMANDS, ...REPLAY_COMMANDS})) {
    bridge[method] = (request) => invoke(command, request);
  }
  for (const [method, channel] of Object.entries(SUBSCRIPTIONS)) {
    bridge[method] = (listener) => {
      if (typeof listener !== "function") throw new TypeError(`${method} requires a listener`);
      return subscribe(channel, listener);
    };
  }
  return Object.freeze(bridge);
}
