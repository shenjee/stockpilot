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
  SeriesMarker,
} from 'lightweight-charts';
import { ChartGroupState, createInitialState, followLatest, setManualRange, calculateVisibleCount, appendData, truncateAtTime } from '../models/chart-group-state';
import { BarData, calculateMACD, calculateVOLMA, calculateBOLL, parseMarketTime, ZhongShu } from '../fixtures/ohlcv-fixture';

export interface ChartGroupConfig {
  container: HTMLElement;
  priceHeight?: number;
  volHeight?: number;
  macdHeight?: number;
  barSlotWidth?: number;
}

// 副图布局模式（注意：这是单图组内副图的高度/可见性切换，不是工作台布局）
export type LayoutMode = 'full' | 'compact' | 'expanded' | 'hide-sub';

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

  // CZSC overlay 系列
  private buyPointSeries: ISeriesApi<'Line'>;
  private sellPointSeries: ISeriesApi<'Line'>;
  private biLineSeries: ISeriesApi<'Line'>;
  private zhongShuUpperSeries: ISeriesApi<'Line'>;
  private zhongShuLowerSeries: ISeriesApi<'Line'>;

  // 离散买卖点 markers（Lightweight Charts 4.x 使用 series.setMarkers）
  // 不再单独持有 markers 实例，直接调用 priceSeries.setMarkers

  // 当前 overlay 数据（用于 truncate 时同步裁剪）
  private currentBuyPoints: Array<{ time: string; price: number }> = [];
  private currentSellPoints: Array<{ time: string; price: number }> = [];
  private currentBiLines: Array<{ start: { time: string; price: number }; end: { time: string; price: number } }> = [];
  private currentZhongShu: ZhongShu[] = [];

  private state: ChartGroupState;
  private allBars: BarData[];

  private container: HTMLElement;
  private resizeObserver: ResizeObserver;

  private isSyncing: boolean = false;

  // 布局状态
  private layoutMode: LayoutMode = 'full';

  // 状态变化回调
  private onStateChange?: (state: ChartGroupState) => void;

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
    priceDiv.id = 'price-chart-container';
    volDiv.style.height = `${config.volHeight || 100}px`;
    volDiv.id = 'vol-chart-container';
    macdDiv.style.height = `${config.macdHeight || 100}px`;
    macdDiv.style.marginBottom = '4px';
    macdDiv.id = 'macd-chart-container';

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

    // CZSC overlay 系列
    this.buyPointSeries = this.priceChart.addLineSeries({
      color: '#22c55e',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    this.sellPointSeries = this.priceChart.addLineSeries({
      color: '#ef4444',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    this.biLineSeries = this.priceChart.addLineSeries({
      color: '#f59e0b',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    // 中枢上下沿（用半透明水平线段表示中枢区间）
    this.zhongShuUpperSeries = this.priceChart.addLineSeries({
      color: '#06b6d4',
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      lineStyle: 0,
    });

    this.zhongShuLowerSeries = this.priceChart.addLineSeries({
      color: '#06b6d4',
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      lineStyle: 0,
    });

    // 离散买卖点 markers 初始为空
    this.priceSeries.setMarkers([]);

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
    // 用 Date.UTC 构造时间戳，让 Lightweight Charts 显示直接对应市场时间（09:35 显示为 09:35）
    // 避免本地时区偏移导致显示成 01:35 等错误时间
    return parseMarketTime(timeStr) as Time;
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
    const syncHandler = (sourceChart: IChartApi) => {
      if (this.isSyncing) return;

      const range = sourceChart.timeScale().getVisibleRange();
      if (!range) return;

      this.isSyncing = true;
      try {
        // 同步到其他两张图
        const charts = [this.priceChart, this.volChart, this.macdChart];
        charts.forEach(chart => {
          if (chart !== sourceChart) {
            chart.timeScale().setVisibleRange(range);
          }
        });

        // 转换时间范围到逻辑索引，更新状态
        const fromTime = range.from as number;
        const toTime = range.to as number;

        // 查找对应的开始和结束索引
        let startIndex = 0;
        let endIndex = this.state.logicalToTime.length;

        for (let i = 0; i < this.state.logicalToTime.length; i++) {
          const barTime = Math.floor(new Date(this.state.logicalToTime[i]).getTime() / 1000);
          if (barTime >= fromTime && startIndex === 0) {
            startIndex = i;
          }
          if (barTime <= toTime) {
            endIndex = i + 1;
          }
        }

        // 更新状态并触发手动模式
        const prevState = this.state;
        this.state = setManualRange(this.state, startIndex, endIndex);

        if (prevState.followState !== this.state.followState || this.onStateChange) {
          this.notifyStateChange();
        }
      } finally {
        this.isSyncing = false;
      }
    };

    // 监听三张图的可见范围变化
    this.priceChart.timeScale().subscribeVisibleLogicalRangeChange(() => syncHandler(this.priceChart));
    this.volChart.timeScale().subscribeVisibleLogicalRangeChange(() => syncHandler(this.volChart));
    this.macdChart.timeScale().subscribeVisibleLogicalRangeChange(() => syncHandler(this.macdChart));
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
    if (this.isSyncing) return;

    this.isSyncing = true;
    try {
      const fromTime = this.formatTime(this.state.logicalToTime[this.state.visibleStart]);
      const toTime = this.formatTime(this.state.logicalToTime[Math.min(this.state.visibleEnd, this.state.logicalToTime.length) - 1]);

      const range = { from: fromTime, to: toTime };

      this.priceChart.timeScale().setVisibleRange(range);
      this.volChart.timeScale().setVisibleRange(range);
      this.macdChart.timeScale().setVisibleRange(range);
    } finally {
      this.isSyncing = false;
    }
  }

  private notifyStateChange() {
    if (this.onStateChange) {
      this.onStateChange(this.state);
    }
  }

  // 手动设置可见范围（用户拖动后）
  public setManualVisibleRange(startIndex: number, endIndex: number) {
    const prevState = this.state;
    this.state = setManualRange(this.state, startIndex, endIndex);
    this.updateVisibleRange();

    if (prevState.followState !== this.state.followState || this.onStateChange) {
      this.notifyStateChange();
    }
  }

  // 切换到跟随最新
  public followLatest(visibleCount?: number) {
    const count = visibleCount || this.state.visibleEnd - this.state.visibleStart;
    this.state = followLatest(this.state, count);
    this.updateVisibleRange();
    this.notifyStateChange();
  }

  // 增量更新数据
  public appendData(newBars: BarData[]) {
    this.allBars = [...this.allBars, ...newBars];
    this.state = appendData(this.state, newBars.map(b => b.time));
    this.setData(this.allBars);
    this.updateVisibleRange();
    // 重新应用 overlay（新数据到来后，之前因时间点不存在被过滤的 overlay 可能需要恢复）
    this.applyBuyMarkers();
    this.applySellMarkers();
    this.applyBiLines();
    this.applyZhongShu();
    this.notifyStateChange();
  }

  // 回放截断
  public truncateAtTime(maxTime: string) {
    // 找到截断点
    const maxIndex = this.allBars.findIndex(b => b.time > maxTime);
    const effectiveEnd = maxIndex === -1 ? this.allBars.length : maxIndex;

    this.allBars = this.allBars.slice(0, effectiveEnd);
    this.state = truncateAtTime(this.state, maxTime);
    this.setData(this.allBars);
    this.updateVisibleRange();
    // 重新应用 overlay：truncate 后 T 之后的数据被丢弃，overlay 也必须同步裁剪
    // 不能让回放窗口出现未来时间点上的 CZSC 买卖点、笔或中枢
    this.applyBuyMarkers();
    this.applySellMarkers();
    this.applyBiLines();
    this.applyZhongShu();
    this.notifyStateChange();
  }

  // 设置布局模式
  public setLayoutMode(mode: LayoutMode) {
    this.layoutMode = mode;
    const divs = this.container.children as HTMLCollectionOf<HTMLElement>;
    const volDiv = divs[1];
    const macdDiv = divs[2];

    switch (mode) {
      case 'full':
        volDiv.style.display = 'block';
        macdDiv.style.display = 'block';
        volDiv.style.height = '100px';
        macdDiv.style.height = '100px';
        break;
      case 'compact':
        volDiv.style.display = 'block';
        macdDiv.style.display = 'block';
        volDiv.style.height = '80px';
        macdDiv.style.height = '80px';
        break;
      case 'expanded':
        volDiv.style.display = 'block';
        macdDiv.style.display = 'block';
        volDiv.style.height = '120px';
        macdDiv.style.height = '120px';
        break;
      case 'hide-sub':
        volDiv.style.display = 'none';
        macdDiv.style.display = 'none';
        break;
    }

    // 触发 resize 更新图表尺寸
    this.handleResize();
  }

  // 设置 CZSC 买点（离散 markers）
  public setCZSCBuyPoints(points: Array<{ time: string; price: number }>) {
    this.currentBuyPoints = points;
    this.applyBuyMarkers();
  }

  // 设置 CZSC 卖点（离散 markers）
  public setCZSCSellPoints(points: Array<{ time: string; price: number }>) {
    this.currentSellPoints = points;
    this.applySellMarkers();
  }

  private applyBuyMarkers() {
    // 只保留当前 allBars 中存在的时间点
    const validTimes = new Set(this.allBars.map(b => b.time));
    const buyMarkers: SeriesMarker<Time>[] = this.currentBuyPoints
      .filter(p => validTimes.has(p.time))
      .map(p => ({
        time: this.formatTime(p.time),
        position: 'belowBar',
        color: '#22c55e',
        shape: 'arrowUp',
        text: 'B',
      }));
    // 合并买卖点到同一个 markers 数组（Lightweight Charts 4.x 一个 series 只能有一组 markers）
    const sellMarkers: SeriesMarker<Time>[] = this.currentSellPoints
      .filter(p => validTimes.has(p.time))
      .map(p => ({
        time: this.formatTime(p.time),
        position: 'aboveBar',
        color: '#ef4444',
        shape: 'arrowDown',
        text: 'S',
      }));
    // 按时间排序合并
    const allMarkers = [...buyMarkers, ...sellMarkers].sort((a, b) => (a.time as number) - (b.time as number));
    this.priceSeries.setMarkers(allMarkers);
  }

  private applySellMarkers() {
    // 4.x 中买卖点共享同一组 markers，在 applyBuyMarkers 中统一处理
    this.applyBuyMarkers();
  }

  // 设置笔线段
  public setBiLines(lines: Array<{ start: { time: string; price: number }; end: { time: string; price: number } }>) {
    this.currentBiLines = lines;
    this.applyBiLines();
  }

  private applyBiLines() {
    const validTimes = new Set(this.allBars.map(b => b.time));
    const data: LineData<Time>[] = [];
    this.currentBiLines.forEach(line => {
      // 只保留起止点都在当前数据中的线段
      if (validTimes.has(line.start.time) && validTimes.has(line.end.time)) {
        data.push({ time: this.formatTime(line.start.time), value: line.start.price });
        data.push({ time: this.formatTime(line.end.time), value: line.end.price });
      }
    });
    this.biLineSeries.setData(data);
  }

  // 设置中枢（zone overlay）
  public setZhongShu(zhongShu: ZhongShu[]) {
    this.currentZhongShu = zhongShu;
    this.applyZhongShu();
  }

  private applyZhongShu() {
    const upperData: LineData<Time>[] = [];
    const lowerData: LineData<Time>[] = [];
    const barCount = this.allBars.length;

    this.currentZhongShu.forEach(zs => {
      // 只保留完全在当前数据范围内的中枢
      if (zs.endIndex < barCount) {
        const startTime = this.formatTime(this.allBars[zs.startIndex].time);
        const endTime = this.formatTime(this.allBars[zs.endIndex].time);
        upperData.push({ time: startTime, value: zs.high });
        upperData.push({ time: endTime, value: zs.high });
        lowerData.push({ time: startTime, value: zs.low });
        lowerData.push({ time: endTime, value: zs.low });
      }
    });

    this.zhongShuUpperSeries.setData(upperData);
    this.zhongShuLowerSeries.setData(lowerData);
  }

  // 显示/隐藏 BOLL
  public setBOLLVisible(visible: boolean) {
    this.bollUpperSeries.applyOptions({ visible });
    this.bollMiddleSeries.applyOptions({ visible });
    this.bollLowerSeries.applyOptions({ visible });
  }

  // 显示/隐藏 CZSC overlay
  public setCZSCVisible(visible: boolean) {
    this.buyPointSeries.applyOptions({ visible });
    this.sellPointSeries.applyOptions({ visible });
    this.biLineSeries.applyOptions({ visible });
    this.zhongShuUpperSeries.applyOptions({ visible });
    this.zhongShuLowerSeries.applyOptions({ visible });
  }

  // 获取当前状态
  public getState(): ChartGroupState {
    return this.state;
  }

  // 获取当前布局模式
  public getLayoutMode(): LayoutMode {
    return this.layoutMode;
  }

  // 设置状态变化回调
  public setOnStateChange(callback: (state: ChartGroupState) => void) {
    this.onStateChange = callback;
  }

  // 清理
  public destroy() {
    this.resizeObserver.disconnect();
    this.priceChart.remove();
    this.volChart.remove();
    this.macdChart.remove();
  }
}
