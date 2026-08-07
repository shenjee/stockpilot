/**
 * 背驰（Bull Div / Bear Div）文本原语。
 *
 * 视觉对齐 chan-viewer / chantheory plotting：
 * - Bull Div：绿色文字，锚定所在 K 线 low，画在 K 线下方
 * - Bear Div：红色文字，锚定所在 K 线 high，画在 K 线上方
 * 无箭头；字体略大于 CZSC 买卖点标签。
 *
 * 外层偏移 CZSC_CLEARANCE 为单层买卖点堆叠预留空间，避免与同侧 1B/1S
 * 完全重叠。找不到对应 K 线时回退到契约 meta.price。
 *
 * 本文件是 production 实现，由 SynchronizedChartGroup 消费。
 */
const BULL_COLOR = "#059669";
const BEAR_COLOR = "#B91C1C";
const LABEL_FONT = 11;
const LABEL_GAP = 2;
/** 为同侧一层 CZSC 买卖点（箭头+标签）预留的像素高度。 */
const CZSC_CLEARANCE = 28;

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
      const labelFont = `bold ${LABEL_FONT * vRatio}px sans-serif`;
      const gap = (CZSC_CLEARANCE + LABEL_GAP) * vRatio;

      ctx.textAlign = "center";
      ctx.font = labelFont;

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
        ctx.fillStyle =
          marker.divergenceType === "bullish" ? BULL_COLOR : BEAR_COLOR;

        if (marker.side === "buy") {
          ctx.textBaseline = "top";
          ctx.fillText(marker.label, cx, cy + gap);
        } else {
          ctx.textBaseline = "bottom";
          ctx.fillText(marker.label, cx, cy - gap);
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
    const margin = CZSC_CLEARANCE + LABEL_GAP + LABEL_FONT;
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
