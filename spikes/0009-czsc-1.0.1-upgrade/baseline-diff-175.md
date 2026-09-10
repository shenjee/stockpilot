# #175 信号接口迁移 — 旧基线差异排查与 #176 待验证清单

- 日期：2026-09-10
- 分支：`upgrade/czsc-1.0.1`
- 提交：`7c6bae3`（初版）→ 修复提交（本轮）
- 父 issue：#172；本报告对应子任务 #175
- 环境：`~/.venvs/czsc`（czsc 1.0.1，Python 3.14.5）

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

**结论：结构输出完全一致，无差异。**

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

**结论：信号触发/未触发场景、active 点数、事件数完全一致，无差异。**

### 2.3 警告差异

| 场景 | 旧版 warnings | 新版 warnings | 差异说明 |
|---|---|---|---|
| 日线合成 120 根 | AMOUNT_DERIVED, SIGNAL_EVALUATION_FAILED | AMOUNT_DERIVED | 旧版 czsc.signals.cxt 模块在求值时抛异常产生 SIGNAL_EVALUATION_FAILED；新版 Rust 原生分发器无此问题，警告消失 |
| 5m 真实 548 根 | SIGNAL_EVALUATION_FAILED | (无) | 同上 |
| 30m 真实 1336 根 | AMOUNT_DERIVED, SIGNAL_EVALUATION_FAILED | AMOUNT_DERIVED | 同上 |

**结论：唯一差异是 SIGNAL_EVALUATION_FAILED 警告消失。** 该警告在旧版由
`czsc.signals.cxt` 模块信号函数内部抛出异常触发（非项目代码问题），新版 Rust 原生
分发器 `czsc._native.call_signal` 正常求值，不再产生此警告。这是预期行为改善，
非回归。

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
| 自定义模块函数缺失 | SIGNAL_FUNCTION_UNAVAILABLE | `test_custom_module_function_missing_produces_function_unavailable_warning` |
| 返回值不兼容（None/裸字符串/无 .value） | SIGNAL_EVALUATION_FAILED (error) | `test_incompatible_return_type_produces_error_not_silent_inactive`、`test_incompatible_return_type_via_dispatcher_produces_error` |
| 求值失败（IndexError/ValueError） | SIGNAL_EVALUATION_FAILED | `test_signal_status_distinguishes_active_inactive_not_ready_error` |

**关键行为保证：返回值不兼容时状态为 "error"（非 "inactive"），不产生假 invalidated 事件。**

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
4. **pyproject.toml 依赖 pin 更新**：当前仍 pin `czsc==0.10.12`，需 #177 更新为
   `czsc==1.0.1`。
5. **正式金标 fixture 对照**：#175 仅使用 #173 冻结的三个场景输入对照，
   不覆盖旧金标或正式 fixture（DoD 明确排除）。

---

## 7. 测试统计

- `packages/chantheory/tests/` 全量：**111 passed**（21.48s）
  - `test_adapters.py`：47 passed
  - `test_czsc_5m_spike.py`：23 passed（含 7 个 RealEngineSignalTests）
  - `test_normalize.py`：3 passed
  - `test_plotting.py`：1 passed
  - `test_segments.py`：37 passed
