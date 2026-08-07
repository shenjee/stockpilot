/**
 * 背驰（Bull Div / Bear Div）原语。
 *
 * 布局与 CZSC 买卖点一致：箭头 + 标签，锚定所在 K 线极值。
 * - Bull Div：红色，K 线下方，"↑" 指向 K 线，标签在箭头之下
 * - Bear Div：绿色，K 线上方，标签在上，"↓" 指向 K 线
 * 颜色与同侧买卖点一致（买/多红、卖/空绿）。
 *
 * 外层偏移 CZSC_CLEARANCE 为单层买卖点堆叠预留空间，避免与同侧 1B/1S
 * 完全重叠。找不到对应 K 线时回退到契约 meta.price。
 *
 * 本文件是 production 实现，由 SynchronizedChartGroup 消费。
 */
const BULL_COLOR = "#ef4444";
const BEAR_COLOR = "#22c55e";
const BUY_ARROW = "↑";
const SELL_ARROW = "↓";
const ARROW_FONT = 13;
const ARROW_GAP = 2;
const LABEL_FONT = 10;
const LABEL_GAP = 2;
/** 为同侧一层 CZSC 买卖点（箭头+标签）预留的像素高度。 */
const CZSC_CLEARANCE = 28;

function markerMarginPx() {
  return (
    CZSC_CLEARANCE +
    ARROW_GAP +
    ARROW_FONT +
    LABEL_GAP +
    LABEL_FONT
  );
}

class DivergenceMarkerRenderer {
  constructor(markers, chart, series, barByTime) {
    this.markers = markers;
    this.chart = chart;
    this.series = series;
    this.barByTime = barByTime;
  }

  draw(target) {
    if (this.markers.length === 0) {
      return;
    }
    const timeScale = this.chart.timeScale();
    const barByTime = this.barByTime;

    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const hRatio = scope.horizontalPixelRatio;
      const vRatio = scope.verticalPixelRatio;
      const arrowFont = `bold ${ARROW_FONT * vRatio}px sans-serif`;
      const arrowAdvance = ARROW_FONT * vRatio;
      const clearance = CZSC_CLEARANCE * vRatio;
      const gap = ARROW_GAP * vRatio;
      const labelGap = LABEL_GAP * vRatio;
      const labelFont = `${LABEL_FONT * vRatio}px sans-serif`;

      ctx.textAlign = "center";

      for (const marker of this.markers) {
        const barTime = Number(marker.time);
        const bar = barByTime.get(barTime);
        const anchorPrice = bar
          ? marker.side === "buy"
            ? bar.low
            : bar.high
          : marker.price;
        const x = timeScale.timeToCoordinate(marker.time);
        const y = this.series.priceToCoordinate(anchorPrice);
        if (x === null || y === null) {
          continue;
        }
        const cx = x * hRatio;
        const cy = y * vRatio;
        const isBull = marker.divergenceType === "bullish";
        ctx.fillStyle = isBull ? BULL_COLOR : BEAR_COLOR;

        if (marker.side === "buy") {
          // K 线下方：先越过一层买卖点，再画 "↑" + Bull Div。
          const arrowTop = cy + clearance + gap;
          ctx.font = arrowFont;
          ctx.textBaseline = "top";
          ctx.fillText(BUY_ARROW, cx, arrowTop);
          ctx.font = labelFont;
          ctx.fillText(marker.label, cx, arrowTop + arrowAdvance + labelGap);
        } else {
          // K 线上方：先越过一层买卖点，再画 Bear Div + "↓"。
          const arrowBottom = cy - clearance - gap;
          ctx.font = labelFont;
          ctx.textBaseline = "bottom";
          ctx.fillText(
            marker.label,
            cx,
            arrowBottom - arrowAdvance - labelGap,
          );
          ctx.font = arrowFont;
          ctx.fillText(SELL_ARROW, cx, arrowBottom);
        }
      }
    });
  }
}

class DivergenceMarkerPaneView {
  constructor(primitive) {
    this.primitive = primitive;
  }

  zOrder() {
    return "top";
  }

  renderer() {
    const chart = this.primitive.getChart();
    const series = this.primitive.getSeries();
    if (!chart || !series) {
      return null;
    }
    return new DivergenceMarkerRenderer(
      this.primitive.getMarkers(),
      chart,
      series,
      this.primitive.resolveBarByTime(),
    );
  }
}

export class DivergenceMarkerPrimitive {
  constructor() {
    this.chart = undefined;
    this.series = undefined;
    this.requestUpdate = undefined;
    this.markers = [];
    this.paneView = new DivergenceMarkerPaneView(this);
    this.barsSignature = "";
    this.barByTime = new Map();
    this.autoScaleMargins = null;
  }

  attached(params) {
    this.chart = params.chart;
    this.series = params.series;
    this.requestUpdate = params.requestUpdate;
    this.barsSignature = "";
    this.barByTime = new Map();
  }

  detached() {
    this.chart = undefined;
    this.series = undefined;
    this.requestUpdate = undefined;
    this.barsSignature = "";
    this.barByTime = new Map();
  }

  setMarkers(markers) {
    this.markers = markers;
    let hasBuy = false;
    let hasSell = false;
    for (const marker of markers) {
      if (marker.side === "buy") {
        hasBuy = true;
      } else {
        hasSell = true;
      }
    }
    const margin = markerMarginPx();
    this.autoScaleMargins =
      hasBuy || hasSell
        ? {
            above: hasSell ? margin : 0,
            below: hasBuy ? margin : 0,
          }
        : null;
    this.requestUpdate?.();
  }

  updateAllViews() {}

  paneViews() {
    return [this.paneView];
  }

  autoscaleInfo(_startTimePoint, _endTimePoint) {
    if (!this.autoScaleMargins) {
      return null;
    }
    return { priceRange: null, margins: this.autoScaleMargins };
  }

  resolveBarByTime() {
    if (!this.series) {
      this.barsSignature = "";
      this.barByTime = new Map();
      return this.barByTime;
    }
    const data = this.series.data();
    let signature = String(data.length);
    for (const bar of data) {
      signature += `|${Number(bar.time)}:${bar.high ?? ""}:${bar.low ?? ""}`;
    }
    if (signature !== this.barsSignature) {
      this.barsSignature = signature;
      this.barByTime = new Map();
      for (const bar of data) {
        if (bar && typeof bar.high === "number" && typeof bar.low === "number") {
          this.barByTime.set(Number(bar.time), bar);
        }
      }
    }
    return this.barByTime;
  }

  getMarkers() {
    return this.markers;
  }

  getChart() {
    return this.chart;
  }

  getSeries() {
    return this.series;
  }
}
