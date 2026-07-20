import { describe, it, expect } from 'vitest';
import {
  createInitialState,
  followLatest,
  setManualRange,
  appendData,
  getVisibleTimes,
  truncateAtTime,
  calculateVisibleCount,
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
      expect(state.visibleEnd).toBe(100);
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

    it('should stay in following mode when at latest edge', () => {
      let state = createInitialState(testTimes);
      // Drag to latest edge (end - 1)
      state = setManualRange(state, 50, 99);
      expect(state.followState).toBe('following');
    });

    it('should clamp out of bounds', () => {
      const state = createInitialState(testTimes);
      const result = setManualRange(state, -10, 200);
      expect(result.visibleStart).toBe(0);
      expect(result.visibleEnd).toBe(100);
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

      // Drag back to latest edge (end - 1)
      state = setManualRange(state, 50, 99);
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
});
