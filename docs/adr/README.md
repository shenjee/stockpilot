# StockPilot Architecture Decision Records

本目录记录 StockPilot 已接受、待验证、被拒绝或已被替代的架构决策。
ADR 只固化具有长期影响的技术选择；实现细节、短期任务进度和验证原始数据
分别放在模块技术方案、任务和 Spike 报告中。

## 状态约定

| 状态 | 含义 |
| --- | --- |
| `Proposed` | 已明确问题、候选方案和所需证据，尚未作最终决定 |
| `Accepted` | 已获得足够证据并确认采用 |
| `Rejected` | 已评估但决定不采用；保留记录以避免重复讨论 |
| `Superseded` | 已被后续 ADR 替代；旧 ADR 中必须指向替代它的 ADR |

`Proposed` ADR 中的“当前倾向”不是最终决定。相关 Spike 完成后，应把证据摘要和
报告路径写回 ADR，再将状态更新为 `Accepted` 或 `Rejected`。如果决策问题本身被
重新定义，则使用新的 ADR 替代原 ADR，而不是覆盖历史。

## 索引

| ADR | 状态 | 决策范围 | 验证依赖 |
| --- | --- | --- | --- |
| [0001](./0001-modular-monolith.md) | Accepted | 默认采用模块化单体 | 无 |
| [0002](./0002-packages-own-domain-logic.md) | Accepted | 可复用领域逻辑归属 `packages/` | 无 |
| [0003](./0003-local-sqlite-runtime-boundary.md) | Accepted | 本地 SQLite 与运行时目录边界 | 无 |
| [0004](./0004-schema-first-analysis-contracts.md) | Accepted | Schema-first 分析契约 | 无 |
| [0005](./0005-t0-chart-engine-and-logical-time-axis.md) | Accepted | T+0 图表引擎与逻辑时间轴能力 | 图表 Spike |
| [0006](./0006-electron-managed-python-process.md) | Accepted | Electron 管理 Python 服务生命周期 | Electron/Python Spike |
| [0007](./0007-local-python-transport.md) | Accepted | Electron 与 Python 的本地请求/事件传输 | Electron/Python Spike；依赖 0006 的进程边界 |
| [0008](./0008-czsc-update-and-rebuild-strategy.md) | Accepted | 5 分钟 CZSC 使用 full project-level rebuild | 发布前修复项目环境并执行性能回归 |

## T+0 Assistant 决策顺序

```text
0005 图表能力验证 ───────────────┐
                                 ├─> 前后端接口契约与模块技术方案
0006 Python 进程模型 ─> 0007 传输 ┤
                                 │
0008 CZSC 推进与重建 ────────────┘
```

以下事项已经由产品与架构基线约束，不应在 Spike 中被悄然改写：

- Electron Renderer 不直接访问 Python、SQLite 或 Node.js 通用能力；
- Python 是行情加工、指标与 CZSC 结果的权威来源；
- Live 与 Replay 共用处理管线实现，但不共享可变实例状态；
- 回放结果只能由目标时点及以前的数据产生；
- 可见窗口是前端视口，不是指标或 CZSC 的计算边界。

## 编写规则

1. 使用四位递增编号；编号一旦分配不复用。
2. 至少包含 Context、Decision Drivers、Options、Current Direction、
   Validation Required、Decision Outcome 和 Consequences。
3. `Proposed` 阶段在 Decision Outcome 中明确写“Pending”，避免当前倾向被误读为
   已接受决策。
4. 验证任务必须产出可复现原型、测试或测量数据，不能只提交主观结论。
5. ADR 状态变更与证据回填应在同一个可审查变更中完成。
