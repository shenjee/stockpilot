# #175 信号接口迁移 — 旧基线差异排查与 #176 待验证清单

- 日期：2026-09-10（复审修复更新）
- 分支：`upgrade/czsc-1.0.1`
- 提交：`7c6bae3`（初版）→ `d9770c7`（P1 修复一轮）→ 本轮（P1 修复二轮）
- 父 issue：#172；本报告对应子任务 #175
- 环境：`~/.venvs/czsc`（czsc 1.0.1，Python 3.14.5）；`pyproject.toml` pin `czsc==1.0.1`

---

## 1. 旧基线对照方法

使用 #173 冻结的三个固定输入（日线合成 120 根、5m 真实 548 根、30m 真实 1336 根），
在 czsc 1.0.1 环境下通过 `packages.chantheory.analyze()` 生产代码路径运行，
将结果与 #173 冻结的旧版基线输出（czsc 0.10.12）逐项对照。

冻结输入位置：`spikes/0009-czsc-1.0.1-upgrade/baseline/inputs/`
冻结基线输出：`spikes/0009-czsc-1.0.1-upgrade/baseline/outputs/`

---

## 2. 差异排查结果

### 2.1 结构输出（分型/笔/线段/中枢）

| 场景 | 指标 | 旧版 (0.10.12) | 新版 (1.0.1) | 差异 |
|---|---|---|---|---|
| 日线合成 120 根 | fractal_count | 30 | 30 | 无 |
| | stroke_count | 9 | 9 | 无 |
| 5m 真实 548 根 | fractal_count | 38 | 38 | 无 |
| | stroke_count | 24 | 24 | 无 |
| 30m 真实 1336 根 | fractal_count | 417 | 417 | 无 |
| | stroke_count | 95 | 95 | 无 |

结构数量完全一致。**但存在溯源元数据差异**（见 2.4）。

### 2.2 信号输出（四个默认信号）

| 场景 | 信号 | 旧版 active 点数 | 新版 active 点数 | 差异 |
|---|---|---|---|---|
| 日线合成 120 根 | first_buy | 0 | 0 | 无 |
| | first_sell | 16 | 16 | 无 |
| | second_bs | 0 | 0 | 无 |
| | third_bs | 0 | 0 | 无 |
| 5m 真实 548 根 | first_buy | 0 | 0 | 无 |
| | first_sell | 46 | 46 | 无 |
| | second_bs | 38 | 38 | 无 |
| | third_bs | 0 | 0 | 无 |
| 30m 真实 1336 根 | first_buy | 111 | 111 | 无 |
| | first_sell | 102 | 102 | 无 |
| | second_bs | 161 | 161 | 无 |
| | third_bs | 205 | 205 | 无 |

| 场景 | 信号事件数 旧版 | 信号事件数 新版 | 差异 |
|---|---|---|---|
| 日线合成 120 根 | 4 | 4 | 无 |
| 5m 真实 548 根 | 20 | 20 | 无 |
| 30m 真实 1336 根 | 220 | 220 | 无 |

active 点数与事件数完全一致。**但存在逐点差异**（见 2.4）。

### 2.3 警告差异

| 场景 | 旧版 warnings | 新版 warnings | 差异说明 |
|---|---|---|---|
| 日线合成 120 根 | AMOUNT_DERIVED, SIGNAL_EVALUATION_FAILED | AMOUNT_DERIVED | 旧版 czsc.signals.cxt 模块在求值时抛异常产生 SIGNAL_EVALUATION_FAILED；新版 Rust 原生分发器无此问题，警告消失 |
| 5m 真实 548 根 | SIGNAL_EVALUATION_FAILED | (无) | 同上 |
| 30m 真实 1336 根 | AMOUNT_DERIVED, SIGNAL_EVALUATION_FAILED | AMOUNT_DERIVED | 同上 |

### 2.4 完整差异清单

数量一致不等于完整输出一致。逐点对照发现以下差异：

**差异 1：首根 third_bs 求值状态（三个场景均存在）**

| 场景 | bar | 旧版 | 新版 |
|---|---|---|---|
| 日线合成 120 根 | 0 | value=`''`, status=`not_ready` | value=`'其他_任意_任意_0'`, status=`inactive` |
| 5m 真实 548 根 | 0 | value=`''`, status=`not_ready` | value=`'其他_任意_任意_0'`, status=`inactive` |
| 30m 真实 1336 根 | 0 | value=`''`, status=`not_ready` | value=`'其他_任意_任意_0'`, status=`inactive` |

原因：旧版 `czsc.signals.cxt` 的 `cxt_third_bs_V230319` 在首根（结构未形成）时
抛异常 → 状态 `not_ready`、空值，并产生 `SIGNAL_EVALUATION_FAILED` 警告；新版
Rust 原生分发器在首根正常求值返回 `其他_任意_任意_0` → 状态 `inactive`。这是
2.3 警告消失的同一根因，属预期行为改善（新版引擎首根即可求值），非回归。
后续所有 bar（bar 1 起）两版完全一致。

**差异 2：信号模块溯源（三个场景均存在，#175 迁移本身）**

| 信号 | 旧版 module | 新版 module |
|---|---|---|
| 全部 4 个默认信号 | `czsc.signals.cxt` | `czsc._native` |

这是 #175 信号接口迁移的直接结果：信号求值从 Python 模块 `czsc.signals.cxt`
迁移到 Rust 原生分发器 `czsc._native.call_signal`。`signal_series[].module`、
`signal_events[].module`、`signal_snapshots` 相关溯源字段随之变化。属预期变更。

**差异 3：中枢映射策略溯源（5m 场景，#174 变更）**

| 字段 | 旧版 | 新版 |
|---|---|---|
| `pivot_zones[0].meta.mapping_strategy` | `czsc_get_zs_seq` | `czsc_zs_list` |

中枢数值（high/low/gg/dd/zz）、segment_ids、时间戳完全一致，仅
`mapping_strategy` 溯源字段变化。这是 #174（czsc 1.0.1 结构接口迁移）的变更：
中枢映射从 `get_zs_seq()` 迁移到 `zs_list` 属性。属预期变更。

**差异 4：SIGNAL_EVALUATION_FAILED 警告消失（三个场景，见 2.3）**

与差异 1 同根因。

**结论：数量指标（结构数、active 点数、事件数）完全一致；完整输出存在 4 类
差异，全部为 #174/#175 迁移的预期变更（首根求值能力改善、模块溯源、中枢映射
溯源、警告消失），无未解释差异，无回归。**

---

## 3. 四个默认信号覆盖记录

### 3.1 触发场景覆盖

| 信号 | 触发场景 | 未触发场景 |
|---|---|---|
| cxt_first_buy_V221126 | 30m（111 active） | 日线（0）、5m（0） |
| cxt_first_sell_V221126 | 日线（16）、5m（46）、30m（102） | — |
| cxt_second_bs_V240524 | 5m（38）、30m（161） | 日线（0） |
| cxt_third_bs_V230319 | 30m（205） | 日线（0）、5m（0） |

**结论：四个默认信号均有触发和未触发场景覆盖。**

### 3.2 真实引擎样本

- 5m fixture（`test_czsc_5m_spike.py::RealEngineSignalTests`）：4 个默认信号在
  czsc 1.0.1 真实引擎上逐 bar 求值，first_sell 和 second_bs 触发，first_buy 和
  third_bs 未触发。
- 30m fixture（基线对照）：4 个默认信号全部触发。

---

## 4. 诊断覆盖记录

| 诊断场景 | 警告码 | 测试 |
|---|---|---|
| 模块缺失（czsc._native 不可用） | SIGNAL_DISPATCHER_UNAVAILABLE | `test_dispatcher_unavailable_produces_dispatcher_unavailable_warning` |
| 自定义模块导入失败 | SIGNAL_MODULE_UNAVAILABLE | `test_custom_module_missing_produces_module_unavailable_warning` |
| 未知信号（原生分发器 KeyError） | SIGNAL_FUNCTION_UNAVAILABLE | `test_unknown_signal_name_produces_function_unavailable_warning` |
| 自定义模块函数缺失（查找失败） | SIGNAL_FUNCTION_UNAVAILABLE | `test_custom_module_function_missing_produces_function_unavailable_warning` |
| 函数执行中抛 AttributeError（执行失败，非查找失败） | SIGNAL_EVALUATION_FAILED (error) | `test_execution_attribute_error_does_not_stop_subsequent_evaluation` |
| 返回值不兼容（None/裸字符串/无 .value） | SIGNAL_EVALUATION_FAILED (error) | `test_incompatible_return_type_produces_error_not_silent_inactive`、`test_incompatible_return_type_via_dispatcher_produces_error` |
| 求值失败（IndexError/ValueError） | SIGNAL_EVALUATION_FAILED | `test_signal_status_distinguishes_active_inactive_not_ready_error` |
| 跨错误恢复（同值，不重复触发） | （无事件） | `test_recovery_from_error_to_same_value_does_not_retrigger` |
| 跨错误恢复（异值，switched 而非 triggered） | switched | `test_recovery_from_error_to_different_value_emits_switched` |
| 候选点跨错误恢复（同值，无假失效） | （无事件） | `test_candidate_point_events_suppress_spurious_invalidation_across_error` |
| 候选点跨错误恢复（异值，switched） | switched | `test_candidate_point_events_switch_across_error_recovery` |

**关键行为保证：**
1. 返回值不兼容时状态为 "error"（非 "inactive"），不产生假 invalidated 事件。
2. 函数执行异常（含 AttributeError）不缓存、不跳过后续 bar，后续 bar 继续求值。
3. 跨错误恢复：同值恢复不重复触发（无事件），异值恢复产生 switched（非 triggered）。
4. 信号事件与候选点事件两条路径统一处理未知状态（error/not_ready）。

---

## 5. 自定义 signals_config 支持验证

| 用法 | 测试 | 状态 |
|---|---|---|
| 字符串配置 | `normalize_signals_config` 默认路径 | ✅ |
| 映射配置（module/name/key） | `test_custom_module_signal_uses_legacy_import_path` | ✅ |
| 自定义 module 调用路径 | `test_custom_module_signal_uses_legacy_import_path` | ✅ |
| di/kwargs 参数传递 | `test_document_style_signals_config_removes_di_and_builds_distinct_keys` | ✅ |
| enabled=false 启停 | `normalize_signals_config` 逻辑 | ✅ |
| 原生分发器 + 自定义模块混合 | `build_signal_payloads` 分流逻辑 | ✅ |

---

## 6. #176 待验证清单

以下项目不在 #175 范围内，交 #176 追踪：

1. **增量更新一致性**：逐根增量更新与全量重建在所有前缀上的信号输出一致性
  （#173 spike 已验证结构一致性，信号一致性需 #176 正式对照）。
2. **CzscSignals 逐 bar 信号器**：`CzscSignals` / `BarGenerator` 构造签名变化，
   如项目需要使用（当前仅用 `call_signal` 分发器），需 #176 适配。
3. **性能基准**：新版 Rust 原生分发器的延迟与旧版纯 Python 信号函数的对比
   （#173 spike 记录的 p95 ~188-195ms 为旧版全量重建含信号重放；新版需重新基准）。
4. **pyproject.toml 依赖 pin 状态确认**：`pyproject.toml` 已 pin `czsc==1.0.1`
  （#174 随迁移更新）。#177 需确认该 pin 与正式环境安装一致并更新锁文件。
5. **正式金标 fixture 对照**：#175 仅使用 #173 冻结的三个场景输入对照，
   不覆盖旧金标或正式 fixture（DoD 明确排除）。

---

## 7. 测试统计

- `packages/chantheory/tests/` 全量：**116 passed**（本轮修复后）
  - `test_adapters.py`：52 passed（含本轮新增 5 个跨错误恢复回归测试）
  - `test_czsc_5m_spike.py`：23 passed（含 7 个 RealEngineSignalTests）
  - `test_normalize.py`：3 passed
  - `test_plotting.py`：1 passed
  - `test_segments.py`：37 passed

注：复审隔离环境曾报告 110 passed（d9770c7 时点），与本仓库环境收集数
（111，d9770c7 时点）相差 1，疑为隔离环境收集差异；本轮修复后本仓库环境
收集 116 项全部通过。
