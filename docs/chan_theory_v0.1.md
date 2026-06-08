# Chan Theory v0.1

## 1. 文档目标

这份文档用于冻结 Stock Pilot Phase 2 的缠论口径与工程落地方式。

本版本不追求完整重写缠论核心算法，而是明确以下三件事：

- 我们在 Phase 2 采用什么缠论能力边界
- 我们如何使用开源 `czsc` 作为底层分析引擎
- 我们如何通过项目自己的 `chantheory` 适配层向 skills、agents、apps、调试工具暴露统一结果

这份文档同时承担三种作用：

- 口径对照文档：说明哪些能力直接沿用 `czsc`，哪些能力由项目补充约束
- 简化规则文档：说明 Phase 2 最终暴露哪些结构与输出
- 工程映射文档：说明这些规则如何落到 `chantheory`、Streamlit、skill 集成中

## 1.1 术语约定

本项目后续文档默认采用以下中英术语对照：

| 中文 | 推荐英文 |
| --- | --- |
| 缠论 | Chan Theory |
| 分型 | Fractal |
| 笔 | Stroke |
| 线段 | Segment |
| 中枢 | Pivot Zone |
| 走势类型 | Trend Structure |
| 背驰 | Divergence |
| 一买二买三买 | First/Second/Third Buy Point |
| 一卖二卖三卖 | First/Second/Third Sell Point |

说明：

- 上述英文术语用于产品文档、对外说明、UI 文案和后续英文设计文档
- 项目对外 schema 统一使用 `pivot_zones` 作为正式字段名
- 若后续 `czsc` 原生命名与本项目术语不同，以本项目术语表为对外口径

## 1.2 字段命名规范

`chantheory` 对外暴露的所有字段统一采用 `snake_case`。

命名规则：

- 顶层字段和嵌套字段都使用 `snake_case`
- 列表字段优先使用复数名词
- 单值标识字段优先使用简短名词，如 `symbol`、`timeframe`
- 版本和元信息字段使用带语义后缀的命名，如 `engine_version`
- 不再混用 `camelCase`、`PascalCase`、缩写不清晰的字段名

当前统一的顶层字段：

- `symbol`
- `timeframe`
- `engine`
- `engine_version`
- `fractals`
- `strokes`
- `segments`
- `pivot_zones`
- `divergences`
- `plot_primitives`
- `summary`
- `warnings`

保留字段命名约定：

- `structure_alerts`
- `candidate_buy_points`
- `candidate_sell_points`
- `first_buy_points`
- `second_buy_points`
- `third_buy_points`
- `first_sell_points`
- `second_sell_points`
- `third_sell_points`

## 2. Phase 2 基本原则

- Phase 2 的目标是回答“这只股票现在处于什么走势结构”
- 图形化输出是主输出，文字说明只是辅助输出
- 不在 `china-stock-daily-tracker` 的日报脚本中直接堆叠缠论核心逻辑
- 不从零自研 `分型 / 笔 / 线段 / 中枢` 全套核心算法
- 默认采用开源 `czsc` 作为底层缠论分析引擎
- 项目保留自己的 `chantheory` 适配层，统一输入、输出、图形数据和告警语义
- 上层 skill、agent、UI 不直接依赖 `czsc` 内部对象和版本细节

## 3. 总体架构

```text
本地K线库 / 市场数据
        ->
数据标准化与适配层（chantheory）
        ->
czsc
        ->
统一结果模型 / plot_primitives / summary / warnings
        ->
  ├─ china-stock-daily-tracker
  ├─ Streamlit debug app
  ├─ desktop / local app
  └─ agent / app integrations
```

职责划分如下：

- `czsc`
  负责底层缠论核心分析，包括分型、笔、中枢等核心对象与多周期能力
- `chantheory`
  负责输入标准化、参数约束、调用 `czsc`、结果清洗、统一 schema、图形数据输出、摘要和告警
- `china-stock-daily-tracker`
  负责业务编排、日报生成、结构摘要接入和配置管理
- `Streamlit`
  负责调试、验算、可视化检查和参数试验

## 4. 为什么采用 czsc

- `czsc` 已经具备成熟的缠论核心能力，不需要项目重复造轮子
- `czsc` 仍在持续维护，适合作为当前阶段的底层引擎
- `czsc` 同时覆盖缠论分析、多周期处理、信号、可视化相关能力，能降低 Phase 2 试错成本
- 直接自研分型、笔、线段、中枢会带来较高的测试、口径和维护成本
- 采用成熟开源引擎后，项目可以把精力集中在结果契约、产品输出和可视化验算上

## 5. 口径策略

Phase 2 的口径策略不是“完全照搬 `czsc` 的所有能力”，而是“以 `czsc` 为默认底层引擎，在项目侧冻结自己的对外口径”。

### 5.1 直接沿用的部分

- 以 `czsc` 作为默认结构识别引擎
- 以 `czsc` 的核心结构识别结果作为项目输出的主要来源
- 使用 `czsc` 已支持的多周期能力作为后续扩展基础

### 5.2 项目侧补充定义的部分

- 项目认可哪些 `czsc` 输出字段进入对外 schema
- 项目如何定义 `plot_primitives`
- 项目如何定义 `summary` 和 `warnings`
- 项目如何处理低置信度、结构未完成、数据不足等异常场景
- 项目如何保证不同上层消费者看到的结果一致

### 5.3 不直接暴露的部分

- 不直接把 `czsc` 原生对象透传给 skills 或 UI
- 不直接让上层依赖 `czsc` 内部类名、属性名和版本细节
- 不把 `czsc` 的全部能力原样包装成项目 API

## 6. Phase 2 对外能力边界

Phase 2 只暴露“足够支撑结构观察、图形验证和日报摘要”的能力，不一次性开放所有缠论细节。

### 6.1 Phase 2 结构范围

- `fractals`
- `strokes`
- `segments`
- `pivot_zones`
- `divergences`
- 多周期观察接口预留

### 6.2 Phase 2 输出范围

- `plot_primitives`
- `summary`
- `warnings`
- 结构变化提醒
- 潜在买卖点候选

### 6.3 Phase 2 非目标

- 不做完整桌面端产品
- 不做完整交易系统
- 不做全量信号函数开放
- 不承诺“唯一正确”的缠论口径

## 7. 统一输出契约

`chantheory` 对上层暴露统一结果模型，而不是直接返回 `czsc` 的原生对象。

建议的顶层输出：

```json
{
  "symbol": "000001.SZ",
  "timeframe": "day",
  "engine": "czsc",
  "engine_version": "pinned-version",
  "fractals": [],
  "strokes": [],
  "segments": [],
  "pivot_zones": [],
  "divergences": [],
  "structure_alerts": [],
  "candidate_buy_points": [],
  "candidate_sell_points": [],
  "plot_primitives": [],
  "summary": [],
  "warnings": []
}
```

字段说明：

- `fractals`
  对外统一的分型结果列表
- `strokes`
  对外统一的笔结果列表
- `segments`
  对外统一的线段结果列表
- `pivot_zones`
  对外统一的 Pivot Zone 结果列表
- `divergences`
  对外统一的背驰候选或背驰提示结果
- `structure_alerts`
  对外统一的结构变化提醒列表
- `candidate_buy_points`
  对外统一的潜在买点候选列表
- `candidate_sell_points`
  对外统一的潜在卖点候选列表
- `plot_primitives`
  UI 用的点、线、框、标签、颜色等绘图原语
- `summary`
  供日报和 agent 使用的简短结构摘要
- `warnings`
  数据不足、结构不稳定、转换异常、版本差异等告警

### 7.1 顶层字段最小要求

- `symbol`
  标的代码，建议使用稳定的市场代码格式，如 `000001.SZ`
- `timeframe`
  分析周期，如 `1m`、`5m`、`30m`、`day`、`week`
- `engine`
  当前底层引擎标识，Phase 2 默认值为 `czsc`
- `engine_version`
  当前分析结果对应的底层引擎版本

### 7.2 结构类字段最小子项

以下对象是字段级 schema 草案，用于冻结最小可用结构，不代表 Phase 2 一开始就要填满所有字段。

`fractals` 的建议子项：

```json
{
  "id": "fx_20240607_001",
  "fractal_type": "top",
  "bar_index": 123,
  "timestamp": "2024-06-07",
  "price": 12.34,
  "confirmed": true,
  "source": "czsc",
  "meta": {}
}
```

建议字段：

- `id`
- `fractal_type`
- `bar_index`
- `timestamp`
- `price`
- `confirmed`
- `source`
- `meta`

`strokes` 的建议子项：

```json
{
  "id": "stroke_20240607_001",
  "direction": "up",
  "start_fractal_id": "fx_20240601_001",
  "end_fractal_id": "fx_20240607_001",
  "start_timestamp": "2024-06-01",
  "end_timestamp": "2024-06-07",
  "start_price": 10.21,
  "end_price": 12.34,
  "confirmed": true,
  "meta": {}
}
```

建议字段：

- `id`
- `direction`
- `start_fractal_id`
- `end_fractal_id`
- `start_timestamp`
- `end_timestamp`
- `start_price`
- `end_price`
- `confirmed`
- `meta`

`segments` 的建议子项：

```json
{
  "id": "segment_20240607_001",
  "direction": "up",
  "stroke_ids": ["stroke_001", "stroke_002", "stroke_003"],
  "start_timestamp": "2024-05-20",
  "end_timestamp": "2024-06-07",
  "start_price": 9.88,
  "end_price": 12.34,
  "confirmed": false,
  "meta": {}
}
```

建议字段：

- `id`
- `direction`
- `stroke_ids`
- `start_timestamp`
- `end_timestamp`
- `start_price`
- `end_price`
- `confirmed`
- `meta`

`pivot_zones` 的建议子项：

```json
{
  "id": "pivot_zone_20240607_001",
  "start_timestamp": "2024-05-28",
  "end_timestamp": "2024-06-07",
  "high": 11.85,
  "low": 10.92,
  "segment_ids": ["segment_001", "segment_002", "segment_003"],
  "level": "segment",
  "active": true,
  "meta": {}
}
```

建议字段：

- `id`
- `start_timestamp`
- `end_timestamp`
- `high`
- `low`
- `segment_ids`
- `level`
- `active`
- `meta`

`divergences` 的建议子项：

```json
{
  "id": "divergence_20240607_001",
  "divergence_type": "bearish",
  "reference_type": "segment",
  "reference_id": "segment_001",
  "timestamp": "2024-06-07",
  "strength": "weak",
  "confirmed": false,
  "description": "price makes a new high while momentum weakens",
  "meta": {}
}
```

建议字段：

- `id`
- `divergence_type`
- `reference_type`
- `reference_id`
- `timestamp`
- `strength`
- `confirmed`
- `description`
- `meta`

### 7.3 提醒、买卖点与告警字段

`structure_alerts` 的建议子项：

```json
{
  "id": "alert_20240607_001",
  "alert_type": "new_pivot_zone",
  "severity": "info",
  "timestamp": "2024-06-07",
  "related_ids": ["pivot_zone_001"],
  "message": "new Pivot Zone formed",
  "meta": {}
}
```

`candidate_buy_points` 的建议子项：

```json
{
  "id": "buy_point_20240607_001",
  "point_type": "first_buy_point",
  "timestamp": "2024-06-07",
  "price": 10.98,
  "reference_id": "pivot_zone_001",
  "confirmed": false,
  "reason": "price returns to Pivot Zone edge with supportive structure",
  "meta": {}
}
```

`candidate_sell_points` 的建议子项：

```json
{
  "id": "sell_point_20240607_001",
  "point_type": "first_sell_point",
  "timestamp": "2024-06-07",
  "price": 12.26,
  "reference_id": "segment_001",
  "confirmed": false,
  "reason": "structure weakens near prior high",
  "meta": {}
}
```

`warnings` 的建议子项：

```json
{
  "id": "warning_20240607_001",
  "warning_code": "INSUFFICIENT_BARS",
  "severity": "warning",
  "message": "not enough bars to stabilize Pivot Zone detection",
  "field": "pivot_zones",
  "meta": {}
}
```

### 7.4 summary 字段建议

`summary` 建议采用短句数组，而不是一整段长文本。

示例：

```json
[
  "current Trend Structure is still upward",
  "a new Pivot Zone is forming",
  "First Buy Point is not confirmed yet"
]
```

### 7.5 字段设计原则

- 同一类对象必须包含稳定的 `id`
- 所有时间字段统一使用 `timestamp`、`start_timestamp`、`end_timestamp`
- 所有方向字段统一使用 `direction`
- 所有确认态字段统一使用 `confirmed`
- 所有扩展信息统一放入 `meta`
- 字段设计优先支持 skill、debug UI 和未来 desktop app 共享
- 不把 `czsc` 原生字段名直接暴露为最终公开 schema

## 8. plot_primitives 约定

图形化结果优先，因此 `plot_primitives` 是 Phase 2 的核心契约之一。

建议支持以下原语：

- `point`
- `line`
- `box`
- `label`
- `marker`

建议字段：

- `type`
- `id`
- `layer`
- `x`
- `y`
- `x1`
- `y1`
- `x2`
- `y2`
- `style`
- `color`
- `text`
- `meta`

`plot_primitives` 的建议子项：

```json
{
  "id": "primitive_20240607_001",
  "type": "line",
  "layer": "stroke",
  "x1": "2024-06-01",
  "y1": 10.21,
  "x2": "2024-06-07",
  "y2": 12.34,
  "color": "#FF6B6B",
  "style": "solid",
  "text": "",
  "meta": {
    "reference_id": "stroke_20240607_001"
  }
}
```

建议图层顺序：

1. K线
2. 分型点
3. 笔
4. 线段
5. 中枢框
6. 背驰或告警标注

## 9. chantheory 工程映射

建议目录：

```text
packages/chantheory/
├── __init__.py
├── normalize.py
├── adapters.py
├── schema.py
├── describe.py
├── plotting.py
└── config.py
```

模块职责：

- `normalize.py`
  把项目内 K 线数据标准化为适合进入 `czsc` 的输入格式
- `adapters.py`
  封装 `czsc` 调用细节，并把结果映射为项目统一结构
- `schema.py`
  定义项目对外暴露的数据结构和序列化格式
- `describe.py`
  基于统一结构生成简短摘要和告警文案
- `plotting.py`
  生成 `plot_primitives`
- `config.py`
  管理 `czsc` 版本锁定、参数默认值和兼容性开关

## 10. 与 china-stock-daily-tracker 的集成方式

推荐流程：

```text
加载本地K线
    ->
标准化输入
    ->
调用 chantheory
    ->
内部由 chantheory 调用 czsc
    ->
返回统一结构结果
    ->
日报输出 summary / warnings / 结构变化提醒
```

集成原则：

- `china-stock-daily-tracker` 不直接操作 `czsc` 原生对象
- skill 层只消费 `chantheory` 的稳定结果
- skill 失败时要能优雅降级，不因为缠论失败而中断整份日报

## 11. Streamlit 调试界面定位

Streamlit 的作用不是替代正式产品 UI，而是作为 Phase 2 的调试与验算工具。

Phase 2 中，Streamlit 应承担：

- 验证 `plot_primitives` 是否正确
- 检查分型、笔、线段、中枢叠加是否符合预期
- 对比不同参数下的结构变化
- 检查 `summary` 与图形结果是否一致
- 暴露 `warnings` 和原始结构 JSON 便于排查问题

## 12. 风险与约束

- `czsc` 版本仍可能持续演进，需要锁定版本并记录兼容性假设
- `czsc` 的内部对象和命名不应直接成为上层长期依赖
- A 股本地数据格式、复权方式、周期定义需要先做兼容性验证
- 多周期结果和背驰判断在 Phase 2 应保持保守，不追求一步到位
- 图形结果、摘要结果、日报结果必须来自同一套统一输出，避免口径分裂

## 13. 本版本的结论

- Phase 2 采用 `czsc` 作为默认底层缠论引擎
- 项目不从零自研完整缠论核心算法
- 项目必须保留自己的 `chantheory` 适配层
- 图形化结果是主输出，文字是辅助输出
- Streamlit 是调试与验算界面，不是长期正式宿主
- `china-stock-daily-tracker` 通过 `chantheory` 接入缠论结果，而不是直接依赖 `czsc`

## 14. 后续待定问题

- 具体锁定哪个 `czsc` 版本
- `divergences` 在 Phase 2 采用多保守的定义
- 是否在 Phase 2 首版就暴露多周期联立结果
- `plot_primitives` 是否需要兼容未来 desktop app 的交互特性
- 哪些结构变化需要进入日报提醒，哪些只保留在调试界面
