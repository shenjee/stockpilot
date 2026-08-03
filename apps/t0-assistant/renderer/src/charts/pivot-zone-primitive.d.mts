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

export interface PivotZonePrimitiveData {
  start: Time;
  end: Time;
  high: number;
  low: number;
  active: boolean;
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

export declare class PivotZoneRenderer implements IPrimitivePaneRenderer {
  constructor(
    zones: readonly PivotZonePrimitiveData[],
    chart: IChartApi,
    series: PriceSeries,
  );
  draw(target: CanvasRenderingTarget): void;
}

export declare class PivotZonePaneView implements IPrimitivePaneView {
  constructor(primitive: PivotZonePrimitive);
  zOrder(): PrimitivePaneViewZOrder;
  renderer(): IPrimitivePaneRenderer | null;
}

export declare class PivotZonePrimitive implements ISeriesPrimitive {
  constructor();

  attached(params: SeriesAttachedParameter<Time, SeriesType>): void;
  detached(): void;
  setZones(zones: readonly PivotZonePrimitiveData[]): void;
  updateAllViews(): void;
  paneViews(): readonly IPrimitivePaneView[];
  getZones(): readonly PivotZonePrimitiveData[];
  getChart(): IChartApi | undefined;
  getSeries(): PriceSeries | undefined;
}
