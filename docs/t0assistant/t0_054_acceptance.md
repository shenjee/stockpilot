# T0-054 FR-06、图表交互和目标设备验收记录

本文记录 Issue #86（T0-054）的可重复验收证据。冻结需求仍以
[`t0_assistant_prd.md`](./t0_assistant_prd.md)、
[`ui_layout_spec.md`](./ui_layout_spec.md) 和
[`development_backlog.md`](./development_backlog.md) 为准；本文不扩展产品范围。

## 1. 自动化验收

在仓库根目录使用 `~/.venvs/czsc`：

```bash
source ~/.venvs/czsc/bin/activate

cd apps/t0-assistant
npm run typecheck
npm test
npm run acceptance:target-viewports

cd ../..
python -m unittest \
  packages.t0assistant.tests.test_runtime_market_processing \
  packages.t0assistant.tests.test_workbench_pipeline \
  packages.t0assistant.tests.test_replay_data \
  packages.t0assistant.tests.test_replay_e2e_acceptance \
  packages.t0assistant.tests.test_replay_seek_and_simulated_trades
```

`acceptance:target-viewports` 使用生产 Vite 构建、沙箱化 preload 和隐藏的 Electron
窗口，不使用 fixture-mode 回退。它在以下 macOS 默认缩放逻辑视口逐一验证：

| 目标设备基线 | 逻辑视口 | 自动检查 |
| --- | --- | --- |
| 13 英寸 MacBook Air M1 | 1440 × 900 | 64/36、50/50、隐藏分时、三行对齐、280px 行情栏、回放控制区、成交 Drawer 覆盖、无横向滚动 |
| 14 英寸 MacBook Pro M3 | 1512 × 982 | 同上 |

自动化视口测试不能替代物理屏幕上的可读性、鼠标缩放、拖动和十字光标验收。

## 2. FR-06 强制检查

| 检查项 | 结果 | 证据 |
| --- | --- | --- |
| 不输出买入、卖出、数量、仓位或“能不能做 T”的建议 | 通过代码审查 | Renderer 只呈现行情、CZSC 原始标签和用户手工成交事实；生产源码不存在上述建议文案 |
| 不增加趋势判断、信号处理状态机、指标解释卡或未经验证的自定义策略 | 通过代码审查 | 图表直接消费后端指标/CZSC 投影；本 Issue 未增加分析逻辑 |
| 不读取券商账户、持仓、现金或可卖数量，不连接自动下单 | 通过代码审查 | Safe Bridge 命令表只包含行情、回放、偏好和手工成交能力 |
| 不自动配对成交，不计算盈亏、胜率、收益率或资金曲线 | 通过代码审查 | `packages/t0assistant/trading/` 与 Renderer 仅维护独立成交事实和手续费 |
| 不提供“跳转到下一个 CZSC 买卖点” | 通过 | `apps/t0-assistant/tests/replay-controls.test.mjs` 固定播放、单步、倍速和进度定位控制面 |
| CZSC 标签只是当前结构结果，不保存首次出现快照或“失效”状态 | 通过 | `apps/t0-assistant/tests/chart-projection.test.mjs` 验证完整快照替换和修订门禁 |
| 动态未闭合 5 分钟 K 不进入 CZSC | 通过 | `test_workbench_pipeline.py::test_dynamic_5m_does_not_enter_indicators_or_czsc`、`test_runtime_market_processing.py::test_official_bar_replaces_dynamic_and_is_only_analysis_input` |
| Replay 输出不读取目标时点之后的数据 | 通过 | `test_replay_e2e_acceptance.py::test_one_minute_flow_prepares_once_rebuilds_and_never_reads_future`、`::test_backward_seek_removes_every_future_prefix_from_prior_snapshot` |
| Replay 模拟成交不进入真实成交仓储 | 通过 | `test_replay_seek_and_simulated_trades.py::test_simulated_trade_never_writes_app_sqlite` |
| 缺失行情不生成虚假 K 线，也不改写市场规定的回放结束时间 | 通过 | `test_runtime_market_processing.py::test_lunch_is_skipped_without_placeholder_bars`、`test_replay_data.py::test_end_time_still_from_calendar_in_fallback` |

## 3. 物理设备手工验收

每台设备使用系统默认显示缩放和正式 production build。验证前记录 macOS 版本、
Electron 版本、逻辑视口和 App 窗口尺寸。

### 13 英寸 MacBook Air M1

- [ ] 顶部选股、股票名称和实盘/回放按钮完整可用。
- [ ] 三种布局均无横向滚动，行情栏字段完整可读。
- [ ] 5 分钟组内价格/VOL/MACD 的缩放、拖动和十字光标同步。
- [ ] 1 分钟组内价格/VOL/MACD 的缩放、拖动和十字光标同步。
- [ ] 操作一个图表组不会带动另一个图表组。
- [ ] 手工浏览后切换布局、刷新和展开/收起成交栏不会跳回最新端。
- [ ] 回到最新边缘后恢复跟随最新，布局变化重新按宽度满轴。
- [ ] 回放控制区完整可操作，成交 Drawer 不压缩或重建图表。

### 14 英寸 MacBook Pro M3

- [ ] 顶部选股、股票名称和实盘/回放按钮完整可用。
- [ ] 三种布局均无横向滚动，行情栏字段完整可读。
- [ ] 5 分钟组内价格/VOL/MACD 的缩放、拖动和十字光标同步。
- [ ] 1 分钟组内价格/VOL/MACD 的缩放、拖动和十字光标同步。
- [ ] 操作一个图表组不会带动另一个图表组。
- [ ] 手工浏览后切换布局、刷新和展开/收起成交栏不会跳回最新端。
- [ ] 回到最新边缘后恢复跟随最新，布局变化重新按宽度满轴。
- [ ] 回放控制区完整可操作，成交 Drawer 不压缩或重建图表。

## 4. 已知边界

- 逻辑视口自动化验证布局几何和状态切换，不判断文字的主观可读性。
- Lightweight Charts 的逻辑范围适配由
  `chart-viewport-lc.test.mjs` 使用真实图表库验证；Canvas 十字光标视觉对齐仍需
  物理设备手工确认。
- 物理设备两组清单全部完成前，T0-054 不应关闭。
