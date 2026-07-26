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
  type ChartGroupModel,
} from "./chart-model.mjs";
import {
  buildCrosshairFallbackIndex,
  resolveCrosshairTarget,
} from "./chart-interaction.mjs";
import type { TradeMarkerModel } from "./trade-markers.mjs";

interface ChartGroupContainers {
  price: HTMLElement;
  volume: HTMLElement;
  macd: HTMLElement;
}

interface ChartGroupOptions {
  containers: ChartGroupContainers;
  kind: ChartGroupModel["kind"];
}

type NumericSeries = ISeriesApi<"Line"> | ISeriesApi<"Histogram">;

const RED = "#ef5350";
const GREEN = "#26a69a";
const BLUE = "#4f8cff";
const AMBER = "#f6b94a";
const MUTED = "#8090a8";
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
  private hasAppliedInitialRange = false;
  private syncingRange = false;
  private syncingCrosshair = false;
  private previousTimeByTime = new Map<number, number | null>();
  private priceValues = new Map<number, number>();
  private volumeValues = new Map<number, number>();
  private macdValues = new Map<number, number>();
  private macdSeriesByTime = new Map<number, NumericSeries>();
  private structureSeries: ISeriesApi<"Line">[] = [];
  private tradeMarkerSeries = new Map<string, ISeriesApi<"Line">>();

  constructor(options: ChartGroupOptions) {
    this.containers = options.containers;
    this.kind = options.kind;

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

    if (model.timestamps.length === 0) {
      // setSeriesData() has cleared every series; allow the next non-empty
      // model to establish a fresh initial viewport.
      this.hasAppliedInitialRange = false;
    } else if (!this.hasAppliedInitialRange) {
      this.charts.forEach((chart) => chart.timeScale().fitContent());
      this.hasAppliedInitialRange = true;
    }
  }

  destroy() {
    this.resizeObserver.disconnect();
    for (const [chart, handler] of this.crosshairHandlers) {
      chart.unsubscribeCrosshairMove(handler);
    }
    for (const [chart, handler] of this.rangeHandlers) {
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(handler);
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
      this.setStructureData(time);
      this.setTradeMarkerData();
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

    for (const zone of this.model.pivotZones) {
      for (const value of [zone.high, zone.low]) {
        const series = this.priceChart.addLineSeries({
          color: "#f59e0b",
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          priceLineVisible: false,
          lastValueVisible: false,
        });
        series.setData([
          { time: time(zone.start_timestamp), value },
          { time: time(zone.end_timestamp), value },
        ]);
        this.structureSeries.push(series);
      }
    }
  }

  private setTradeMarkerData() {
    for (const [id, series] of this.tradeMarkerSeries) {
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
  }
}
