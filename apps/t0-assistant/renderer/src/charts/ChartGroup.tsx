import { useEffect, useRef } from "react";
import {
  ChartGroupKind,
  type ChartGroupModel,
} from "./chart-model.mjs";
import { SynchronizedChartGroup } from "./SynchronizedChartGroup";

interface ChartGroupProps {
  model: ChartGroupModel;
  priceHeader: React.ReactNode;
  onViewportChange?: (range: { from: number; to: number } | null) => void;
}

export function ChartGroup({
  model,
  priceHeader,
  onViewportChange,
}: ChartGroupProps) {
  const priceRef = useRef<HTMLDivElement>(null);
  const volumeRef = useRef<HTMLDivElement>(null);
  const macdRef = useRef<HTMLDivElement>(null);
  const controllerRef = useRef<SynchronizedChartGroup | null>(null);
  // 保持回调最新，避免 controller 持有过期闭包。
  const onViewportChangeRef = useRef(onViewportChange);
  onViewportChangeRef.current = onViewportChange;

  useEffect(() => {
    const price = priceRef.current;
    const volume = volumeRef.current;
    const macd = macdRef.current;
    if (!price || !volume || !macd) {
      return;
    }
    const controller = new SynchronizedChartGroup({
      containers: { price, volume, macd },
      kind: model.kind,
      onViewportChange: (range) => onViewportChangeRef.current?.(range),
    });
    controllerRef.current = controller;
    return () => {
      controller.destroy();
      controllerRef.current = null;
    };
    // A group instance is scoped to one chart kind. Data updates use setModel.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model.kind]);

  useEffect(() => {
    controllerRef.current?.setModel(model);
  }, [model]);

  const isIntraday = model.kind === ChartGroupKind.ONE_MINUTE;

  return (
    <>
      <section className="chart-panel price-panel">
        {priceHeader}
        <div
          ref={priceRef}
          className="chart-canvas"
          aria-label={
            isIntraday ? "1 分钟价格与 VWAP" : "5 分钟价格图"
          }
        />
        {isIntraday && (
          <div className="chart-legend" aria-label="分时图例">
            <span className="legend-price">价格</span>
            <span className="legend-vwap">分时均价线（VWAP）</span>
          </div>
        )}
      </section>
      <section className="chart-panel indicator-panel">
        <h2>{isIntraday ? "1 分钟 VOL" : "VOL"}</h2>
        <div
          ref={volumeRef}
          className="chart-canvas"
          aria-label={isIntraday ? "1 分钟成交量" : "5 分钟成交量"}
        />
      </section>
      <section className="chart-panel indicator-panel">
        <h2>{isIntraday ? "1 分钟 MACD" : "MACD"}</h2>
        <div
          ref={macdRef}
          className="chart-canvas"
          aria-label={isIntraday ? "1 分钟 MACD" : "5 分钟 MACD"}
        />
      </section>
    </>
  );
}
