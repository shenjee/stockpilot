# Spike 索引

本文档用于维护 ADR 与验证报告、执行任务、PR 和结论之间的对应关系。

## 规则

- 主验证报告与 ADR 使用相同编号和 slug。
- 同一 ADR 的补充验证报告使用 `-01`、`-02` 等后缀。
- `spikes/` 目录保存原型代码，`docs/spikes/` 保存验证报告。
- 一个 Issue 可以产出多份 Spike 报告；一个 ADR 也可以累计多份验证证据。

## 当前列表

| ADR | Spike 状态 | 执行者 | Issue | PR | 结论 |
| --- | --- | --- | --- | --- | --- |
| 0005 | In Progress | Trae | #25 | — | Pending |
| 0006 | Planned | Claude | #26 | — | Pending |
| 0007 | Planned | Claude | #26 | — | Pending |
| 0008 | Planned | Codex | #27 | — | Pending |

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
