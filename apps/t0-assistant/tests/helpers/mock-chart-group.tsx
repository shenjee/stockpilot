import { useEffect, type ReactNode } from "react";
import type { ChartViewportSnapshot } from "../../renderer/src/charts/chart-viewport.mjs";

export type ChartKind = "five_minute" | "one_minute" | "thirty_minute";

export type CommittedChartMount = {
  kind: ChartKind;
  initialViewport: ChartViewportSnapshot | null;
};

export const committedChartMounts: CommittedChartMount[] = [];

const viewportReporters = new Map<
  ChartKind,
  (snapshot: ChartViewportSnapshot | null) => void
>();

export function resetCommittedChartMounts() {
  committedChartMounts.length = 0;
  viewportReporters.clear();
}

export function reportManualViewport(
  kind: ChartKind,
  snapshot: ChartViewportSnapshot,
) {
  const report = viewportReporters.get(kind);
  if (!report) {
    throw new Error(`No mounted ChartGroup reporter for ${kind}`);
  }
  report(snapshot);
}

export function mountsFor(kind: ChartKind) {
  return committedChartMounts.filter((mount) => mount.kind === kind);
}

export function ChartGroup({
  model,
  initialViewport = null,
  onViewportChange,
  priceHeader,
}: {
  model: { kind: ChartKind };
  initialViewport?: ChartViewportSnapshot | null;
  onViewportChange?: (snapshot: ChartViewportSnapshot | null) => void;
  priceHeader?: ReactNode;
  [key: string]: unknown;
}) {
  // Record the committed instance only. initialViewport is consumed once at
  // mount, matching ChartGroup.tsx; callback identity changes must not look
  // like a new ChartGroup.
  useEffect(() => {
    committedChartMounts.push({
      kind: model.kind,
      initialViewport: initialViewport ?? null,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mount-only
  }, []);

  useEffect(() => {
    if (onViewportChange) {
      viewportReporters.set(model.kind, onViewportChange);
    } else {
      viewportReporters.delete(model.kind);
    }
    return () => {
      if (viewportReporters.get(model.kind) === onViewportChange) {
        viewportReporters.delete(model.kind);
      }
    };
  }, [model.kind, onViewportChange]);

  return (
    <div
      data-testid={`mock-chart-${model.kind}`}
      data-initial-viewport={JSON.stringify(initialViewport ?? null)}
    >
      {priceHeader}
    </div>
  );
}
