import { useEffect, useMemo, useRef, useState } from "react";
import chartFixture from "../../contracts/fixtures/chart-groups-v1.json";
import { ChartGroup } from "./charts/ChartGroup";
import {
  ChartGroupKind,
  createChartGroupModel,
  type WorkbenchChartSnapshot,
} from "./charts/chart-model.mjs";
import {
  applyLiveChartEvent,
  applyWorkbenchSnapshot,
  createChartProjection,
  type ChartProjection,
} from "./charts/chart-projection.mjs";
import {
  createWorkbenchState,
  selectWorkbenchLayout,
  WorkbenchLayoutMode,
  workbenchLayoutMode,
  type WorkbenchLayoutModeValue,
  type WorkbenchState,
} from "./workbench-layout.mjs";

const initialStatus: ServiceStatus = {
  state: "starting",
  service_generation: 1,
  message: "正在启动本地服务…",
};

const emptyChartSnapshot: WorkbenchChartSnapshot = {
  timezone: "Asia/Shanghai",
  market: { bars_1m: [], bars_5m: [] },
  indicators: {
    five_minute: {
      volume: { values: [], ma5: [], ma10: [] },
      macd: {
        fast_period: 12,
        slow_period: 26,
        signal_period: 9,
        dif: [],
        dea: [],
        histogram: [],
      },
    },
    one_minute: {
      vwap: [],
      volume: { values: [] },
      macd: {
        fast_period: 12,
        slow_period: 26,
        signal_period: 9,
        dif: [],
        dea: [],
        histogram: [],
      },
    },
  },
};

export function App() {
  const [status, setStatus] = useState<ServiceStatus>(initialStatus);
  const [workbench, setWorkbench] = useState<WorkbenchState>(
    createWorkbenchState,
  );
  const [projection, setProjection] = useState<ChartProjection>(() =>
    createChartProjection(
      window.stockpilot
        ? emptyChartSnapshot
        : (chartFixture as unknown as WorkbenchChartSnapshot),
    ),
  );
  const rebaselineRequest = useRef<string | null>(null);
  const snapshot = projection.snapshot;

  useEffect(() => {
    if (!window.stockpilot) {
      setStatus({
        state: "disconnected",
        service_generation: 0,
        message: "Renderer fixture 模式",
      });
      return;
    }
    const updateServiceStatus = (next: ServiceStatus) => {
      setStatus(next);
      if (next.service_generation <= 0) {
        return;
      }
      setProjection((current) =>
        current.serviceGeneration !== null &&
        current.serviceGeneration !== next.service_generation
          ? createChartProjection(emptyChartSnapshot, {
              service_generation: next.service_generation,
            })
          : current,
      );
    };
    void window.stockpilot.getServiceStatus().then(updateServiceStatus);
    return window.stockpilot.onServiceStatus(updateServiceStatus);
  }, []);

  useEffect(() => {
    if (!window.stockpilot) {
      return;
    }
    return window.stockpilot.onAppEvent((event) => {
      const baseline = chartProjectionFromEvent(event);
      if (baseline) {
        rebaselineRequest.current = null;
        setProjection((current) =>
          applyWorkbenchSnapshot(
            current,
            baseline.snapshot,
            projectionIdentity(baseline),
          ),
        );
        return;
      }
      if (isChartAppEvent(event)) {
        setProjection((current) => applyLiveChartEvent(current, event));
      }
    });
  }, []);

  useEffect(() => {
    if (
      !window.stockpilot ||
      !projection.rebaselineRequired ||
      projection.serviceGeneration === null ||
      projection.sessionId === null ||
      projection.revision === null
    ) {
      return;
    }
    const requestKey = [
      projection.serviceGeneration,
      projection.sessionId,
      projection.revision,
    ].join(":");
    if (rebaselineRequest.current === requestKey) {
      return;
    }
    rebaselineRequest.current = requestKey;
    const requestedProjection = projection;
    void window.stockpilot
      .getLiveSnapshot({
        schema_version: "t0_app_v1",
        request_id: `renderer-rebaseline-${requestKey}`,
        session_id: projection.sessionId,
        command: "get_live_snapshot",
        payload: {},
      })
      .then((response) => {
        const candidate = workbenchSnapshotFromResponse(response);
        if (!candidate) {
          return;
        }
        setProjection((current) => {
          if (
            !current.rebaselineRequired ||
            current.serviceGeneration !==
              requestedProjection.serviceGeneration ||
            current.sessionId !== requestedProjection.sessionId
          ) {
            return current;
          }
          return applyWorkbenchSnapshot(current, candidate, {
            service_generation:
              requestedProjection.serviceGeneration ?? undefined,
            session_id: requestedProjection.sessionId,
            revision: candidate.session?.revision,
          });
        });
      })
      .catch(() => {
        // The gateway also rebaselines gaps and will publish a replacement
        // workbench_snapshot if this defensive renderer request fails.
      });
  }, [projection]);

  const fiveMinuteModel = useMemo(
    () => createChartGroupModel(snapshot, ChartGroupKind.FIVE_MINUTE),
    [snapshot],
  );
  const intradayModel = useMemo(
    () => createChartGroupModel(snapshot, ChartGroupKind.ONE_MINUTE),
    [snapshot],
  );
  const layoutMode = workbenchLayoutMode(workbench);
  const selectLayout = (mode: WorkbenchLayoutModeValue) => {
    setWorkbench((current) => selectWorkbenchLayout(current, mode));
  };

  return (
    <main className="shell">
      <header className="toolbar">
        <div className="brand">StockPilot</div>
        <div className="mode">T+0 助手</div>
      </header>
      <section
        className="workspace"
        data-chart-split={workbench.layout.chartSplit}
        data-show-intraday={workbench.layout.showIntraday}
        aria-label="T+0 三栏三行工作台"
      >
        <article className="chart-group five-minute-group" aria-label="5 分钟图表组">
          <ChartGroup
            model={fiveMinuteModel}
            priceHeader={
              <div className="panel-heading">
                <h1>5 分钟</h1>
                <div className="layout-switcher" aria-label="工作台布局">
                  <LayoutButton
                    active={layoutMode === WorkbenchLayoutMode.MAIN_PRIORITY}
                    label="64 / 36"
                    onClick={() =>
                      selectLayout(WorkbenchLayoutMode.MAIN_PRIORITY)
                    }
                  />
                  <LayoutButton
                    active={layoutMode === WorkbenchLayoutMode.EQUAL}
                    label="50 / 50"
                    onClick={() => selectLayout(WorkbenchLayoutMode.EQUAL)}
                  />
                  <LayoutButton
                    active={layoutMode === WorkbenchLayoutMode.HIDE_INTRADAY}
                    label="隐藏分时"
                    onClick={() =>
                      selectLayout(WorkbenchLayoutMode.HIDE_INTRADAY)
                    }
                  />
                </div>
              </div>
            }
          />
        </article>

        <article
          className="chart-group intraday-group"
          aria-label="分时图表组"
          hidden={!workbench.layout.showIntraday}
        >
          <ChartGroup
            model={intradayModel}
            priceHeader={
              <div className="panel-heading">
                <h2>分时</h2>
              </div>
            }
          />
        </article>

        <aside className="market-sidebar" aria-label="行情栏">
          <section className="daily-chart-placeholder">
            <h2>日 K</h2>
            <ChartPlaceholder label="日 K 图" />
          </section>
          <section className="quote-placeholder">
            <h2>行情</h2>
            <dl>
              <div><dt>最新价</dt><dd>--</dd></div>
              <div><dt>涨跌幅</dt><dd>--</dd></div>
              <div><dt>成交量</dt><dd>--</dd></div>
              <div><dt>行情时间</dt><dd>--</dd></div>
            </dl>
          </section>
          <section className="service-card" aria-label="本地服务状态">
            <span className={`status status-${status.state}`}>{status.state}</span>
            <span className="service-message">{status.message}</span>
          </section>
        </aside>
      </section>
    </main>
  );
}

interface LayoutButtonProps {
  active: boolean;
  label: string;
  onClick: () => void;
}

function LayoutButton({ active, label, onClick }: LayoutButtonProps) {
  return (
    <button type="button" aria-pressed={active} onClick={onClick}>
      {label}
    </button>
  );
}

function ChartPlaceholder({ label }: { label: string }) {
  return <div className="chart-placeholder" aria-label={label} />;
}

function chartProjectionFromEvent(event: unknown): ChartProjection | null {
  if (!event || typeof event !== "object") {
    return null;
  }
  const envelope = event as {
    event_type?: unknown;
    service_generation?: unknown;
    session_id?: unknown;
    revision?: unknown;
    payload?: unknown;
  };
  return envelope.event_type === "workbench_snapshot" &&
    Number.isInteger(envelope.service_generation) &&
    typeof envelope.session_id === "string" &&
    Number.isInteger(envelope.revision) &&
    isWorkbenchChartSnapshot(envelope.payload)
    ? createChartProjection(envelope.payload, {
        service_generation: envelope.service_generation as number,
        session_id: envelope.session_id,
        revision: envelope.revision as number,
      })
    : null;
}

function isChartAppEvent(
  candidate: unknown,
): candidate is {
  event_type: string;
  service_generation?: number;
  session_id?: string | null;
  revision?: number;
  payload: unknown;
} {
  if (!candidate || typeof candidate !== "object") {
    return false;
  }
  const event = candidate as { event_type?: unknown };
  return typeof event.event_type === "string";
}

function workbenchSnapshotFromResponse(
  response: unknown,
): WorkbenchChartSnapshot | null {
  if (!response || typeof response !== "object") {
    return null;
  }
  const result = response as {
    data?: unknown;
    snapshot?: unknown;
  };
  const candidate = result.data ?? result.snapshot ?? response;
  return isWorkbenchChartSnapshot(candidate) ? candidate : null;
}

function projectionIdentity(projection: ChartProjection) {
  return {
    service_generation: projection.serviceGeneration ?? undefined,
    session_id: projection.sessionId,
    revision: projection.revision ?? undefined,
  };
}

function isWorkbenchChartSnapshot(
  candidate: unknown,
): candidate is WorkbenchChartSnapshot {
  if (!candidate || typeof candidate !== "object") {
    return false;
  }
  const snapshot = candidate as {
    timezone?: unknown;
    market?: { bars_1m?: unknown; bars_5m?: unknown };
    indicators?: { five_minute?: unknown; one_minute?: unknown };
  };
  return (
    snapshot.timezone === "Asia/Shanghai" &&
    Array.isArray(snapshot.market?.bars_1m) &&
    Array.isArray(snapshot.market?.bars_5m) &&
    typeof snapshot.indicators?.five_minute === "object" &&
    snapshot.indicators.five_minute !== null &&
    typeof snapshot.indicators?.one_minute === "object" &&
    snapshot.indicators.one_minute !== null
  );
}
