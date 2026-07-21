import { describe, it, expect } from 'vitest';
import {
  createInitialState,
  followLatest,
  setManualRange,
  appendData,
  getVisibleTimes,
  truncateAtTime,
  calculateVisibleCount,
  isAtLatestEdge,
  ChartGroupState,
} from '../src/models/chart-group-state';

describe('ChartGroupState', () => {
  // 创建测试用的 100 个时间戳
  const testTimes = Array.from({ length: 100 }, (_, i) => {
    const hour = 9 + Math.floor(i / 12);
    const minute = (i % 12) * 5;
    return `2026-07-18 ${hour.toString().padStart(2, '0')}:${minute.toString().padStart(2, '0')}:00`;
  });

  describe('createInitialState', () => {
    it('should create state with correct mappings', () => {
      const state = createInitialState(testTimes);
      expect(state.logicalToTime.length).toBe(100);
      expect(state.timeToLogical.get(testTimes[0])).toBe(0);
      expect(state.timeToLogical.get(testTimes[99])).toBe(99);
    });

    it('should start in following mode with latest bars visible', () => {
      const state = createInitialState(testTimes);
      expect(state.followState).toBe('following');
      // visibleEnd 是排他右端，等于 length 时表示包含最后一根
      expect(state.visibleEnd).toBe(100);
      expect(isAtLatestEdge(state)).toBe(true);
    });
  });

  describe('calculateVisibleCount', () => {
    it('should calculate based on slot width', () => {
      expect(calculateVisibleCount(800, 10)).toBe(80);
      expect(calculateVisibleCount(100, 10)).toBe(10);
      expect(calculateVisibleCount(5, 10)).toBe(1);
    });
  });

  describe('followLatest', () => {
    it('should align right edge with latest data', () => {
      const state = createInitialState(testTimes);
      const result = followLatest(state, 50);
      expect(result.visibleStart).toBe(50);
      // visibleEnd 等于 length，表示包含最后一根
      expect(result.visibleEnd).toBe(100);
      expect(result.followState).toBe('following');
    });

    it('should handle when requesting more than available', () => {
      const state = createInitialState(testTimes);
      const result = followLatest(state, 200);
      expect(result.visibleStart).toBe(0);
      expect(result.visibleEnd).toBe(100);
    });
  });

  describe('setManualRange', () => {
    it('should switch to manual mode', () => {
      let state = createInitialState(testTimes);
      state = setManualRange(state, 20, 70);
      expect(state.visibleStart).toBe(20);
      expect(state.visibleEnd).toBe(70);
      expect(state.followState).toBe('manual');
    });

    it('should stay in following mode only when visibleEnd equals length', () => {
      let state = createInitialState(testTimes);
      // visibleEnd = 99 表示最后一根（索引 99）尚未进入范围
      state = setManualRange(state, 50, 99);
      expect(state.followState).toBe('manual');
      expect(isAtLatestEdge(state)).toBe(false);

      // visibleEnd = 100 表示最后一根（索引 99）已进入范围
      state = setManualRange(state, 50, 100);
      expect(state.followState).toBe('following');
      expect(isAtLatestEdge(state)).toBe(true);
    });

    it('should clamp out of bounds', () => {
      const state = createInitialState(testTimes);
      const result = setManualRange(state, -10, 200);
      expect(result.visibleStart).toBe(0);
      expect(result.visibleEnd).toBe(100);
      expect(result.followState).toBe('following');
    });
  });

  describe('appendData', () => {
    it('should add new data and maintain follow state', () => {
      let state = createInitialState(testTimes.slice(0, 80));
      state = followLatest(state, 20);
      expect(state.visibleStart).toBe(60);
      expect(state.visibleEnd).toBe(80);

      state = appendData(state, testTimes.slice(80, 100));
      expect(state.logicalToTime.length).toBe(100);
      // Should still be following and showing last 20
      expect(state.visibleStart).toBe(80);
      expect(state.visibleEnd).toBe(100);
      expect(state.followState).toBe('following');
    });

    it('should not scroll when in manual mode', () => {
      let state = createInitialState(testTimes.slice(0, 80));
      state = setManualRange(state, 30, 50); // Manual mode

      state = appendData(state, testTimes.slice(80, 100));
      expect(state.followState).toBe('manual');
      // Should still show 30-50, not scroll
      expect(state.visibleStart).toBe(30);
      expect(state.visibleEnd).toBe(50);
    });
  });

  describe('getVisibleTimes', () => {
    it('should return only times in visible range', () => {
      const state = createInitialState(testTimes);
      const manualState = setManualRange(state, 10, 20);
      const visible = getVisibleTimes(manualState);
      expect(visible.length).toBe(10);
      expect(visible[0]).toBe(testTimes[10]);
      expect(visible[9]).toBe(testTimes[19]);
    });
  });

  describe('truncateAtTime', () => {
    it('should remove data after max time', () => {
      const state = createInitialState(testTimes);
      const maxTime = testTimes[50];
      const truncated = truncateAtTime(state, maxTime);

      expect(truncated.logicalToTime.length).toBe(51); // 0-50 inclusive
      expect(truncated.logicalToTime[50]).toBe(maxTime);
      expect(truncated.followState).toBe('following');
    });

    it('should adjust viewport if it was beyond truncation point', () => {
      const state = createInitialState(testTimes);
      // Set view to last 10 (90-100)
      const manualState = setManualRange(state, 90, 100);
      // Truncate to index 50
      const truncated = truncateAtTime(manualState, testTimes[50]);
      // Should reset to show last N of remaining
      expect(truncated.visibleEnd).toBe(51);
      expect(truncated.visibleStart).toBeLessThanOrEqual(51);
    });
  });

  describe('state transition: follow -> manual -> follow', () => {
    it('should transition correctly', () => {
      let state = createInitialState(testTimes);
      expect(state.followState).toBe('following');

      // Drag away from latest
      state = setManualRange(state, 0, 50);
      expect(state.followState).toBe('manual');

      // Drag back to latest edge (visibleEnd = length)
      state = setManualRange(state, 50, 100);
      expect(state.followState).toBe('following');
    });
  });

  describe('manual range preservation during data update', () => {
    it('should keep manual position when new data arrives', () => {
      let state = createInitialState(testTimes.slice(0, 80));
      state = setManualRange(state, 10, 30); // Manual mode, viewing old data

      // New K lines arrive
      state = appendData(state, testTimes.slice(80, 100));

      // Should still be viewing same old range
      expect(state.visibleStart).toBe(10);
      expect(state.visibleEnd).toBe(30);
      expect(state.followState).toBe('manual');
    });
  });

  describe('latest-edge boundary cases', () => {
    it('should not be at latest edge when visibleEnd is length - 1', () => {
      const state = createInitialState(testTimes);
      const manualState = setManualRange(state, 0, 99);
      expect(isAtLatestEdge(manualState)).toBe(false);
      expect(manualState.followState).toBe('manual');
    });

    it('should be at latest edge when visibleEnd equals length', () => {
      const state = createInitialState(testTimes);
      const manualState = setManualRange(state, 0, 100);
      expect(isAtLatestEdge(manualState)).toBe(true);
      expect(manualState.followState).toBe('following');
    });

    it('should handle empty data', () => {
      const state = createInitialState([], 10);
      expect(isAtLatestEdge(state)).toBe(true);
    });

    it('should handle single data point', () => {
      const state = createInitialState([testTimes[0]], 10);
      expect(state.visibleEnd).toBe(1);
      expect(isAtLatestEdge(state)).toBe(true);
    });
  });
});
