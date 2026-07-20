# T+0 Chart Spike

验证 T+0 助手图表引擎能力的技术 Spike，对应 [ADR 0005](../../docs/adr/0005-t0-chart-engine-and-logical-time-axis.md) 和 StockPilot Issue #25。

---

## 架构目标

- ✅ 逻辑时间轴: 非交易时段（午休、隔夜、周末、停牌）不显示空槽
- ✅ 5分钟图表组: 价格图 + VOL + MACD，共享同一时间轴
- ✅ 跟随最新 / 手工浏览双状态
- ✅ 宽度驱动满轴（容器宽度 -> 显示 N 根 K 线）
- ✅ 布局切换保持视口状态（三栏工作台: 5m主图区 vs 分时区 vs 固定280px行情栏）
- ✅ 回放截断（不显示 T 之后的数据，含 CZSC overlay）
- ✅ BOLL 指标 + CZSC overlay（买卖点、笔、中枢）
- ✅ 增量更新不破坏手工浏览

---

## 技术选型

当前首选: **Lightweight Charts 4.2.0**

验证目标:
- 多图同步（十字光标、可见范围）
- 性能（500根 K 线流畅交互）
- 包体大小
- 自定义 overlay 能力（中枢、买卖点、笔）

---

## 项目结构

```
src/
├── fixtures/
│   └── ohlcv-fixture.ts          # 确定性 500 根跨交易日 5 分钟 K 线（仅工作日/交易时段）
├── models/
│   └── chart-group-state.ts      # 图表组状态模型（逻辑索引、视口、跟随状态）
├── charts/
│   ├── five-minute-chart-group.ts  # 5分钟图表组（价格/VOL/MACD，含 CZSC overlay）
│   └── time-sharing-chart-group.ts # 1分钟分时图组（验证布局用，简化实现）
├── workbench/
│   └── workbench-grid.ts          # 三栏工作台布局（5m区 vs 分时区 vs 280px行情栏）
├── main.ts                        # 演示入口
└── vite-env.d.ts

tests/
├── setup.ts                       # jsdom 环境补丁
├── chart-group-state.test.ts      # 状态模型单元测试
├── ohlcv-fixture.test.ts          # fixture 确定性与交易规则测试
└── five-minute-chart-group.test.ts # 图表集成测试
```

---

## 开发

```bash
cd spikes/0005-t0-chart-engine-and-logical-time-axis

# 安装依赖
npm install

# 启动开发服务器
npm run dev

# 运行全部测试
npm test

# 查看测试覆盖率
npm run test -- --coverage

# 生产构建
npm run build
```

---

## 已知限制（演示程序缺陷，不影响选型判断）

| 项 | 说明 |
|---|---|
| 重置按钮绑定旧实例 | 点击"重置数据"后图层开关仍绑定旧实例 |
| BOLL 按钮重置后失效 | 同上 |
| 截断显示 451 根 | `time <= T` 是合理的包含式语义 |
| 按钮颜色/交互细节 | 非验证目的，无需产品化 |

---

## 验证状态（严格分层）

### 自动验证（✅）

- ✅ 3 个文件，56 个测试全部通过
- ✅ 覆盖逻辑时间轴、状态模型、三图同步、回放截断、CZSC overlay、布局切换

### 人工验证（需在真实浏览器运行 `npm run dev`）

- ✅ 时区显示（不再出现 03:00/06:00）
- ✅ 增量更新不报错，时间顺序正确
- ✅ 三图双向范围与十字光标同步
- ✅ 手工拖动与恢复跟随
- ✅ 三栏工作台布局（5m区 vs 分时区 vs 280px行情栏）
- ✅ CZSC overlay 显示与截断
- ⏸️ 帧率/内存/CPU 未测
- ⏸️ 增量更新延迟未测
- ⏸️ 初始渲染时间未测

### 未验证（需后续完成）

- [ ] 帧率测量: >30fps
- [ ] 内存测量: <100MB
- [ ] CPU测量: 空闲<5%，拖动<50%
- [ ] 包体大小: <50KB（实测≈55.6KB）
- [ ] 创建 Draft PR 关联 Issue #25 和 ADR 0005

---

## 验收标准与 ADR 0005 状态建议

当前状态: **Issue #25 暂不标记 Done，ADR 0005 保持 Proposed / Continue investigation**

待完成项见验证报告。
