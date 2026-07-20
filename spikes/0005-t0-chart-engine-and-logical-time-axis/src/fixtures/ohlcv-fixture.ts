/**
 * 确定性的 5 分钟 K 线 fixture
 * - 500 根真实交易时段形态的 5 分钟 K
 * - 跨越 5 个交易日
 * - 包含午休、隔夜、当日不足填满视口的场景
 */

export interface BarData {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

// 交易日期：2026-07-14 至 2026-07-18（5 个交易日）
const TRADING_DATES = ['2026-07-14', '2026-07-15', '2026-07-16', '2026-07-17', '2026-07-18'];

// 早盘：09:35-11:30 (24 根)，午盘：13:05-15:00 (24 根)，每天 48 根
const MORNING_START = 9 * 60 + 35; // 09:35
const MORNING_END = 11 * 60 + 30; // 11:30
const AFTERNOON_START = 13 * 60 + 5; // 13:05
const AFTERNOON_END = 15 * 60; // 15:00

function generateDayBars(date: string, startPrice: number, seed: number): { bars: BarData[]; lastPrice: number } {
  const bars: BarData[] = [];
  let currentPrice = startPrice;
  const random = seededRandom(seed);

  // 早盘
  for (let minute = MORNING_START; minute <= MORNING_END; minute += 5) {
    const hour = Math.floor(minute / 60);
    const min = minute % 60;
    const timeStr = `${date} ${hour.toString().padStart(2, '0')}:${min.toString().padStart(2, '0')}`;
    const bar = generateSingleBar(currentPrice, random);
    bars.push({ ...bar, time: timeStr });
    currentPrice = bar.close;
  }

  // 午盘
  for (let minute = AFTERNOON_START; minute <= AFTERNOON_END; minute += 5) {
    const hour = Math.floor(minute / 60);
    const min = minute % 60;
    const timeStr = `${date} ${hour.toString().padStart(2, '0')}:${min.toString().padStart(2, '0')}`;
    const bar = generateSingleBar(currentPrice, random);
    bars.push({ ...bar, time: timeStr });
    currentPrice = bar.close;
  }

  return { bars, lastPrice: currentPrice };
}

function generateSingleBar(startPrice: number, random: () => number): Omit<BarData, 'time'> {
  const volatility = 0.002; // 0.2%
  const trend = (random() - 0.48) * volatility; // 略微向上偏
  const open = startPrice;
  const close = open * (1 + trend);
  const high = Math.max(open, close) * (1 + random() * volatility);
  const low = Math.min(open, close) * (1 - random() * volatility);
  const volume = Math.floor(1000000 + random() * 5000000);

  return {
    open: parseFloat(open.toFixed(2)),
    high: parseFloat(high.toFixed(2)),
    low: parseFloat(low.toFixed(2)),
    close: parseFloat(close.toFixed(2)),
    volume,
  };
}

// 确定性伪随机数生成器
function seededRandom(seed: number): () => number {
  let s = seed;
  return () => {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
}

// 生成 500 根 K 线
export function generate500BarsFixture(): BarData[] {
  const allBars: BarData[] = [];
  let lastPrice = 100.00;
  let seed = 12345;

  // 首先生成前 4 个完整交易日（每个 48 根，共 192 根）
  for (let i = 0; i < 4; i++) {
    const { bars, lastPrice: newLast } = generateDayBars(TRADING_DATES[i], lastPrice, seed + i);
    allBars.push(...bars);
    lastPrice = newLast;
  }

  // 第 5 天只生成部分数据（模拟当日盘中）
  const partialBars = 20; // 只生成 20 根
  const { bars } = generateDayBars(TRADING_DATES[4], lastPrice, seed + 4);
  allBars.push(...bars.slice(0, partialBars));
  lastPrice = bars[partialBars - 1].close;

  // 如果不足 500 根，继续往前加历史交易日
  const additionalDays = Math.ceil((500 - allBars.length) / 48);
  for (let i = 1; i <= additionalDays; i++) {
    const prevDate = new Date(TRADING_DATES[0]);
    prevDate.setDate(prevDate.getDate() - i);
    const dateStr = prevDate.toISOString().split('T')[0];
    const { bars: histBars, lastPrice: newLast } = generateDayBars(dateStr, lastPrice, seed - i);
    allBars.unshift(...histBars);
    lastPrice = histBars[0].open;
  }

  // 精确截取到 500 根
  return allBars.slice(-500);
}

// 预生成的 fixture 数据
export const FIVE_MINUTE_FIXTURE: BarData[] = generate500BarsFixture();

// MACD 计算
export function calculateMACD(bars: BarData[]): Array<{ time: string; macd: number; signal: number; histogram: number }> {
  const closes = bars.map(b => b.close);
  const ema12 = calculateEMA(closes, 12);
  const ema26 = calculateEMA(closes, 26);
  const macdLine = ema12.map((e12, i) => e12 - ema26[i]);
  const signalLine = calculateEMA(macdLine, 9);

  return bars.map((bar, i) => ({
    time: bar.time,
    macd: macdLine[i],
    signal: signalLine[i],
    histogram: macdLine[i] - signalLine[i],
  }));
}

function calculateEMA(values: number[], period: number): number[] {
  const k = 2 / (period + 1);
  const ema: number[] = [];
  ema[0] = values[0];
  for (let i = 1; i < values.length; i++) {
    ema[i] = values[i] * k + ema[i - 1] * (1 - k);
  }
  return ema;
}

// VOL MA
export function calculateVOLMA(bars: BarData[], period: number): number[] {
  const volumes = bars.map(b => b.volume);
  const ma: number[] = [];
  for (let i = 0; i < volumes.length; i++) {
    if (i < period - 1) {
      ma.push(0);
    } else {
      const sum = volumes.slice(i - period + 1, i + 1).reduce((a, b) => a + b, 0);
      ma.push(sum / period);
    }
  }
  return ma;
}

// 计算简单移动平均线
export function calculateSMA(values: number[], period: number): number[] {
  const result: number[] = [];
  for (let i = 0; i < values.length; i++) {
    if (i < period - 1) {
      result.push(NaN);
    } else {
      const sum = values.slice(i - period + 1, i + 1).reduce((a, b) => a + b, 0);
      result.push(sum / period);
    }
  }
  return result;
}

// BOLL
export function calculateBOLL(bars: BarData[], period: number = 20, stdDev: number = 2): Array<{ time: string; upper: number; middle: number; lower: number }> {
  const closes = bars.map(b => b.close);
  const middle = calculateSMA(closes, period);
  const upper: number[] = [];
  const lower: number[] = [];

  for (let i = 0; i < closes.length; i++) {
    if (i < period - 1) {
      upper.push(NaN);
      lower.push(NaN);
    } else {
      const slice = closes.slice(i - period + 1, i + 1);
      const mean = middle[i];
      const variance = slice.reduce((sum, v) => sum + Math.pow(v - mean, 2), 0) / period;
      const std = Math.sqrt(variance);
      upper.push(mean + stdDev * std);
      lower.push(mean - stdDev * std);
    }
  }

  return bars.map((bar, i) => ({
    time: bar.time,
    upper: upper[i],
    middle: middle[i],
    lower: lower[i],
  }));
}
