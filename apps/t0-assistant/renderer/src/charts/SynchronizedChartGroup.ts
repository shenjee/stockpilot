import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  MismatchDirection,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
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
  calculateIntradayPriceRange,
  createPriceExactPriceFormat,
  formatMarketTick,
  formatPriceAxisTickLabels,
  formatPriceExactLabel,
  formatVolumeAxisLabel,
  formatVolumeAxisLabels,
  PRICE_EXACT_PRICE_FORMAT,
  resolvePriceAxisMinMove,
  type ChartGroupModel,
} from "./chart-model.mjs";
import { PivotZonePrimitive } from "./pivot-zone-primitive.mjs";
import { CzscMarkerPrimitive } from "./czsc-marker-primitive.mjs";
import { DivergenceMarkerPrimitive } from "./divergence-marker-primitive.mjs";
import {
  CHART_RIGHT_Y_AXIS_WIDTH,
  syncChartGroupPriceScaleWidths,
} from "./chart-scale-alignment.mjs";
import {
  FOLLOW_MIN_VISIBLE_BARS_5M,
  FollowState,
  MANUAL_MIN_VISIBLE_BARS_5M,
  MAX_VISIBLE_BARS,
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
import {
  MARKET_BAR_TOOLTIP_CLASS,
  MARKET_BAR_TOOLTIP_MARGIN_PX,
  buildMarketBarTooltipViewModel,
  findMarketBarByUtcSeconds,
  isPointerInPricePlotArea,
  renderMarketBarTooltipContent,
  resolveMarketBarTooltipCorner,
  shouldShowMarketBarTooltip,
} from "./market-bar-tooltip.mjs";

interface ChartGroupContainers {
  price: HTMLElement;
  volume: HTMLElement;
  macd: HTMLElement;
}

interface ChartGroupOptions {
  containers: ChartGroupContainers;
  kind: ChartGroupModel["kind"];
  /** 5 分钟行情 Tooltip 挂载点；不可挂在 LC container 内，否则破坏布局。 */
  tooltipHost?: HTMLElement | null;
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
  tickmarksFormatter: formatVolumeAxisLabels,
  minMove: 1,
};
// LC default right-price-scale margins; restored when the intraday chart falls
// back to auto-scale (no valid previous close).
const AUTOSCALE_MARGINS = { top: 0.2, bottom: 0.1 };
// Zero margins so the previous-close-centred symmetric range maps edge-to-edge:
// P0 at vertical centre, yMax/yMin at the top/bottom boundary (spec §6.2.1).
const INTRADAY_FIXED_MARGINS = { top: 0, bottom: 0 };

export class SynchronizedChartGroup {
  private readonly containers: ChartGroupContainers;
  private readonly tooltipHost: HTMLElement | null;
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
  private readonly divergenceMarkerPrimitive: DivergenceMarkerPrimitive | null;
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
  // Live 追加竞态抑制窗口：setModel 后，LC 可能在数据追加的下一帧布局时发出
  // “视图尚未跟进”的落后范围通知（其 to 仍指向追加前的旧右边缘）。该通知是
  // 图表库副作用而非用户操作，若在 following 下交给 setManualRange 会把视图钉在
  // 旧右边缘，导致后续新 K 线不再右移。setModel 用 requestAnimationFrame 把该
  // 标记保持到数据追加的下一帧结束之后；窗口内 setupViewportTracking 忽略范围
  // 通知。用户真实交互（指针/滚轮）发生在更晚的独立事件循环，不受该窗口影响。
  private suppressUntilFrame: number | null = null;
  private reportTimer: ReturnType<typeof setTimeout> | null = null;
  private viewportRangeHandler:
    | ((range: LogicalRange | null) => void)
    | null = null;
  private syncingRange = false;
  private syncingCrosshair = false;
  private previousTimeByTime = new Map<number, number | null>();
  private structureSeries: ISeriesApi<"Line">[] = [];
  private tradeMarkerSeries = new Map<string, ISeriesApi<"Line">>();
  private tradeMarkerPlugins = new Map<string, ISeriesMarkersPluginApi<Time>>();
  private alignedPriceScaleWidth = CHART_RIGHT_Y_AXIS_WIDTH;
  private priceScaleResyncFrame: number | null = null;
  private priceAxisMinMoveFrame: number | null = null;
  private crosshairClearFrame: number | null = null;
  // 5 分钟价格主图固定角落行情 Tooltip（issue #144）。仅 FIVE_MINUTE 启用。
  private marketBarTooltipEl: HTMLElement | null = null;
  private marketBarTooltipFrame: number | null = null;
  private marketBarTooltipCrosshairHandler:
    | ((param: MouseEventParams<Time>) => void)
    | null = null;
  private marketBarTooltipPointerHandlers: {
    move: (event: PointerEvent) => void;
    leave: () => void;
    down: () => void;
    up: (event: PointerEvent) => void;
  } | null = null;
  private marketBarTooltipPointerOverPlot = false;
  /** 最近一次 pointer 的 client 坐标；resize/refresh 用最新布局重算命中。 */
  private marketBarTooltipPointerClient: { x: number; y: number } | null =
    null;
  private marketBarTooltipDragging = false;
  private marketBarTooltipActiveTime: number | null = null;
  private marketBarTooltipCorner: "left" | "right" | null = null;

  constructor(options: ChartGroupOptions) {
    this.containers = options.containers;
    this.tooltipHost = options.tooltipHost ?? null;
    this.kind = options.kind;
    this.barSlotWidth = options.barSlotWidth ?? 8;
    this.initialViewport = options.initialViewport;
    this.onViewportChange = options.onViewportChange;

    this.priceChart = this.createChart(options.containers.price, false, {
      exactPriceLabels: true,
    });
    this.volumeChart = this.createChart(options.containers.volume, false, {
      compactVolumeLabels: true,
    });
    this.macdChart = this.createChart(options.containers.macd, true, {
      exactPriceLabels: true,
    });
    this.charts = [this.priceChart, this.volumeChart, this.macdChart];

    // VOL histogram 必须是 volume 图上的第一个 series：LC 用 formatterSource[0]
    // 决定右轴刻度格式。若 MA 线先创建，轴标签会退回 4000000.00 这类宽格式。
    this.volumeSeries = this.volumeChart.addSeries(HistogramSeries, {
      priceFormat: VOLUME_PRICE_FORMAT,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    if (this.kind === ChartGroupKind.FIVE_MINUTE) {
      this.priceSeries = this.priceChart.addSeries(CandlestickSeries, {
        upColor: RED,
        downColor: GREEN,
        borderUpColor: RED,
        borderDownColor: GREEN,
        wickUpColor: RED,
        wickDownColor: GREEN,
        priceFormat: PRICE_EXACT_PRICE_FORMAT,
        priceLineVisible: false,
      });
      this.vwapSeries = null;
      for (const [period, color] of Object.entries(MA_COLORS) as Array<
        [keyof typeof MA_COLORS, string]
      >) {
        this.movingAverageSeries[period] = this.priceChart.addSeries(LineSeries, {
          color,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
        });
      }
      // BOLL 三条线默认显示，无独立开关。
      this.bollUpperSeries = this.priceChart.addSeries(LineSeries, {
        color: BOLL_COLOR,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      this.bollMiddleSeries = this.priceChart.addSeries(LineSeries, {
        color: BOLL_COLOR,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      this.bollLowerSeries = this.priceChart.addSeries(LineSeries, {
        color: BOLL_COLOR,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      // 笔中枢以填充矩形原语表达，替换歧义的双线实现。
      this.pivotZonePrimitive = new PivotZonePrimitive();
      this.priceSeries.attachPrimitive(this.pivotZonePrimitive);
      // CZSC 买卖点按 (time, price) 精确定位，不依赖内置 markers 的 bar 相对定位。
      this.czscMarkerPrimitive = new CzscMarkerPrimitive();
      this.priceSeries.attachPrimitive(this.czscMarkerPrimitive);
      // 背驰标注（Bull Div / Bear Div）：箭头+标签，颜色与同侧买卖点一致。
      this.divergenceMarkerPrimitive = new DivergenceMarkerPrimitive();
      this.priceSeries.attachPrimitive(this.divergenceMarkerPrimitive);
      this.volumeMa5Series = this.volumeChart.addSeries(LineSeries, {
        color: AMBER,
        lineWidth: 1,
        priceFormat: VOLUME_PRICE_FORMAT,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      this.volumeMa10Series = this.volumeChart.addSeries(LineSeries, {
        color: BLUE,
        lineWidth: 1,
        priceFormat: VOLUME_PRICE_FORMAT,
        priceLineVisible: false,
        lastValueVisible: false,
      });
    } else {
      this.priceSeries = this.priceChart.addSeries(LineSeries, {
        color: BLUE,
        lineWidth: 2,
        priceFormat: PRICE_EXACT_PRICE_FORMAT,
        priceLineVisible: false,
      });
      this.vwapSeries = this.priceChart.addSeries(LineSeries, {
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
      this.divergenceMarkerPrimitive = null;
      this.volumeMa5Series = null;
      this.volumeMa10Series = null;
    }

    this.difSeries = this.macdChart.addSeries(LineSeries, {
      color: BLUE,
      lineWidth: 1,
      priceFormat: PRICE_EXACT_PRICE_FORMAT,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    this.deaSeries = this.macdChart.addSeries(LineSeries, {
      color: AMBER,
      lineWidth: 1,
      priceFormat: PRICE_EXACT_PRICE_FORMAT,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    this.macdHistogramSeries = this.macdChart.addSeries(HistogramSeries, {
      priceFormat: PRICE_EXACT_PRICE_FORMAT,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    this.setupRangeSynchronization();
    this.setupCrosshairSynchronization();
    this.setupViewportTracking();
    this.setupMarketBarTooltip();
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
      this.applyIntradayPriceRange();
      this.syncPriceAxisMinMove();
      this.syncRightPriceScaleWidths();
      this.applyViewport();
      this.schedulePriceScaleResync();
    } finally {
      this.applyingViewportRange = wasApplyingViewportRange;
    }
    // 异步竞态窗口：LC 在数据追加的下一帧布局时可能再发出“视图尚未跟进”的落后
    // 范围通知（to 仍指向追加前旧右边缘）。suppressUntilFrame 覆盖到该帧结束之后，
    // 让 setupViewportTracking 忽略它。嵌套 rAF 确保清除晚于 LC 同帧的布局通知。
    // 每次 setModel 重置窗口（取消前一次的 rAF 再重新设置），确保高频连续追加时
    // 每次都有独立覆盖窗口，不因前一次窗口提前过期而遗漏后续追加的落后通知。
    if (this.suppressUntilFrame !== null) {
      cancelAnimationFrame(this.suppressUntilFrame);
    }
    this.suppressUntilFrame = requestAnimationFrame(() => {
      this.suppressUntilFrame = requestAnimationFrame(() => {
        this.suppressUntilFrame = null;
      });
    });
    // 实盘动态 K / 回放推进后原地刷新；激活时间不存在则立即隐藏。
    this.scheduleMarketBarTooltipRefresh();
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
      // Live 追加竞态（异步变体）：following 下 setModel 已把视图推进到新 latest，
      // 但 LC 可能在 guard 释放后、于数据追加的下一帧布局时再发出一条“视图尚未跟
      // 进”的过渡通知，其范围仍停留在追加前的旧位置。它不是用户操作（用户交互由
      // 指针/滚轮驱动，发生在独立事件循环，远晚于该过渡帧）。若按用户操作走
      // setManualRange，atLatestEdge=false 会把 following 翻成 manual 并把视图钉在
      // 旧右边缘，之后的新 K 线全部停在可视窗口之外（Live 不右移）。
      // setModel 会把 suppressUntilFrame 标记到“下一帧结束之后”；此处若仍处于该
      // 帧窗口内，判定为 LC 过渡通知并忽略，保持 following。提前返回避免无谓的
      // span/isZoom 计算（被忽略的通知不应污染缩放判定基线）。
      if (this.suppressUntilFrame !== null) {
        return;
      }
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
      // 平移/缩放后价格轴自动范围会变；等三图 range sync + LC layout 后再重算 minMove。
      this.schedulePriceAxisMinMoveSync();
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
    if (this.priceAxisMinMoveFrame !== null) {
      cancelAnimationFrame(this.priceAxisMinMoveFrame);
      this.priceAxisMinMoveFrame = null;
    }
    if (this.suppressUntilFrame !== null) {
      cancelAnimationFrame(this.suppressUntilFrame);
      this.suppressUntilFrame = null;
    }
    this.cancelCrosshairClear();
    this.teardownMarketBarTooltip();
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
    options: { compactVolumeLabels?: boolean; exactPriceLabels?: boolean } = {},
  ) {
    return createChart(container, {
      width: Math.max(1, container.clientWidth),
      height: Math.max(1, container.clientHeight),
      layout: {
        background: { type: ColorType.Solid, color: "#0d1421" },
        textColor: MUTED,
        fontSize: 10,
        // LC 5.x default; keeps TradingView attribution link on the chart pane.
        attributionLogo: true,
      },
      grid: {
        vertLines: { color: "#182235" },
        horzLines: { color: "#182235" },
      },
      crosshair: {
        // Magnet：水平标签吸附到光标时间对应系列的真实数据值（而非自由 Y 坐标）。
        mode: CrosshairMode.Magnet,
        vertLine: { color: "#64748b", width: 1, style: 2, labelVisible: true },
        horzLine: { color: "#475569", width: 1, style: 2 },
      },
      rightPriceScale: {
        borderColor: "#2a3850",
        minimumWidth: CHART_RIGHT_Y_AXIS_WIDTH,
      },
      timeScale: {
        visible: showTimeScale,
        borderColor: "#2a3850",
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 0,
        // 5 分钟：钳制滚动边界，latest/oldest 贴边后不可拖出空白（右缘=最新 K）。
        // 分时：必须关闭。分时用全日交易分钟轴 + 未来分钟 whitespace；若开启
        // fixRightEdge，LC 会把右缘钉在最后一根有值分钟上，把 {from:0,to:全日}
        // 钳成「贴右、新分钟往左顶」——与 09:30→15:00 从左往右生长相反。
        fixLeftEdge: this.kind === ChartGroupKind.FIVE_MINUTE,
        fixRightEdge: this.kind === ChartGroupKind.FIVE_MINUTE,
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
        ...(options.compactVolumeLabels
          ? {
              priceFormatter: (price: number) => formatVolumeAxisLabel(price),
              tickmarksPriceFormatter: formatVolumeAxisLabels,
            }
          : options.exactPriceLabels
            ? {
                priceFormatter: (price: number) => formatPriceExactLabel(price),
                tickmarksPriceFormatter: formatPriceAxisTickLabels,
              }
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
      this.applyDivergenceMarkers(time);
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
      const series = this.priceChart.addSeries(LineSeries, {
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

  private applyDivergenceMarkers(
    time: (timestamp: string) => UTCTimestamp,
  ) {
    if (!this.model || this.kind !== ChartGroupKind.FIVE_MINUTE) {
      return;
    }
    this.divergenceMarkerPrimitive?.setMarkers(
      this.model.divergenceMarkers.map((marker) => ({
        time: time(marker.timestamp),
        price: marker.price,
        side: marker.side,
        label: marker.label,
        divergenceType: marker.divergenceType,
      })),
    );
  }

  private setTradeMarkerData() {
    for (const plugin of this.tradeMarkerPlugins.values()) {
      plugin.detach();
    }
    this.tradeMarkerPlugins.clear();
    for (const series of this.tradeMarkerSeries.values()) {
      this.priceChart.removeSeries(series);
    }
    this.tradeMarkerSeries.clear();

    if (!this.model || this.model.kind !== ChartGroupKind.FIVE_MINUTE) {
      return;
    }

    for (const marker of this.model.tradeMarkers) {
      const series = this.priceChart.addSeries(LineSeries, {
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
      const markersPlugin = createSeriesMarkers(series, [seriesMarker]);
      this.tradeMarkerSeries.set(marker.trade_id, series);
      this.tradeMarkerPlugins.set(marker.trade_id, markersPlugin);
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
            const price = this.seriesValueAtTime(target.series, param.time);
            if (price === null) {
              target.chart.clearCrosshairPosition();
              continue;
            }
            target.chart.setCrosshairPosition(price, param.time, target.series);
          }
        } finally {
          this.syncingCrosshair = false;
        }
      };
      source.chart.subscribeCrosshairMove(handler);
      this.crosshairHandlers.set(source.chart, handler);
    }
  }

  private seriesValueAtTime(
    series: ISeriesApi<"Candlestick"> | ISeriesApi<"Line"> | ISeriesApi<"Histogram">,
    time: Time,
  ): number | null {
    const index = this.priceChart.timeScale().timeToIndex(time, true);
    if (index === null) {
      return null;
    }
    // None：不要回退到邻近 bar，避免 whitespace（如 MACD dif=null）误用上一根数值。
    const point = series.dataByIndex(index, MismatchDirection.None);
    if (!point || typeof point !== "object") {
      return null;
    }
    if ("close" in point && typeof point.close === "number") {
      return point.close;
    }
    if ("value" in point && typeof point.value === "number") {
      return point.value;
    }
    return null;
  }

  // 用户平移/缩放后 LC 会先更新可见逻辑范围，再按可见数据重算价格轴 autoScale。
  // 用 rAF 等到 layout 完成后再读 getVisibleRange，才能正确在 0.01 ↔ 1 之间切换。
  private schedulePriceAxisMinMoveSync() {
    if (this.priceAxisMinMoveFrame !== null) {
      cancelAnimationFrame(this.priceAxisMinMoveFrame);
    }
    this.priceAxisMinMoveFrame = requestAnimationFrame(() => {
      this.priceAxisMinMoveFrame = null;
      this.syncPriceAxisMinMove();
    });
  }

  // 分时价格图纵轴：以前收价 P0 为固定中轴，按当日开盘至当前数据前缀的最大绝对
  // 偏离生成上下镜像范围（spec §6.2.1, issue #143）。R 是数据前缀的纯派生值，不保存
  // 历史状态——实盘只扩不缩、回放确定性重算和组件重建一致性由同一套算法满足。
  // previousClose 无效时回退到 LC 自动缩放，不指定中心价格。
  private applyIntradayPriceRange() {
    if (this.kind !== ChartGroupKind.ONE_MINUTE || !this.model) {
      return;
    }
    const scale = this.priceSeries.priceScale();
    const result = calculateIntradayPriceRange(
      this.model.previousClose,
      this.model.bars,
    );
    if (result === null) {
      // previousClose 缺失/无效：沿用 LC 自动缩放与默认边距。
      if (scale.options().autoScale) {
        return;
      }
      scale.applyOptions({ scaleMargins: AUTOSCALE_MARGINS });
      scale.setAutoScale(true);
      return;
    }
    // previousClose 有效：固定中轴 + 零边距镜像范围。
    const margins = scale.options().scaleMargins;
    if (margins.top !== 0 || margins.bottom !== 0) {
      scale.applyOptions({ scaleMargins: INTRADAY_FIXED_MARGINS });
    }
    const current = scale.getVisibleRange();
    const needsUpdate =
      !current ||
      Math.abs(current.from - result.yMin) > 1e-9 ||
      Math.abs(current.to - result.yMax) > 1e-9;
    if (needsUpdate) {
      scale.setVisibleRange({ from: result.yMin, to: result.yMax });
    }
  }

  private syncPriceAxisMinMove() {
    // 分时价格图使用前收居中四等分刻度（spec §6.2.1）：minMove = R*P0/4，
    // 使 LC 以四分之一半轴间距生成刻度。previousClose 无效时回退通用规则。
    const intradayResult =
      this.kind === ChartGroupKind.ONE_MINUTE && this.model
        ? calculateIntradayPriceRange(
            this.model.previousClose,
            this.model.bars,
          )
        : null;

    if (intradayResult !== null) {
      this.applyPriceExactMinMove(this.priceSeries, intradayResult.tickStep);
    } else {
      const priceMinMove = resolvePriceAxisMinMove(
        ...this.priceAxisExtent(this.priceSeries),
      );
      this.applyPriceExactMinMove(this.priceSeries, priceMinMove);
    }

    const macdExtent = this.macdAxisExtent();
    const macdMinMove = resolvePriceAxisMinMove(...macdExtent);
    for (const series of [this.difSeries, this.deaSeries, this.macdHistogramSeries]) {
      this.applyPriceExactMinMove(series, macdMinMove);
    }
  }

  private applyPriceExactMinMove(
    series: ISeriesApi<"Candlestick"> | ISeriesApi<"Line"> | ISeriesApi<"Histogram">,
    minMove: number,
  ) {
    const current = series.options().priceFormat;
    if (
      current.type === "custom" &&
      typeof current.minMove === "number" &&
      current.minMove === minMove
    ) {
      return;
    }
    series.applyOptions({
      priceFormat: createPriceExactPriceFormat(minMove),
    });
  }

  private priceAxisExtent(
    series: ISeriesApi<"Candlestick"> | ISeriesApi<"Line"> | ISeriesApi<"Histogram">,
  ): [number | null, number | null] {
    const visible = series.priceScale().getVisibleRange();
    if (visible) {
      return [visible.from, visible.to];
    }
    if (!this.model) {
      return [null, null];
    }
    let min = Number.POSITIVE_INFINITY;
    let max = Number.NEGATIVE_INFINITY;
    for (const point of this.model.price) {
      if ("high" in point && typeof point.high === "number") {
        min = Math.min(min, point.low, point.high);
        max = Math.max(max, point.low, point.high);
      } else if ("value" in point && typeof point.value === "number") {
        min = Math.min(min, point.value);
        max = Math.max(max, point.value);
      }
    }
    if (!Number.isFinite(min) || !Number.isFinite(max)) {
      return [null, null];
    }
    return [min, max];
  }

  private macdAxisExtent(): [number | null, number | null] {
    const visible = this.difSeries.priceScale().getVisibleRange();
    if (visible) {
      return [visible.from, visible.to];
    }
    if (!this.model) {
      return [null, null];
    }
    let min = Number.POSITIVE_INFINITY;
    let max = Number.NEGATIVE_INFINITY;
    for (const series of [
      this.model.macd.dif,
      this.model.macd.dea,
      this.model.macd.histogram,
    ]) {
      for (const point of series) {
        if (typeof point.value === "number" && Number.isFinite(point.value)) {
          min = Math.min(min, point.value);
          max = Math.max(max, point.value);
        }
      }
    }
    if (!Number.isFinite(min) || !Number.isFinite(max)) {
      return [null, null];
    }
    return [min, max];
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
    this.scheduleMarketBarTooltipRefresh();
  }

  private setupMarketBarTooltip() {
    if (this.kind !== ChartGroupKind.FIVE_MINUTE) {
      return;
    }
    const host = this.tooltipHost;
    if (!host) {
      return;
    }
    const container = this.containers.price;
    const tip = document.createElement("div");
    tip.className = MARKET_BAR_TOOLTIP_CLASS;
    tip.setAttribute("role", "status");
    tip.setAttribute("aria-live", "polite");
    tip.style.display = "none";
    tip.style.pointerEvents = "none";
    host.appendChild(tip);
    this.marketBarTooltipEl = tip;

    const crosshairHandler = (param: MouseEventParams<Time>) => {
      if (this.syncingCrosshair) {
        // 兄弟图同步写入的 crosshair 不是用户在价格主图上的指针，不改变激活时间。
        return;
      }
      if (param.time === undefined) {
        this.marketBarTooltipActiveTime = null;
        this.scheduleMarketBarTooltipRefresh();
        return;
      }
      const numericTime = Number(param.time);
      if (!Number.isFinite(numericTime)) {
        this.marketBarTooltipActiveTime = null;
        this.scheduleMarketBarTooltipRefresh();
        return;
      }
      this.marketBarTooltipActiveTime = numericTime;
      this.scheduleMarketBarTooltipRefresh();
    };
    this.priceChart.subscribeCrosshairMove(crosshairHandler);
    this.marketBarTooltipCrosshairHandler = crosshairHandler;

    const move = (event: PointerEvent) => {
      this.marketBarTooltipDragging = event.buttons > 0;
      this.marketBarTooltipPointerClient = {
        x: event.clientX,
        y: event.clientY,
      };
      this.recomputeMarketBarTooltipPointerHit();
      this.scheduleMarketBarTooltipRefresh();
    };
    const leave = () => {
      this.marketBarTooltipPointerClient = null;
      this.marketBarTooltipPointerOverPlot = false;
      this.marketBarTooltipDragging = false;
      this.marketBarTooltipActiveTime = null;
      this.scheduleMarketBarTooltipRefresh();
    };
    const down = () => {
      this.marketBarTooltipDragging = true;
      this.scheduleMarketBarTooltipRefresh();
    };
    const up = (event: PointerEvent) => {
      this.marketBarTooltipDragging = event.buttons > 0;
      this.scheduleMarketBarTooltipRefresh();
    };
    container.addEventListener("pointermove", move);
    container.addEventListener("pointerleave", leave);
    container.addEventListener("pointerdown", down);
    container.addEventListener("pointerup", up);
    this.marketBarTooltipPointerHandlers = { move, leave, down, up };
  }

  private teardownMarketBarTooltip() {
    if (this.marketBarTooltipFrame !== null) {
      cancelAnimationFrame(this.marketBarTooltipFrame);
      this.marketBarTooltipFrame = null;
    }
    if (this.marketBarTooltipCrosshairHandler) {
      this.priceChart.unsubscribeCrosshairMove(
        this.marketBarTooltipCrosshairHandler,
      );
      this.marketBarTooltipCrosshairHandler = null;
    }
    const handlers = this.marketBarTooltipPointerHandlers;
    if (handlers) {
      const container = this.containers.price;
      container.removeEventListener("pointermove", handlers.move);
      container.removeEventListener("pointerleave", handlers.leave);
      container.removeEventListener("pointerdown", handlers.down);
      container.removeEventListener("pointerup", handlers.up);
      this.marketBarTooltipPointerHandlers = null;
    }
    if (this.marketBarTooltipEl?.parentNode) {
      this.marketBarTooltipEl.parentNode.removeChild(this.marketBarTooltipEl);
    }
    this.marketBarTooltipEl = null;
    this.marketBarTooltipPointerOverPlot = false;
    this.marketBarTooltipPointerClient = null;
    this.marketBarTooltipDragging = false;
    this.marketBarTooltipActiveTime = null;
    this.marketBarTooltipCorner = null;
  }

  private scheduleMarketBarTooltipRefresh() {
    if (!this.marketBarTooltipEl) {
      return;
    }
    if (this.marketBarTooltipFrame !== null) {
      cancelAnimationFrame(this.marketBarTooltipFrame);
    }
    this.marketBarTooltipFrame = requestAnimationFrame(() => {
      this.marketBarTooltipFrame = null;
      this.refreshMarketBarTooltip();
    });
  }

  /**
   * 价格主图隐藏了 timeScale，timeScale().width() 恒为 0。
   * 命中与角落定位改用 paneSize（实际绘图区）。
   */
  private pricePlotSize(): { width: number; height: number } {
    const pane = this.priceChart.paneSize(0);
    return {
      width: pane?.width ?? 0,
      height: pane?.height ?? 0,
    };
  }

  /** 用最近指针 client 坐标与当前 rect/paneSize 重算是否在绘图区内。 */
  private recomputeMarketBarTooltipPointerHit() {
    const pointer = this.marketBarTooltipPointerClient;
    if (!pointer) {
      this.marketBarTooltipPointerOverPlot = false;
      return;
    }
    const { width: plotWidth, height: plotHeight } = this.pricePlotSize();
    this.marketBarTooltipPointerOverPlot = isPointerInPricePlotArea({
      clientX: pointer.x,
      clientY: pointer.y,
      containerRect: this.containers.price.getBoundingClientRect(),
      plotWidth,
      plotHeight,
    });
  }

  private refreshMarketBarTooltip() {
    const tip = this.marketBarTooltipEl;
    if (!tip) {
      return;
    }
    if (this.kind !== ChartGroupKind.FIVE_MINUTE || !this.model) {
      this.hideMarketBarTooltip();
      return;
    }
    // 布局变化后可能没有新的 pointermove；用缓存坐标按最新 rect 重测。
    this.recomputeMarketBarTooltipPointerHit();
    const activeTime = this.marketBarTooltipActiveTime;
    const bar =
      activeTime === null
        ? null
        : findMarketBarByUtcSeconds(
            this.model.bars,
            this.model.timeByTimestamp,
            activeTime,
          );
    if (
      !shouldShowMarketBarTooltip({
        pointerOverPricePlot: this.marketBarTooltipPointerOverPlot,
        isDragging: this.marketBarTooltipDragging,
        bar,
      })
    ) {
      this.hideMarketBarTooltip();
      return;
    }

    const { width: plotWidth } = this.pricePlotSize();
    const barCoordinate =
      activeTime === null
        ? null
        : this.priceChart.timeScale().timeToCoordinate(activeTime as Time);
    let corner: "left" | "right" | null = null;
    if (
      typeof barCoordinate === "number" &&
      Number.isFinite(barCoordinate) &&
      plotWidth > 0
    ) {
      corner = resolveMarketBarTooltipCorner({
        barCoordinate: Number(barCoordinate),
        plotWidth,
      });
    } else if (plotWidth <= 0) {
      // 布局尚未就绪：仍展示内容（实盘 OHLC 原地刷新），角落用上次或默认右上。
      corner = this.marketBarTooltipCorner ?? "right";
    } else {
      // 绘图区已就绪但激活 bar 不在当前可见范围。
      this.hideMarketBarTooltip();
      return;
    }
    if (!corner) {
      // 含 timeToCoordinate 有限但已越出 [0, plotWidth] 的情况。
      this.hideMarketBarTooltip();
      return;
    }
    this.marketBarTooltipCorner = corner;

    const viewModel = buildMarketBarTooltipViewModel(bar);
    if (!viewModel) {
      this.hideMarketBarTooltip();
      return;
    }
    renderMarketBarTooltipContent(tip, viewModel);
    tip.style.display = "block";
    tip.style.top = `${MARKET_BAR_TOOLTIP_MARGIN_PX}px`;
    if (corner === "left") {
      tip.style.left = `${MARKET_BAR_TOOLTIP_MARGIN_PX}px`;
      tip.style.right = "auto";
    } else {
      tip.style.left = "auto";
      tip.style.right = `${
        this.alignedPriceScaleWidth + MARKET_BAR_TOOLTIP_MARGIN_PX
      }px`;
    }
  }

  private hideMarketBarTooltip() {
    const tip = this.marketBarTooltipEl;
    if (!tip) {
      return;
    }
    tip.style.display = "none";
    renderMarketBarTooltipContent(tip, null);
  }

  private visibleCount(plotWidth: number, seriesLength: number) {
    if (this.kind === ChartGroupKind.ONE_MINUTE) {
      return seriesLength;
    }
    return calculateVisibleCount(plotWidth, this.barSlotWidth, {
      minimum: FOLLOW_MIN_VISIBLE_BARS_5M,
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
    this.syncPriceAxisMinMove();
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
      minimumVisibleCount: MANUAL_MIN_VISIBLE_BARS_5M,
      maximumVisibleCount: MAX_VISIBLE_BARS,
    };
  }
}
