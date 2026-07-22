export const WorkbenchLayoutMode = Object.freeze({
  MAIN_PRIORITY: "main_priority",
  EQUAL: "equal",
  HIDE_INTRADAY: "hide_intraday",
});

export function createWorkbenchState() {
  return {
    layout: {
      chartSplit: "64_36",
      showIntraday: true,
    },
    chartViews: {
      fiveMinute: null,
      intraday: null,
    },
  };
}

export function selectWorkbenchLayout(state, mode) {
  switch (mode) {
    case WorkbenchLayoutMode.MAIN_PRIORITY:
      return {
        ...state,
        layout: { chartSplit: "64_36", showIntraday: true },
      };
    case WorkbenchLayoutMode.EQUAL:
      return {
        ...state,
        layout: { chartSplit: "50_50", showIntraday: true },
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
  if (!state.layout.showIntraday) {
    return WorkbenchLayoutMode.HIDE_INTRADAY;
  }
  return state.layout.chartSplit === "50_50"
    ? WorkbenchLayoutMode.EQUAL
    : WorkbenchLayoutMode.MAIN_PRIORITY;
}

