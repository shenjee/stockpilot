# #174 正式验收记录：引擎入口与结构兼容适配

- 日期：2026-09-11
- 分支：`upgrade/czsc-1.0.1`
- 父 issue：#172；本记录对应子任务 #174
- 代码提交：`82521cb`（入口适配）→ `0e6ff81`（尾笔排除断言 / `min_bi_len` 对齐）→ `d71b5ca`（注释）
- 对照证据：#176 `acceptance-report-176.md`（被测代码 `52f8025`）与 #173 冻结基线
- 判断方式：对照 DoD 与提交/测试/对照产物；**不以 issue CLOSED 代替验收**
- 结论：**#174 DoD 在升级分支上验收通过。** 不关闭 #172。

---

## 0. 范围

只升级 czsc 至 1.0.1。新接口仅替代旧接口。结构输出相对 0.10.12 冻结长样本数量一致；已确认的行为差异（首根 `third_bs`）属 #175/#176，不在本项重复确认。

---

## 1. DoD 核对

| DoD | 证据 | 结论 |
|---|---|---|
| 2a：RawBar/Freq/CZSC 可构造，分型/笔映射到现有 schema | `engine.py` 顶层 `czsc.RawBar` / `Freq` / `CZSC`；`test_czsc_5m_spike` 真实 5m 样本产生非空 fractals/strokes | 通过 |
| 日线日期、分钟时间戳、图形索引不偏移 | #176 `baseline_compare.json`：三场景结构端点与冻结基线一致；`strict_behavior_pass=True` | 通过 |
| 分型 / `bi_list` / `finished_bis` / 未完成笔 / 尾部确认；按 1.0.1 源码写尾笔排除回归 | `test_finished_bis_tail_exclusion_condition`：`bars_ubi<5` 排除尾笔，`>=5` 纳入；端点钉死 | 通过 |
| 中枢入口迁至 `zs_list` / `get_zs_seq`；有中枢样本必须出结果；接口失败有 warning | `structure_mapping.py` 优先 `analyzer.zs_list`；`test_zs_list_matches_get_zs_seq_on_finished_bis`；长样本中枢数量与旧基线一致 | 通过 |
| `min_bi_len` / `max_bi_num` 实际生效并写入 parameters/meta | 显式 `min_bi_len=6`；`engine_probe.min_bi_len` / `min_bi_len_requested` / `max_bi_num`；分钟级 500 | 通过 |
| 声明 pin 与运行时版本一致；升级分支隔离依赖；主分支 pin 留到正式切换 | `assert_engine_version()`；升级分支 `pyproject.toml` / `PINNED_ENGINE_VERSION=1.0.1`；`origin/main` 仍为 `czsc==0.10.12` | 通过 |
| 线段、背驰、候选点保持 chantheory 自有计算 | 未迁入上游线段/背驰算法；候选点仍由项目信号回放映射 | 通过 |
| 同时间 K 线更新、尾笔延伸、历史保留窗口 | 见 §2 | 通过 |

---

## 2. 同 dt / 尾笔 / 保留窗口

生产路径按 ADR 0008 **全量重建**。重复时间戳在 normalize 层保留最后一根（`test_normalization_sorts_and_keeps_last_duplicate`），再重建。

本轮补齐引擎层回归：`test_same_timestamp_update_replaces_last_bar_in_place`。1.0.1 `CZSC.update` 对相同 `dt` **原地替换**最后一根（`bars_raw` 长度不变，收盘价更新），不会追加一根未来 K。

尾笔延伸：`_map_pending_stroke_*` 与 `last_bi_extend` 探针。历史保留窗口：日线 `max_bi_num=50`、分钟 `500`，由 `get_default_max_bi_num` 覆盖。

---

## 3. 差异清单（交 #176 已处理）

- 溯源元数据：`engine_version`、`module`→`czsc._native`、`mapping_strategy`→`czsc_zs_list`、显式 `min_bi_len`。属预期迁移，不是结构行为差异。
- 结构数量（日线 30/9、5m 38/24、30m 417/95）与 #173 冻结输出一致。
- 无未确认的用户可见结构差异。

---

## 4. 结论

#174 在升级分支验收通过，允许作为 #175/#176 的结构前提。正式环境切换与主分支 pin 仍由 #177 收口。
