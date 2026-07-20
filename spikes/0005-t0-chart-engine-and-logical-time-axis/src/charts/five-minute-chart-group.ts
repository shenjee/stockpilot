/**
 * 5 分钟图表组：价格图 + VOL + MACD
 * - 三图共享逻辑时间轴
 * - 同步可见范围和十字光标
 */
import {
  IChartApi,
  ISeriesApi,
  createChart,
  CandlestickData,
  HistogramData,
  LineData,
  CrosshairMode,
  Time,
} from 'lightweight-charts';
import { ChartGroupState, createInitialState, followLatest, setManualRange, calculateVisibleCount } from '../models/chart-group-state';
import { BarData, calculateMACD, calculateVOLMA, calculateBOLL } from '../fixtures/ohlcv-fixture';

export interface ChartGroupConfig {
  container: HTMLElement;
  priceHeight?: number;
  volHeight?: number;
  macdHeight?: number;
  barSlotWidth?: number;
}

export class FiveMinuteChartGroup {
  private priceChart: IChartApi;
  private volChart: IChartApi;
  private macdChart: IChartApi;

  private priceSeries: ISeriesApi<'Candlestick'>;
  private volSeries: ISeriesApi<'Histogram'>;
  private volMaSeries: ISeriesApi<'Line'>;
  private macdLineSeries: ISeriesApi<'Line'>;
  private signalSeries: ISeriesApi<'Line'>;
  private histogramSeries: ISeriesApi<'Histogram'>;
  private bollUpperSeries: ISeriesApi<'Line'>;
  private bollMiddleSeries: ISeriesApi<'Line'>;
  private bollLowerSeries: ISeriesApi<'Line'>;

  private state: ChartGroupState;
  private allBars: BarData[];

  private container: HTMLElement;
  private resizeObserver: ResizeObserver;

  constructor(config: ChartGroupConfig, bars: BarData[]) {
    this.container = config.container;
    this.allBars = bars;
    this.state = createInitialState(bars.map(b => b.time), config.barSlotWidth || 10);

    // 创建容器布局
    this.container.style.display = 'flex';
    this.container.style.flexDirection = 'column';
    this.container.style.width = '100%';
    this.container.style.height = '100%';

    // 创建三个图表容器
    const priceDiv = document.createElement('div');
    const volDiv = document.createElement('div');
    const macdDiv = document.createElement('div');

    priceDiv.style.flex = '1';
    volDiv.style.height = `${config.volHeight || 100}px`;
    macdDiv.style.height = `${config.macdHeight || 100}px`;

    this.container.appendChild(priceDiv);
    this.container.appendChild(volDiv);
    this.container.appendChild(macdDiv);

    // 创建图表
    const commonOptions = {
      layout: {
        background: { type: 'solid', color: '#1a1a1a' },
        textColor: '#9ca3af',
      },
      grid: {
        vertLines: { color: '#2a2a2a' },
        horzLines: { color: '#2a2a2a' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: '#4b5563',
          width: 1,
          style: 2,
        },
        horzLine: {
          color: '#4b5563',
          width: 1,
          style: 2,
        },
      },
      rightPriceScale: {
        borderColor: '#374151',
      },
      timeScale: {
        borderColor: '#374151',
        timeVisible: true,
        secondsVisible: false,
      },
    };

    this.priceChart = createChart(priceDiv, {
      ...commonOptions,
      width: priceDiv.clientWidth,
      height: priceDiv.clientHeight,
    });

    this.volChart = createChart(volDiv, {
      ...commonOptions,
      width: volDiv.clientWidth,
      height: volDiv.clientHeight,
    });

    this.macdChart = createChart(macdDiv, {
      ...commonOptions,
      width: macdDiv.clientWidth,
      height: macdDiv.clientHeight,
    });

    // 创建系列
    this.priceSeries = this.priceChart.addCandlestickSeries({
      upColor: '#ef4444',
      downColor: '#22c55e',
      borderUpColor: '#ef4444',
      borderDownColor: '#22c55e',
      wickUpColor: '#ef4444',
      wickDownColor: '#22c55e',
    });

    this.volSeries = this.volChart.addHistogramSeries({
      color: '#374151',
    });

    this.volMaSeries = this.volChart.addLineSeries({
      color: '#f59e0b',
      lineWidth: 1,
      priceLineVisible: false,
    });

    this.macdLineSeries = this.macdChart.addLineSeries({
      color: '#3b82f6',
      lineWidth: 1,
      priceLineVisible: false,
    });

    this.signalSeries = this.macdChart.addLineSeries({
      color: '#f59e0b',
      lineWidth: 1,
      priceLineVisible: false,
    });

    this.histogramSeries = this.macdChart.addHistogramSeries({
      color: '#6b7280',
      priceFormat: { type: 'volume' },
      priceLineVisible: false,
    });

    this.bollUpperSeries = this.priceChart.addLineSeries({
      color: '#8b5cf6',
      lineWidth: 1,
      priceLineVisible: false,
    });

    this.bollMiddleSeries = this.priceChart.addLineSeries({
      color: '#8b5cf6',
      lineWidth: 1,
      priceLineVisible: false,
    });

    this.bollLowerSeries = this.priceChart.addLineSeries({
      color: '#8b5cf6',
      lineWidth: 1,
      priceLineVisible: false,
    });

    // 设置数据
    this.setData(bars);

    // 同步十字光标
    this.setupCrosshairSync();

    // 同步可见范围
    this.setupVisibleRangeSync();

    // 响应大小变化
    this.resizeObserver = new ResizeObserver(() => this.handleResize());
    this.resizeObserver.observe(this.container);

    // 初始设置可见范围
    this.updateVisibleRange();
  }

  private setData(bars: BarData[]) {
    // K 线数据
    const candleData: CandlestickData<Time>[] = bars.map(b => ({
      time: this.formatTime(b.time),
      open: b.open,
      high: b.high,
      low: b.low,
      close: b.close,
    }));

    // VOL 数据
    const volData: HistogramData<Time>[] = bars.map(b => ({
      time: this.formatTime(b.time),
      value: b.volume,
      color: b.close >= b.open ? '#22c55e80' : '#ef444480',
    }));

    // VOL MA
    const volMa = calculateVOLMA(bars, 5);
    const volMaData: LineData<Time>[] = bars.map((b, i) => ({
      time: this.formatTime(b.time),
      value: volMa[i] || 0,
    }));

    // MACD 数据
    const macdData = calculateMACD(bars);
    const macdLineData: LineData<Time>[] = macdData.map(d => ({
      time: this.formatTime(d.time),
      value: d.macd,
    }));
    const signalData: LineData<Time>[] = macdData.map(d => ({
      time: this.formatTime(d.time),
      value: d.signal,
    }));
    const histogramData: HistogramData<Time>[] = macdData.map(d => ({
      time: this.formatTime(d.time),
      value: d.histogram,
      color: d.histogram >= 0 ? '#22c55e80' : '#ef444480',
    }));

    // BOLL 数据
    const bollData = calculateBOLL(bars);
    const bollUpperData: LineData<Time>[] = bollData.map(d => ({
      time: this.formatTime(d.time),
      value: d.upper,
    }));
    const bollMiddleData: LineData<Time>[] = bollData.map(d => ({
      time: this.formatTime(d.time),
      value: d.middle,
    }));
    const bollLowerData: LineData<Time>[] = bollData.map(d => ({
      time: this.formatTime(d.time),
      value: d.lower,
    }));

    this.priceSeries.setData(candleData);
    this.volSeries.setData(volData);
    this.volMaSeries.setData(volMaData);
    this.macdLineSeries.setData(macdLineData);
    this.signalSeries.setData(signalData);
    this.histogramSeries.setData(histogramData);
    this.bollUpperSeries.setData(bollUpperData);
    this.bollMiddleSeries.setData(bollMiddleData);
    this.bollLowerSeries.setData(bollLowerData);
  }

  private formatTime(timeStr: string): Time {
    // "2026-07-18 09:35:00" -> convert to business day / timestamp
    const date = new Date(timeStr);
    return Math.floor(date.getTime() / 1000) as Time;
  }

  private setupCrosshairSync() {
    // 价格图十字光标同步到 VOL 和 MACD
    this.priceChart.subscribeCrosshairMove(param => {
      if (param.time) {
        this.volChart.setCrosshairPosition(0, param.time, this.volSeries);
        this.macdChart.setCrosshairPosition(0, param.time, this.macdLineSeries);
      }
    });

    // VOL 十字光标同步到价格图和 MACD
    this.volChart.subscribeCrosshairMove(param => {
      if (param.time) {
        this.priceChart.setCrosshairPosition(0, param.time, this.priceSeries);
        this.macdChart.setCrosshairPosition(0, param.time, this.macdLineSeries);
      }
    });

    // MACD 十字光标同步到价格图和 VOL
    this.macdChart.subscribeCrosshairMove(param => {
      if (param.time) {
        this.priceChart.setCrosshairPosition(0, param.time, this.priceSeries);
        this.volChart.setCrosshairPosition(0, param.time, this.volSeries);
      }
    });
  }

  private setupVisibleRangeSync() {
    const syncHandler = () => {
      const range = this.priceChart.timeScale().getVisibleRange();
      if (!range) return;

      // 转换为逻辑索引范围更新状态
      // 注意：实际实现需要 time -> logical index 的反向映射
      // 这里简化处理，只同步时间范围
      this.volChart.timeScale().setVisibleRange(range);
      this.macdChart.timeScale().setVisibleRange(range);
    };

    this.priceChart.timeScale().subscribeVisibleLogicalRangeChange(syncHandler);
  }

  private handleResize() {
    const width = this.container.clientWidth;

    // 重新计算可见 K 线数量
    const visibleCount = calculateVisibleCount(width, this.state.barSlotWidth);

    // 更新图表尺寸
    const charts = [this.priceChart, this.volChart, this.macdChart];
    const divs = this.container.children as HTMLCollectionOf<HTMLElement>;

    charts.forEach((chart, i) => {
      chart.applyOptions({ width, height: divs[i].clientHeight });
    });

    // 如果在跟随模式，根据新宽度调整可见范围
    if (this.state.followState === 'following') {
      this.state = followLatest(this.state, visibleCount);
      this.updateVisibleRange();
    }
  }

  private updateVisibleRange() {
    const range = {
      from: this.formatTime(this.state.logicalToTime[this.state.visibleStart]),
      to: this.formatTime(this.state.logicalToTime[this.state.visibleEnd - 1]),
    };

    this.priceChart.timeScale().setVisibleRange(range);
    this.volChart.timeScale().setVisibleRange(range);
    this.macdChart.timeScale().setVisibleRange(range);
  }

  // 手动设置可见范围（用户拖动后）
  public setManualVisibleRange(startIndex: number, endIndex: number) {
    this.state = setManualRange(this.state, startIndex, endIndex);
    this.updateVisibleRange();
  }

  // 切换到跟随最新
  public followLatest(visibleCount?: number) {
    const count = visibleCount || this.state.visibleEnd - this.state.visibleStart;
    this.state = followLatest(this.state, count);
    this.updateVisibleRange();
  }

  // 获取当前状态
  public getState(): ChartGroupState {
    return this.state;
  }

  // 清理
  public destroy() {
    this.resizeObserver.disconnect();
    this.priceChart.remove();
    this.volChart.remove();
    this.macdChart.remove();
  }
}
