# #175 正式验收记录：信号接口与候选点契约迁移

- 日期：2026-09-11
- 分支：`upgrade/czsc-1.0.1`
- 父 issue：#172；本记录对应子任务 #175
- 代码提交：`7c6bae3`（分发器迁移）→ `d9770c7` / `d8f126a` / `52f8025`（查找 vs 执行错误、自定义模块路径、返回值诊断）
- 差异排查：`baseline-diff-175.md`（对照提交起点 `7c6bae3`）
- 真实引擎样本：`packages/chantheory/tests/test_czsc_5m_spike.py::RealEngineSignalTests` + #173 三冻结场景
- 判断方式：对照 DoD 与提交/测试/对照产物；**不以 issue CLOSED 代替验收**
- 结论：**#175 DoD 在升级分支上验收通过。** 不关闭 #172。

---

## 0. 验证所用结构范围

| 项 | 值 |
|---|---|
| 升级分支信号适配头 | `52f8025` |
| 结构前提 | #174 验收通过（`82521cb`…`d71b5ca`） |
| 样本 | 日线合成 120、5m 真实 548、30m 真实 1336（#173 冻结输入） |
| 真实引擎测试 | 5m 548 全量（`RealEngineSignalTests`） |

无「结构未稳故信号未验」的缺口。`CzscSignals` / `BarGenerator` 项目未使用，明确不测（#176 已记录）。

---

## 1. DoD 核对

| DoD | 证据 | 结论 |
|---|---|---|
| 迁移分发入口、di/kwargs、Signal 列表 → 现有序列/事件/快照 | `signals.py`：`czsc._native.call_signal`；原生模块名走分发器，其他模块走 `import_module` | 通过 |
| 四个默认信号各自触发与未触发 | 30m：四信号均 active；日线/5m：first_sell 触发，first_buy/third_bs 未触发。`RealEngineSignalTests` 钉死 5m first_sell@276、second_bs@341 | 通过 |
| 保持自定义 `signals_config`（字符串/映射、参数、启停） | `test_adapters` 自定义模块路径、enabled、kwargs；执行期错误不污染缺失缓存 | 通过 |
| 候选点触发/切换/失效，不引入未来数据 | `test_candidate_point_events_align_with_signal_events`；回放 seek 丢弃未来时间戳（`test_backward_seek_discards_future...`） | 通过 |
| 模块缺失、未知信号、返回值不兼容、求值失败有明确诊断 | dispatcher unavailable / unknown name / 返回类型 / 执行期 ImportError·KeyError 均有 warning 或 `error` 状态，不误判为未触发 | 通过 |
| 单测可 mock，至少 1–2 个真实新版引擎样本 | mock 分发器覆盖契约；真实样本见 `RealEngineSignalTests` | 通过 |
| 对照旧基线；本项不覆盖旧金标或正式 fixture | `baseline-diff-175.md`；正式短样本改由 #176 收口 | 通过 |

---

## 2. 已确认差异（不再询问）

首根 `third_bs`：`not_ready` + 空值 + `SIGNAL_EVALUATION_FAILED` → `inactive` + `其他_任意_任意_0`。用户已接受，范围仅限上游正常返回未触发值；真实求值失败仍须保留诊断。

其余 active 点数与事件数与 0.10.12 冻结输出一致。

---

## 3. 原交 #176 追踪项（现已关闭）

| 原遗留 | #176 处理 |
|---|---|
| 增量 vs 全量信号一致性 | `incremental_consistency.py` 三场景 PASS |
| `CzscSignals` / `BarGenerator` | 项目未使用，不适配 |
| 性能 | 绝对耗时可接受；多轮无稳定相对回退（已确认） |
| pyproject pin / 「锁文件」 | 升级分支已 pin `czsc==1.0.1`；#177 改为归档依赖快照，不新建锁体系 |
| 正式 fixture | 本轮 #176 按真实输出修正 `p2_sample_result.json` |

---

## 4. 结论

#175 在升级分支验收通过。信号契约可进入 #176 正式 fixture 收口与 #177 切换准备。
