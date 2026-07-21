# StockPilot Spike 验证索引

本目录记录为 ADR 提供可复现证据的 Spike 验证报告。每份报告与对应 ADR 共用编号，
便于从决策直接定位证据。

## 编号与命名约定

- Spike 报告与对应 ADR 使用相同编号：`docs/spikes/<编号>-<adr-slug>.md`，
  例如 [`0006-electron-managed-python-process.md`](./0006-electron-managed-python-process.md)。
- 主验证报告与 ADR 同名；补充验证在编号后追加 `-01`、`-02`，
  例如 `0005-01-macbook-air-performance.md`。ADR 的 `Evidence` 部分列出主报告与补充报告。
- Spike 原型代码位于仓库根 `spikes/`，目录命名与报告对应；关联紧密的多 ADR 可共用一个
  原型目录，例如 `spikes/0006-0007-electron-python/`，但报告仍按 ADR 拆分。
- ADR 状态变更与证据回填由 ADR 所有者在同一可审查变更中完成；Spike 执行者只产出报告与
  推荐，不直接修改 ADR（见 [`docs/adr/README.md`](../adr/README.md) 编写规则）。

## 索引

| ADR | Spike 状态 | 执行者 | Issue | PR | 结论 |
| --- | --- | --- | --- | --- | --- |
| [0005](../adr/0005-t0-chart-engine-and-logical-time-axis.md) | In Progress | Trae | [#25](https://github.com/shenjee/stockpilot/issues/25) | - | Pending |
| [0006](../adr/0006-electron-managed-python-process.md) | Reported | Claude | [#26](https://github.com/shenjee/stockpilot/issues/26) | [#30](https://github.com/shenjee/stockpilot/pull/30) | Continue investigation (lifecycle OK; real packaging is decision-blocking) |
| [0007](../adr/0007-local-python-transport.md) | Reported | Claude | [#26](https://github.com/shenjee/stockpilot/issues/26) | [#30](https://github.com/shenjee/stockpilot/pull/30) | Accept (after rev 2 review fixes) |
| [0008](../adr/0008-czsc-update-and-rebuild-strategy.md) | Planned | Codex | [#27](https://github.com/shenjee/stockpilot/issues/27) | - | Pending |

## 状态约定

| 状态 | 含义 |
| --- | --- |
| Planned | Issue 已创建，Spike 尚未开始 |
| In Progress | 原型或测试正在开发或验证中 |
| Reported | 验证报告已提交，ADR 尚未回填结论 |
| Accepted | 对应 ADR 已采纳 Spike 结论 |
| Rejected | 对应 ADR 拒绝该 Spike 结论 |
| Superseded | 由后续补充验证替代 |

各执行者维护自己所属行的 Spike 状态、PR 与结论字段；ADR 维护者负责把证据摘要回填到
ADR 并更新 ADR 状态。
