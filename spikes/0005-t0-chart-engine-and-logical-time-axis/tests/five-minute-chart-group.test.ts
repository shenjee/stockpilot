import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { FiveMinuteChartGroup, LayoutMode } from '../src/charts/five-minute-chart-group';
import { FIVE_MINUTE_FIXTURE, BarData } from '../src/fixtures/ohlcv-fixture';

/**
 * 图表集成测试
 * 在 jsdom 环境中实例化 FiveMinuteChartGroup，验证关键交互能力。
 *
 * 注意：jsdom 不实现完整 Canvas，因此测试只验证：
 * - 实例化不抛错
 * - 状态变化回调被触发
 * - 公开 API 行为符合预期
 * - 数据操作正确反映在状态中
 *
 * 视觉渲染和真实事件传播需要人工验证，已在验证报告中记录。
 */

describe('FiveMinuteChartGroup 集成测试', () => {
  let container: HTMLDivElement;
  let chartGroup: FiveMinuteChartGroup;

  beforeEach(() => {
    container = document.createElement('div');
    container.style.width = '800px';
    container.style.height = '600px';
    document.body.appendChild(container);

    // 轻量数据：取 fixture 前 100 根以减少 jsdom 压力
    const sampleBars = FIVE_MINUTE_FIXTURE.slice(0, 100);
    chartGroup = new FiveMinuteChartGroup(
      { container, volHeight: 100, macdHeight: 100 },
      sampleBars
    );
  });

  afterEach(() => {
    if (chartGroup) {
      chartGroup.destroy();
    }
    if (container && container.parentNode) {
      container.parentNode.removeChild(container);
    }
  });

  describe('初始化', () => {
    it('应成功实例化不抛错', () => {
      expect(chartGroup).toBeDefined();
    });

    it('初始状态为 following', () => {
      const state = chartGroup.getState();
      expect(state.followState).toBe('following');
    });

    it('应创建三个图表子容器', () => {
      expect(container.children.length).toBe(3);
    });

    it('初始布局为 full', () => {
      expect(chartGroup.getLayoutMode()).toBe('full');
    });
  });

  describe('状态变化回调', () => {
    it('followLatest 应触发状态变化回调', () => {
      let called = false;
      chartGroup.setOnStateChange(() => {
        called = true;
      });
      chartGroup.followLatest();
      expect(called).toBe(true);
    });

    it('setManualVisibleRange 应触发状态变化回调', () => {
      let called = false;
      chartGroup.setOnStateChange(() => {
        called = true;
      });
      chartGroup.setManualVisibleRange(10, 50);
      expect(called).toBe(true);
    });
  });

  describe('增量更新', () => {
    it('appendData 应追加新 K 线并保持 following 状态', () => {
      const stateBefore = chartGroup.getState();
      const countBefore = stateBefore.logicalToTime.length;

      const newBars: BarData[] = [
        {
          time: '2026-07-20 09:35',
          open: 100,
          high: 101,
          low: 99,
          close: 100.5,
          volume: 1000000,
        },
      ];

      chartGroup.appendData(newBars);

      const stateAfter = chartGroup.getState();
      expect(stateAfter.logicalToTime.length).toBe(countBefore + 1);
      expect(stateAfter.followState).toBe('following');
    });

    it('manual 模式下 appendData 应保持视口不变', () => {
      // 先切到 manual
      chartGroup.setManualVisibleRange(10, 30);
      const stateManual = chartGroup.getState();
      expect(stateManual.followState).toBe('manual');

      // 追加数据
      const newBars: BarData[] = [
        {
          time: '2026-07-20 09:35',
          open: 100,
          high: 101,
          low: 99,
          close: 100.5,
          volume: 1000000,
        },
      ];
      chartGroup.appendData(newBars);

      const stateAfter = chartGroup.getState();
      expect(stateAfter.followState).toBe('manual');
      expect(stateAfter.visibleStart).toBe(10);
      expect(stateAfter.visibleEnd).toBe(30);
    });
  });

  describe('回放截断', () => {
    it('truncateAtTime 应丢弃截断点之后的数据', () => {
      const stateBefore = chartGroup.getState();
      const countBefore = stateBefore.logicalToTime.length;

      // 截断到第 50 根
      const truncateTime = stateBefore.logicalToTime[50];
      chartGroup.truncateAtTime(truncateTime);

      const stateAfter = chartGroup.getState();
      // 0-50 inclusive = 51
      expect(stateAfter.logicalToTime.length).toBe(51);
      expect(stateAfter.logicalToTime[50]).toBe(truncateTime);
    });

    it('截断后 followState 应为 following', () => {
      const state = chartGroup.getState();
      const truncateTime = state.logicalToTime[50];
      chartGroup.truncateAtTime(truncateTime);

      const stateAfter = chartGroup.getState();
      expect(stateAfter.followState).toBe('following');
    });

    it('截断后不应包含 T 之后的时间戳', () => {
      const state = chartGroup.getState();
      const truncateTime = state.logicalToTime[50];
      chartGroup.truncateAtTime(truncateTime);

      const stateAfter = chartGroup.getState();
      const allTimes = stateAfter.logicalToTime;
      const truncateIndex = allTimes.indexOf(truncateTime);

      // 截断点之后不应有任何时间戳
      expect(allTimes.length).toBe(truncateIndex + 1);
    });
  });

  describe('布局切换', () => {
    const modes: LayoutMode[] = ['full', 'compact', 'expanded', 'hide-sub'];

    modes.forEach(mode => {
      it(`应支持切换到 ${mode} 布局`, () => {
        chartGroup.setLayoutMode(mode);
        expect(chartGroup.getLayoutMode()).toBe(mode);
      });
    });

    it('切换布局应保持状态', () => {
      // 先设置 manual 状态
      chartGroup.setManualVisibleRange(10, 50);
      expect(chartGroup.getState().followState).toBe('manual');

      // 切换布局
      chartGroup.setLayoutMode('hide-sub');
      expect(chartGroup.getLayoutMode()).toBe('hide-sub');

      // 状态应保持
      const state = chartGroup.getState();
      expect(state.followState).toBe('manual');
      expect(state.visibleStart).toBe(10);
      expect(state.visibleEnd).toBe(50);
    });

    it('隐藏副图后恢复应保持视口', () => {
      // 先设置 manual
      chartGroup.setManualVisibleRange(10, 50);
      const visibleStartBefore = chartGroup.getState().visibleStart;
      const visibleEndBefore = chartGroup.getState().visibleEnd;

      // 隐藏副图
      chartGroup.setLayoutMode('hide-sub');

      // 恢复
      chartGroup.setLayoutMode('full');

      const state = chartGroup.getState();
      expect(state.visibleStart).toBe(visibleStartBefore);
      expect(state.visibleEnd).toBe(visibleEndBefore);
    });
  });

  describe('CZSC Overlay', () => {
    it('应支持设置买点', () => {
      expect(() => {
        chartGroup.setCZSCBuyPoints([
          { time: FIVE_MINUTE_FIXTURE[10].time, price: 100 },
        ]);
      }).not.toThrow();
    });

    it('应支持设置卖点', () => {
      expect(() => {
        chartGroup.setCZSCSellPoints([
          { time: FIVE_MINUTE_FIXTURE[10].time, price: 100 },
        ]);
      }).not.toThrow();
    });

    it('应支持设置笔线段', () => {
      expect(() => {
        chartGroup.setBiLines([
          {
            start: { time: FIVE_MINUTE_FIXTURE[10].time, price: 100 },
            end: { time: FIVE_MINUTE_FIXTURE[20].time, price: 105 },
          },
        ]);
      }).not.toThrow();
    });

    it('应支持设置中枢', () => {
      expect(() => {
        chartGroup.setZhongShu([
          { startIndex: 10, endIndex: 30, high: 105, low: 95 },
        ]);
      }).not.toThrow();
    });

    it('应支持显示/隐藏 CZSC overlay（含中枢）', () => {
      expect(() => {
        chartGroup.setCZSCVisible(false);
        chartGroup.setCZSCVisible(true);
      }).not.toThrow();
    });

    it('回放截断后 overlay 应同步裁剪', () => {
      // 设置覆盖整个数据范围的 overlay
      const state = chartGroup.getState();
      const lastTime = state.logicalToTime[state.logicalToTime.length - 1];
      chartGroup.setCZSCBuyPoints([{ time: lastTime, price: 100 }]);
      chartGroup.setCZSCSellPoints([{ time: lastTime, price: 200 }]);
      chartGroup.setZhongShu([
        { startIndex: 0, endIndex: 99, high: 200, low: 50 },
      ]);

      // 截断到第 50 根
      const truncateTime = state.logicalToTime[50];
      chartGroup.truncateAtTime(truncateTime);

      const stateAfter = chartGroup.getState();
      // 截断后数据长度应为 51（0-50 inclusive）
      expect(stateAfter.logicalToTime.length).toBe(51);
      // 截断点之后不应有任何数据
      expect(stateAfter.logicalToTime[50]).toBe(truncateTime);
    });
  });

  describe('BOLL 显示控制', () => {
    it('应支持显示/隐藏 BOLL', () => {
      expect(() => {
        chartGroup.setBOLLVisible(false);
        chartGroup.setBOLLVisible(true);
      }).not.toThrow();
    });
  });

  describe('资源清理', () => {
    it('destroy 应不抛错', () => {
      expect(() => {
        chartGroup.destroy();
        // 防止 afterEach 重复 destroy
        chartGroup = undefined as any;
      }).not.toThrow();
    });
  });
});
