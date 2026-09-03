export const WorkbenchLayoutMode = Object.freeze({
  SHOW_INTRADAY: "show_intraday",
  HIDE_INTRADAY: "hide_intraday",
});

export const WorkbenchMode = Object.freeze({
  LIVE: "live",
  REPLAY: "replay",
});

export const WorkbenchLayer = Object.freeze({
  MA5: "ma5",
  MA10: "ma10",
  MA20: "ma20",
  MA30: "ma30",
  MA60: "ma60",
  STROKES: "strokes",
  PIVOT_ZONES: "pivot_zones",
});

export function createWorkbenchState() {
  return {
    mode: WorkbenchMode.LIVE,
    security: null,
    layout: {
      showIntraday: true,
    },
    layers: {
      ma5: false,
      ma10: false,
      ma20: false,
      ma30: false,
      ma60: false,
      strokes: true,
      pivot_zones: true,
    },
    chartViews: {
      fiveMinute: null,
      intraday: null,
      thirtyMinute: null,
    },
  };
}

export function selectWorkbenchLayout(state, mode) {
  switch (mode) {
    case WorkbenchLayoutMode.SHOW_INTRADAY:
      return {
        ...state,
        layout: { showIntraday: true },
      };
    case WorkbenchLayoutMode.HIDE_INTRADAY:
      return {
        ...state,
        layout: { ...state.layout, showIntraday: false },
      };
    default:
      throw new TypeError(`Unsupported workbench layout: ${mode}`);
  }
}

export function workbenchLayoutMode(state) {
  return state.layout.showIntraday
    ? WorkbenchLayoutMode.SHOW_INTRADAY
    : WorkbenchLayoutMode.HIDE_INTRADAY;
}

export function selectWorkbenchMode(state, mode) {
  if (!Object.values(WorkbenchMode).includes(mode)) {
    throw new TypeError(`Unsupported workbench mode: ${mode}`);
  }
  return { ...state, mode };
}

export function selectWorkbenchSecurity(state, security) {
  if (
    !security ||
    typeof security !== "object" ||
    !/^(sh|sz)\.[0-9]{6}$/.test(security.symbol) ||
    !/^[0-9]{6}$/.test(security.code) ||
    typeof security.name !== "string" ||
    security.name.length === 0 ||
    !["stock", "etf", "index"].includes(security.instrument_type)
  ) {
    throw new TypeError("Invalid standard security identity");
  }
  // 切股时丢弃上一只股票的可见范围，避免错误继承（UI 规格 §12）。
  return {
    ...state,
    security: { ...security },
    chartViews: { fiveMinute: null, intraday: null, thirtyMinute: null },
  };
}

export function toggleWorkbenchLayer(state, layer) {
  if (!Object.values(WorkbenchLayer).includes(layer)) {
    throw new TypeError(`Unsupported workbench layer: ${layer}`);
  }
  return {
    ...state,
    layers: {
      ...state.layers,
      [layer]: !state.layers[layer],
    },
  };
}

export function applyWorkbenchPreferences(state, preferences) {
  if (!preferences || typeof preferences !== "object") {
    throw new TypeError("Invalid workbench preferences");
  }
  const { layout, layers } = preferences;
  if (
    !layout ||
    typeof layout.show_intraday !== "boolean" ||
    !layers ||
    Object.values(WorkbenchLayer).some(
      (layer) => typeof layers[layer] !== "boolean",
    )
  ) {
    throw new TypeError("Invalid workbench preferences");
  }
  return {
    ...state,
    layout: { showIntraday: layout.show_intraday },
    layers: { ...layers },
  };
}

export function workbenchPreferences(state) {
  return {
    last_symbol: state.security?.symbol ?? null,
    layout: { show_intraday: state.layout.showIntraday },
    layers: { ...state.layers },
  };
}
