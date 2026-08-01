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
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
  type WhitespaceData,
} from "lightweight-charts";
import {
  ChartGroupKind,
  formatMarketTick,
  formatVolumeAxisLabel,
  type ChartGroupModel,
} from "./chart-model.mjs";
import { PivotZonePrimitive } from "./pivot-zone-primitive";
import { CzscMarkerPrimitive } from "./czsc-marker-primitive";
import {
  DEFAULT_PRICE_SCALE_MIN_WIDTH,
  syncChartGroupPriceScaleWidths,
} from "./chart-scale-alignment.mjs";
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


const RED = "#ef5350";
const GREEN = "#26a69a";
const BLUE = "#4f8cff";
const AMBER = "#f6b94a";
const MUTED = "#8090a8";
const BOLL_COLOR = "#e879f9";
// 缩放/平移判定阈值（LC 连续逻辑范围跨度差）。纯平移跨度精确不变（浮点误差 ~1e-9），
// 缩放跨度变化至少数个 K；0.01 仅吸收浮点漂移，可靠区分二者。
const ZOOM_SPAN_EPSILON = 0.01;
// Match Chan Viewer's horizontal window policy: never let an interaction or
// transient zero-width layout collapse a populated chart to a handful of bars.
const MIN_VISIBLE_BARS = 40;
const MAX_VISIBLE_BARS = 360;
const MA_COLORS = {
  ma5: "#f6d365",
  ma10: "#7dd3fc",
  ma20: "#c4b5fd",
  ma30: "#fb923c",
  ma60: "#f472b6",
} as const;
const VOLUME_PRICE_FORMAT = {
  type: "custom" as const,
  formatter: formatVolumeAxisLabel,
  minMove: 1,
};

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
  private structureSeries: ISeriesApi<"Line">[] = [];
  private tradeMarkerSeries = new Map<string, ISeriesApi<"Line">>();
  private alignedPriceScaleWidth = DEFAULT_PRICE_SCALE_MIN_WIDTH;
  private priceScaleResyncFrame: number | null = null;
  private crosshairClearFrame: number | null = null;

  constructor(options: ChartGroupOptions) {
    this.containers = options.containers;
    this.kind = options.kind;
    this.barSlotWidth = options.barSlotWidth ?? 8;
    this.initialViewport = options.initialViewport;
    this.onViewportChange = options.onViewportChange;

    this.priceChart = this.createChart(options.containers.price, false);
    this.volumeChart = this.createChart(options.containers.volume, false, {
      compactVolumeLabels: true,
    });
    this.macdChart = this.createChart(options.containers.macd, true);
    this.charts = [this.priceChart, this.volumeChart, this.macdChart];

    // VOL histogram 必须是 volume 图上的第一个 series：LC 用 formatterSource[0]
    // 决定右轴刻度格式。若 MA 线先创建，轴标签会退回 4000000.00 这类宽格式。
    this.volumeSeries = this.volumeChart.addHistogramSeries({
      priceFormat: VOLUME_PRICE_FORMAT,
      priceLineVisible: false,
      lastValueVisible: false,
    });

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
        priceFormat: VOLUME_PRICE_FORMAT,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      this.volumeMa10Series = this.volumeChart.addLineSeries({
        color: BLUE,
        lineWidth: 1,
        priceFormat: VOLUME_PRICE_FORMAT,
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
    // setData() can synchronously emit a visible-range notification before
    // applyViewport() has advanced the logical viewport to the new model.
    // That notification is a chart-library side effect, not a user pan/zoom.
    // If it reaches setupViewportTracking it converts FOLLOWING to MANUAL and
    // pins Live at the old right edge, so later bars exist but stay off-screen.
    const wasApplyingViewportRange = this.applyingViewportRange;
    this.applyingViewportRange = true;
    try {
      this.model = model;
      this.rebuildTimeMaps();
      this.setSeriesData();
      this.syncRightPriceScaleWidths();
      this.applyViewport();
      this.schedulePriceScaleResync();
    } finally {
      this.applyingViewportRange = wasApplyingViewportRange;
    }
  }

  // 视口状态机：following 右对齐最新；manual 保留逻辑范围；空数据重置。
  // 5 分钟首次 setModel 可从 React 保存的 initialViewport 恢复；分时始终忽略旧视口，
  // 展示目标日截至当前的完整交易分钟。
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
    const visibleCount = this.visibleCount(plotWidth, times.length);
    const viewportBounds = this.viewportBounds(times.length);
    if (this.viewport === null) {
      this.viewport = restoreViewportFromSnapshot(
        this.kind === ChartGroupKind.ONE_MINUTE
          ? null
          : (this.initialViewport ?? null),
        times,
        visibleCount,
        {
          barSlotWidth: this.barSlotWidth,
          ...viewportBounds,
        },
      );
    } else {
      this.viewport = applyModel(this.viewport, times, visibleCount);
      if (this.viewport.followState === FollowState.MANUAL) {
        this.viewport = setManualRange(
          this.viewport,
          this.viewport.visibleStart,
          this.viewport.visibleEnd,
          {
            allowResumeFollowing: false,
            ...viewportBounds,
          },
        );
      }
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
    const wasApplyingViewportRange = this.applyingViewportRange;
    this.applyingViewportRange = true;
    try {
      this.priceChart.timeScale().setVisibleLogicalRange(
        range as LogicalRange,
      );
    } finally {
      this.applyingViewportRange = wasApplyingViewportRange;
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
        {
          allowResumeFollowing: !isZoom,
          ...this.viewportBounds(this.viewport.logicalToTime.length),
        },
      );
      if (
        this.viewport.visibleStart !== internal.start ||
        this.viewport.visibleEnd !== internal.end
      ) {
        // The library already displayed the over-zoomed range; immediately
        // restore the bounded range instead of waiting for the next refresh.
        this.applyVisibleRange();
      } else {
        this.reportViewport();
      }
    };
    this.priceChart.timeScale().subscribeVisibleLogicalRangeChange(handler);
    this.viewportRangeHandler = handler;
  }

  destroy() {
    if (this.reportTimer) {
      clearTimeout(this.reportTimer);
      this.reportTimer = null;
    }
    if (this.priceScaleResyncFrame !== null) {
      cancelAnimationFrame(this.priceScaleResyncFrame);
      this.priceScaleResyncFrame = null;
    }
    this.cancelCrosshairClear();
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

  private createChart(
    container: HTMLElement,
    showTimeScale: boolean,
    options: { compactVolumeLabels?: boolean } = {},
  ) {
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
        minimumWidth: DEFAULT_PRICE_SCALE_MIN_WIDTH,
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
        // LC chart-level priceFormatter 只接收 price；此处固定中文 compact 作兜底。
        ...(options.compactVolumeLabels
          ? { priceFormatter: (price: number) => formatVolumeAxisLabel(price) }
          : {}),
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
      // 分时图是“当日已发生的完整交易时间”视图，不允许手势把早盘数据
      // 滚出屏幕。5 分钟 K 线仍保留 Chan Viewer 风格的缩放和平移。
      handleScale:
        this.kind === ChartGroupKind.FIVE_MINUTE
          ? {
              axisPressedMouseMove: { time: true, price: false },
              mouseWheel: true,
              pinch: true,
            }
          : false,
      handleScroll:
        this.kind === ChartGroupKind.FIVE_MINUTE
          ? {
              mouseWheel: true,
              pressedMouseMove: true,
              horzTouchDrag: true,
              vertTouchDrag: false,
            }
          : false,
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
      this.setTradeMarkerData();
    } else {
      const priceData: Array<LineData<Time> | WhitespaceData<Time>> =
        this.model.price.flatMap((point) =>
          "value" in point
            ? [
                point.value === null
                  ? { time: time(point.timestamp) }
                  : { time: time(point.timestamp), value: point.value },
              ]
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
    const volumeData: Array<HistogramData<Time> | WhitespaceData<Time>> =
      this.model.volume.map(
      (point) =>
        point.value === null
          ? { time: time(point.timestamp) }
          : {
              time: time(point.timestamp),
              value: point.value,
              color: `${directionByTimestamp.get(point.timestamp) ?? MUTED}aa`,
            },
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

  private setTradeMarkerData() {
    for (const series of this.tradeMarkerSeries.values()) {
      this.priceChart.removeSeries(series);
    }
    this.tradeMarkerSeries.clear();

    if (!this.model || this.model.kind !== ChartGroupKind.FIVE_MINUTE) {
      return;
    }

    for (const marker of this.model.tradeMarkers) {
      const series = this.priceChart.addLineSeries({
        color: marker.color,
        lineVisible: false,
        lastValueVisible: false,
        priceLineVisible: false,
        pointMarkersVisible: false,
        priceScaleId: "right",
      });
      series.setData([{ time: marker.time as UTCTimestamp, value: marker.price }]);
      const seriesMarker: SeriesMarker<Time> = {
        time: marker.time as UTCTimestamp,
        position: "inBar",
        shape: marker.shape,
        color: marker.color,
        text: marker.label,
        size: 2,
      };
      series.setMarkers([seriesMarker]);
      this.tradeMarkerSeries.set(marker.trade_id, series);
    }
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

    let previous: number | null = null;
    for (const timestamp of this.model.timestamps) {
      const time = this.model.timeByTimestamp[timestamp];
      this.previousTimeByTime.set(time, previous);
      previous = time;
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
      { chart: this.priceChart, series: this.priceSeries },
      { chart: this.volumeChart, series: this.volumeSeries },
      { chart: this.macdChart, series: this.difSeries },
    ];

    for (const source of targets) {
      const handler = (param: MouseEventParams<Time>) => {
        if (this.syncingCrosshair) {
          return;
        }
        if (param.time === undefined) {
          // 源图 LC 会自动清除自身十字线；兄弟图由 setCrosshairPosition 写入，需延迟清除，
          // 以便鼠标从一张图移到另一张图时，目标图有机会先触发带 time 的事件并取消清除。
          this.scheduleCrosshairClear();
          return;
        }
        this.cancelCrosshairClear();
        this.syncingCrosshair = true;
        try {
          for (const target of targets) {
            if (target === source) {
              continue;
            }
            // 十字线是时间标尺：同步只传 time，price 用 0 占位即可。
            target.chart.setCrosshairPosition(0, param.time, target.series);
          }
        } finally {
          this.syncingCrosshair = false;
        }
      };
      source.chart.subscribeCrosshairMove(handler);
      this.crosshairHandlers.set(source.chart, handler);
    }
  }

  private scheduleCrosshairClear() {
    if (this.crosshairClearFrame !== null) {
      return;
    }
    this.crosshairClearFrame = requestAnimationFrame(() => {
      this.crosshairClearFrame = null;
      this.clearSyncedCrosshairs();
    });
  }

  private cancelCrosshairClear() {
    if (this.crosshairClearFrame !== null) {
      cancelAnimationFrame(this.crosshairClearFrame);
      this.crosshairClearFrame = null;
    }
  }

  private clearSyncedCrosshairs() {
    if (this.syncingCrosshair) {
      return;
    }
    this.syncingCrosshair = true;
    try {
      for (const chart of this.charts) {
        chart.clearCrosshairPosition();
      }
    } finally {
      this.syncingCrosshair = false;
    }
  }

  private resize() {
    // ResizeObserver 触发的 applyOptions 也会让 Lightweight Charts 上报可见范围。
    // 这不是用户缩放，不能进入 setupViewportTracking 的 manual/zoom 分支；否则首屏从
    // 极窄的过渡布局扩展到最终宽度时，会把初始化阶段的 1～2 根范围错误锁定下来。
    const wasApplyingViewportRange = this.applyingViewportRange;
    this.applyingViewportRange = true;
    try {
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
    } finally {
      this.applyingViewportRange = wasApplyingViewportRange;
    }
    this.syncRightPriceScaleWidths();
    // following：按新绘图区宽度重算 N 并右对齐；manual 保留逻辑范围不跳回最新。
    if (
      this.viewport?.followState === FollowState.FOLLOWING &&
      this.model &&
      this.model.timestamps.length > 0
    ) {
      const plotWidth = this.priceChart.timeScale().width();
      const visibleCount = this.visibleCount(
        plotWidth,
        this.model.timestamps.length,
      );
      this.viewport = followLatest(this.viewport, visibleCount);
      this.applyVisibleRange();
    }
    this.schedulePriceScaleResync();
  }

  private visibleCount(plotWidth: number, seriesLength: number) {
    if (this.kind === ChartGroupKind.ONE_MINUTE) {
      return seriesLength;
    }
    return calculateVisibleCount(plotWidth, this.barSlotWidth, {
      minimum: MIN_VISIBLE_BARS,
      maximum: MAX_VISIBLE_BARS,
    });
  }

  // 三图右轴标签宽度不一致时，timeScale().width() 会不同，K/VOL/MACD 无法垂直对齐。
  // 以绘图区宽度为收敛目标；右轴 minimumWidth 只是调节手段。
  private syncRightPriceScaleWidths(): boolean {
    const result = syncChartGroupPriceScaleWidths(this.charts, {
      alignedWidth: this.alignedPriceScaleWidth,
      flush: () => this.flushChartLayout(),
    });
    this.alignedPriceScaleWidth = result.alignedPriceScaleWidth;
    if (!result.converged) {
      console.warn(
        "[SynchronizedChartGroup] plot widths failed to converge",
        result.plotWidths,
      );
    }
    return result.converged;
  }

  private flushChartLayout() {
    const testFlush = (
      globalThis as typeof globalThis & { __flushRaf?: () => void }
    ).__flushRaf;
    if (testFlush) {
      testFlush();
    }
  }

  // 生产环境 LC 在 applyOptions 后需等 rAF 才能完成 layout；首帧 sync 后再调度一次。
  private schedulePriceScaleResync() {
    if (this.priceScaleResyncFrame !== null) {
      cancelAnimationFrame(this.priceScaleResyncFrame);
    }
    this.priceScaleResyncFrame = requestAnimationFrame(() => {
      this.priceScaleResyncFrame = null;
      this.resyncPriceScaleAfterLayout();
    });
  }

  private resyncPriceScaleAfterLayout() {
    this.syncRightPriceScaleWidths();
    if (
      this.viewport?.followState !== FollowState.FOLLOWING ||
      !this.model ||
      this.model.timestamps.length === 0
    ) {
      return;
    }
    const wasApplyingViewportRange = this.applyingViewportRange;
    this.applyingViewportRange = true;
    try {
      const plotWidth = this.priceChart.timeScale().width();
      const visibleCount = this.visibleCount(
        plotWidth,
        this.model.timestamps.length,
      );
      this.viewport = followLatest(this.viewport, visibleCount);
      this.applyVisibleRange();
    } finally {
      this.applyingViewportRange = wasApplyingViewportRange;
    }
  }

  private viewportBounds(seriesLength: number) {
    if (this.kind === ChartGroupKind.ONE_MINUTE) {
      return {
        minimumVisibleCount: seriesLength,
        maximumVisibleCount: seriesLength,
      };
    }
    return {
      minimumVisibleCount: MIN_VISIBLE_BARS,
      maximumVisibleCount: MAX_VISIBLE_BARS,
    };
  }
}
