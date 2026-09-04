export const WorkbenchLayoutMode: Readonly<{
  SHOW_INTRADAY: "show_intraday";
  HIDE_INTRADAY: "hide_intraday";
}>;

export type WorkbenchLayoutModeValue =
  (typeof WorkbenchLayoutMode)[keyof typeof WorkbenchLayoutMode];

export const WorkbenchSecondaryChart: Readonly<{
  INTRADAY: "intraday";
  THIRTY_MINUTE: "thirty_minute";
}>;

export type WorkbenchSecondaryChartValue =
  (typeof WorkbenchSecondaryChart)[keyof typeof WorkbenchSecondaryChart];

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

/** 图表可见范围快照：range 为 LC 连续逻辑范围，followState 标记跟随/手工。 */
export interface ChartViewportSnapshot {
  range: LogicalRange;
  followState: "following" | "manual";
}

export interface SecurityIdentity {
  symbol: string;
  code: string;
  market: "sh" | "sz";
  name: string;
  instrument_type: "stock" | "etf" | "index";
}

export interface WorkbenchState {
  mode: "live" | "replay";
  security: SecurityIdentity | null;
  layout: {
    showIntraday: boolean;
  };
  secondaryChart: WorkbenchSecondaryChartValue;
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
    fiveMinute: ChartViewportSnapshot | null;
    intraday: ChartViewportSnapshot | null;
    thirtyMinute: ChartViewportSnapshot | null;
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
export function selectWorkbenchSecondaryChart(
  state: WorkbenchState,
  secondaryChart: WorkbenchSecondaryChartValue,
): WorkbenchState;
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
    layout: { show_intraday: boolean };
    layers: WorkbenchState["layers"];
  },
): WorkbenchState;
export function workbenchPreferences(state: WorkbenchState): {
  last_symbol: string | null;
  layout: { show_intraday: boolean };
  layers: WorkbenchState["layers"];
};
