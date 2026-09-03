# 30 分钟 K 线功能 — 开发交接 Prompt（Step 6+）

> 本文件是 30m K 线功能（Issue #168）已完成步骤的交接文档，供新 thread 继续开发。
> 唯一权威基线：`docs/t0assistant/30m_chart_feature_design.md`（commit `886cce9`，已冻结全部决策）。
> 导航摘要：`docs/t0assistant/30m_dev_handoff.md`，与设计稿冲突时以设计稿为准。

---

## 一、环境与分支

| 项 | 值 |
|---|---|
| 仓库 | `/Users/jishen/projects/stockpilot` |
| Python 环境 | `source ~/.venvs/czsc/bin/activate` |
| 分支 | `feature/issue-168-t0assistant-30m-chart`（已推送，与 `origin` 同步） |
| 设计稿 | `docs/t0assistant/30m_chart_feature_design.md` |
| 交接摘要 | `docs/t0assistant/30m_dev_handoff.md` |
| 本交接 | `docs/t0assistant/30m_handoff_step6plus.md` |

---

## 二、已完成工作总结

### Step 1: 冻结 30m 语义 ✅
设计稿已冻结（commit `886cce9`）。

### Step 2: 契约设计 ✅
- 在 v2 上增量增加 `market.bars_30m`、`indicators.thirty_minute`、`chan_analysis_30m`，保留现有字段含义，不发布 v3。
- Schema 已更新：`apps/t0-assistant/contracts/` 和 `packages/t0assistant/contracts/` 中的 `logical-v2`、`app-v2`、`replay-v2`。

### Step 3: 基础设施 ✅
- `packages/marketdata/services/market_context_service.py`：`_SUPPORTED_BAR_MINUTES = {1, 5, 30}`
- `packages/marketdata/`：`T0_TIMEFRAMES` 包含 `"30m"`，Provider 支持 `MINUTE_KTYPES = {"1m":"m1","5m":"m5","30m":"m30","60m":"m60"}`，page size 800
- `packages/indicators/core.py`：`calculate_thirty_minute_indicators(bars)` — 与 `calculate_five_minute_indicators` 相同的 ma/boll/volume/macd 结构
- `packages/t0assistant/runtime/thirty_minute.py`：`DynamicThirtyMinuteAggregator` — 镜像 `DynamicFiveMinuteAggregator`，30m 边界为 `10:00/10:30/11:00/11:30/13:30/14:00/14:30/15:00`，午休不合并
- `packages/t0assistant/tests/test_thirty_minute.py`：8 个测试全部通过
- Provider 30m 获取已验证：7 个月范围返回 640 根（超过 500 根预热需求）

### Step 3b: 30m 预热加载 ✅
- `packages/t0assistant/runtime/live_market_view.py`：新增 `DEFAULT_CHART_PREHEAT_COUNT = 500`，`MINIMUM_PREHEAT_5M = DEFAULT_CHART_PREHEAT_COUNT`（别名）
- `packages/t0assistant/runtime/live_session.py`：`LiveSession.MINIMUM_PREHEAT_5M = DEFAULT_CHART_PREHEAT_COUNT`
- `packages/t0assistant/runtime/replay_data.py`：
  - `ReplayPreparationConfig` 新增 `preheat_30m_count: int = DEFAULT_CHART_PREHEAT_COUNT`
  - 新增 `_load_preheat_30m()` 方法（镜像 `_load_preheat_5m`，使用 `timeframe="30m"`，`limit=config.preheat_30m_count + 15 * 8`）
  - `prepare()` 方法中加载 preheat_30m 和 official_30m（`_load_target_day_bars(timeframe="30m")`）
- `packages/t0assistant/runtime/live_data.py`：
  - 新增 `_load_preheat_30m()` 和 `_load_preheat_30m_best_effort()` 方法
  - `prepare()` 方法中加载 preheat_30m（best-effort）和 official_30m
  - `load_refresh_bars()` 接受 `"30m"` timeframe
- `packages/t0assistant/runtime/computation_contract.py`：`PreparedReplayData` 新增 `preheat_30m_bars` 和 `official_30m_bars` 字段（有默认值 `()`，放在所有无默认值字段之后）
- `packages/t0assistant/runtime/replay_data.py`：`_InMemoryMarketInputPort` 新增 `preheat_30m_bars` 和 `official_30m_bars` 字段，`read()` 方法传递给 `PipelineMarketInput`

### Step 4: 管线集成 ✅
- `packages/t0assistant/runtime/pipeline.py`：
  - `PipelineMarketInput`：新增 `preheat_30m_bars`、`official_30m_bars` 字段（默认 `field(default_factory=tuple)`）
  - `PipelineResult`：新增 `bars_30m`、`closed_30m_prefix`、`indicators_30m`、`chan_analysis_30m` 字段（默认 `()`、`()`、`field(default_factory=dict)`、`field(default_factory=dict)`）
  - `to_dict()`：包含所有 30m 字段
  - `degraded()`：包含 30m 空默认值（`bars_30m=()`, `closed_30m_prefix=()`, `indicators_30m=_empty_30m_indicators()`, `chan_analysis_30m=_empty_chan_analysis_30m(symbol)`）
  - `_compute_unlocked()`：构建 `DynamicThirtyMinuteAggregator`，喂入 1m bars 和 official 30m bars，合并 preheat_30m 与 analysis_bars 和 display_bars，计算 `indicators_30m` 和 `chan_analysis_30m`
  - 新增 `_default_analyze_30m()`（使用 `analyze(timeframe="30m")`）、`_empty_chan_analysis_30m()`、`_empty_30m_indicators()`

### Step 5: 30m 指标 + 缠论 ✅
- `calculate_thirty_minute_indicators` 和 `_default_analyze_30m(timeframe="30m")` 已接入 pipeline

### Workbench 投影 ✅
- `packages/t0assistant/runtime/workbench_projection.py`：`build_workbench_projection()` payload 包含：
  - `market.bars_30m`: `list(pipeline_result.bars_30m)`
  - `indicators.thirty_minute`: `pipeline_result.indicators_30m`
  - `chan_analysis_30m`: `pipeline_result.chan_analysis_30m`（顶层 key，与 `chan_analysis` 同级）

### Replay 验证 ✅
- `packages/t0assistant/replay/validation.py`：
  - Root snapshot fields：新增 `"chan_analysis_30m"` 到必需集合
  - `_market()`：field set 从 `{"bars_1m", "bars_5m", "daily_bars", "quote"}` → `{"bars_1m", "bars_5m", "bars_30m", "daily_bars", "quote"}`，bars_30m 加入 bar 验证循环
  - `_indicators()`：field set 从 `{"five_minute", "one_minute"}` → `{"five_minute", "thirty_minute", "one_minute"}`，验证 thirty_minute block
  - 重构：提取 `_indicator_block(block, path)` helper（验证 ma/boll/volume/macd，five_minute 和 thirty_minute 共用）
  - 重构：提取 `_chan_analysis_at(value, path)` helper，`_chan_analysis()` 和 `_chan_analysis_30m()` 都委托给它

### Fixtures ✅
- `apps/t0-assistant/contracts/fixtures/workbench-flow-v1.json`：已添加 `bars_30m: []`、`thirty_minute` 指标 block、`chan_analysis_30m`（完整 schema-valid 空结构，`timeframe="30m"`）
- `apps/t0-assistant/contracts/fixtures/replay-speed-v1.json`：已添加 `bars_30m: []`、`thirty_minute` 指标 block、`chan_analysis_30m`（完整 schema-valid 空结构，`timeframe="30m"`）

### 测试修复 ✅
- `packages/t0assistant/tests/test_computation_contract.py`：`PreparedReplayDataShapeTests` 的 expected field set 新增 `preheat_30m_bars`、`official_30m_bars`
- `apps/t0-assistant/tests/test_contracts.py`：两处 `PipelineResult` 构造（`test_historical_command_response_validates` 和 `test_workbench_projection_output_validates_against_logical_schema`）都添加了 `bars_30m`、`indicators_30m`、`chan_analysis_30m`
- `packages/t0assistant/tests/test_workbench_projection.py`：新增 `_make_chan_analysis_30m()` helper、`_empty_30m_indicators()` helper，`_make_pipeline_result()` 接受并传递 30m 字段

### 测试状态
- `packages/t0assistant/tests/`：605 passed, 1 skipped（排除 `test_live_dynamic_five_minute.py`，该文件有 pre-existing failure：`SessionSpec.__init__() missing 'instrument'`，与 30m 工作无关）
- `apps/t0-assistant/tests/`：104 passed
- 总计：709 passed, 1 skipped, 82 subtests

---

## 三、未完成工作

### Step 6: Live/Replay/Historical 完整快照打通（下一步，最复杂）

#### 6.1 `live_refresh.py` — 新增 `OFFICIAL_THIRTY_MINUTE` 刷新分支

文件：`packages/t0assistant/runtime/live_refresh.py`

需要修改的位置：

1. **`LiveRefreshKind` 枚举**（~line 49）：
   ```python
   class LiveRefreshKind(str, Enum):
       QUOTE = "quote"
       ONE_MINUTE = "one_minute"
       OFFICIAL_FIVE_MINUTE = "official_five_minute"
       OFFICIAL_THIRTY_MINUTE = "official_thirty_minute"  # 新增
   ```

2. **`LiveRefreshIntervals` dataclass**（~line 57）：
   - 新增字段：`official_thirty_minute: timedelta = timedelta(seconds=15)` 和 `reduced_official_thirty_minute: timedelta = timedelta(seconds=60)`
   - `__post_init__` 的验证列表新增这两个字段
   - `for_kind()` 方法新增分支：
     ```python
     if polling_profile == "reduced":
         ...
         if kind is LiveRefreshKind.OFFICIAL_THIRTY_MINUTE:
             return self.reduced_official_thirty_minute
     ...
     if kind is LiveRefreshKind.OFFICIAL_THIRTY_MINUTE:
         return self.official_thirty_minute
     ```

3. **`LiveRefreshScheduler._KINDS`**（~line 227）：
   ```python
   _KINDS = (
       LiveRefreshKind.QUOTE,
       LiveRefreshKind.ONE_MINUTE,
       LiveRefreshKind.OFFICIAL_FIVE_MINUTE,
       LiveRefreshKind.OFFICIAL_THIRTY_MINUTE,  # 新增
   )
   ```

4. **`_validate_update` 方法**（~line 570）：
   - 新增 `OFFICIAL_THIRTY_MINUTE` 的 allowed event types（与 `OFFICIAL_FIVE_MINUTE` 相同）：
     ```python
     LiveRefreshKind.OFFICIAL_THIRTY_MINUTE: {
         "market_update",
         "indicators_updated",
         "chan_analysis_replaced",
         "live_market_view_updated",
     },
     ```

设计规则（§10）：
- 30m 结束时间到达 5 秒后首次请求正式 30m K
- 如果接口尚未返回，每 15 秒重试
- 2 分钟仍未取得 → 发布 `thirty_minute_official_delayed` warning（仅 Live），降为 60 秒
- 按结束时间推进 watermark，防止同一结束时间重复启动刷新
- 与现有刷新分支故障隔离
- 获取成功、切股、切日、Session 退休、任务取消时停止对应刷新任务

**注意**：当前 `LiveRefreshScheduler` 使用固定间隔轮询（`next_due_at = observed_at + interval`）。设计稿要求 30m 分支使用"按结束时间推进的 watermark"触发，而非固定间隔。这意味着 30m 分支的 `next_due_at` 需要根据下一个 30m 边界时间 + 5s 计算，而不是 `now + interval`。这是最复杂的部分，需要单独评审和测试。

#### 6.2 `live_runtime.py` — `BranchingLiveInput` 新增 30m 分支

文件：`packages/t0assistant/runtime/live_runtime.py`

需要修改的位置：

1. **`refresh()` 方法**（~line 285）：
   - 在 `if kind is LiveRefreshKind.QUOTE:` / `else:` 分支中新增 30m 处理：
     ```python
     if kind is LiveRefreshKind.QUOTE:
         rows = tuple(self._source.load_refresh_quotes(...))
     elif kind is LiveRefreshKind.OFFICIAL_THIRTY_MINUTE:
         rows = tuple(self._source.load_refresh_bars(
             spec, timeframe="30m", trade_date=trade_date,
         ))
     else:
         rows = tuple(self._source.load_refresh_bars(
             spec, timeframe=("1m" if kind is LiveRefreshKind.ONE_MINUTE else "5m"),
             trade_date=trade_date,
         ))
     ```
   - `data_time` 计算：`closed_only=True`（与 OFFICIAL_FIVE_MINUTE 相同）
   - `updated_input` 赋值：`replace(self._market_input, official_30m_bars=rows)`

2. **`_branch_updates()` 函数**（~line 1068）：
   - 新增 `OFFICIAL_THIRTY_MINUTE` 分支，返回：
     - `market_update` target=`"bars_30m"`（完整显示序列）
     - `indicators_updated`（包含 `thirty_minute`）
     - `chan_analysis_replaced`（payload = `snapshot["chan_analysis_30m"]`）
     - `live_market_view_updated`

3. **`_snapshot_branch_time()` 函数**（~line 1051）：
   - 新增 30m case：返回 `market["bars_30m"]` 的 latest closed bar timestamp

4. **`_initial_data_times()`**（~line 1164）：已遍历 `LiveRefreshKind`，自动包含新 kind

#### 6.3 `live_market_view.py` — 新增 30m as-of 字段

文件：`packages/t0assistant/runtime/live_market_view.py`

需要修改的位置：

1. **`build_live_market_view()` 函数**（~line 611）：
   - 新增参数：`bars_30m: Sequence` (from market)、`thirty_minute` indicators、`chan_analysis_30m`、`closed_30m_prefix`
   - payload 新增：
     - `"bars_30m_as_of"`: `_latest_closed_bar_timestamp(bars_30m_rows, closed_only=True)`
     - `"thirty_minute_indicators_as_of"`: `_latest_closed_bar_timestamp(closed_30m_rows, closed_only=True)`
     - `"czsc_30m_as_of"`: `_latest_closed_bar_timestamp(closed_30m_rows, closed_only=True)`

#### 6.4 `live_session.py` — `LiveSnapshotCandidate.build_projection()`

文件：`packages/t0assistant/runtime/live_session.py`

需要修改的位置：

1. **`build_projection()` 方法**（~line 130）：
   - `market` dict 新增 `"bars_30m": preview["bars_30m"]`
   - `indicators` dict 新增 `"thirty_minute": preview["indicators_30m"]`
   - 传递 `chan_analysis_30m=preview["chan_analysis_30m"]` 和 `closed_30m_prefix=self.pipeline_result.closed_30m_prefix`

#### 6.5 Historical 快照

Historical 快照使用 `PipelineResult` → `build_workbench_projection()`，已通过 Step 4-5 打通。需验证 Historical 命令响应包含 30m 字段（`test_contracts.py` 已修复）。

#### 6.6 Replay 快照

Replay 快照使用 `PipelineMarketInput` → `WorkbenchPipeline` → `PipelineResult` → `build_workbench_projection()`。30m preheat 和 official 30m 已在 Step 3b 中加载到 `PipelineMarketInput`。Replay validation 已更新。需验证 Replay 快照包含 30m 字段。

---

### Step 7: Renderer（分时/30m 副图切换）

文件：
- `apps/t0-assistant/renderer/src/workbench-layout.mjs`
- `apps/t0-assistant/renderer/src/App.tsx`
- `apps/t0-assistant/renderer/src/charts/chart-model.mjs`
- `apps/t0-assistant/renderer/src/charts/chart-viewport.mjs`

需要实现：
- **直接删除** `layout.chart_split`、`"64_36"`/`"50_50"`、`MAIN_PRIORITY`/`EQUAL` 三态布局，无迁移代码
- 布局只剩"显示/隐藏副图"；副图显示时固定 50/50
- 副图内部 `[分时][30m]` 切换
- 新增 `chartViews.thirtyMinute` 独立视口槽位；视口参数复用 5m（跟随≥72 / 手工≥48 / 上限 360）
- 图层开关与 5m 共用一组偏好；成交标记沿用 `executed_at`，无新契约字段
- 分时/30m 选择**不跨重启保存**
- 30m 不可用时保留选择 + 空态 + warning，不自动切回分时
- Tooltip：完全沿用 5 分钟 Tooltip 的字段和交互；当前未完成状态只用低透明度表达，不增加文字

---

### Step 8: 测试

需要完成：
- 契约测试：验证 30m 字段在所有快照中存在且 schema-valid
- 管线测试：验证 30m bars/indicators/chan_analysis 正确计算
- Renderer 测试：验证副图切换、视口独立、空态
- Replay 测试：验证 30m 数据在 Replay 中正确展示，不展示未来数据
- Historical 测试：验证 30m 数据在 Historical 快照中完整对齐
- 端到端测试：验证 Live/Replay/Historical 完整流程
- **30m 合并 fixture**（独立于 5m fixture）：测试"正式 K 只替换相同结束时间的临时 K，不删除下一根正在形成的临时 K"

---

### Step 9: 文档更新

需要更新：
- PRD
- 架构文档（`docs/architecture/`）
- 模块设计
- UI 布局文档
- 回放文档

---

## 四、关键文件索引

| 层 | 文件 | 30m 状态 |
|---|---|---|
| 契约 schema | `apps/t0-assistant/contracts/logical-v2.schema.json`, `app-v2`, `replay-v2` | ✅ 已更新 |
| 契约 fixtures | `apps/t0-assistant/contracts/fixtures/workbench-flow-v1.json`, `replay-speed-v1.json` | ✅ 已更新 |
| 30m 聚合器 | `packages/t0assistant/runtime/thirty_minute.py` | ✅ 已创建 |
| 30m 聚合器测试 | `packages/t0assistant/tests/test_thirty_minute.py` | ✅ 8 passed |
| 管线 | `packages/t0assistant/runtime/pipeline.py` | ✅ 已集成 30m |
| Workbench 投影 | `packages/t0assistant/runtime/workbench_projection.py` | ✅ 已集成 30m |
| Replay 验证 | `packages/t0assistant/replay/validation.py` | ✅ 已更新 |
| Replay 预热 | `packages/t0assistant/runtime/replay_data.py` | ✅ 已加载 30m preheat + official |
| Live 预热 | `packages/t0assistant/runtime/live_data.py` | ✅ 已加载 30m preheat + official |
| 计算契约 | `packages/t0assistant/runtime/computation_contract.py` | ✅ 已添加 30m 字段 |
| 预热常量 | `packages/t0assistant/runtime/live_market_view.py` | ✅ `DEFAULT_CHART_PREHEAT_COUNT = 500` |
| Live Session | `packages/t0assistant/runtime/live_session.py` | ✅ 常量已更新；❌ `build_projection()` 需传 30m |
| Live 刷新 | `packages/t0assistant/runtime/live_refresh.py` | ❌ 需新增 `OFFICIAL_THIRTY_MINUTE` 分支 |
| Live Runtime | `packages/t0assistant/runtime/live_runtime.py` | ❌ 需新增 30m refresh + branch_updates |
| Live Market View | `packages/t0assistant/runtime/live_market_view.py` | ❌ `build_live_market_view()` 需新增 30m as-of 字段 |
| 指标 | `packages/indicators/core.py` | ✅ `calculate_thirty_minute_indicators` 已创建 |
| 缠论 | `packages/chantheory/` | ✅ 已支持 `timeframe="30m"` → F30 |
| 市场数据 | `packages/marketdata/` | ✅ `_SUPPORTED_BAR_MINUTES={1,5,30}`, `T0_TIMEFRAMES` 含 "30m" |
| Renderer | `apps/t0-assistant/renderer/src/` | ❌ 需实现副图切换 |
| 投影测试 | `packages/t0assistant/tests/test_workbench_projection.py` | ✅ 58 passed |
| 契约测试 | `apps/t0-assistant/tests/test_contracts.py` | ✅ 已修复 |
| 计算契约测试 | `packages/t0assistant/tests/test_computation_contract.py` | ✅ 已修复 |

---

## 五、风险与注意

- **最复杂处**：`OFFICIAL_THIRTY_MINUTE` 边界触发调度（非固定间隔轮询，按 30m 结束时间 + 5s 触发），需单独评审 + 测试。当前 `LiveRefreshScheduler` 使用固定间隔 `next_due_at = now + interval`，30m 分支需要根据下一个 30m 边界时间计算 `next_due_at`。
- **最易错处**：30m 合并语义不能照抄 5m 的"丢弃未闭合根"规则。正式 K 只替换相同结束时间的临时 K，不删除下一根正在形成的临时 K。
- **计算层低风险**：chantheory 与 indicators 已支持 30m，直接复用。
- **测试就近放在各包 `tests/`**；改动跨包时扩大覆盖。
- **Pre-existing failure**：`test_live_dynamic_five_minute.py` 因 `SessionSpec.__init__() missing 'instrument'` 失败，与 30m 工作无关，测试时用 `--ignore=packages/t0assistant/tests/test_live_dynamic_five_minute.py` 排除。

---

## 六、新 Thread 启动 Prompt

将以下内容作为新 thread 的第一条消息：

---

我正在开发 stockpilot 项目的 30 分钟 K 线功能（Issue #168）。设计稿已冻结在 `docs/t0assistant/30m_chart_feature_design.md`（commit `886cce9`）。

**环境**：
- 仓库：`/Users/jishen/projects/stockpilot`
- Python 环境：`source ~/.venvs/czsc/bin/activate`
- 分支：`feature/issue-168-t0assistant-30m-chart`（已推送，与 origin 同步）

**已完成**（Steps 1-5 + 3b + fixtures + validation + preheat loading）：
- 契约 v2 增量：`market.bars_30m`、`indicators.thirty_minute`、`chan_analysis_30m`
- `DynamicThirtyMinuteAggregator` + 8 个测试通过
- `calculate_thirty_minute_indicators` + chantheory `timeframe="30m"`
- Pipeline 集成：`PipelineMarketInput`/`PipelineResult` 30m 字段、`_compute_unlocked()` 构建 aggregator
- Workbench projection 30m 字段
- Replay validation 30m 字段
- Fixtures（workbench-flow-v1.json + replay-speed-v1.json）30m 字段
- 30m preheat loading：`DEFAULT_CHART_PREHEAT_COUNT=500`，`_load_preheat_30m()` in replay_data.py + live_data.py
- `PreparedReplayData`/`_InMemoryMarketInputPort` 30m 字段
- 测试：709 passed, 1 skipped（排除 pre-existing failure `test_live_dynamic_five_minute.py`）

**下一步**：Step 6 — Live/Replay/Historical 完整快照打通

需要修改的文件和具体位置详见 `docs/t0assistant/30m_handoff_step6plus.md` 第三节。核心任务：

1. `live_refresh.py`：新增 `OFFICIAL_THIRTY_MINUTE` 到 `LiveRefreshKind`、`LiveRefreshIntervals`、`_KINDS`、`_validate_update`
2. `live_runtime.py`：`BranchingLiveInput.refresh()` 新增 30m 分支，`_branch_updates()` 新增 30m updates，`_snapshot_branch_time()` 新增 30m case
3. `live_market_view.py`：`build_live_market_view()` 新增 `bars_30m_as_of`、`thirty_minute_indicators_as_of`、`czsc_30m_as_of`
4. `live_session.py`：`LiveSnapshotCandidate.build_projection()` 传递 30m 数据给 `build_live_market_view()`

设计稿 §10 的核心规则：30m 结束时间 +5s 首次请求，15s 重试，2 分钟未达发 `thirty_minute_official_delayed` warning 并降为 60s，按结束时间推进 watermark，分支间故障隔离。

请先阅读 `docs/t0assistant/30m_handoff_step6plus.md` 和 `docs/t0assistant/30m_chart_feature_design.md` 的 §10 和 §15，然后开始 Step 6 的实现。若没有问题，请开始开发。
