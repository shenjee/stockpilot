/**
 * 1 分钟图表组：分时价格 + VWAP + VOL + MACD
 * - 与 5 分钟图组共享状态模型，但独立实例
 * - 组内三图同步可见范围和十字光标
 * - 组间不联动（PRD/UI spec 明确）
 *
 * 这是验证用最小实现，复用 fixture 和状态模型，不实现真实 VWAP 算法。
 */
import {
  IChartApi,
  ISeriesApi,
  createChart,
  LineData,
  HistogramData,
  CrosshairMode,
  Time,
} from 'lightweight-charts';
import { ChartGroupState, createInitialState, followLatest, setManualRange, calculateVisibleCount, appendData, truncateAtTime } from '../models/chart-group-state';
import { BarData, calculateMACD, parseMarketTime } from '../fixtures/ohlcv-fixture';

export interface TimeSharingConfig {
  container: HTMLElement;
  volHeight?: number;
  macdHeight?: number;
  barSlotWidth?: number;
}

export class TimeSharingChartGroup {
  private priceChart: IChartApi;
  private volChart: IChartApi;
  private macdChart: IChartApi;

  private priceSeries: ISeriesApi<'Line'>;
  private vwapSeries: ISeriesApi<'Line'>;
  private volSeries: ISeriesApi<'Histogram'>;
  private macdLineSeries: ISeriesApi<'Line'>;
  private signalSeries: ISeriesApi<'Line'>;
  private histogramSeries: ISeriesApi<'Histogram'>;

  private state: ChartGroupState;
  private allBars: BarData[];
  private container: HTMLElement;
  private resizeObserver: ResizeObserver;
  private isSyncing: boolean = false;
  private onStateChange?: (state: ChartGroupState) => void;

  constructor(config: TimeSharingConfig, bars: BarData[]) {
    this.container = config.container;
    this.allBars = bars;
    this.state = createInitialState(bars.map(b => b.time), config.barSlotWidth || 6);

    this.container.style.display = 'flex';
    this.container.style.flexDirection = 'column';
    this.container.style.width = '100%';
    this.container.style.height = '100%';

    const priceDiv = document.createElement('div');
    const volDiv = document.createElement('div');
    const macdDiv = document.createElement('div');
    priceDiv.style.flex = '1';
    volDiv.style.height = `${config.volHeight || 80}px`;
    macdDiv.style.height = `${config.macdHeight || 80}px`;
    this.container.appendChild(priceDiv);
    this.container.appendChild(volDiv);
    this.container.appendChild(macdDiv);

    const commonOptions = {
      layout: { background: { type: 'solid', color: '#1a1a1a' }, textColor: '#9ca3af' },
      grid: { vertLines: { color: '#2a2a2a' }, horzLines: { color: '#2a2a2a' } },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#4b5563', width: 1, style: 2 },
        horzLine: { color: '#4b5563', width: 1, style: 2 },
      },
      rightPriceScale: { borderColor: '#374151' },
      timeScale: { borderColor: '#374151', timeVisible: true, secondsVisible: false },
    };

    this.priceChart = createChart(priceDiv, { ...commonOptions, width: priceDiv.clientWidth, height: priceDiv.clientHeight });
    this.volChart = createChart(volDiv, { ...commonOptions, width: volDiv.clientWidth, height: volDiv.clientHeight });
    this.macdChart = createChart(macdDiv, { ...commonOptions, width: macdDiv.clientWidth, height: macdDiv.clientHeight });

    this.priceSeries = this.priceChart.addLineSeries({ color: '#3b82f6', lineWidth: 2 });
    this.vwapSeries = this.priceChart.addLineSeries({ color: '#f59e0b', lineWidth: 1, priceLineVisible: false });
    this.volSeries = this.volChart.addHistogramSeries({ color: '#374151' });
    this.macdLineSeries = this.macdChart.addLineSeries({ color: '#3b82f6', lineWidth: 1, priceLineVisible: false });
    this.signalSeries = this.macdChart.addLineSeries({ color: '#f59e0b', lineWidth: 1, priceLineVisible: false });
    this.histogramSeries = this.macdChart.addHistogramSeries({ color: '#6b7280', priceFormat: { type: 'volume' }, priceLineVisible: false });

    this.setData(bars);
    this.setupCrosshairSync();
    this.setupVisibleRangeSync();
    this.resizeObserver = new ResizeObserver(() => this.handleResize());
    this.resizeObserver.observe(this.container);
    this.updateVisibleRange();
  }

  private setData(bars: BarData[]) {
    const priceData: LineData<Time>[] = bars.map(b => ({
      time: parseMarketTime(b.time) as Time,
      value: b.close,
    }));

    // VWAP 简化为当日累计成交额 / 累计成交量，按交易日重置
    const vwapData: LineData<Time>[] = [];
    let currentDay = '';
    let cumAmount = 0;
    let cumVolume = 0;
    bars.forEach(b => {
      const day = b.time.split(' ')[0];
      if (day !== currentDay) {
        currentDay = day;
        cumAmount = 0;
        cumVolume = 0;
      }
      cumAmount += (b.high + b.low + b.close) / 3 * b.volume;
      cumVolume += b.volume;
      vwapData.push({
        time: parseMarketTime(b.time) as Time,
        value: cumVolume > 0 ? cumAmount / cumVolume : b.close,
      });
    });

    const volData: HistogramData<Time>[] = bars.map(b => ({
      time: parseMarketTime(b.time) as Time,
      value: b.volume,
      color: b.close >= b.open ? '#22c55e80' : '#ef444480',
    }));

    const macdData = calculateMACD(bars);
    const macdLineData: LineData<Time>[] = macdData.map(d => ({ time: parseMarketTime(d.time) as Time, value: d.macd }));
    const signalData: LineData<Time>[] = macdData.map(d => ({ time: parseMarketTime(d.time) as Time, value: d.signal }));
    const histogramData: HistogramData<Time>[] = macdData.map(d => ({
      time: parseMarketTime(d.time) as Time,
      value: d.histogram,
      color: d.histogram >= 0 ? '#22c55e80' : '#ef444480',
    }));

    this.priceSeries.setData(priceData);
    this.vwapSeries.setData(vwapData);
    this.volSeries.setData(volData);
    this.macdLineSeries.setData(macdLineData);
    this.signalSeries.setData(signalData);
    this.histogramSeries.setData(histogramData);
  }

  private setupCrosshairSync() {
    this.priceChart.subscribeCrosshairMove(param => {
      if (param.time) {
        this.volChart.setCrosshairPosition(0, param.time, this.volSeries);
        this.macdChart.setCrosshairPosition(0, param.time, this.macdLineSeries);
      }
    });
    this.volChart.subscribeCrosshairMove(param => {
      if (param.time) {
        this.priceChart.setCrosshairPosition(0, param.time, this.priceSeries);
        this.macdChart.setCrosshairPosition(0, param.time, this.macdLineSeries);
      }
    });
    this.macdChart.subscribeCrosshairMove(param => {
      if (param.time) {
        this.priceChart.setCrosshairPosition(0, param.time, this.priceSeries);
        this.volChart.setCrosshairPosition(0, param.time, this.volSeries);
      }
    });
  }

  private setupVisibleRangeSync() {
    const syncHandler = (sourceChart: IChartApi) => {
      if (this.isSyncing) return;
      const range = sourceChart.timeScale().getVisibleRange();
      if (!range) return;
      this.isSyncing = true;
      try {
        [this.priceChart, this.volChart, this.macdChart].forEach(chart => {
          if (chart !== sourceChart) chart.timeScale().setVisibleRange(range);
        });

        const fromTime = range.from as number;
        const toTime = range.to as number;
        let startIndex = 0;
        let endIndex = this.state.logicalToTime.length;
        for (let i = 0; i < this.state.logicalToTime.length; i++) {
          const barTime = parseMarketTime(this.state.logicalToTime[i]);
          if (barTime >= fromTime && startIndex === 0) startIndex = i;
          if (barTime <= toTime) endIndex = i + 1;
        }
        this.state = setManualRange(this.state, startIndex, endIndex);
        this.onStateChange?.(this.state);
      } finally {
        this.isSyncing = false;
      }
    };
    this.priceChart.timeScale().subscribeVisibleLogicalRangeChange(() => syncHandler(this.priceChart));
    this.volChart.timeScale().subscribeVisibleLogicalRangeChange(() => syncHandler(this.volChart));
    this.macdChart.timeScale().subscribeVisibleLogicalRangeChange(() => syncHandler(this.macdChart));
  }

  private handleResize() {
    const width = this.container.clientWidth;
    const visibleCount = calculateVisibleCount(width, this.state.barSlotWidth);
    const charts = [this.priceChart, this.volChart, this.macdChart];
    const divs = this.container.children as HTMLCollectionOf<HTMLElement>;
    charts.forEach((chart, i) => chart.applyOptions({ width, height: divs[i].clientHeight }));
    if (this.state.followState === 'following') {
      this.state = followLatest(this.state, visibleCount);
      this.updateVisibleRange();
    }
  }

  private updateVisibleRange() {
    if (this.isSyncing) return;
    this.isSyncing = true;
    try {
      const range = {
        from: parseMarketTime(this.state.logicalToTime[this.state.visibleStart]),
        to: parseMarketTime(this.state.logicalToTime[Math.min(this.state.visibleEnd, this.state.logicalToTime.length) - 1]),
      };
      this.priceChart.timeScale().setVisibleRange(range);
      this.volChart.timeScale().setVisibleRange(range);
      this.macdChart.timeScale().setVisibleRange(range);
    } finally {
      this.isSyncing = false;
    }
  }

  public followLatest(visibleCount?: number) {
    const count = visibleCount || this.state.visibleEnd - this.state.visibleStart;
    this.state = followLatest(this.state, count);
    this.updateVisibleRange();
    this.onStateChange?.(this.state);
  }

  public appendData(newBars: BarData[]) {
    this.allBars = [...this.allBars, ...newBars];
    this.state = appendData(this.state, newBars.map(b => b.time));
    this.setData(this.allBars);
    this.updateVisibleRange();
    this.onStateChange?.(this.state);
  }

  public truncateAtTime(maxTime: string) {
    const maxIndex = this.allBars.findIndex(b => b.time > maxTime);
    const effectiveEnd = maxIndex === -1 ? this.allBars.length : maxIndex;
    this.allBars = this.allBars.slice(0, effectiveEnd);
    this.state = truncateAtTime(this.state, maxTime);
    this.setData(this.allBars);
    this.updateVisibleRange();
    this.onStateChange?.(this.state);
  }

  public getState(): ChartGroupState {
    return this.state;
  }

  public setOnStateChange(cb: (state: ChartGroupState) => void) {
    this.onStateChange = cb;
  }

  public destroy() {
    this.resizeObserver.disconnect();
    this.priceChart.remove();
    this.volChart.remove();
    this.macdChart.remove();
  }
}
