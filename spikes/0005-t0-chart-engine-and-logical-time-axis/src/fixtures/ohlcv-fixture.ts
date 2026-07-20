/**
 * 确定性的 5 分钟 K 线 fixture
 * - 500 根真实交易时段形态的 5 分钟 K
 * - 跨越多个交易日
 * - 只包含工作日，不生成周末 K 线
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

// 基础交易日：从 2026-07-18（周五）开始往前推
// 确保不包含周末
const BASE_DATE = '2026-07-18'; // 周五

// 早盘：09:35-11:30 (24 根)，午盘：13:05-15:00 (24 根)，每天 48 根
const MORNING_START = 9 * 60 + 35; // 09:35
const MORNING_END = 11 * 60 + 30; // 11:30
const AFTERNOON_START = 13 * 60 + 5; // 13:05
const AFTERNOON_END = 15 * 60; // 15:00

// 给定上一根 K 线时间字符串 "YYYY-MM-DD HH:MM"，返回下一根符合 A 股交易时段的时间字符串
// 跳过午休、隔夜、周末
export function nextBarTime(prevTimeStr: string): string {
  const [datePart, timePart] = prevTimeStr.split(' ');
  const [yearStr, monthStr, dayStr] = datePart.split('-');
  const [hourStr, minStr] = timePart.split(':');

  let year = parseInt(yearStr, 10);
  let month = parseInt(monthStr, 10);
  let day = parseInt(dayStr, 10);
  let hour = parseInt(hourStr, 10);
  let minute = parseInt(minStr, 10);

  minute += 5;
  if (minute >= 60) {
    minute -= 60;
    hour += 1;
  }

  // 跳过午休：11:35 -> 13:05（11:30 是早盘最后一根，下一根应该到 13:05）
  if (hour === 11 && minute > 30) {
    hour = 13;
    minute = 5;
  }

  // 跨日：15:00 是收盘最后一根，下一根到次日 09:35
  if (hour >= 15) {
    hour = 9;
    minute = 35;
    const date = new Date(Date.UTC(year, month - 1, day));
    do {
      date.setUTCDate(date.getUTCDate() + 1);
    } while (date.getUTCDay() === 0 || date.getUTCDay() === 6);
    year = date.getUTCFullYear();
    month = date.getUTCMonth() + 1;
    day = date.getUTCDate();
  }

  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')} ${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`;
}

// 将 "YYYY-MM-DD HH:MM" 时间字符串解析为 UTC 时间戳（秒）
// 用 Date.UTC 构造，让时间戳直接对应"市场时间"，避免 Lightweight Charts 按本地时区偏移显示
export function parseMarketTime(timeStr: string): number {
  const [datePart, timePart] = timeStr.split(' ');
  const [year, month, day] = datePart.split('-').map(Number);
  const [hour, minute] = (timePart || '00:00').split(':').map(Number);
  return Math.floor(Date.UTC(year, month - 1, day, hour, minute) / 1000);
}

// 获取前 N 个交易日（跳过周末）
function getPreviousTradingDays(baseDate: string, count: number): string[] {
  const dates: string[] = [];
  const current = new Date(baseDate);

  while (dates.length < count) {
    const day = current.getDay();
    // 0 = 周日, 6 = 周六
    if (day !== 0 && day !== 6) {
      dates.push(current.toISOString().split('T')[0]);
    }
    current.setDate(current.getDate() - 1);
  }

  return dates.reverse(); // 按时间升序排列
}

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

  // 需要约 11 个交易日（11 * 48 = 528）
  const tradingDays = getPreviousTradingDays(BASE_DATE, 11);

  // 前 10 个完整交易日（10 * 48 = 480 根）
  for (let i = 0; i < 10; i++) {
    const { bars, lastPrice: newLast } = generateDayBars(tradingDays[i], lastPrice, seed + i);
    allBars.push(...bars);
    lastPrice = newLast;
  }

  // 第 11 天只生成部分数据（模拟当日盘中，20 根）
  const partialBars = 20;
  const { bars } = generateDayBars(tradingDays[10], lastPrice, seed + 10);
  allBars.push(...bars.slice(0, partialBars));

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

// 中枢覆盖数据
export interface ZhongShu {
  startIndex: number;
  endIndex: number;
  high: number;
  low: number;
}

// 生成模拟中枢数据：在给定 K 线序列上找出若干区间
// 这是验证 overlay 显示用的模拟数据，不实现真实缠论算法
export function generateMockZhongShu(bars: BarData[]): ZhongShu[] {
  const result: ZhongShu[] = [];
  const segmentSize = 40;
  for (let i = 0; i + segmentSize - 1 < bars.length; i += segmentSize) {
    const segment = bars.slice(i, i + segmentSize);
    const high = Math.max(...segment.map(b => b.high));
    const low = Math.min(...segment.map(b => b.low));
    result.push({
      startIndex: i,
      endIndex: i + segmentSize - 1,
      high: parseFloat(high.toFixed(2)),
      low: parseFloat(low.toFixed(2)),
    });
  }
  return result;
}

// 验证 fixture 不包含周末
export function validateFixture(bars: BarData[]): { valid: boolean; errors: string[] } {
  const errors: string[] = [];

  // 检查每天最多 48 根 K 线
  const dayCounts: Record<string, number> = {};
  bars.forEach(bar => {
    const day = bar.time.split(' ')[0];
    dayCounts[day] = (dayCounts[day] || 0) + 1;
  });

  Object.entries(dayCounts).forEach(([day, count]) => {
    if (count > 48) {
      errors.push(`日期 ${day} 有 ${count} 根 K 线，超过 48 根`);
    }
  });

  // 检查没有周末
  Object.keys(dayCounts).forEach(day => {
    const date = new Date(day);
    const dayOfWeek = date.getDay();
    if (dayOfWeek === 0 || dayOfWeek === 6) {
      errors.push(`日期 ${day} 是周末，不应有 K 线`);
    }
  });

  // 检查时间严格递增且无重复
  for (let i = 1; i < bars.length; i++) {
    const prevTime = new Date(bars[i - 1].time).getTime();
    const currTime = new Date(bars[i].time).getTime();
    if (currTime <= prevTime) {
      errors.push(`时间 ${bars[i].time} 不大于前一个时间 ${bars[i - 1].time}`);
    }
  }

  // 检查没有午休时间的 K 线（11:30-13:00 之间）
  bars.forEach(bar => {
    const timePart = bar.time.split(' ')[1];
    const [hour, min] = timePart.split(':').map(Number);
    const minutes = hour * 60 + min;
    if (minutes > MORNING_END && minutes < AFTERNOON_START) {
      errors.push(`时间 ${bar.time} 在午休时段，不应有 K 线`);
    }
  });

  return { valid: errors.length === 0, errors };
}
