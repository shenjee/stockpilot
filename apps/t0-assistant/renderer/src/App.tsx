import { useEffect, useState } from "react";
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

export function App() {
  const [status, setStatus] = useState<ServiceStatus>(initialStatus);
  const [workbench, setWorkbench] = useState<WorkbenchState>(
    createWorkbenchState,
  );

  useEffect(() => {
    void window.stockpilot.getServiceStatus().then(setStatus);
    return window.stockpilot.onServiceStatus(setStatus);
  }, []);

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
          <section className="chart-panel price-panel">
            <div className="panel-heading">
              <h1>5 分钟</h1>
              <div className="layout-switcher" aria-label="工作台布局">
                <LayoutButton
                  active={layoutMode === WorkbenchLayoutMode.MAIN_PRIORITY}
                  label="64 / 36"
                  onClick={() => selectLayout(WorkbenchLayoutMode.MAIN_PRIORITY)}
                />
                <LayoutButton
                  active={layoutMode === WorkbenchLayoutMode.EQUAL}
                  label="50 / 50"
                  onClick={() => selectLayout(WorkbenchLayoutMode.EQUAL)}
                />
                <LayoutButton
                  active={layoutMode === WorkbenchLayoutMode.HIDE_INTRADAY}
                  label="隐藏分时"
                  onClick={() => selectLayout(WorkbenchLayoutMode.HIDE_INTRADAY)}
                />
              </div>
            </div>
            <ChartPlaceholder label="5 分钟价格图" />
          </section>
          <ChartPanel title="VOL" label="5 分钟成交量" />
          <ChartPanel title="MACD" label="5 分钟 MACD" />
        </article>

        <article
          className="chart-group intraday-group"
          aria-label="分时图表组"
          hidden={!workbench.layout.showIntraday}
        >
          <section className="chart-panel price-panel">
            <div className="panel-heading">
              <h2>分时</h2>
            </div>
            <ChartPlaceholder label="1 分钟价格与 VWAP" />
          </section>
          <ChartPanel title="VOL" label="1 分钟成交量" />
          <ChartPanel title="MACD" label="1 分钟 MACD" />
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

function ChartPanel({ title, label }: { title: string; label: string }) {
  return (
    <section className="chart-panel indicator-panel">
      <h2>{title}</h2>
      <ChartPlaceholder label={label} />
    </section>
  );
}

function ChartPlaceholder({ label }: { label: string }) {
  return <div className="chart-placeholder" aria-label={label} />;
}
