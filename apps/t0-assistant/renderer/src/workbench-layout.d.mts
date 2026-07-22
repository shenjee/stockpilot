export const WorkbenchLayoutMode: Readonly<{
  MAIN_PRIORITY: "main_priority";
  EQUAL: "equal";
  HIDE_INTRADAY: "hide_intraday";
}>;

export type WorkbenchLayoutModeValue =
  (typeof WorkbenchLayoutMode)[keyof typeof WorkbenchLayoutMode];

export interface LogicalRange {
  from: number;
  to: number;
}

export interface WorkbenchState {
  layout: {
    chartSplit: "64_36" | "50_50";
    showIntraday: boolean;
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
