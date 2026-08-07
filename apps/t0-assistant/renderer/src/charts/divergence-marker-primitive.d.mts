import type {
  AutoscaleInfo,
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  Logical,
  PrimitivePaneViewZOrder,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from "lightweight-charts";

export interface DivergenceMarkerPrimitiveData {
  time: Time;
  price: number;
  side: "buy" | "sell";
  label: "Bull Div" | "Bear Div";
  divergenceType: "bullish" | "bearish";
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

type PriceSeries = ISeriesApi<"Candlestick"> | ISeriesApi<"Line">;

declare class DivergenceMarkerRenderer implements IPrimitivePaneRenderer {
  constructor(
    markers: readonly DivergenceMarkerPrimitiveData[],
    chart: IChartApi,
    series: PriceSeries,
    barByTime: ReadonlyMap<number, { time: Time; high: number; low: number }>,
  );
  draw(target: CanvasRenderingTarget): void;
}

declare class DivergenceMarkerPaneView implements IPrimitivePaneView {
  constructor(primitive: DivergenceMarkerPrimitive);
  zOrder(): PrimitivePaneViewZOrder;
  renderer(): IPrimitivePaneRenderer | null;
}

export declare class DivergenceMarkerPrimitive implements ISeriesPrimitive {
  constructor();

  attached(params: SeriesAttachedParameter<Time, SeriesType>): void;
  detached(): void;
  setMarkers(markers: readonly DivergenceMarkerPrimitiveData[]): void;
  updateAllViews(): void;
  paneViews(): readonly IPrimitivePaneView[];
  autoscaleInfo(
    startTimePoint: Logical,
    endTimePoint: Logical,
  ): AutoscaleInfo | null;
  resolveBarByTime(): ReadonlyMap<
    number,
    { time: Time; high: number; low: number }
  >;
  getMarkers(): readonly DivergenceMarkerPrimitiveData[];
  getChart(): IChartApi | undefined;
  getSeries(): PriceSeries | undefined;
}
