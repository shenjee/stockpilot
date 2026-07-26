/**
 * CZSC 候选买卖点原语。
 *
 * Lightweight Charts 4.x 的内置 setMarkers 只能按 belowBar/aboveBar 定位，无法落在
 * 候选点的实际价格上；同一根 K 上不同价格的同侧信号也会被合并。这里用 series primitive
 * 在价格面板按 (time, price) 精确绘制箭头 + 标签：买点在价格下方指向上、卖点在价格上方
 * 指向下，与 packages/chantheory/plotting.py 的 y=base_point.price 锚点一致。
 *
 * 数据内但离屏的时间/价格会返回越界坐标（画布裁剪）；数据外返回 null 时跳过该标记。
 */
import type {
  Coordinate,
  IChartApi,
  ISeriesApi,
  ISeriesPrimitive,
  ISeriesPrimitivePaneRenderer,
  ISeriesPrimitivePaneView,
  SeriesAttachedParameter,
  SeriesPrimitivePaneViewZOrder,
  SeriesType,
  Time,
} from "lightweight-charts";

export interface CzscMarkerPrimitiveData {
  time: Time;
  price: number;
  side: "buy" | "sell";
  label: string;
}

interface BitmapCoordinateScope {
  context: CanvasRenderingContext2D;
  horizontalPixelRatio: number;
  verticalPixelRatio: number;
}

interface CanvasRenderingTarget {
  useBitmapCoordinateSpace(
    fn: (scope: BitmapCoordinateScope) => void,
  ): void;
}

const BUY_COLOR = "#22c55e";
const SELL_COLOR = "#ef4444";
const ARROW_SIZE = 7;
const ARROW_GAP = 2;
const LABEL_FONT = 10;
const LABEL_GAP = 2;

type PriceSeries = ISeriesApi<"Candlestick"> | ISeriesApi<"Line">;

class CzscMarkerRenderer implements ISeriesPrimitivePaneRenderer {
  constructor(
    private readonly markers: readonly CzscMarkerPrimitiveData[],
    private readonly chart: IChartApi,
    private readonly series: PriceSeries,
  ) {}

  draw(target: CanvasRenderingTarget): void {
    if (this.markers.length === 0) {
      return;
    }
    const timeScale = this.chart.timeScale();
    if (timeScale.width() <= 0) {
      return;
    }

    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const hRatio = scope.horizontalPixelRatio;
      const vRatio = scope.verticalPixelRatio;
      const size = ARROW_SIZE * Math.min(hRatio, vRatio);
      const gap = ARROW_GAP * vRatio;
      const labelGap = LABEL_GAP * vRatio;
      const font = `${LABEL_FONT * vRatio}px sans-serif`;

      for (const marker of this.markers) {
        const x = timeScale.timeToCoordinate(marker.time);
        const y = this.series.priceToCoordinate(marker.price);
        if (x === null || y === null) {
          continue;
        }
        const cx = (x as Coordinate) * hRatio;
        const cy = (y as Coordinate) * vRatio;
        const color = marker.side === "buy" ? BUY_COLOR : SELL_COLOR;
        ctx.fillStyle = color;
        ctx.strokeStyle = color;

        if (marker.side === "buy") {
          // 上三角：尖端在价格下方（指向价格），底边再向下。
          const tipY = cy + gap;
          const baseY = tipY + size;
          ctx.beginPath();
          ctx.moveTo(cx, tipY);
          ctx.lineTo(cx - size, baseY);
          ctx.lineTo(cx + size, baseY);
          ctx.closePath();
          ctx.fill();
          ctx.font = font;
          ctx.textAlign = "center";
          ctx.textBaseline = "top";
          ctx.fillText(marker.label, cx, baseY + labelGap);
        } else {
          // 下三角：尖端在价格上方（指向价格），底边再向上。
          const tipY = cy - gap;
          const baseY = tipY - size;
          ctx.beginPath();
          ctx.moveTo(cx, tipY);
          ctx.lineTo(cx - size, baseY);
          ctx.lineTo(cx + size, baseY);
          ctx.closePath();
          ctx.fill();
          ctx.font = font;
          ctx.textAlign = "center";
          ctx.textBaseline = "bottom";
          ctx.fillText(marker.label, cx, baseY - labelGap);
        }
      }
    });
  }
}

class CzscMarkerPaneView implements ISeriesPrimitivePaneView {
  constructor(private readonly primitive: CzscMarkerPrimitive) {}

  zOrder(): SeriesPrimitivePaneViewZOrder {
    // 绘制在 K 线之上，保证买卖点可见。
    return "top";
  }

  renderer(): ISeriesPrimitivePaneRenderer | null {
    const chart = this.primitive.getChart();
    const series = this.primitive.getSeries();
    if (!chart || !series) {
      return null;
    }
    return new CzscMarkerRenderer(this.primitive.getMarkers(), chart, series);
  }
}

export class CzscMarkerPrimitive implements ISeriesPrimitive {
  private chart?: IChartApi;
  private series?: PriceSeries;
  private requestUpdate?: () => void;
  private markers: readonly CzscMarkerPrimitiveData[] = [];
  private readonly paneView = new CzscMarkerPaneView(this);

  attached(params: SeriesAttachedParameter<Time, SeriesType>): void {
    this.chart = params.chart;
    this.series = params.series as PriceSeries;
    this.requestUpdate = params.requestUpdate;
  }

  detached(): void {
    this.chart = undefined;
    this.series = undefined;
    this.requestUpdate = undefined;
  }

  setMarkers(markers: readonly CzscMarkerPrimitiveData[]): void {
    this.markers = markers;
    this.requestUpdate?.();
  }

  updateAllViews(): void {
    // 渲染器在 draw 时即时读取最新 markers，无需缓存视图。
  }

  paneViews(): readonly ISeriesPrimitivePaneView[] {
    return [this.paneView];
  }

  getMarkers(): readonly CzscMarkerPrimitiveData[] {
    return this.markers;
  }

  getChart(): IChartApi | undefined {
    return this.chart;
  }

  getSeries(): PriceSeries | undefined {
    return this.series;
  }
}
