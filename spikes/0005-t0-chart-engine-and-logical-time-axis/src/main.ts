import { FIVE_MINUTE_FIXTURE } from './fixtures/ohlcv-fixture';
import { FiveMinuteChartGroup } from './charts/five-minute-chart-group';

function initApp() {
  const app = document.getElementById('app');
  if (!app) return;

  // 创建测试容器
  const container = document.createElement('div');
  container.style.width = '100%';
  container.style.height = '600px';
  container.style.padding = '20px';
  app.appendChild(container);

  // 标题和状态指示
  const header = document.createElement('div');
  header.style.color = '#fff';
  header.style.marginBottom = '10px';
  header.innerHTML = `
    <h2 style="margin: 0 0 10px 0">T+0 Chart Spike - Lightweight Charts</h2>
    <p style="margin: 0; color: #9ca3af">
      500 根 5 分钟 K 线 | 跨交易日连续显示 | VOL + MACD 同步
    </p>
    <div id="follow-state" style="margin-top: 8px; color: #22c55e">
      状态: 跟随最新
    </div>
  `;
  app.appendChild(header);

  // 控制面板
  const controls = document.createElement('div');
  controls.style.marginBottom = '15px';
  controls.innerHTML = `
    <button id="follow-latest" style="
      background: #3b82f6;
      color: white;
      border: none;
      padding: 8px 16px;
      border-radius: 4px;
      cursor: pointer;
      margin-right: 10px;
    ">跟随最新</button>
    <button id="sim-increment" style="
      background: #22c55e;
      color: white;
      border: none;
      padding: 8px 16px;
      border-radius: 4px;
      cursor: pointer;
      margin-right: 10px;
    ">模拟增量更新</button>
    <button id="test-replay-truncate" style="
      background: #f59e0b;
      color: white;
      border: none;
      padding: 8px 16px;
      border-radius: 4px;
      cursor: pointer;
    ">测试回放截断</button>
  `;
  app.appendChild(controls);

  // 创建图表组
  const chartGroup = new FiveMinuteChartGroup(
    { container },
    FIVE_MINUTE_FIXTURE
  );

  // 更新状态显示
  const updateStateDisplay = () => {
    const stateEl = document.getElementById('follow-state');
    if (stateEl) {
      const state = chartGroup.getState();
      const color = state.followState === 'following' ? '#22c55e' : '#f59e0b';
      stateEl.style.color = color;
      stateEl.textContent = `状态: ${state.followState === 'following' ? '跟随最新' : '手动浏览'} | 显示: ${state.visibleEnd - state.visibleStart} 根 K 线`;
    }
  };

  // 绑定按钮事件
  document.getElementById('follow-latest')?.addEventListener('click', () => {
    chartGroup.followLatest();
    updateStateDisplay();
  });

  document.getElementById('sim-increment')?.addEventListener('click', () => {
    // 这里应该调用 appendData 方法
    alert('增量更新功能需要扩展图表类实现');
  });

  document.getElementById('test-replay-truncate')?.addEventListener('click', () => {
    // 截断到第 450 根 K 线
    const truncateTime = FIVE_MINUTE_FIXTURE[450].time;
    alert(`回放截断测试: 截断到 ${truncateTime}`);
    // 实际实现需要扩展状态管理
  });

  // 初始显示
  updateStateDisplay();

  // 监听可见范围变化（需要在图表类中暴露事件）
  // 这是一个简化版本
  setInterval(updateStateDisplay, 500);
}

// 启动
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initApp);
} else {
  initApp();
}
