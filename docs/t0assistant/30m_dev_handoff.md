# 30 分钟 K 线功能 — 开发交接总结

> 本文是设计讨论结束后的开发交接索引。唯一权威基线是
> `docs/t0assistant/30m_chart_feature_design.md`（commit `886cce9`，已冻结全部决策）。
> 本文只做导航与摘要，与设计稿冲突时以设计稿为准。

## 一、入口信息

| 项 | 值 |
|---|---|
| Issue | [shenjee/stockpilot #168](https://github.com/shenjee/stockpilot/issues/168) |
| 分支 | `feature/issue-168-t0assistant-30m-chart`（已推送，与 `origin` 同步） |
| 设计稿 | `docs/t0assistant/30m_chart_feature_design.md` |
| Python 环境 | `source ~/.venvs/czsc/bin/activate` |
| 运行 App | `cd apps/t0-assistant && npm install && npm start` |

**开工前必读**：设计稿全文，它是唯一权威基线。

## 二、已冻结的核心决策

### 数据

- 历史/已完成 30m K：直接用 Provider `30m` 接口 + 通用 K 线 Repository，**绝不从 5m 聚合**。
- 预热 500 根正式 30m K；5m/30m 共用新常量 `DEFAULT_CHART_PREHEAT_COUNT = 500`。
- 当前未完成 30m K：用**当日已完成 1m K** 实时形成，`closed=false`，仅展示、不进指标/缠论。
- 严格空值传播：任一 1m 的 volume/amount 未知 → 30m 对应字段 `null`。
- 30m 结束时间桶：`10:00/10:30/11:00/11:30/13:30/14:00/14:30/15:00`，午休不合并。
- 首根临时 30m = 1m 时间戳 09:31–10:00，结果结束时间 10:00。

### 契约（v2 增量，不发 v3）

新增 `market.bars_30m`、`indicators.thirty_minute`、`chan_analysis_30m`；
现有 `chan_analysis` 保持 5m 含义。

### Warning codes

- `thirty_minute_official_delayed`（**仅 Live**）
- `thirty_minute_market_data_unavailable`
- `thirty_minute_indicators_unavailable`
- `thirty_minute_chan_analysis_unavailable`

### 合并语义

正式 K 只替换**相同结束时间**的临时 K；**不得删除下一根正在形成的临时 K**。
区别于现有 5m 的 "late official drops unclosed" 规则，需独立实现 + 独立 fixture。

### Live 刷新

新增独立 `OFFICIAL_THIRTY_MINUTE` 分支，按结束时间推进 watermark；
结束后 5s 首次、15s 重试、2 分钟未达 → 发延迟 warning 并降为 60s；
分支间故障隔离；单独写测试。

### UI

- **直接删除** `layout.chart_split`、`"64_36"`/`"50_50"`、`MAIN_PRIORITY`/`EQUAL` 三态布局，无迁移代码。
- 布局只剩"显示/隐藏副图"；副图显示时固定 50/50；副图内部 `[分时][30m]` 切换。
- 新增 `chartViews.thirtyMinute` 独立视口槽位；视口参数复用 5m（跟随≥72 / 手工≥48 / 上限 360）。
- 图层开关与 5m 共用一组偏好；成交标记沿用 `executed_at`，无新契约字段。
- 分时/30m 选择**不跨重启保存**；30m 不可用时保留选择 + 空态 + warning，不自动切回分时。

### 共享包改动（已批准）

`MarketContextService._SUPPORTED_BAR_MINUTES` 从 `{1,5}` → `{1,5,30}`。

## 三、实施顺序（设计稿 §15）

1. 冻结 30m 时间/完成状态/缺失值/替换语义
2. 设计评审公共快照契约（三个新字段 + warning）
3. 接入 Provider `30m` 获取、存储、覆盖证据、历史读取
4. 实现未完成 30m K + 正式数据替换合并
5. 接入 30m 指标 + `timeframe="30m"` 缠论
6. 打通 Live / Replay / Historical 完整快照
7. Renderer 副图切换、独立视口、Tooltip、成交点
8. 契约/管线/Renderer/Replay/Historical/端到端测试
9. 更新 PRD、架构、模块设计、UI 布局、回放文档

## 四、关键落点文件

| 层 | 文件 |
|---|---|
| 契约 | `apps/t0-assistant/contracts/logical-v2.schema.json`、`app-v2`、`replay-v2`、`fixtures/` |
| 管线 | `packages/t0assistant/runtime/pipeline.py`、`five_minute.py`（镜像 `DynamicFiveMinuteAggregator`）、`workbench_projection.py` |
| Live 刷新 | `packages/t0assistant/runtime/live_refresh.py` |
| 预热常量 | `replay_data.py`、`live_market_view.py`（抽共享常量） |
| 市场数据 | `packages/marketdata/services/market_context_service.py`、`market_data.py`、`repositories/kline_store.py` |
| 缠论 | `packages/chantheory/`（已支持 `30m→F30`） |
| 指标 | `packages/indicators/core.py`（按 bar 序列通用，直接复用） |
| Renderer | `renderer/src/workbench-layout.mjs`、`App.tsx`、`charts/chart-model.mjs`、`charts/chart-viewport.mjs` |

## 五、风险与注意

- **最复杂处**：`OFFICIAL_THIRTY_MINUTE` 边界触发调度（非固定间隔轮询），单独评审 + 测试。
- **最易错处**：30m 合并语义不能照抄 5m 的"丢弃未闭合根"规则。
- **计算层低风险**：chantheory 与 indicators 已支持 30m，直接复用。
- 测试就近放在各包 `tests/`；改动跨包时扩大覆盖。
