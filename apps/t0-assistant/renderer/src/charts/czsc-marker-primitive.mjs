/**
 * CZSC 候选买卖点原语。
 *
 * Lightweight Charts 内置 markers 只能按 belowBar/aboveBar（或价格位）定位时，
 * 仍不足以表达候选点业务语义的完整自定义绘制；同一根 K 上不同价格的同侧信号
 * 也需要独立呈现。这里用 series primitive 在价格面板按 (time, price) 精确绘制
 * "↑"/"↓" 箭头 + 标签，视觉对齐 chan-viewer：买点红色在 K 线下方（"↑" 指向 K 线，
 * 标签在箭头之下），卖点绿色在 K 线上方（标签在上，"↓" 指向 K 线）。
 * 锚点取所在 K 线的极值（买点 low / 卖点 high）而非候选点原始价格——
 * 原始价格常落在实体内部，会导致箭头与 K 线重叠；找不到对应 K 线时
 * 回退到候选点价格。同一根 K 线、同一方向的多个信号（同侧不同价）共享
 * 同一锚点，因此按 (time, side) 分组向外纵向堆叠：价格更靠近 K 线的
 * 信号排在最内层，整组保持在 K 线之外。堆叠深度通过 autoscaleInfo 的
 * AutoScaleMargins 反馈给价格轴，避免信号落在视口最高/最低 K 线时
 * 外层标签被 Canvas 裁掉。
 *
 * 数据内但离屏的时间/价格会返回越界坐标（画布裁剪）；数据外返回 null 时跳过该标记。
 *
 * 本文件是 production 实现，由 renderer/src/charts/SynchronizedChartGroup.ts
 * 和 tests/chart-primitives-lc.test.mjs 共同消费（Issue #134 回归保护）。
 * 不要在测试中复制本实现。
 */
const BUY_COLOR = "#ef4444";
const SELL_COLOR = "#22c55e";
const BUY_ARROW = "↑";
const SELL_ARROW = "↓";
const ARROW_FONT = 13;
const ARROW_GAP = 2;
const LABEL_FONT = 10;
const LABEL_GAP = 2;
const STACK_GAP = 4;

/** 一组同侧堆叠标记占用的媒体像素高度（与 draw 布局一致）。 */
function stackMarginPx(depth) {
  if (depth <= 0) {
    return 0;
  }
  return (
    ARROW_GAP +
    depth * (ARROW_FONT + LABEL_GAP + LABEL_FONT) +
    (depth - 1) * STACK_GAP
  );
}

/** 按 (time, side) 统计最大买/卖堆叠深度。 */
function maxStackDepths(markers) {
  const counts = new Map();
  for (const marker of markers) {
    const key = `${Number(marker.time)}:${marker.side}`;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  let buy = 0;
  let sell = 0;
  for (const [key, count] of counts) {
    if (key.endsWith(":buy")) {
      buy = Math.max(buy, count);
    } else {
      sell = Math.max(sell, count);
    }
  }
  return { buy, sell };
}

class CzscMarkerRenderer {
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
    // 同一 timestamp、同一 side 的标记共享 high/low 锚点，先分组再纵向堆叠，
    // 避免同侧不同价信号画在同一位置互相覆盖。组内排序让价格更靠近 K 线的
    // 信号排在最内层（买：价高者靠内；卖：价低者靠内）。
    // group 直接保存 time / barTime / side，避免从 key 字符串反解析。
    const groups = new Map();
    for (const marker of this.markers) {
      const key = `${Number(marker.time)}:${marker.side}`;
      let group = groups.get(key);
      if (!group) {
        group = {
          time: marker.time,
          barTime: Number(marker.time),
          side: marker.side,
          items: [],
        };
        groups.set(key, group);
      }
      group.items.push(marker);
    }
    for (const group of groups.values()) {
      const sign = group.side === "buy" ? -1 : 1;
      group.items.sort((a, b) => sign * (a.price - b.price));
    }

    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const hRatio = scope.horizontalPixelRatio;
      const vRatio = scope.verticalPixelRatio;
      const arrowFont = `bold ${ARROW_FONT * vRatio}px sans-serif`;
      const arrowAdvance = ARROW_FONT * vRatio;
      const gap = ARROW_GAP * vRatio;
      const labelGap = LABEL_GAP * vRatio;
      const labelFont = `${LABEL_FONT * vRatio}px sans-serif`;
      const labelAdvance = LABEL_FONT * vRatio;
      const stackGap = STACK_GAP * vRatio;
      const slotHeight = arrowAdvance + labelGap + labelAdvance + stackGap;

      ctx.textAlign = "center";
      for (const group of groups.values()) {
        const { side, items, time, barTime } = group;
        const bar = barByTime.get(barTime);
        const anchorPrice = bar
          ? side === "buy"
            ? bar.low
            : bar.high
          : items[0].price;
        const x = timeScale.timeToCoordinate(time);
        const y = this.series.priceToCoordinate(anchorPrice);
        if (x === null || y === null) {
          continue;
        }
        const cx = x * hRatio;
        const cy = y * vRatio;
        ctx.fillStyle = side === "buy" ? BUY_COLOR : SELL_COLOR;

        items.forEach((marker, slot) => {
          if (side === "buy") {
            // K 线下方："↑" 顶端贴近 K 线，标签在箭头之下，同侧信号向下堆叠。
            const arrowTop = cy + gap + slot * slotHeight;
            ctx.font = arrowFont;
            ctx.textBaseline = "top";
            ctx.fillText(BUY_ARROW, cx, arrowTop);
            ctx.font = labelFont;
            ctx.fillText(marker.label, cx, arrowTop + arrowAdvance + labelGap);
          } else {
            // K 线上方：标签在上，"↓" 底端贴近 K 线，同侧信号向上堆叠。
            const arrowBottom = cy - gap - slot * slotHeight;
            ctx.font = labelFont;
            ctx.textBaseline = "bottom";
            ctx.fillText(marker.label, cx, arrowBottom - arrowAdvance - labelGap);
            ctx.font = arrowFont;
            ctx.fillText(SELL_ARROW, cx, arrowBottom);
          }
        });
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
    return new CzscMarkerRenderer(
      this.primitive.getMarkers(),
      chart,
      series,
      this.primitive.resolveBarByTime(),
    );
  }
}

export class CzscMarkerPrimitive {
  constructor() {
    this.chart = undefined;
    this.series = undefined;
    this.requestUpdate = undefined;
    this.markers = [];
    this.paneView = new CzscMarkerPaneView(this);
    // draw 在 pan/zoom 每帧触发，而 K 线数据只在快照/行情更新时变化，
    // 因此 barByTime 按内容签名缓存。签名编码每根 bar 的 time/high/low，
    // 避免 high/low 对冲或跨 bar 变化互相抵消时误命中旧缓存。
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
    const depths = maxStackDepths(markers);
    const above = stackMarginPx(depths.sell);
    const below = stackMarginPx(depths.buy);
    this.autoScaleMargins =
      above > 0 || below > 0 ? { above, below } : null;
    this.requestUpdate?.();
  }

  // 渲染器在 draw 时即时读取最新 markers，无需缓存视图。
  updateAllViews() {}

  paneViews() {
    return [this.paneView];
  }

  /**
   * 按最大同侧堆叠深度向价格轴申请像素边距，确保视口最高/最低 K 线上的
   * 外层标签不被 Canvas 裁掉。margins 在 setMarkers 时已算好；此处只读缓存。
   */
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
