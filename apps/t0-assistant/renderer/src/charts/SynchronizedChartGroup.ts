import {
  ColorType,
  CrosshairMode,
  createChart,
  LineStyle,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type LogicalRange,
  type MouseEventParams,
  type Time,
  type UTCTimestamp,
  type WhitespaceData,
} from "lightweight-charts";
import {
  ChartGroupKind,
  formatMarketTick,
  type ChartGroupModel,
} from "./chart-model.mjs";
import {
  buildCrosshairFallbackIndex,
  resolveCrosshairTarget,
} from "./chart-interaction.mjs";
import { PivotZonePrimitive } from "./pivot-zone-primitive";
import { CzscMarkerPrimitive } from "./czsc-marker-primitive";
import {
  FollowState,
  applyModel,
  calculateVisibleCount,
  followLatest,
  fromChartLogicalRange,
  restoreViewportFromSnapshot,
  setManualRange,
  toChartLogicalRange,
  type ChartViewportSnapshot,
  type ChartViewportState,
} from "./chart-viewport.mjs";

interface ChartGroupContainers {
  price: HTMLElement;
  volume: HTMLElement;
  macd: HTMLElement;
}

interface ChartGroupOptions {
  containers: ChartGroupContainers;
  kind: ChartGroupModel["kind"];
  barSlotWidth?: number;
  initialViewport?: ChartViewportSnapshot | null;
  onViewportChange?: (snapshot: ChartViewportSnapshot | null) => void;
}

type NumericSeries = ISeriesApi<"Line"> | ISeriesApi<"Histogram">;

const RED = "#ef5350";
const GREEN = "#26a69a";
const BLUE = "#4f8cff";
const AMBER = "#f6b94a";
const MUTED = "#8090a8";
const BOLL_COLOR = "#e879f9";
// 缩放/平移判定阈值（LC 连续逻辑范围跨度差）。纯平移跨度精确不变（浮点误差 ~1e-9），
// 缩放跨度变化至少数个 K；0.01 仅吸收浮点漂移，可靠区分二者。
const ZOOM_SPAN_EPSILON = 0.01;
const MA_COLORS = {
  ma5: "#f6d365",
  ma10: "#7dd3fc",
  ma20: "#c4b5fd",
  ma30: "#fb923c",
  ma60: "#f472b6",
} as const;

export class SynchronizedChartGroup {
  private readonly containers: ChartGroupContainers;
  private readonly kind: ChartGroupModel["kind"];
  private readonly priceChart: IChartApi;
  private readonly volumeChart: IChartApi;
  private readonly macdChart: IChartApi;
  private readonly charts: IChartApi[];
  private readonly priceSeries:
    | ISeriesApi<"Candlestick">
    | ISeriesApi<"Line">;
  private readonly vwapSeries: ISeriesApi<"Line"> | null;
  private readonly movingAverageSeries: Partial<
    Record<keyof typeof MA_COLORS, ISeriesApi<"Line">>
  > = {};
  private readonly bollUpperSeries: ISeriesApi<"Line"> | null;
  private readonly bollMiddleSeries: ISeriesApi<"Line"> | null;
  private readonly bollLowerSeries: ISeriesApi<"Line"> | null;
  private readonly pivotZonePrimitive: PivotZonePrimitive | null;
  private readonly czscMarkerPrimitive: CzscMarkerPrimitive | null;
  private readonly volumeSeries: ISeriesApi<"Histogram">;
  private readonly volumeMa5Series: ISeriesApi<"Line"> | null;
  private readonly volumeMa10Series: ISeriesApi<"Line"> | null;
  private readonly difSeries: ISeriesApi<"Line">;
  private readonly deaSeries: ISeriesApi<"Line">;
  private readonly macdHistogramSeries: ISeriesApi<"Histogram">;
  private readonly resizeObserver: ResizeObserver;
  private readonly crosshairHandlers = new Map<
    IChartApi,
    (param: MouseEventParams<Time>) => void
  >();
  private readonly rangeHandlers = new Map<
    IChartApi,
    (range: LogicalRange | null) => void
  >();

  private model: ChartGroupModel | null = null;
  private readonly barSlotWidth: number;
  private readonly initialViewport?: ChartViewportSnapshot | null;
  private readonly onViewportChange?:
    | ((snapshot: ChartViewportSnapshot | null) => void);
  private viewport: ChartViewportState | null = null;
  private applyingViewportRange = false;
  // 最近一次已知 LC 连续逻辑范围（原始值，未 floor）。作为缩放/平移判定的跨度基线：
  // 用户平移不会重跑 setModel/applyVisibleRange，故该字段在平移间保留原始跨度，避免
  // toChartLogicalRange(state) 取整引入的 floor 噪声污染判定。
  private lastTrackedLcRange: { from: number; to: number } | null = null;
  private lastReportedFollowState: "following" | "manual" | null = null;
  private lastReportedRange: { from: number; to: number } | null = null;
  private reportTimer: ReturnType<typeof setTimeout> | null = null;
  private viewportRangeHandler:
    | ((range: LogicalRange | null) => void)
    | null = null;
  private syncingRange = false;
  private syncingCrosshair = false;
  private previousTimeByTime = new Map<number, number | null>();
  private priceValues = new Map<number, number>();
  private volumeValues = new Map<number, number>();
  private macdValues = new Map<number, number>();
  private macdSeriesByTime = new Map<number, NumericSeries>();
  private structureSeries: ISeriesApi<"Line">[] = [];

  constructor(options: ChartGroupOptions) {
    this.containers = options.containers;
    this.kind = options.kind;
    this.barSlotWidth = options.barSlotWidth ?? 8;
    this.initialViewport = options.initialViewport;
    this.onViewportChange = options.onViewportChange;

    this.priceChart = this.createChart(options.containers.price, false);
    this.volumeChart = this.createChart(options.containers.volume, false);
    this.macdChart = this.createChart(options.containers.macd, true);
    this.charts = [this.priceChart, this.volumeChart, this.macdChart];

    if (this.kind === ChartGroupKind.FIVE_MINUTE) {
      this.priceSeries = this.priceChart.addCandlestickSeries({
        upColor: RED,
        downColor: GREEN,
        borderUpColor: RED,
        borderDownColor: GREEN,
        wickUpColor: RED,
        wickDownColor: GREEN,
        priceLineVisible: false,
      });
      this.vwapSeries = null;
      for (const [period, color] of Object.entries(MA_COLORS) as Array<
        [keyof typeof MA_COLORS, string]
      >) {
        this.movingAverageSeries[period] = this.priceChart.addLineSeries({
          color,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
        });
      }
      // BOLL 三条线默认显示，无独立开关。
      this.bollUpperSeries = this.priceChart.addLineSeries({
        color: BOLL_COLOR,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      this.bollMiddleSeries = this.priceChart.addLineSeries({
        color: BOLL_COLOR,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      this.bollLowerSeries = this.priceChart.addLineSeries({
        color: BOLL_COLOR,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      // 笔中枢以填充矩形原语表达，替换歧义的双线实现。
      this.pivotZonePrimitive = new PivotZonePrimitive();
      this.priceSeries.attachPrimitive(this.pivotZonePrimitive);
      // CZSC 买卖点按 (time, price) 精确定位，替换内置 setMarkers 的 belowBar/aboveBar。
      this.czscMarkerPrimitive = new CzscMarkerPrimitive();
      this.priceSeries.attachPrimitive(this.czscMarkerPrimitive);
      this.volumeMa5Series = this.volumeChart.addLineSeries({
        color: AMBER,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      this.volumeMa10Series = this.volumeChart.addLineSeries({
        color: BLUE,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
      });
    } else {
      this.priceSeries = this.priceChart.addLineSeries({
        color: BLUE,
        lineWidth: 2,
        priceLineVisible: false,
      });
      this.vwapSeries = this.priceChart.addLineSeries({
        color: AMBER,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      this.bollUpperSeries = null;
      this.bollMiddleSeries = null;
      this.bollLowerSeries = null;
      this.pivotZonePrimitive = null;
      this.czscMarkerPrimitive = null;
      this.volumeMa5Series = null;
      this.volumeMa10Series = null;
    }

    this.volumeSeries = this.volumeChart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceLineVisible: false,
      lastValueVisible: false,
    });
    this.difSeries = this.macdChart.addLineSeries({
      color: BLUE,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    this.deaSeries = this.macdChart.addLineSeries({
      color: AMBER,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    this.macdHistogramSeries = this.macdChart.addHistogramSeries({
      priceLineVisible: false,
      lastValueVisible: false,
    });

    this.setupRangeSynchronization();
    this.setupCrosshairSynchronization();
    this.setupViewportTracking();
    this.resizeObserver = new ResizeObserver(() => this.resize());
    Object.values(this.containers).forEach((container) =>
      this.resizeObserver.observe(container),
    );
  }

  setModel(model: ChartGroupModel) {
    if (model.kind !== this.kind) {
      throw new TypeError(
        `Cannot render ${model.kind} data in ${this.kind} chart group`,
      );
    }
    this.model = model;
    this.rebuildTimeMaps();
    this.setSeriesData();
    this.applyViewport();
  }

  // 视口状态机：following 右对齐最新；manual 保留逻辑范围；空数据重置。
  // 首次 setModel（viewport 为空）从 React 保存的 initialViewport 恢复，使组件重建后
  // 不依赖图表实例存活即可还原可见范围（UI 规格 §12）。
  private applyViewport() {
    if (!this.model) {
      return;
    }
    const times = this.model.timestamps;
    if (times.length === 0) {
      this.viewport = null;
      this.lastReportedFollowState = null;
      this.lastReportedRange = null;
      this.onViewportChange?.(null);
      return;
    }
    const plotWidth = this.priceChart.timeScale().width();
    const visibleCount = calculateVisibleCount(
      plotWidth,
      this.barSlotWidth,
    );
    if (this.viewport === null) {
      this.viewport = restoreViewportFromSnapshot(
        this.initialViewport ?? null,
        times,
        visibleCount,
        { barSlotWidth: this.barSlotWidth },
      );
    } else {
      this.viewport = applyModel(this.viewport, times, visibleCount);
    }
    this.applyVisibleRange();
  }

  private applyVisibleRange() {
    if (!this.viewport) {
      return;
    }
    // 内部排他范围转 LC 连续逻辑范围（to = visibleEnd - 1，避免右侧空槽）。
    const range = toChartLogicalRange(this.viewport);
    // 程序化设值后的跨度基线（整数），供下一次用户交互的缩放/平移判定参照。
    this.lastTrackedLcRange = range;
    const current = this.priceChart.timeScale().getVisibleLogicalRange();
    if (
      current &&
      Math.abs(current.from - range.from) < 0.001 &&
      Math.abs(current.to - range.to) < 0.001
    ) {
      this.reportViewport(true);
      return;
    }
    this.applyingViewportRange = true;
    try {
      this.priceChart.timeScale().setVisibleLogicalRange(
        range as LogicalRange,
      );
    } finally {
      this.applyingViewportRange = false;
    }
    this.reportViewport(true);
  }

  // 上报视口快照到 React：followState 切换立即上报；manual 拖动期间仅 range 变化时节流
  // 上报（~120ms），保证 React 作为运行时权威且不被高频 setState 拖垮。force 用于程序化
  // 设置（applyVisibleRange）立即同步。
  private reportViewport(force = false) {
    if (!this.viewport || !this.onViewportChange) {
      return;
    }
    const snapshot: ChartViewportSnapshot = {
      range: toChartLogicalRange(this.viewport),
      followState: this.viewport.followState,
    };
    const followChanged =
      snapshot.followState !== this.lastReportedFollowState;
    const rangeChanged =
      !this.lastReportedRange ||
      Math.abs(this.lastReportedRange.from - snapshot.range.from) > 0.001 ||
      Math.abs(this.lastReportedRange.to - snapshot.range.to) > 0.001;
    if (followChanged) {
      if (this.reportTimer) {
        clearTimeout(this.reportTimer);
        this.reportTimer = null;
      }
      this.commitReport(snapshot);
      return;
    }
    if (!rangeChanged) {
      return;
    }
    if (force) {
      if (this.reportTimer) {
        clearTimeout(this.reportTimer);
        this.reportTimer = null;
      }
      this.commitReport(snapshot);
      return;
    }
    // manual 拖动：节流上报最新范围。
    if (this.reportTimer) {
      return;
    }
    this.reportTimer = setTimeout(() => {
      this.reportTimer = null;
      if (!this.viewport || !this.onViewportChange) {
        return;
      }
      this.commitReport({
        range: toChartLogicalRange(this.viewport),
        followState: this.viewport.followState,
      });
    }, 120);
  }

  private commitReport(snapshot: ChartViewportSnapshot) {
    this.lastReportedFollowState = snapshot.followState;
    this.lastReportedRange = { ...snapshot.range };
    this.onViewportChange?.(snapshot);
  }

  // 用户主动拖动/缩放价格图 -> manual（仅平移回到最新边缘才恢复 following）。
  // applyingViewportRange 守卫避免程序化 setVisibleLogicalRange 被误判为用户操作。
  // 缩放/平移判定：LC 连续逻辑范围跨度 (to - from) 在纯平移时不变、缩放时变化。
  // 缩放即便落在最新边缘也强制 manual，避免刷新/布局变化按密度重算 N 丢弃缩放。
  private setupViewportTracking() {
    const handler = (range: LogicalRange | null) => {
      if (this.applyingViewportRange || !range || !this.viewport) {
        return;
      }
      // LC 连续逻辑范围 -> 内部排他范围，再交给状态机判定 following/manual。
      const length = this.viewport.logicalToTime.length;
      const internal = fromChartLogicalRange(range, length);
      const previousSpan = this.lastTrackedLcRange
        ? this.lastTrackedLcRange.to - this.lastTrackedLcRange.from
        : range.to - range.from;
      const currentSpan = range.to - range.from;
      const isZoom =
        Math.abs(currentSpan - previousSpan) > ZOOM_SPAN_EPSILON;
      this.lastTrackedLcRange = range;
      this.viewport = setManualRange(
        this.viewport,
        internal.start,
        internal.end,
        { allowResumeFollowing: !isZoom },
      );
      this.reportViewport();
    };
    this.priceChart.timeScale().subscribeVisibleLogicalRangeChange(handler);
    this.viewportRangeHandler = handler;
  }

  destroy() {
    if (this.reportTimer) {
      clearTimeout(this.reportTimer);
      this.reportTimer = null;
    }
    this.resizeObserver.disconnect();
    for (const [chart, handler] of this.crosshairHandlers) {
      chart.unsubscribeCrosshairMove(handler);
    }
    for (const [chart, handler] of this.rangeHandlers) {
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(handler);
    }
    if (this.viewportRangeHandler) {
      this.priceChart.timeScale().unsubscribeVisibleLogicalRangeChange(
        this.viewportRangeHandler,
      );
      this.viewportRangeHandler = null;
    }
    this.charts.forEach((chart) => chart.remove());
  }

  private createChart(container: HTMLElement, showTimeScale: boolean) {
    return createChart(container, {
      width: Math.max(1, container.clientWidth),
      height: Math.max(1, container.clientHeight),
      layout: {
        background: { type: ColorType.Solid, color: "#0d1421" },
        textColor: MUTED,
        fontSize: 10,
      },
      grid: {
        vertLines: { color: "#182235" },
        horzLines: { color: "#182235" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "#64748b", width: 1, style: 2, labelVisible: true },
        horzLine: { color: "#475569", width: 1, style: 2 },
      },
      rightPriceScale: {
        borderColor: "#2a3850",
        minimumWidth: 58,
      },
      timeScale: {
        visible: showTimeScale,
        borderColor: "#2a3850",
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 0,
        fixLeftEdge: false,
        fixRightEdge: false,
        // 布局变化时锁定可见时间范围（仅改 barSpacing，不增减可见 K）：manual 下保留
        // 用户逻辑范围不被 LC 自动左移露更多 K；following 仍由 resize() 显式重算 N 覆盖。
        lockVisibleTimeRangeOnResize: true,
        tickMarkFormatter: (time: Time) => {
          const numericTime = Number(time);
          return formatMarketTick(
            numericTime,
            this.previousTimeByTime.get(numericTime) ?? null,
          );
        },
      },
      localization: {
        timeFormatter: (time: Time) => {
          const date = new Date(Number(time) * 1000);
          return `${date.getUTCFullYear()}-${String(
            date.getUTCMonth() + 1,
          ).padStart(2, "0")}-${String(date.getUTCDate()).padStart(
            2,
            "0",
          )} ${String(date.getUTCHours()).padStart(2, "0")}:${String(
            date.getUTCMinutes(),
          ).padStart(2, "0")}`;
        },
      },
      handleScale: {
        axisPressedMouseMove: { time: true, price: false },
        mouseWheel: true,
        pinch: true,
      },
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: false,
      },
    });
  }

  private setSeriesData() {
    if (!this.model) {
      return;
    }
    const time = (timestamp: string) =>
      this.model!.timeByTimestamp[timestamp] as UTCTimestamp;

    if (this.kind === ChartGroupKind.FIVE_MINUTE) {
      const candleData: CandlestickData<Time>[] = this.model.price.flatMap(
        (point) => {
          if (!("open" in point)) {
            return [];
          }
          const opacity = point.closed ? "" : "99";
          return [
            {
              time: time(point.timestamp),
              open: point.open,
              high: point.high,
              low: point.low,
              close: point.close,
              color: `${point.close >= point.open ? RED : GREEN}${opacity}`,
              borderColor: `${point.close >= point.open ? RED : GREEN}${opacity}`,
              wickColor: `${point.close >= point.open ? RED : GREEN}${opacity}`,
            },
          ];
        },
      );
      (this.priceSeries as ISeriesApi<"Candlestick">).setData(candleData);
      for (const period of Object.keys(MA_COLORS) as Array<
        keyof typeof MA_COLORS
      >) {
        this.movingAverageSeries[period]?.setData(
          this.toLineData(this.model.movingAverages[period], time),
        );
      }
      this.bollUpperSeries?.setData(this.toLineData(this.model.boll.upper, time));
      this.bollMiddleSeries?.setData(
        this.toLineData(this.model.boll.middle, time),
      );
      this.bollLowerSeries?.setData(this.toLineData(this.model.boll.lower, time));
      this.setStructureData(time);
      this.applyCzscMarkers(time);
    } else {
      const priceData: LineData<Time>[] = this.model.price.flatMap((point) =>
        "value" in point
          ? [{ time: time(point.timestamp), value: point.value }]
          : [],
      );
      (this.priceSeries as ISeriesApi<"Line">).setData(priceData);
      this.vwapSeries?.setData(
        this.toLineData(this.model.vwap, time),
      );
    }

    const directionByTimestamp = new Map(
      this.model.bars.map((bar) => [
        bar.timestamp,
        bar.close >= bar.open ? RED : GREEN,
      ]),
    );
    const volumeData: HistogramData<Time>[] = this.model.volume.flatMap(
      (point) =>
        point.value === null
          ? []
          : [
              {
                time: time(point.timestamp),
                value: point.value,
                color: `${directionByTimestamp.get(point.timestamp) ?? MUTED}aa`,
              },
            ],
    );
    this.volumeSeries.setData(volumeData);
    this.volumeMa5Series?.setData(
      this.toLineData(this.model.volumeMa5, time),
    );
    this.volumeMa10Series?.setData(
      this.toLineData(this.model.volumeMa10, time),
    );
    this.difSeries.setData(this.toLineData(this.model.macd.dif, time));
    this.deaSeries.setData(this.toLineData(this.model.macd.dea, time));
    this.macdHistogramSeries.setData(
      this.model.macd.histogram.flatMap((point) =>
        point.value === null
          ? []
          : [
              {
                time: time(point.timestamp),
                value: point.value,
                color: `${point.value >= 0 ? RED : GREEN}aa`,
              },
            ],
      ),
    );
  }

  private setStructureData(
    time: (timestamp: string) => UTCTimestamp,
  ) {
    // 笔线段：原子移除旧 series 后按当前 model 重建。
    for (const series of this.structureSeries) {
      this.priceChart.removeSeries(series);
    }
    this.structureSeries = [];
    if (!this.model) return;

    for (const stroke of this.model.strokes) {
      const series = this.priceChart.addLineSeries({
        color: stroke.color ?? BLUE,
        lineWidth: 2,
        lineStyle: stroke.dashed ? LineStyle.Dashed : LineStyle.Solid,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      series.setData([
        { time: time(stroke.start.timestamp), value: stroke.start.value },
        { time: time(stroke.end.timestamp), value: stroke.end.value },
      ]);
      this.structureSeries.push(series);
    }

    // 笔中枢：原语整体替换 zone 数组（原子替换，旧 zone 不残留）。
    this.pivotZonePrimitive?.setZones(
      this.model.pivotZones.map((zone) => ({
        start: time(zone.start_timestamp),
        end: time(zone.end_timestamp),
        high: zone.high,
        low: zone.low,
        active: zone.active === true,
      })),
    );
  }

  private applyCzscMarkers(
    time: (timestamp: string) => UTCTimestamp,
  ) {
    if (!this.model || this.kind !== ChartGroupKind.FIVE_MINUTE) {
      return;
    }
    // 原语整体替换 markers 数组（原子替换，旧标记不残留），按 (time, price) 精确定位。
    this.czscMarkerPrimitive?.setMarkers(
      this.model.czscMarkers.map((marker) => ({
        time: time(marker.timestamp),
        price: marker.price,
        side: marker.side,
        label: marker.label,
      })),
    );
  }

  private toLineData(
    points: ChartGroupModel["vwap"],
    time: (timestamp: string) => UTCTimestamp,
  ): Array<LineData<Time> | WhitespaceData<Time>> {
    return points.map((point) =>
      point.value === null
        ? { time: time(point.timestamp) }
        : { time: time(point.timestamp), value: point.value },
    );
  }

  private rebuildTimeMaps() {
    if (!this.model) {
      return;
    }
    this.previousTimeByTime.clear();
    this.priceValues.clear();
    this.volumeValues.clear();
    this.macdValues.clear();
    this.macdSeriesByTime.clear();

    let previous: number | null = null;
    for (const bar of this.model.bars) {
      const time = this.model.timeByTimestamp[bar.timestamp];
      this.previousTimeByTime.set(time, previous);
      this.priceValues.set(time, bar.close);
      previous = time;
    }
    for (const point of this.model.volume) {
      if (point.value !== null) {
        this.volumeValues.set(
          this.model.timeByTimestamp[point.timestamp],
          point.value,
        );
      }
    }
    const macdSeries = [
      this.difSeries,
      this.deaSeries,
      this.macdHistogramSeries,
    ] as const;
    const macdIndex = buildCrosshairFallbackIndex(
      [
        this.model.macd.dif,
        this.model.macd.dea,
        this.model.macd.histogram,
      ],
      this.model.timeByTimestamp,
    );
    this.macdValues = macdIndex.values;
    for (const [time, seriesIndex] of macdIndex.seriesIndexes) {
      this.macdSeriesByTime.set(time, macdSeries[seriesIndex]);
    }
  }

  private setupRangeSynchronization() {
    for (const source of this.charts) {
      const handler = (range: LogicalRange | null) => {
        if (this.syncingRange || !range) {
          return;
        }
        this.syncingRange = true;
        try {
          for (const chart of this.charts) {
            if (chart !== source) {
              chart.timeScale().setVisibleLogicalRange(range);
            }
          }
        } finally {
          this.syncingRange = false;
        }
      };
      source.timeScale().subscribeVisibleLogicalRangeChange(handler);
      this.rangeHandlers.set(source, handler);
    }
  }

  private setupCrosshairSynchronization() {
    const targets = [
      {
        chart: this.priceChart,
        series: this.priceSeries,
        values: this.priceValues,
      },
      {
        chart: this.volumeChart,
        series: this.volumeSeries,
        values: this.volumeValues,
      },
      {
        chart: this.macdChart,
        series: this.difSeries,
        values: this.macdValues,
      },
    ];

    for (const source of targets) {
      const handler = (param: MouseEventParams<Time>) => {
        if (this.syncingCrosshair) {
          return;
        }
        this.syncingCrosshair = true;
        try {
          if (param.time === undefined) {
            targets
              .filter((target) => target !== source)
              .forEach((target) => target.chart.clearCrosshairPosition());
            return;
          }
          const numericTime = Number(param.time);
          for (const target of targets) {
            if (target === source) {
              continue;
            }
            const resolved = resolveCrosshairTarget(
              target.values,
              numericTime,
            );
            if (resolved.action === "clear") {
              target.chart.clearCrosshairPosition();
              continue;
            }
            target.chart.setCrosshairPosition(
              resolved.value,
              param.time,
              target === targets[2]
                ? (this.macdSeriesByTime.get(numericTime) ??
                    this.difSeries)
                : (target.series as NumericSeries),
            );
          }
        } finally {
          this.syncingCrosshair = false;
        }
      };
      source.chart.subscribeCrosshairMove(handler);
      this.crosshairHandlers.set(source.chart, handler);
    }
  }

  private resize() {
    for (const [key, chart] of [
      ["price", this.priceChart],
      ["volume", this.volumeChart],
      ["macd", this.macdChart],
    ] as const) {
      const container = this.containers[key];
      if (container.clientWidth > 0 && container.clientHeight > 0) {
        chart.applyOptions({
          width: container.clientWidth,
          height: container.clientHeight,
        });
      }
    }
    // following：按新绘图区宽度重算 N 并右对齐；manual 保留逻辑范围不跳回最新。
    if (
      this.viewport?.followState === FollowState.FOLLOWING &&
      this.model &&
      this.model.timestamps.length > 0
    ) {
      const plotWidth = this.priceChart.timeScale().width();
      const visibleCount = calculateVisibleCount(
        plotWidth,
        this.barSlotWidth,
      );
      this.viewport = followLatest(this.viewport, visibleCount);
      this.applyVisibleRange();
    }
  }
}
