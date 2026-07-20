import { describe, it, expect } from 'vitest';
import {
  FIVE_MINUTE_FIXTURE,
  generate500BarsFixture,
  validateFixture,
  BarData,
} from '../src/fixtures/ohlcv-fixture';

describe('OHLCV Fixture', () => {
  describe('数据量', () => {
    it('应生成恰好 500 根 K 线', () => {
      expect(FIVE_MINUTE_FIXTURE.length).toBe(500);
    });

    it('每次生成结果应确定且相同', () => {
      const first = generate500BarsFixture();
      const second = generate500BarsFixture();
      expect(first).toEqual(second);
    });
  });

  describe('交易日规则', () => {
    const validation = validateFixture(FIVE_MINUTE_FIXTURE);

    it('fixture 应通过校验', () => {
      expect(validation.valid).toBe(true);
      if (!validation.valid) {
        console.error('Fixture 校验错误:', validation.errors);
      }
    });

    it('不包含周末 K 线', () => {
      const weekendBars = FIVE_MINUTE_FIXTURE.filter(bar => {
        const date = new Date(bar.time);
        const day = date.getDay();
        return day === 0 || day === 6;
      });
      expect(weekendBars.length).toBe(0);
    });

    it('每个交易日最多 48 根 K 线', () => {
      const dayCounts: Record<string, number> = {};
      FIVE_MINUTE_FIXTURE.forEach(bar => {
        const day = bar.time.split(' ')[0];
        dayCounts[day] = (dayCounts[day] || 0) + 1;
      });

      Object.entries(dayCounts).forEach(([day, count]) => {
        expect(count).toBeLessThanOrEqual(48);
      });
    });

    it('不包含午休时段（11:30-13:00）的 K 线', () => {
      const lunchBars = FIVE_MINUTE_FIXTURE.filter(bar => {
        const timePart = bar.time.split(' ')[1];
        const [hour, min] = timePart.split(':').map(Number);
        const minutes = hour * 60 + min;
        // 11:30 之后到 13:00 之前
        return minutes > 11 * 60 + 30 && minutes < 13 * 60;
      });
      expect(lunchBars.length).toBe(0);
    });

    it('时间严格递增且无重复', () => {
      for (let i = 1; i < FIVE_MINUTE_FIXTURE.length; i++) {
        const prevTime = new Date(FIVE_MINUTE_FIXTURE[i - 1].time).getTime();
        const currTime = new Date(FIVE_MINUTE_FIXTURE[i].time).getTime();
        expect(currTime).toBeGreaterThan(prevTime);
      }
    });

    it('覆盖至少 5 个交易日', () => {
      const days = new Set(FIVE_MINUTE_FIXTURE.map(bar => bar.time.split(' ')[0]));
      expect(days.size).toBeGreaterThanOrEqual(5);
    });

    it('最后一个交易日是部分数据（盘中状态）', () => {
      const dayCounts: Record<string, number> = {};
      FIVE_MINUTE_FIXTURE.forEach(bar => {
        const day = bar.time.split(' ')[0];
        dayCounts[day] = (dayCounts[day] || 0) + 1;
      });

      const days = Object.keys(dayCounts).sort();
      const lastDay = days[days.length - 1];
      expect(dayCounts[lastDay]).toBeLessThan(48);
    });
  });

  describe('数据完整性', () => {
    it('每根 K 线的 high >= max(open, close) 且 low <= min(open, close)', () => {
      FIVE_MINUTE_FIXTURE.forEach((bar, i) => {
        const maxBody = Math.max(bar.open, bar.close);
        const minBody = Math.min(bar.open, bar.close);
        expect(bar.high).toBeGreaterThanOrEqual(maxBody);
        expect(bar.low).toBeLessThanOrEqual(minBody);
      });
    });

    it('每根 K 线的 volume 为正数', () => {
      FIVE_MINUTE_FIXTURE.forEach(bar => {
        expect(bar.volume).toBeGreaterThan(0);
      });
    });

    it('价格为正数', () => {
      FIVE_MINUTE_FIXTURE.forEach(bar => {
        expect(bar.open).toBeGreaterThan(0);
        expect(bar.high).toBeGreaterThan(0);
        expect(bar.low).toBeGreaterThan(0);
        expect(bar.close).toBeGreaterThan(0);
      });
    });
  });
});
