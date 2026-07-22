import { useEffect, useState } from "react";

const initialStatus: ServiceStatus = {
  state: "starting",
  service_generation: 1,
  message: "正在启动本地服务…",
};

export function App() {
  const [status, setStatus] = useState<ServiceStatus>(initialStatus);

  useEffect(() => {
    void window.stockpilot.getServiceStatus().then(setStatus);
    return window.stockpilot.onServiceStatus(setStatus);
  }, []);

  return (
    <main className="shell">
      <header className="toolbar">
        <div className="brand">StockPilot</div>
        <div className="mode">T+0 助手 · 工程骨架</div>
      </header>
      <section className="workspace" aria-label="T+0 workbench skeleton">
        <article className="panel primary">
          <h1>5 分钟工作区</h1>
          <p>价格、VOL 与 MACD 图表将在后续垂直切片接入。</p>
        </article>
        <article className="panel">
          <h2>分时工作区</h2>
          <p>当前只验证 React Renderer 的正式装配边界。</p>
        </article>
        <aside className="panel sidebar">
          <h2>本地服务</h2>
          <div className={`status status-${status.state}`}>{status.state}</div>
          <p>{status.message}</p>
          <small>generation {status.service_generation}</small>
        </aside>
      </section>
    </main>
  );
}
