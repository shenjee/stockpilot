/**
 * 笔中枢（pivot zone）图层原语。
 *
 * Lightweight Charts 没有原生矩形/box 基元；用 series primitive 在价格
 * 面板上绘制半透明填充矩形表达中枢区域，避免“两条普通指标线”造成的歧义。
 * active 与 inactive 中枢使用不同填充/边框样式以可辨识。
 *
 * 起点位于视口左侧、但有效部分进入视口的中枢仍能正确显示：timeToCoordinate
 * 对数据内但离屏的时间返回越界坐标，画布自然裁剪；对数据外时间返回 null 时
 * 夹紧到边缘。
 *
 * 本文件是 production 实现，由 renderer/src/charts/SynchronizedChartGroup.ts
 * 和 tests/chart-primitives-lc.test.mjs 共同消费（Issue #134 回归保护）。
 * 不要在测试中复制本实现。
 */
const ACTIVE_FILL = "rgba(245, 158, 11, 0.18)";
const ACTIVE_BORDER = "rgba(245, 158, 11, 0.75)";
const INACTIVE_FILL = "rgba(148, 163, 184, 0.10)";
const INACTIVE_BORDER = "rgba(148, 163, 184, 0.55)";

class PivotZoneRenderer {
  constructor(zones, chart, series) {
    this.zones = zones;
    this.chart = chart;
    this.series = series;
  }

  draw(target) {
    if (this.zones.length === 0) {
      return;
    }
    const timeScale = this.chart.timeScale();
    const plotWidth = timeScale.width();
    if (plotWidth <= 0) {
      return;
    }

    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const hRatio = scope.horizontalPixelRatio;
      const vRatio = scope.verticalPixelRatio;

      for (const zone of this.zones) {
        const startX = timeScale.timeToCoordinate(zone.start);
        const endX = timeScale.timeToCoordinate(zone.end);
        const highY = this.series.priceToCoordinate(zone.high);
        const lowY = this.series.priceToCoordinate(zone.low);
        if (highY === null || lowY === null) {
          continue;
        }
        // 数据内但离屏的时间会返回越界坐标（由画布裁剪）；数据外时间返回 null
        // 时夹紧到对应边缘，保证“起点在视口左侧”的中枢仍能显示可见部分。
        const left = startX === null ? 0 : startX;
        const right = endX === null ? plotWidth : endX;
        if (right <= 0 || left >= plotWidth) {
          continue;
        }
        const clampedLeft = Math.max(0, left);
        const clampedRight = Math.min(plotWidth, right);
        if (clampedRight <= clampedLeft) {
          continue;
        }

        const top = Math.min(highY, lowY) * vRatio;
        const height = Math.abs(highY - lowY) * vRatio;
        const x = clampedLeft * hRatio;
        const width = (clampedRight - clampedLeft) * hRatio;

        ctx.fillStyle = zone.active ? ACTIVE_FILL : INACTIVE_FILL;
        ctx.fillRect(x, top, width, height);

        ctx.lineWidth = Math.max(1, hRatio);
        ctx.strokeStyle = zone.active ? ACTIVE_BORDER : INACTIVE_BORDER;
        ctx.setLineDash(zone.active ? [] : [4 * hRatio, 3 * hRatio]);
        ctx.strokeRect(x, top, width, height);
        ctx.setLineDash([]);
      }
    });
  }
}

class PivotZonePaneView {
  constructor(primitive) {
    this.primitive = primitive;
  }

  // 绘制在 K 线之下、网格之上，保证 K 线可见。
  zOrder() {
    return "bottom";
  }

  renderer() {
    const chart = this.primitive.getChart();
    const series = this.primitive.getSeries();
    if (!chart || !series) {
      return null;
    }
    return new PivotZoneRenderer(this.primitive.getZones(), chart, series);
  }
}

export class PivotZonePrimitive {
  constructor() {
    this.chart = undefined;
    this.series = undefined;
    this.requestUpdate = undefined;
    this.zones = [];
    this.paneView = new PivotZonePaneView(this);
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

  setZones(zones) {
    this.zones = zones;
    this.requestUpdate?.();
  }

  // 渲染器在 draw 时即时读取最新 zones，无需缓存视图。
  updateAllViews() {}

  paneViews() {
    return [this.paneView];
  }

  getZones() {
    return this.zones;
  }

  getChart() {
    return this.chart;
  }

  getSeries() {
    return this.series;
  }
}
