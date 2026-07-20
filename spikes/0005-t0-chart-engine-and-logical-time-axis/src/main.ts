import {
  FIVE_MINUTE_FIXTURE,
  BarData,
  nextBarTime,
  generateMockZhongShu,
} from './fixtures/ohlcv-fixture';
import { FiveMinuteChartGroup } from './charts/five-minute-chart-group';
import { TimeSharingChartGroup } from './charts/time-sharing-chart-group';
import { WorkbenchGrid, WorkbenchLayout } from './workbench/workbench-grid';

function initApp() {
  const app = document.getElementById('app');
  if (!app) return;

  app.style.padding = '12px';
  app.style.boxSizing = 'border-box';

  // 标题和状态指示
  const header = document.createElement('div');
  header.style.color = '#fff';
  header.style.marginBottom = '10px';
  header.innerHTML = `
    <h2 style="margin: 0 0 6px 0">T+0 Chart Spike - Lightweight Charts</h2>
    <p style="margin: 0; color: #9ca3af">
      500 根 5 分钟 K 线 | 三栏工作台布局 | 5m + 分时 + 行情栏 | CZSC Overlay
    </p>
    <div id="follow-state" style="margin-top: 8px; color: #22c55e">
      5m 状态: 跟随最新 | 80 根 K 线 | 总数据: 500 根
    </div>
  `;
  app.appendChild(header);

  // 控制面板 - 第一行：跟随 / 增量 / 回放
  const controls1 = document.createElement('div');
  controls1.style.marginBottom = '8px';
  controls1.innerHTML = `
    <button id="follow-latest" style="background:#3b82f6;color:#fff;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;margin-right:8px">跟随最新</button>
    <button id="sim-increment" style="background:#22c55e;color:#fff;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;margin-right:8px">模拟增量更新</button>
    <button id="test-replay-truncate" style="background:#f59e0b;color:#fff;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;margin-right:8px">测试回放截断(含 Overlay)</button>
    <button id="reset-data" style="background:#6b7280;color:#fff;border:none;padding:8px 16px;border-radius:4px;cursor:pointer">重置数据</button>
  `;
  app.appendChild(controls1);

  // 控制面板 - 第二行：工作台布局（5m / 分时 / 行情栏 三栏）
  const controls2 = document.createElement('div');
  controls2.style.marginBottom = '8px';
  controls2.innerHTML = `
    <span style="color:#9ca3af;margin-right:8px">工作台布局:</span>
    <button id="layout-main" data-layout="main-priority" style="background:#8b5cf6;color:#fff;border:none;padding:6px 12px;border-radius:4px;cursor:pointer;margin-right:4px">主图优先 64/36</button>
    <button id="layout-half" data-layout="half-half" style="background:#6b7280;color:#fff;border:none;padding:6px 12px;border-radius:4px;cursor:pointer;margin-right:4px">左右各半 50/50</button>
    <button id="layout-hide" data-layout="hide-time" style="background:#6b7280;color:#fff;border:none;padding:6px 12px;border-radius:4px;cursor:pointer">隐藏分时</button>
  `;
  app.appendChild(controls2);

  // 控制面板 - 第三行：图层开关
  const controls3 = document.createElement('div');
  controls3.style.marginBottom = '12px';
  controls3.innerHTML = `
    <span style="color:#9ca3af;margin-right:8px">图层:</span>
    <button id="toggle-boll" style="background:#8b5cf6;color:#fff;border:none;padding:6px 12px;border-radius:4px;cursor:pointer;margin-right:4px">BOLL</button>
    <button id="toggle-czsc" style="background:#6b7280;color:#fff;border:none;padding:6px 12px;border-radius:4px;cursor:pointer;margin-right:4px">CZSC Overlay</button>
  `;
  app.appendChild(controls3);

  // 创建工作台三栏容器
  const workbenchContainer = document.createElement('div');
  workbenchContainer.style.width = '100%';
  workbenchContainer.style.height = '620px';
  workbenchContainer.style.border = '1px solid #374151';
  app.appendChild(workbenchContainer);

  const fiveMinuteArea = document.createElement('div');
  fiveMinuteArea.id = 'five-minute-area';
  const timeSharingArea = document.createElement('div');
  timeSharingArea.id = 'time-sharing-area';
  const quoteArea = document.createElement('div');
  quoteArea.id = 'quote-area';
  quoteArea.style.background = '#0f0f0f';
  quoteArea.style.color = '#9ca3af';
  quoteArea.style.fontSize = '12px';

  workbenchContainer.appendChild(fiveMinuteArea);
  workbenchContainer.appendChild(timeSharingArea);
  workbenchContainer.appendChild(quoteArea);

  // 初始化数据副本
  let current5mData = [...FIVE_MINUTE_FIXTURE];
  // 1 分钟图组使用同一 fixture 的前 120 根作为简化数据（验证用，不实现真实 1 分钟数据源）
  let current1mData = FIVE_MINUTE_FIXTURE.slice(0, 120);

  // 创建工作台布局
  const workbench = new WorkbenchGrid({
    container: workbenchContainer,
    fiveMinuteArea,
    timeSharingArea,
    quoteArea,
  });

  // 创建 5 分钟图表组
  let fiveMinuteGroup = new FiveMinuteChartGroup(
    { container: fiveMinuteArea, volHeight: 100, macdHeight: 100 },
    current5mData
  );

  // 创建 1 分钟图表组（分时区）
  let timeSharingGroup = new TimeSharingChartGroup(
    { container: timeSharingArea, volHeight: 80, macdHeight: 80 },
    current1mData
  );

  // 渲染行情栏内容
  const renderQuoteBar = () => {
    const lastBar = current5mData[current5mData.length - 1];
    quoteArea.innerHTML = `
      <div style="font-weight:bold;color:#fff;margin-bottom:8px">行情</div>
      <div style="margin-bottom:6px">代码: 600000</div>
      <div style="margin-bottom:6px">名称: 浦发银行</div>
      <div style="margin-bottom:6px">最新: <span style="color:${lastBar.close >= lastBar.open ? '#ef4444' : '#22c55e'}">${lastBar.close.toFixed(2)}</span></div>
      <div style="margin-bottom:6px">开: ${lastBar.open.toFixed(2)}</div>
      <div style="margin-bottom:6px">高: ${lastBar.high.toFixed(2)}</div>
      <div style="margin-bottom:6px">低: ${lastBar.low.toFixed(2)}</div>
      <div style="margin-bottom:6px">量: ${lastBar.volume.toLocaleString()}</div>
      <div style="margin-bottom:6px">量比: --</div>
      <div style="margin-bottom:6px">换手率: --</div>
      <div style="margin-bottom:6px">委比: --</div>
      <div style="margin-top:12px;color:#6b7280">行情栏固定 280px</div>
      <div style="color:#6b7280">不参与三行对齐</div>
    `;
  };
  renderQuoteBar();

  // 图层状态
  let bollVisible = true;
  let czscVisible = false;

  // 生成 CZSC overlay 数据
  const generateCZSCData = (bars: BarData[]) => {
    const buyPoints: Array<{ time: string; price: number }> = [];
    const sellPoints: Array<{ time: string; price: number }> = [];
    const biLines: Array<{ start: { time: string; price: number }; end: { time: string; price: number } }> = [];

    const step = Math.floor(bars.length / 10);
    for (let i = step; i < bars.length; i += step) {
      if (i % 2 === 0) {
        buyPoints.push({ time: bars[i].time, price: bars[i].low * 0.998 });
      } else {
        sellPoints.push({ time: bars[i].time, price: bars[i].high * 1.002 });
      }
    }

    for (let i = 0; i < bars.length - step; i += step * 2) {
      biLines.push({
        start: { time: bars[i].time, price: bars[i].low },
        end: { time: bars[i + step].time, price: bars[i + step].high },
      });
    }

    const zhongShu = generateMockZhongShu(bars);

    return { buyPoints, sellPoints, biLines, zhongShu };
  };

  const applyCZSC = (group: FiveMinuteChartGroup, bars: BarData[]) => {
    const czsc = generateCZSCData(bars);
    group.setCZSCBuyPoints(czsc.buyPoints);
    group.setCZSCSellPoints(czsc.sellPoints);
    group.setBiLines(czsc.biLines);
    group.setZhongShu(czsc.zhongShu);
    group.setCZSCVisible(czscVisible);
  };

  applyCZSC(fiveMinuteGroup, current5mData);

  // 状态显示
  const updateStateDisplay = () => {
    const stateEl = document.getElementById('follow-state');
    if (stateEl) {
      const state = fiveMinuteGroup.getState();
      const color = state.followState === 'following' ? '#22c55e' : '#f59e0b';
      stateEl.style.color = color;
      stateEl.textContent = `5m 状态: ${state.followState === 'following' ? '跟随最新' : '手动浏览'} | ${state.visibleEnd - state.visibleStart} 根 K 线 | 总数据: ${state.logicalToTime.length} 根`;
    }
  };

  // 布局按钮状态
  const updateLayoutButtons = (activeLayout: WorkbenchLayout) => {
    document.querySelectorAll('[data-layout]').forEach(btn => {
      const layout = btn.getAttribute('data-layout');
      btn.style.background = layout === activeLayout ? '#8b5cf6' : '#6b7280';
    });
  };

  // 设置状态回调
  fiveMinuteGroup.setOnStateChange(updateStateDisplay);

  // 跟随最新
  document.getElementById('follow-latest')?.addEventListener('click', () => {
    fiveMinuteGroup.followLatest();
  });

  // 模拟增量更新（修复时间顺序）
  document.getElementById('sim-increment')?.addEventListener('click', () => {
    const lastBar = current5mData[current5mData.length - 1];
    const newBars: BarData[] = [];
    let currentPrice = lastBar.close;
    let prevTime = lastBar.time;

    for (let i = 0; i < 5; i++) {
      const time = nextBarTime(prevTime);
      const change = (Math.random() - 0.5) * 0.01;
      const open = currentPrice;
      const close = open * (1 + change);
      const high = Math.max(open, close) * (1 + Math.random() * 0.005);
      const low = Math.min(open, close) * (1 - Math.random() * 0.005);
      const volume = Math.floor(1000000 + Math.random() * 5000000);

      newBars.push({
        time,
        open: parseFloat(open.toFixed(2)),
        high: parseFloat(high.toFixed(2)),
        low: parseFloat(low.toFixed(2)),
        close: parseFloat(close.toFixed(2)),
        volume,
      });
      currentPrice = close;
      prevTime = time;
    }

    fiveMinuteGroup.appendData(newBars);
    current5mData = [...current5mData, ...newBars];
    renderQuoteBar();
  });

  // 测试回放截断（含 overlay 同步截断）
  document.getElementById('test-replay-truncate')?.addEventListener('click', () => {
    // 截断到当前数据 90% 位置（包含截断点，语义为 time <= T）
    const truncateIndex = Math.floor(current5mData.length * 0.9);
    const truncateTime = current5mData[truncateIndex].time;
    fiveMinuteGroup.truncateAtTime(truncateTime);
    current5mData = current5mData.slice(0, truncateIndex + 1);
    renderQuoteBar();
  });

  // 重置数据（已知演示缺陷：重置后按钮可能绑定旧实例，记录为限制）
  document.getElementById('reset-data')?.addEventListener('click', () => {
    fiveMinuteGroup.destroy();
    timeSharingGroup.destroy();
    current5mData = [...FIVE_MINUTE_FIXTURE];
    current1mData = FIVE_MINUTE_FIXTURE.slice(0, 120);

    fiveMinuteGroup = new FiveMinuteChartGroup(
      { container: fiveMinuteArea, volHeight: 100, macdHeight: 100 },
      current5mData
    );
    timeSharingGroup = new TimeSharingChartGroup(
      { container: timeSharingArea, volHeight: 80, macdHeight: 80 },
      current1mData
    );

    applyCZSC(fiveMinuteGroup, current5mData);
    fiveMinuteGroup.setOnStateChange(updateStateDisplay);
    renderQuoteBar();
    updateStateDisplay();
  });

  // 工作台布局切换
  document.getElementById('layout-main')?.addEventListener('click', () => {
    workbench.setLayout('main-priority');
    updateLayoutButtons('main-priority');
  });
  document.getElementById('layout-half')?.addEventListener('click', () => {
    workbench.setLayout('half-half');
    updateLayoutButtons('half-half');
  });
  document.getElementById('layout-hide')?.addEventListener('click', () => {
    workbench.setLayout('hide-time');
    updateLayoutButtons('hide-time');
  });

  // 图层开关
  document.getElementById('toggle-boll')?.addEventListener('click', () => {
    bollVisible = !bollVisible;
    fiveMinuteGroup.setBOLLVisible(bollVisible);
    const btn = document.getElementById('toggle-boll') as HTMLButtonElement;
    if (btn) btn.style.background = bollVisible ? '#8b5cf6' : '#6b7280';
  });

  document.getElementById('toggle-czsc')?.addEventListener('click', () => {
    czscVisible = !czscVisible;
    fiveMinuteGroup.setCZSCVisible(czscVisible);
    const btn = document.getElementById('toggle-czsc') as HTMLButtonElement;
    if (btn) btn.style.background = czscVisible ? '#8b5cf6' : '#6b7280';
  });

  updateStateDisplay();
  updateLayoutButtons('main-priority');

  // 暴露到全局，便于测试
  (window as any).fiveMinuteGroup = fiveMinuteGroup;
  (window as any).timeSharingGroup = timeSharingGroup;
  (window as any).workbench = workbench;
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initApp);
} else {
  initApp();
}
