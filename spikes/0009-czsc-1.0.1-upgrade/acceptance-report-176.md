# #176 新旧行为回归、性能与下游验收（进行中）

- 日期：2026-09-11
- 分支：`upgrade/czsc-1.0.1`
- 被测代码 SHA：`52f80259fecdf592095615e07c7dee319375b73e`（行为/性能对照所针对的升级分支代码）
- 验收文件提交 SHA：`d3de2344ab6d14103ff271bc48e2b01f61dc59a1`（验收脚本/产物/本报告首次入库；后续仅补充 UI 证据等增量时另记）
- 父 issue：#172；本报告对应子任务 #176
- **门禁**：#174/#175 尚未正式验收通过 → **不得关闭 #176、不得更新正式 fixture、不得进入 #177**
- 已确认口径（首根 `third_bs`、绝对性能可接受）**无需再等待批准**；#176 仍因 UI 证据与 #174/#175 保持打开
- 本次确认 **不** 代表 #177 正式切换获准，也 **不** 代表 #172 升级完成

---

## 0. 执行口径（已锁定）

1. 同分支并行验收，但不提前放行依赖门禁。
2. 对照样本：`600584.SH` + #173 冻结三输入（5m 548 / 30m 1336 / 合成日线 120）。
3. 性能：隔离重跑 0.10.12（worktree `stockpilot-wt-01012` + venv `~/.venvs/czsc01012-bench`），不降级当前正式环境。
4. `CzscSignals` / `BarGenerator`：**项目未使用，本次不适配、不测试**。
5. 正式 fixture 暂不更新；短样本 `p2_sample_*` 待门禁通过后再按真实输出修正。
6. 生产路径保持 ADR 0008 **全量重建**；不为追回几十毫秒做架构重构。

---

## 0.1 用户确认记录（2026-09-11）

### 确认 1 — 首根 `third_bs`（已接受）

**接受**首根 `third_bs`：`not_ready → inactive`，以及对应 `SIGNAL_EVALUATION_FAILED` 消失。

范围限定：

- 仅限已验证的「上游正常返回未触发值」场景；
- **真实求值失败仍须保留诊断，不能统一转成 `inactive`**；
- 不必为模拟旧异常增加启发式规则。

### 确认 2 — 性能（已接受；证据已补）

**性能结论：当前绝对耗时可接受；多轮测试未复现稳定的相对回退。** 对每根闭合 5m K 触发一次分析可接受，继续全量重建。

已补齐：多轮测量 + 信号回放耗时定位。UI 冒烟（含推进/回退/切换响应）仍待补证据。

---

## 1. 环境偏离记录（交 #177）

| 项 | 状态 |
|---|---|
| #172 约定正式切换顺序 | #177 才改正式环境 / 回退验收 |
| 当前 `~/.venvs/czsc` | **已是 czsc 1.0.1** |
| 结论 | 记为相对 #172 切换顺序的**提前环境偏离**；**不能**据此认定正式切换已完成。由 #177 补齐切换与回退验收。 |

旧版对照专用环境（未触碰正式 venv）：

| 项 | 值 |
|---|---|
| 旧 worktree | `/Users/jishen/development/stockpilot-wt-01012` @ `main` / `2883f34`（pin `czsc==0.10.12`） |
| 旧 venv | `~/.venvs/czsc01012-bench` |
| 新环境 | `~/.venvs/czsc` + 本分支 `52f8025` + 未提交验收代码 |

---

## 2. 基线行为对照（vs #173 冻结输出）

脚本：`acceptance/compare_baseline.py`  
产物：`acceptance/artifacts/baseline_compare.json`

### 2.1 数量指标

| 场景 | fractals/strokes | signal active | signal_events | candidate_point_events |
|---|---|---|---|---|
| 日线合成 120 | 30/9 一致 | first_sell 16 一致 | 4 一致 | 4 一致 |
| 5m 548 | 38/24 一致 | 0/46/38/0 一致 | 20 一致 | 20 一致 |
| 30m 1336 | 417/95 一致 | 111/102/161/205 一致 | 220 一致 | 220 一致 |

### 2.2 差异分类

- **migration_provenance**：`engine_version`、`module`→`czsc._native`、`mapping_strategy`→`czsc_zs_list`、`parameters.min_bi_len` 显式 6、summary 文案、engine_assumptions 文案。属 #174/#175 预期溯源变更。
- **confirmation_queue**：首根 `third_bs` / 警告集合变化 → **已由用户确认接受**（见 §0.1 / §3）。
- **其余行为差异**：无（`strict_behavior_pass=True`）。

### 2.3 plot_primitives

三场景 `plot_primitives` 与结构字段端点/关联检查通过。

### 2.4 多周期

固定样本：`600584.SH` 的 5m+30m 冻结输入。  
`analyze_multi_timeframe` 各 level 与独立 `analyze` 结果一致（`multi_timeframe_pass=True`）。

---

## 3. 首根 `third_bs`：`not_ready → inactive`（已确认）

脚本：`acceptance/probe_first_bar_status.py`  
产物：`acceptance/artifacts/first_bar_status_probe.json`

### 3.1 现象

三场景 bar0：

| 版本 | value | status | 警告 |
|---|---|---|---|
| 0.10.12 | `''` | `not_ready` | `SIGNAL_EVALUATION_FAILED` |
| 1.0.1 | `其他_任意_任意_0` | `inactive` | 无此警告 |

### 3.2 排查结论

在 1.0.1 上对前缀调用 `czsc._native.call_signal("cxt_third_bs_V230319", ...)`：**从不抛异常**，一律返回未触发值。无法在不发明启发式的前提下保持旧口径。

### 3.3 确认结果

**已接受**（范围见 §0.1）。真实求值失败路径仍须保留诊断警告，不得统一折叠为 `inactive`。

---

## 4. 增量更新 vs 全量重建

脚本：`acceptance/incremental_consistency.py`  
产物：`acceptance/artifacts/incremental_vs_rebuild*.json`

| 场景 | warm | 检查步数 | 结果 |
|---|---|---|---|
| 日线合成 120 | 40 | 80 | PASS |
| 5m 548 | 500 | 48 | PASS |
| 30m 1336 | 1200 | 136 | PASS |

ADR 0008 全量重建策略不变。

---

## 5. 性能对照与补证

### 5.1 首轮单次（已接受的绝对水平）

脚本：`acceptance/benchmark_compare.py`  
产物：`acceptance/artifacts/performance_compare.json`  
条件：warmup=1、samples=9

| 场景 | 指标 | 旧 p95 (ms) | 新 p95 (ms) | 相对变化 |
|---|---|---|---|---|
| 5m 548 | full_analyze | 203.796 | 239.219 | +17.4%（+35ms） |
| 5m 548 | signal_replay | 93.474 | 111.614 | +19.4%（+18ms） |

用户已接受该绝对耗时水平；但单次采样不足以认定稳定相对回归。

### 5.2 多轮测量（补证）

脚本：`acceptance/benchmark_compare.py --multi-round`  
产物：`acceptance/artifacts/performance_compare_multi_round.json`  
条件：rounds=5、warmup=2、samples=20（共 100 次有效采样 / 指标）

| 场景 | 指标 | 旧 pooled p95 | 新 pooled p95 | 相对 | 新 round-p95 范围 |
|---|---|---|---|---|---|
| 日线 120 | full_analyze | 47.875 | 47.803 | -0.2% | [42.7, 47.8] |
| 日线 120 | signal_replay | 19.003 | 17.590 | -7.4% | [16.7, 32.1] |
| 5m 548 | full_analyze | 246.131 | 221.724 | **-9.9%** | **[213.5, 224.5]** |
| 5m 548 | signal_replay | 132.633 | 112.787 | **-15.0%** | **[109.7, 113.1]** |
| 5m 548 | engine_update | 0.049 | 0.013 | -73.5% | [0.012, 0.015] |

解读（正式结论）：

1. **当前绝对耗时可接受**（新侧 5m full / signal_replay round-p95 约 214–225ms / 110–113ms，范围窄）。
2. **多轮测试未复现稳定的相对回退**；首轮单次「+17%~19%」不能当作稳定回归结论。
3. 继续全量重建；不为追回几十毫秒做架构重构。

### 5.3 信号回放耗时定位（补证）

脚本：`acceptance/profile_signal_replay.py`  
产物：`acceptance/artifacts/signal_replay_profile.json`  
辅助：`acceptance/artifacts/signal_replay_cprofile.txt`

目的：排除重复计算或适配层转换浪费；**不**据此扩大优化范围。

已撤回早先「分发器调用是主要开销」的初步归因。剖析观测（仅作定位证据）：

| 桶 | mean share（剖析） | 说明 |
|---|---|---|
| `to_timestamp` | ~63% | 适配层 bar / pending-bi 时间戳归一化（**来自性能剖析，不扩大优化范围**） |
| residual 循环记账 | ~16% | 状态映射 / evaluation 组装等 |
| series/events/snapshots | ~11% | 后处理 |
| `call_signal` | ~7% | 原生分发器（**非**主导归因） |
| `extract_signal_value` | ~2% | list[Signal]→string |
| `CZSC.update` | ~1% | 回放增量更新 |

排除项：

- **无重复求值**：`dispatcher_calls == bars × 4 signals`（548×4=2192）。
- **无转换浪费主导**：`extract_signal_value` 仅约 2%。

---

## 6. 下游冒烟

脚本：`acceptance/downstream_smoke.py`  
产物：`acceptance/artifacts/downstream_smoke.json`

### 6.1 自动化（已通过）

| 面 | 套件 | 结果 |
|---|---|---|
| Live 动态 K 不进闭合分析 | `test_live_dynamic_five_minute` | PASS |
| Replay e2e | `test_replay_e2e_acceptance` | PASS |
| Live↔Replay 生命周期 | `test_live_replay_lifecycle_acceptance` | PASS |
| chan-viewer | `apps/chan-viewer/tests` | PASS |
| 引擎 5m spike | `test_czsc_5m_spike` | PASS |

### 6.2 UI 冒烟证据（已完成）

固定标的：`600584` / 长电科技；不下单。产物目录：`acceptance/artifacts/ui-smoke/{live,replay,chan-viewer}/`。  
被测代码 SHA：`52f8025`；本轮 UI 脚本与证据相对验收入库 `d3de234` / SHA 回填 `761fe5d` 的增量提交。

#### Live（PASS）

- 脚本：`acceptance/ui_smoke_t0_electron.mjs`（真实 `PythonServiceHost` + `T0_PYTHON=~/.venvs/czsc/bin/python`）
- 证据：`artifacts/ui-smoke/live/evidence.json` + `01_live_600584_loaded.png` / `02_live_symbol_switch_600000.png` / `03_live_back_600584.png`
- 步骤：服务就绪 → 加载 600584 → 切换 600000 → 切回 600584；可见笔/候选点 overlay；**未下单**
- 响应：各步约 0.5–2.3s；无明显卡顿

#### Replay（PASS）

- 同一 Electron 脚本；回放日 **2026-07-14**（覆盖冻结 5m 窗口）
- 证据：`artifacts/ui-smoke/replay/evidence.json` + `01` 设置 / `02` 开始 / `03` 推进 / `04` 回退 / `05` 回 Live
- 步骤：进入回放 → 开始回放 → **前进**（推进）×2 → **seek 回退** → 切回实盘
- 响应：推进 ~1.4s；回退 seek ~64ms；回 Live ~1.6s；无明显卡顿

#### chan-viewer（PASS）

- 脚本：`acceptance/ui_smoke_chan_viewer.py`（Playwright；需 `streamlit` 已在 `:8501`）
- 固定输入：600584，日期 **2026-06-29 ~ 2026-07-14**
- 证据：`artifacts/ui-smoke/chan-viewer/ui_smoke_chan_viewer.json` + 日线 / 切 5 分 / 切 30 分 / 切标的往返截图
- 步骤：选股 → 运行日线 → 周期切换（5 分 / 30 分）→ 标的切换往返
- 响应：单次分析约 10s（含拉数）；自动化全程无明显挂起（推进/回退不适用于本面）

---

## 7. 自动化回归（包内测试）

新增：`packages/chantheory/tests/test_upgrade_176_acceptance.py`

- plot_primitives 与结构端点一致（5m 冻结输入）
- 多周期 level == 独立分析
- 三冻结场景结构计数钉住（30/9、38/24、417/95）

**未**更新 `p2_sample_result.json` 等正式 fixture（门禁未放行）。

---

## 8. CzscSignals / BarGenerator

生产路径：`chantheory.analyze*` → `call_signal` 分发器；T+0 按 ADR 0008 **全量重建**。  
结论：**项目未使用，本次不适配、不测试。**

---

## 9. 正式 fixture 策略（门禁后）

待 #174/#175 验收、本项性能补证归档、手动 UI 证据齐备后：

1. 以冻结长样本（5m/30m/日线）承担结构金标。
2. 保留 5 根短样本作边界测试，按真实输出修正错误期望；若用于手工绘图，明确合成性质，不当引擎金标。
3. 旧版 `baseline/` 永久保留；每项更新注明原因。
4. 验收脚本与报告入库后，文首回填「验收文件提交 SHA」；关闭前若 #174/#175 有代码变更，重跑受影响项。

---

## 10. 当前状态 / 仍阻塞关闭的事项

| 项 | 状态 |
|---|---|
| 结构/信号数量与事件对照 | 通过 |
| plot_primitives / 多周期 | 通过 |
| 增量 vs 重建 | 通过 |
| 首根 not_ready→inactive | **已确认接受**（限定范围） |
| 性能（绝对可接受；多轮无稳定相对回退） | **已确认；证据已补** |
| 下游自动化 | 通过 |
| 下游手动 UI（含推进/回退/切换响应） | **已通过**（§6.2） |
| #174/#175 正式验收 | **未通过（仍阻塞关闭）** |
| 正式 fixture | **未更新（正确）** |
| 验收脚本/报告入库 | 已入库；UI 证据为本轮增量提交 |
| 关闭 #176 | **保持打开**（等 #174/#175） |
| #177 / #172 完成 | **未获准 / 未完成** |

---

## 11. 复现命令

```bash
source ~/.venvs/czsc/bin/activate
cd /Users/jishen/development/stockpilot

python spikes/0009-czsc-1.0.1-upgrade/acceptance/compare_baseline.py
python spikes/0009-czsc-1.0.1-upgrade/acceptance/probe_first_bar_status.py
python spikes/0009-czsc-1.0.1-upgrade/acceptance/incremental_consistency.py
python spikes/0009-czsc-1.0.1-upgrade/acceptance/benchmark_compare.py
python spikes/0009-czsc-1.0.1-upgrade/acceptance/benchmark_compare.py --multi-round \
  --scenario 5m_real_600584_548 --scenario daily_synthetic_120
python spikes/0009-czsc-1.0.1-upgrade/acceptance/profile_signal_replay.py
python spikes/0009-czsc-1.0.1-upgrade/acceptance/downstream_smoke.py
python spikes/0009-czsc-1.0.1-upgrade/acceptance/ui_smoke_chan_viewer.py
# Live/Replay: see acceptance/README.md (Electron + T0_PYTHON)
python -m unittest packages.chantheory.tests.test_upgrade_176_acceptance
```
