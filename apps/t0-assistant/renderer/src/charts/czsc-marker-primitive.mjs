/**
 * CZSC 候选买卖点原语。
 *
 * Lightweight Charts 内置 markers 只能按 belowBar/aboveBar（或价格位）定位时，
 * 仍不足以表达候选点业务语义的完整自定义绘制；同一根 K 上不同价格的同侧信号
 * 也需要独立呈现。这里用 series primitive 在价格面板按 (time, price) 精确绘制
 * 箭头 + 标签：买点在价格下方指向上、卖点在价格上方指向下，与
 * packages/chantheory/plotting.py 的 y=base_point.price 锚点一致。
 *
 * 数据内但离屏的时间/价格会返回越界坐标（画布裁剪）；数据外返回 null 时跳过该标记。
 *
 * 本文件是 production 实现，由 renderer/src/charts/SynchronizedChartGroup.ts
 * 和 tests/chart-primitives-lc.test.mjs 共同消费（Issue #134 回归保护）。
 * 不要在测试中复制本实现。
 */
const BUY_COLOR = "#22c55e";
const SELL_COLOR = "#ef4444";
const ARROW_SIZE = 7;
const ARROW_GAP = 2;
const LABEL_FONT = 10;
const LABEL_GAP = 2;

class CzscMarkerRenderer {
  constructor(markers, chart, series) {
    this.markers = markers;
    this.chart = chart;
    this.series = series;
  }

  draw(target) {
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
        const cx = x * hRatio;
        const cy = y * vRatio;
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

class CzscMarkerPaneView {
  constructor(primitive) {
    this.primitive = primitive;
  }

  // 绘制在 K 线之上，保证买卖点可见。
  zOrder() {
    return "top";
  }

  renderer() {
    const chart = this.primitive.getChart();
    const series = this.primitive.getSeries();
    if (!chart || !series) {
      return null;
    }
    return new CzscMarkerRenderer(this.primitive.getMarkers(), chart, series);
  }
}

export class CzscMarkerPrimitive {
  constructor() {
    this.chart = undefined;
    this.series = undefined;
    this.requestUpdate = undefined;
    this.markers = [];
    this.paneView = new CzscMarkerPaneView(this);
  }

  attached(params) {
    this.chart = params.chart;
    this.series = params.series;
    this.requestUpdate = params.requestUpdate;
  }

  detached() {
    this.chart = undefined;
    this.series = undefined;
    this.requestUpdate = undefined;
  }

  setMarkers(markers) {
    this.markers = markers;
    this.requestUpdate?.();
  }

  // 渲染器在 draw 时即时读取最新 markers，无需缓存视图。
  updateAllViews() {}

  paneViews() {
    return [this.paneView];
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
