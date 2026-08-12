const { contextBridge, ipcRenderer } = require("electron");


// Sandboxed Electron preload scripts execute as CommonJS and cannot import the
// ESM safe-bridge factory. Keep this delivery adapter deliberately mechanical;
// preload-safe-bridge.test.mjs locks its public method set to safe-bridge.mjs.
const APP_COMMANDS = Object.freeze({
  searchSecurities: "search_securities",
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
  onServiceStatus: "bridge:service-status",
  onAppEvent: "bridge:app-event",
  onReplayEvent: "bridge:replay-event",
  onReplaySnapshot: "bridge:replay-snapshot",
  onWindowLifecycle: "bridge:window-lifecycle",
});

const bridge = {
  getServiceStatus: () =>
    ipcRenderer.invoke("bridge:invoke", "get_service_status", undefined),
  retryService: () =>
    ipcRenderer.invoke("bridge:invoke", "retry_service", undefined),
};

for (const [method, command] of Object.entries({
  ...APP_COMMANDS,
  ...REPLAY_COMMANDS,
})) {
  bridge[method] = (request) =>
    ipcRenderer.invoke("bridge:invoke", command, request);
}

for (const [method, ipcChannel] of Object.entries(SUBSCRIPTIONS)) {
  bridge[method] = (listener) => {
    if (typeof listener !== "function") {
      throw new TypeError(`${method} requires a listener`);
    }
    const handler = (_event, payload) => listener(payload);
    ipcRenderer.on(ipcChannel, handler);
    return () => ipcRenderer.removeListener(ipcChannel, handler);
  };
}

contextBridge.exposeInMainWorld("stockpilot", Object.freeze(bridge));
