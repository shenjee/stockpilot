import type {
  Coordinate,
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  PrimitivePaneViewZOrder,
  SeriesAttachedParameter,
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

type PriceSeries = ISeriesApi<"Candlestick"> | ISeriesApi<"Line">;

declare class CzscMarkerRenderer implements IPrimitivePaneRenderer {
  constructor(
    markers: readonly CzscMarkerPrimitiveData[],
    chart: IChartApi,
    series: PriceSeries,
  );
  draw(target: CanvasRenderingTarget): void;
}

declare class CzscMarkerPaneView implements IPrimitivePaneView {
  constructor(primitive: CzscMarkerPrimitive);
  zOrder(): PrimitivePaneViewZOrder;
  renderer(): IPrimitivePaneRenderer | null;
}

export declare class CzscMarkerPrimitive implements ISeriesPrimitive {
  constructor();

  attached(params: SeriesAttachedParameter<Time, SeriesType>): void;
  detached(): void;
  setMarkers(markers: readonly CzscMarkerPrimitiveData[]): void;
  updateAllViews(): void;
  paneViews(): readonly IPrimitivePaneView[];
  getMarkers(): readonly CzscMarkerPrimitiveData[];
  getChart(): IChartApi | undefined;
  getSeries(): PriceSeries | undefined;
}
