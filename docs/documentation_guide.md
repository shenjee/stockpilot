# 文档权威来源与验收证据

本文提供导航和证据状态，不定义第二份公共字段规范，不改变目录布局。

## 当前维护入口

| 要解决的问题 | 入口与权威范围 |
| --- | --- |
| 组件、启动与测试入口 | [根 README](../README.md)、[T+0 App README](../apps/t0-assistant/README.md)、[基本面 App README](../apps/fundamental-screener/README.md)；命令实际定义以 app 的 package.json、pyproject.toml、测试脚本和 CI 为准 |
| 跨包所有权和进程边界 | [AGENTS.md](../AGENTS.md)、[ADR 状态索引](adr/README.md)、[C4 容器图](architecture/c4-container.md) |
| T+0 产品和模块职责 | [PRD](t0assistant/t0_assistant_prd.md)、[架构](t0assistant/architecture.md)、[模块设计](t0assistant/module_design.md)、[UI 规格](t0assistant/ui_layout_spec.md) |
| T+0 公共字段、事件、Replay 行为 | [契约 README 与 JSON Schema](../apps/t0-assistant/contracts/README.md)、[Replay 行为契约](t0assistant/replay_interface_and_behavior.md)；实现参考和交接摘要不覆盖这些定义 |
| T+0 实现路径和 CI 覆盖 | [实现参考](t0assistant/implementation_reference.md)、[CI 覆盖矩阵](t0assistant/ci_coverage.md) |
| Chan Theory 公共接口 | [包 README](../packages/chantheory/README.md)、[设计说明](chan_theory_v0.1.md) |
| 指标口径 | [指标包 README](../packages/indicators/README.md) |
| 基本面筛选、PIT、同步与质量规则 | [MVP](fundamental_screener_mvp.md)、[阶段计划](fundamental_screener_phase_plan.md)；产品是可度量筛选与比较，不是研报生成或投资建议 |

## 历史材料的身份

- [30m 开发交接](t0assistant/30m_dev_handoff.md)和[Step 6+ 交接](t0assistant/30m_handoff_step6plus.md)
  保留当时的分支、机器路径、步骤和测试数字，仅用于追溯。其“已完成”不构成当前版本验收。
- [30m 功能设计](t0assistant/30m_chart_feature_design.md)是专项设计来源；公共字段仍回到契约目录核对。
- [开发 backlog](t0assistant/development_backlog.md)和阶段计划中的任务勾选是规划/历史记录，
  不能代替实现、PR 或测试证据。ADR 是否生效以状态为准；[Spike](spikes/README.md)用于证明特定决策，不能直接当作当前产品实现。
- 发现设计、实现和证据冲突时，应明确记录差异并修订对应来源，不在交接摘要另造规范。

## T0-054 / #86 的证据状态

2026-10-04 核查结果：

1. [Issue #86](https://github.com/shenjee/stockpilot/issues/86) 已关闭，相关实现来自
   [PR #118](https://github.com/shenjee/stockpilot/pull/118)，本地 Git 有合并提交
   `e754d9f79515a17a93447bbd1516245d8cd6e35f`。
2. [合并时的原始验收记录](https://github.com/shenjee/stockpilot/blob/e754d9f79515a17a93447bbd1516245d8cd6e35f/docs/t0assistant/t0_054_acceptance.md)
   确实存在。该文件后来在
   [删除提交 f99d60e](https://github.com/shenjee/stockpilot/commit/f99d60ecec0e5d59060df5161eb15646a3c60ec2)
   中移除，所以现行文档改用固定提交链接，不凭空重建报告。
3. PR 记录了当时的 246 项测试、96 项 Python 回归，以及 1440×900、1512×982
   逻辑视口自动检查通过。这些是历史报告，不是 #191 重跑结果。
4. 原始报告和 PR 的两台物理设备手工清单均未勾选；报告还明确写了清单完成前不应关闭。
   **Issue 已关闭与所保存的手工验收证据不一致**：本次读取的 issue、PR 和历史报告
   无法证明物理设备验收完成，也不能证明当前版本已通过。保留该限制，不补写通过结论。
5. 当前可运行入口见 [App README](../apps/t0-assistant/README.md) 和
   [视口测试](../apps/t0-assistant/tests/target-viewport.electron.mjs)。打包和发布验收仍由
   [#87](https://github.com/shenjee/stockpilot/issues/87)、[#95](https://github.com/shenjee/stockpilot/issues/95)
   跟踪，文档修复不替代它们。

## #191 最终实现与文档复核

2026-10-04 在 `1d89eb1` 上核对审查基线 `6f3a9c6` 之后的变更。
#181–#188 均已关闭，相关修复均已在当前分支；因此本次不是提前于代码合并的文档 PR。
若后续实现变更，仍需重新核对受影响入口。

| 代码项 / 合并 PR | 复核来源与文案结果 |
| --- | --- |
| #181 / #193 | 财务 PIT 持久化与筛选；MVP §15.4.1 及 App README 已包含披露依据和历史可见性限制，本次无需另写规则 |
| #182 / #194 | sync outcome、CLI 与 App；MVP 和 App README 已区分空结果、部分失败、失败，本次不更改公共状态 |
| #183 / #195 | 债务映射与质量规则；现有 MVP 与 App README 已说明无可靠有息负债输入时不推导比率 |
| #184 / #196 | 共享包导入身份；根 README 和 import_compatibility 已记录兼容入口，保留已有 Skill 历史记录 |
| #185 / #197 | package.json、测试 gate 与 CI 工作流；根 README 补齐 T+0 入口并区分 npm smoke 与完整 CI，详细覆盖矩阵继续复用现有文档 |
| #186 / #198 | Python event publisher、Electron gateway、Renderer；现有契约和 App README 的重启提示保持一致，C4 补齐实际进程边界 |
| #187 / #199 | Chan normalize 与包 README；有限值和宽松时间戳规则已更新，本次无需改写 |
| #188 / #200 | quality 与历史可见性；MVP 已说明不产出 suspended，不把缺行情当停牌，本次无需改写 |

本次仅静态核对启动/测试入口与现有脚本、CI；未启动行情采集、GUI、部署或物理设备验收，
也未重跑上述业务修复的全套测试。Skill 专项文档、启动/测试和部署验证不纳入 #191。

## 可重复的本地 Markdown 目标检查

从仓库根目录运行：

```bash
source ~/.venvs/czsc/bin/activate
python docs/check_local_links.py
python -m unittest discover -s docs -p 'test_check_local_links.py'
git diff --check
```

[检查器](check_local_links.py)覆盖 Git 跟踪及未忽略的新 Markdown 文件，排除 `skills/`。
检查普通内联链接、图片和引用式链接定义；解析 URL 编码，允许目录目标。
相对路径以文档目录为基准，`/` 开头路径以仓库根为基准。外部 URL、协议相对 URL、
mailto 等协议不联网检查；纯锚点忽略，`file.md#anchor` 只检查文件存在性。
代码块、行内代码不是链接；HTML 链接、复杂嵌套 Markdown 和标题锚点有效性不在此检查范围内。
任一文件目标缺失即非零退出；这不是完整 Markdown 渲染或外链可用性验收。

修复前五个已知 Markdown 目标缺失：实现参考中的验收报告一处，以及 Electron Spike
README 中多越一级的 ADR/报告链接四处。App README 另有同一缺失报告的纯文本路径，
本次一并改为可追溯历史链接。修复后应无缺失文件目标。
