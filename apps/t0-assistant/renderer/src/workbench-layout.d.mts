export const WorkbenchLayoutMode: Readonly<{
  MAIN_PRIORITY: "main_priority";
  EQUAL: "equal";
  HIDE_INTRADAY: "hide_intraday";
}>;

export type WorkbenchLayoutModeValue =
  (typeof WorkbenchLayoutMode)[keyof typeof WorkbenchLayoutMode];

export const WorkbenchMode: Readonly<{ LIVE: "live"; REPLAY: "replay" }>;
export const WorkbenchLayer: Readonly<{
  MA5: "ma5";
  MA10: "ma10";
  MA20: "ma20";
  MA30: "ma30";
  MA60: "ma60";
  STROKES: "strokes";
  PIVOT_ZONES: "pivot_zones";
}>;

export interface LogicalRange {
  from: number;
  to: number;
}

export interface SecurityIdentity {
  symbol: string;
  code: string;
  market: "sh" | "sz";
  name: string;
  security_type: "a_share" | "etf";
}

export interface WorkbenchState {
  mode: "live" | "replay";
  security: SecurityIdentity | null;
  layout: {
    chartSplit: "64_36" | "50_50";
    showIntraday: boolean;
  };
  layers: {
    ma5: boolean;
    ma10: boolean;
    ma20: boolean;
    ma30: boolean;
    ma60: boolean;
    strokes: boolean;
    pivot_zones: boolean;
  };
  chartViews: {
    fiveMinute: LogicalRange | null;
    intraday: LogicalRange | null;
  };
}

export function createWorkbenchState(): WorkbenchState;
export function selectWorkbenchLayout(
  state: WorkbenchState,
  mode: WorkbenchLayoutModeValue,
): WorkbenchState;
export function workbenchLayoutMode(
  state: WorkbenchState,
): WorkbenchLayoutModeValue;
export function selectWorkbenchMode(
  state: WorkbenchState,
  mode: "live" | "replay",
): WorkbenchState;
export function selectWorkbenchSecurity(
  state: WorkbenchState,
  security: SecurityIdentity,
): WorkbenchState;
export function toggleWorkbenchLayer(
  state: WorkbenchState,
  layer: keyof WorkbenchState["layers"],
): WorkbenchState;
export function applyWorkbenchPreferences(
  state: WorkbenchState,
  preferences: {
    last_symbol: string | null;
    layout: { chart_split: "64_36" | "50_50"; show_intraday: boolean };
    layers: WorkbenchState["layers"];
  },
): WorkbenchState;
export function workbenchPreferences(state: WorkbenchState): {
  last_symbol: string | null;
  layout: { chart_split: "64_36" | "50_50"; show_intraday: boolean };
  layers: WorkbenchState["layers"];
};
