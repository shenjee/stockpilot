# Spike 索引

本文档用于维护 ADR 与验证报告、执行任务、PR 和结论之间的对应关系。

## 规则

- 主验证报告与 ADR 使用相同编号和 slug。
- 同一 ADR 的补充验证报告使用 `-01`、`-02` 等后缀。
- `spikes/` 目录保存原型代码，`docs/spikes/` 保存验证报告。
- 一个 Issue 可以产出多份 Spike 报告；一个 ADR 也可以累计多份验证证据。

## 当前列表

| ADR | Spike | 状态 | 执行者 | Issue | PR | 建议或决策 |
| --- | --- | --- | --- | --- | --- | --- |
| 0005 | [图表引擎与逻辑时间轴](./0005-t0-chart-engine-and-logical-time-axis.md) | Completed | Trae | [#25](https://github.com/shenjee/stockpilot/issues/25) | [Draft PR #31](https://github.com/shenjee/stockpilot/pull/31) | 建议采用 Lightweight Charts 4.x、项目自有逻辑索引和显式状态机 |
| 0006 | Electron 管理 Python 进程 | In Progress | Claude | [#26](https://github.com/shenjee/stockpilot/issues/26) | [Draft PR #30](https://github.com/shenjee/stockpilot/pull/30) | Pending |
| 0007 | 本地 Python 通信 | In Progress | Claude | [#26](https://github.com/shenjee/stockpilot/issues/26) | [Draft PR #30](https://github.com/shenjee/stockpilot/pull/30) | Pending |
| 0008 | [CZSC 更新与重建策略](./0008-czsc-update-and-rebuild-strategy.md) | Accepted | Codex | [#27](https://github.com/shenjee/stockpilot/issues/27) | [PR #29](https://github.com/shenjee/stockpilot/pull/29) | MVP 使用 full rebuild，不引入增量适配器 |

## 命名示例

```text
docs/spikes/
├── 0005-t0-chart-engine-and-logical-time-axis.md
├── 0005-01-macbook-air-performance.md
└── 0005-02-library-upgrade-regression.md

spikes/
├── 0005-t0-chart-engine-and-logical-time-axis/
├── 0006-electron-managed-python-process/
├── 0007-local-python-transport/
└── 0008-czsc-update-and-rebuild-strategy/
```
