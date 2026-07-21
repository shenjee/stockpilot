# StockPilot T+0 历史行情回放接口与行为约定

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 状态 | 历史行情回放开发基线 |
| 版本 | v0.9 |
| 更新日期 | 2026-07-21 |
| 范围 | 单只股票、单个交易日、只读 Replay |
| 上位需求 | [`t0_assistant_prd.md`](./t0_assistant_prd.md) |
| 架构 | [`architecture.md`](./architecture.md) |
| 相关 ADR | ADR 0004、0005、0006、0007、0008 |

## 2. 目的和边界

本文档明确历史行情回放功能所需的逻辑接口和行为规则，使 Python、Electron
main/preload 和 React 可以独立实现并用同一组确定性 fixture 验收。它定义领域命令、
事件信封、完整工作台快照、错误和失效规则，不把 HTTP 路径、WebSocket 地址、临时
凭据或 Electron IPC 名称暴露为 React 契约。

本功能包含：

- 选择一只标准证券和一个历史交易日；
- 创建一次性 Replay Session；
- 加载开盘前预热数据和目标日行情；
- 开始、暂停、单步、前后定位和结束回放；
- 生成 5 分钟价格/VOL/MACD、1 分钟分时/VOL/MACD、动态日 K 和 CZSC；
- 通过完整快照建立或替换前端状态；
- 验证向后定位和并发定位不会泄漏未来数据。

本功能暂不包含实时行情、真实/模拟成交、收费方案、偏好持久化、后台预取和生产级
增量事件。后续能力只能扩展本契约，不能绕过 Session、revision
或完整快照规则。

## 3. 通用约定

- 所有公开字段使用 `snake_case`。
- `schema_version` 固定为 `t0_replay_v1`；不兼容变更必须提升版本。
- `request_id` 和 `session_id` 是非空、不透明字符串，调用方不得解析。
- `operation_id` 存在时必须是非空、不透明字符串；与具体操作无关的状态或
  快照重新基线事件省略该字段。
- `service_generation` 是 Electron 每次启动或重启 Python 服务时递增的正整数。
- `revision` 在一个 `service_generation + session_id` 范围内从零开始单调递增。
- 交易日期使用 `YYYY-MM-DD`；盘中时间戳使用上海市场本地时间
  `YYYY-MM-DD HH:MM:SS`，并在快照中显式携带 `timezone: Asia/Shanghai`。
- 数量和 revision 使用整数；价格、成交量、成交额和指标值使用 JSON number；缺失值
  使用 `null`，不使用空字符串或 `NaN`。
- 数组按市场时间升序排列；相同时间戳在进入管线前完成去重。
- 标准 1 分钟和 5 分钟 K 的 `timestamp` 表示该 K 线的闭合时刻；Provider 使用起始
  时刻时，必须在进入共享管线前完成标准化。
- Lightweight Charts 的逻辑索引由前端根据有序数组生成，不进入后端 Schema。
- `chantheory` 内容直接使用其稳定 `AnalysisResult.to_dict()` 输出，不暴露原始 `czsc`
  对象。
- 每个对外 JSON 对象都必须在示例旁提供字段表，说明字段类型、是否必填和实际含义；
  后续新增字段时必须同步更新字段表。

### 3.1 通用身份字段

这些字段会在多个请求、返回结果和事件中重复出现：

| 字段 | 类型 | 谁生成 | 含义 |
| --- | --- | --- | --- |
| `schema_version` | string | Python | 当前消息遵循的数据结构版本；固定为 `t0_replay_v1`，不是软件版本号。 |
| `request_id` | string | React | 一次方法调用的唯一请求编号。每调用一次都生成新值，用于把同步结果或错误对应到原请求。 |
| `service_generation` | integer | Electron main | Python 服务启动代次。首次启动为 1，每次重启递增；用于识别并丢弃旧服务的消息。 |
| `session_id` | string | Python | 一整场历史行情回放的唯一编号。从开始回放到结束保持不变；重新开始回放会获得新编号。 |
| `operation_id` | string | Python | 一次已被接受、需要后台继续执行的操作编号，例如加载、单步或定位。一场回放可以有多个操作。 |
| `revision` | integer | Python | 当前回放状态的修订序号。在同一 `service_generation + session_id` 内单调递增，用于排序和丢弃旧事件。 |

`request_id` 标识“这一次调用”，`operation_id` 标识“调用后启动的后台操作”，
`session_id` 标识“这些调用和操作所属的整场回放”。不是每次请求都会创建后台操作，
因此每个请求都有 `request_id`，但只有需要后台处理的请求才有 `operation_id`。

## 4. Renderer 安全桥

历史行情回放功能向 React 暴露以下方法：

| 方法 | 输入 | 成功结果 |
| --- | --- | --- |
| `select_symbol` | `request_id`, `symbol` | 标准证券身份 |
| `begin_replay` | `request_id`, `symbol`, `trade_date` | `session_id`, `operation_id` |
| `set_replay_playback` | `request_id`, `session_id`, `playing` | 接受确认 |
| `step_replay` | `request_id`, `session_id` | `operation_id` |
| `seek_replay` | `request_id`, `session_id`, `target_time` | `operation_id` |
| `end_replay` | `request_id`, `session_id` | 退休确认 |
| `get_replay_snapshot` | `request_id`, `session_id` | 完整工作台快照 |

方法输入字段说明：

| 字段 | 类型 | 是否必填 | 含义 |
| --- | --- | --- | --- |
| `request_id` | string | 是 | 本次方法调用的唯一编号；每次调用都必须使用新值。 |
| `symbol` | string | `select_symbol`、`begin_replay` 必填 | 用户输入或已经标准化的证券标识，例如 `600000`、`sh.600000`。 |
| `trade_date` | string | `begin_replay` 必填 | 要回放的交易日，格式为 `YYYY-MM-DD`。 |
| `session_id` | string | 除选股和开始回放外必填 | 要操作的回放编号。 |
| `playing` | boolean | `set_replay_playback` 必填 | `true` 表示开始或继续播放，`false` 表示暂停。 |
| `target_time` | string | `seek_replay` 必填 | 要定位到的上海市场时间，格式为 `YYYY-MM-DD HH:MM:SS`。 |

安全桥只允许订阅 `on_service_status`、`on_replay_event` 和
`on_replay_snapshot`。它不暴露 URL、端口、凭据、HTTP 动词、任意 `invoke`、文件系统
或子进程句柄。

命令成功只表示服务接受了操作；涉及加载、计算或定位的最终结果通过完整快照或
结构化错误返回。同步接受结果统一包含：

```json
{
  "request_id": "opaque-request-id",
  "service_generation": 1,
  "session_id": "opaque-session-id",
  "operation_id": "opaque-operation-id"
}
```

不适用的 `session_id` 或 `operation_id` 字段可以省略。

同步接受结果字段说明：

| 字段 | 类型 | 是否必填 | 含义 |
| --- | --- | --- | --- |
| `request_id` | string | 是 | 原样返回调用方传入的请求编号。 |
| `service_generation` | integer | 是 | 接受该请求的 Python 服务启动代次。 |
| `session_id` | string | 视命令而定 | 本次命令所属或新建的回放编号；与回放无关时省略。 |
| `operation_id` | string | 视命令而定 | 该命令启动的后台操作编号；没有后台操作时省略。 |

命令交付结果分为三类：

1. **同步接受**：返回上方成功结果；需要后台工作的命令同时返回
   `operation_id`。
2. **同步拒绝**：命令没有进入后台执行，HTTP/本地请求响应直接返回第 8 节错误
   payload，不创建 `operation_id`，也不发布 `operation_failed` 事件。
3. **异步失败**：命令已经同步接受，后台操作随后失败；通过 `operation_failed`
   事件发布第 8 节错误 payload，并携带原 `operation_id`。

同步拒绝和异步失败共用同一错误 Schema。HTTP 状态码和 Electron IPC 映射属于传输
适配细节，不进入 Renderer 领域契约；React 根据同步方法的 rejected result 或
`operation_failed` 事件进入同一错误处理路径。

### 4.1 标准证券身份

`select_symbol` 成功结果冻结为：

```json
{
  "request_id": "opaque-request-id",
  "service_generation": 1,
  "security": {
    "symbol": "sh.600000",
    "code": "600000",
    "market": "sh",
    "name": "浦发银行",
    "security_type": "a_share"
  }
}
```

返回字段说明：

| 字段 | 类型 | 是否必填 | 含义 |
| --- | --- | --- | --- |
| `request_id` | string | 是 | 原样返回本次 `select_symbol` 的请求编号。 |
| `service_generation` | integer | 是 | 处理本次选股请求的 Python 服务启动代次。 |
| `security` | object | 是 | 标准化后的证券身份，前端后续不再解析原始输入。 |
| `security.symbol` | string | 是 | 项目统一证券标识，由市场和代码组成，例如 `sh.600000`。 |
| `security.code` | string | 是 | 不带市场前缀的证券代码，例如 `600000`。 |
| `security.market` | string | 是 | 交易市场；当前只允许 `sh` 或 `sz`。 |
| `security.name` | string | 是 | 用于界面显示的证券名称。 |
| `security.security_type` | string | 是 | 证券类型；当前只允许 `a_share` 或 `etf`。 |

`symbol` 是项目标准证券标识；`market` 只允许 `sh` 或 `sz`；当前版本的
`security_type` 只允许 `a_share` 或 `etf`。React 使用该对象显示工具栏，不解析用户
原始输入或 Provider 字段。

`select_symbol` 是同步操作，不创建后台操作，因此成功结果不包含
`operation_id`。

## 5. 事件信封

所有 Replay 事件使用同一信封：

```json
{
  "schema_version": "t0_replay_v1",
  "service_generation": 1,
  "session_id": "opaque-session-id",
  "revision": 7,
  "event_type": "workbench_snapshot",
  "operation_id": "opaque-operation-id",
  "payload": {}
}
```

事件外层字段说明：

| 字段 | 类型 | 是否必填 | 含义 |
| --- | --- | --- | --- |
| `schema_version` | string | 是 | 事件数据结构版本，当前固定为 `t0_replay_v1`。 |
| `service_generation` | integer | 是 | 发布事件的 Python 服务启动代次。与当前代次不一致的事件必须丢弃。 |
| `session_id` | string | 是 | 事件所属的回放编号。 |
| `revision` | integer | 是 | 事件在本场回放中的修订序号，用于保证处理顺序。 |
| `event_type` | string | 是 | 事件种类，决定 `payload` 使用哪一种结构。 |
| `operation_id` | string | 否 | 产生该事件的后台操作编号；与具体操作无关时省略。 |
| `payload` | object | 是 | 事件内容；具体字段由 `event_type` 决定。 |

`operation_id` 是否存在由事件是否是某个后台操作的结果决定：

| `event_type` | `operation_id` 规则 |
| --- | --- |
| `session_status` | 由 `begin_replay` 加载、`step_replay` 或 `seek_replay` 引起的状态携带对应编号；播放、暂停、退休或服务自身状态省略。 |
| `workbench_snapshot` | 作为 `begin_replay`、`step_replay` 或 `seek_replay` 结果发布时必须携带；由 `get_replay_snapshot` 或重新建立基线获得时省略。 |
| `operation_failed` | 必须携带失败后台操作的编号。 |

历史行情回放功能允许以下 `event_type`：

- `session_status`：`loading`、`ready`、`playing`、`paused`、`failed`、`retired`；
- `workbench_snapshot`：一个 revision 的完整、原子工作台状态；
- `operation_failed`：与 `operation_id` 关联的稳定应用错误。

`session_status.payload` 使用下方状态字段，`workbench_snapshot.payload` 使用第 6 节
完整快照，`operation_failed.payload` 使用第 8 节错误字段。

`session_status.payload` 的最小结构为：

```json
{
  "state": "playing",
  "reason": "user_command"
}
```

`session_status.payload` 字段说明：

| 字段 | 类型 | 是否必填 | 含义 |
| --- | --- | --- | --- |
| `state` | string | 是 | 回放当前状态：`loading`、`ready`、`playing`、`paused`、`failed` 或 `retired`。 |
| `reason` | string | 否 | 进入当前状态的机器可读原因，不承载面向用户的错误说明。 |

`reason` 允许以下值：

- `session_created`：Session 已创建；
- `load_started`、`load_completed`：加载开始或完成；
- `user_command`：用户播放、暂停或结束；
- `step_completed`、`seek_completed`：游标操作完成；
- `operation_failed`：后台操作失败。

不适用时省略 `reason`，不得发送未登记的自由文本值；用户可读说明进入错误或 warning
的 `message`。

前端先比较 `service_generation`，再比较 `session_id`，最后比较 `revision`。任一身份
不匹配，或 `revision <= current_revision`，事件都必须丢弃。连接未断但收到
`revision > current_revision + 1` 时，说明事件序号不连续；前端必须停止应用后续事件，
调用 `get_replay_snapshot` 获取完整状态，再从该快照的 revision 继续。WebSocket 断开
并重连后同样先获取完整快照，不续传断线期间的事件。

`session_status` 用于立即更新前端的 Session 状态和 `playing` 投影，不改变行情、指标
或 CZSC 数据。`workbench_snapshot` 是其 revision 时点的完整权威状态。所有事件共享
同一 revision 序列，因此前端始终采用已接受的最高 revision：较新的快照覆盖此前
状态事件，较旧的状态事件不得覆盖新快照；同一 Session 不得发布两个相同 revision
但内容不同的事件。

## 6. 完整工作台快照

`workbench_snapshot.payload` 至少包含：

```json
{
  "timezone": "Asia/Shanghai",
  "session": {
    "session_id": "opaque-session-id",
    "session_type": "replay",
    "symbol": "sh.600000",
    "trade_date": "2026-07-01",
    "state": "paused",
    "revision": 7
  },
  "replay": {
    "granularity": "one_minute",
    "current_time": "2026-07-01 10:23:00",
    "next_bar_time": "2026-07-01 10:24:00",
    "start_time": "2026-07-01 09:30:00",
    "end_time": "2026-07-01 15:00:00",
    "playing": false,
    "step_seconds": 60
  },
  "market": {
    "bars_1m": [],
    "bars_5m": [],
    "daily_bars": [],
    "quote": null
  },
  "indicators": {
    "five_minute": {
      "ma": {
        "ma5": [{"timestamp": "2026-07-01 10:20:00", "value": 10.25}],
        "ma10": [],
        "ma20": [],
        "ma30": [],
        "ma60": []
      },
      "boll": {
        "period": 20,
        "stddev": 2.0,
        "upper": [],
        "middle": [],
        "lower": []
      },
      "volume": {
        "values": [],
        "ma5": [],
        "ma10": []
      },
      "macd": {
        "fast_period": 12,
        "slow_period": 26,
        "signal_period": 9,
        "dif": [],
        "dea": [],
        "histogram": []
      }
    },
    "one_minute": {
      "vwap": [],
      "volume": {"values": []},
      "macd": {
        "fast_period": 12,
        "slow_period": 26,
        "signal_period": 9,
        "dif": [],
        "dea": [],
        "histogram": []
      }
    }
  },
  "chan_analysis": {},
  "warnings": []
}
```

快照最外层字段说明：

| 字段 | 类型 | 是否必填 | 含义 |
| --- | --- | --- | --- |
| `timezone` | string | 是 | 快照中所有盘中时间使用的时区，固定为 `Asia/Shanghai`。 |
| `session` | object | 是 | 这场回放的身份和生命周期状态。 |
| `replay` | object | 是 | 回放游标、播放状态和步长。 |
| `market` | object | 是 | 截至当前游标可见的行情数据。 |
| `indicators` | object | 是 | 根据当前可见行情计算出的指标数据。 |
| `chan_analysis` | object | 是 | 截至当前正式闭合 5 分钟 K 的缠论分析结果。 |
| `warnings` | array<object> | 是 | 不阻塞当前快照使用的提示或降级信息；没有时为空数组。 |

`session` 字段说明：

| 字段 | 类型 | 是否必填 | 含义 |
| --- | --- | --- | --- |
| `session.session_id` | string | 是 | 本场回放的唯一编号。 |
| `session.session_type` | string | 是 | 固定为 `replay`，用于与未来实盘 Session 区分。 |
| `session.symbol` | string | 是 | 本场回放绑定的标准证券标识。 |
| `session.trade_date` | string | 是 | 本场回放绑定的交易日。 |
| `session.state` | string | 是 | 当前回放状态，取值与 `session_status.payload.state` 相同。 |
| `session.revision` | integer | 是 | 当前快照的修订序号，必须等于事件外层的 `revision`。 |

`replay` 字段说明：

| 字段 | 类型 | 是否必填 | 含义 |
| --- | --- | --- | --- |
| `replay.granularity` | string | 是 | 回放采用的数据粒度：`one_minute` 或 `five_minute`。 |
| `replay.current_time` | string | 是 | 已经处理完成的数据所到达的最晚时刻。 |
| `replay.next_bar_time` | string 或 null | 是 | 当前游标之后下一根实际 K 线的闭合时刻；不存在下一根时为 `null`。只提供时间，不提供未来 K 线的价格或成交量。 |
| `replay.start_time` | string | 是 | 当日第一个可回放的游标边界。 |
| `replay.end_time` | string | 是 | 证券所属市场在目标交易日规定的回放结束时间；当前支持的沪深市场正常交易日为 `15:00:00`。 |
| `replay.playing` | boolean | 是 | 当前是否正在自动播放；仅在 `session.state` 为 `playing` 时为 `true`。 |
| `replay.step_seconds` | integer | 是 | 回放粒度的名义秒数；1 分钟模式为 60，5 分钟降级模式为 300。用于显示单步粒度，实际单步始终直接到达 `next_bar_time`，不对 `current_time` 机械加秒。 |

`market` 字段说明：

| 字段 | 类型 | 是否必填 | 含义 |
| --- | --- | --- | --- |
| `market.bars_1m` | array<object> | 是 | 目标交易日截至当前时刻的 1 分钟 K 线；没有 1 分钟数据时为空数组。 |
| `market.bars_5m` | array<object> | 是 | 预热历史和目标日截至当前时刻的 5 分钟 K 线。 |
| `market.daily_bars` | array<object> | 是 | 历史日 K 和目标日截至当前时刻形成的动态日 K。 |
| `market.quote` | object 或 null | 是 | 截至当前时刻形成的行情摘要；无法形成时为 `null`。 |

`indicators` 的每个字段在第 6.2 节说明，`warnings` 的每个字段在第 6.3 节说明。
`chan_analysis` 直接使用项目已有的
[`AnalysisResult.to_dict()`](../../packages/chantheory/README.md) 结构，其内部字段由
`chantheory` 统一定义；本功能不得增加同名但含义不同的字段。

### 6.1 行情结构

`market.bars_1m` 和 `market.bars_5m` 使用同一 bar 结构：

```json
{
  "timestamp": "2026-07-01 10:20:00",
  "open": 10.20,
  "high": 10.28,
  "low": 10.18,
  "close": 10.25,
  "volume": 125000.0,
  "amount": 1280000.0,
  "closed": true
}
```

Bar 字段说明：

| 字段 | 类型 | 是否必填 | 含义 |
| --- | --- | --- | --- |
| `timestamp` | string | 是 | K 线闭合时刻；分钟 K 为上海市场时间，日 K 为 `YYYY-MM-DD`。 |
| `open` | number | 是 | 本周期第一笔成交对应的开盘价。 |
| `high` | number | 是 | 本周期截至当前的最高价。 |
| `low` | number | 是 | 本周期截至当前的最低价。 |
| `close` | number | 是 | 本周期最后一笔成交对应的收盘价或当前价。 |
| `volume` | number | 是 | 本周期累计成交量。 |
| `amount` | number | 是 | 本周期累计成交额。 |
| `closed` | boolean | 是 | `true` 表示周期已经正式闭合，`false` 表示仍在形成中的动态 K 线。 |

`timestamp` 是标准闭合时刻；OHLC、`volume` 和 `amount` 必须为非负 JSON number，
并满足正常 OHLC 包含关系。只有当前动态 5 分钟 K 可以使用 `closed: false`；
`bars_1m` 和历史/正式 5 分钟 K 均为 `true`。

`daily_bars` 使用相同 OHLCVA 字段，但 `timestamp` 为 `YYYY-MM-DD`；目标日动态日 K
使用 `closed: false`，历史日 K 使用 `true`。

`quote` 为 `null` 或以下结构：

```json
{
  "timestamp": "2026-07-01 10:23:00",
  "latest_price": 10.25,
  "change_percent": 1.18,
  "open": 10.12,
  "high": 10.30,
  "low": 10.05,
  "previous_close": 10.13,
  "volume": 2300000.0,
  "amount": 23600000.0,
  "volume_ratio": null,
  "order_imbalance": null,
  "turnover_rate": null
}
```

Quote 字段说明：

| 字段 | 类型 | 是否必填 | 含义 |
| --- | --- | --- | --- |
| `timestamp` | string | 是 | 该行情摘要对应的上海市场时间。 |
| `latest_price` | number | 是 | 截至该时刻的最新成交价。 |
| `change_percent` | number | 是 | 相对前收盘价的涨跌幅百分比，例如 `1.18` 表示上涨 1.18%。 |
| `open` | number | 是 | 目标交易日截至该时刻的开盘价。 |
| `high` | number | 是 | 目标交易日截至该时刻的最高价。 |
| `low` | number | 是 | 目标交易日截至该时刻的最低价。 |
| `previous_close` | number | 是 | 上一个交易日的正式收盘价。 |
| `volume` | number | 是 | 目标交易日截至该时刻的累计成交量。 |
| `amount` | number | 是 | 目标交易日截至该时刻的累计成交额。 |
| `volume_ratio` | number 或 null | 是 | 量比；当前时点无法可靠计算时为 `null`。 |
| `order_imbalance` | number 或 null | 是 | 委买委卖不平衡指标；数据源不提供或无法计算时为 `null`。 |
| `turnover_rate` | number 或 null | 是 | 换手率百分比；缺少流通股本等必要数据时为 `null`。 |

Quote 字段不可获得时保留字段并使用 `null`。Replay quote 只能由目标时点及以前的
行情形成，不得从目标日收盘快照回填未来值。

### 6.2 指标结构

所有指标序列都由以下 point 组成：

```json
{"timestamp": "2026-07-01 10:20:00", "value": 10.25}
```

指标 point 字段说明：

| 字段 | 类型 | 是否必填 | 含义 |
| --- | --- | --- | --- |
| `timestamp` | string | 是 | 该指标值对应的 K 线闭合时刻。 |
| `value` | number 或 null | 是 | 指标值；预热数据不足、尚不能计算时为 `null`。 |

指标对象字段说明：

| 字段路径 | 类型 | 是否必填 | 含义 |
| --- | --- | --- | --- |
| `indicators.five_minute` | object | 是 | 基于正式闭合 5 分钟 K 计算的全部指标。 |
| `indicators.five_minute.ma` | object | 是 | 5、10、20、30、60 周期简单移动平均线集合。 |
| `indicators.five_minute.ma.ma5/ma10/ma20/ma30/ma60` | array<point> | 是 | 对应周期的移动平均线序列。 |
| `indicators.five_minute.boll` | object | 是 | 5 分钟布林带参数和三条结果序列。 |
| `indicators.five_minute.boll.period` | integer | 是 | 布林带移动窗口，当前为 20。 |
| `indicators.five_minute.boll.stddev` | number | 是 | 上下轨使用的标准差倍数，当前为 2.0。 |
| `indicators.five_minute.boll.upper/middle/lower` | array<point> | 是 | 布林带上轨、中轨和下轨序列。 |
| `indicators.five_minute.volume.values` | array<point> | 是 | 每根正式闭合 5 分钟 K 的成交量序列。 |
| `indicators.five_minute.volume.ma5/ma10` | array<point> | 是 | 5、10 周期成交量移动平均序列。 |
| `indicators.five_minute.macd` | object | 是 | 5 分钟 MACD 参数和结果序列。 |
| `indicators.one_minute` | object | 是 | 目标交易日截至当前时刻的 1 分钟指标。 |
| `indicators.one_minute.vwap` | array<point> | 是 | 当日累计成交额除以累计成交量形成的 VWAP 序列。 |
| `indicators.one_minute.volume.values` | array<point> | 是 | 每根 1 分钟 K 的成交量序列。 |
| `indicators.one_minute.macd` | object | 是 | 1 分钟 MACD 参数和结果序列。 |
| `macd.fast_period` | integer | 是 | MACD 快线 EMA 周期，当前为 12。 |
| `macd.slow_period` | integer | 是 | MACD 慢线 EMA 周期，当前为 26。 |
| `macd.signal_period` | integer | 是 | DEA 信号线 EMA 周期，当前为 9。 |
| `macd.dif` | array<point> | 是 | 快线 EMA 与慢线 EMA 的差值序列。 |
| `macd.dea` | array<point> | 是 | DIF 的信号线序列。 |
| `macd.histogram` | array<point> | 是 | MACD 柱状序列；具体缩放方式必须在指标实现与测试中保持一致。 |

- `timestamp` 必须对应其周期 K 线的标准闭合时刻；数组按时间升序。
- 指标预热不足时仍保留对应 point，并使用 `value: null`，不得使用 `NaN` 或缩短数组
  来隐式表达预热区。
- `five_minute.ma` 固定包含 `ma5`、`ma10`、`ma20`、`ma30`、`ma60`。
- `five_minute.boll` 固定包含参数及 `upper`、`middle`、`lower`。
- 两个周期的 `macd` 固定包含参数及 `dif`、`dea`、`histogram`。
- `volume.values` 使用同一 point 结构；5 分钟动态未闭合 K 的实时成交量来自
  `market.bars_5m`，不混入正式指标序列。
- `one_minute.vwap` 按目标日截至各分钟的累计成交额除以累计成交量计算；预热数据
  不进入该序列。
- 5 分钟 MA、BOLL、VOL MA 和 MACD 只基于正式闭合 5 分钟 K 更新；1 分钟指标只
  包含目标日 `current_time` 及以前的数据。

### 6.3 Warning 结构

`warnings` 只承载当前快照仍可使用时的非阻塞降级或数据缺失。每项必须包含：

```json
{
  "warning_code": "one_minute_data_unavailable",
  "severity": "warning",
  "message": "目标日没有 1 分钟数据，已使用 5 分钟回放",
  "affected_capability": "intraday_chart",
  "affected_field": "market.bars_1m",
  "details": {}
}
```

Warning 字段说明：

| 字段 | 类型 | 是否必填 | 含义 |
| --- | --- | --- | --- |
| `warning_code` | string | 是 | 稳定的机器可读提示代码，React 可据此选择展示方式。 |
| `severity` | string | 是 | 提示级别，只允许 `info` 或 `warning`。 |
| `message` | string | 是 | 可以直接展示给用户的简短说明。 |
| `affected_capability` | string | 是 | 受影响的功能，只允许 `replay`、`intraday_chart`、`five_minute_chart` 或 `chan_analysis`。 |
| `affected_field` | string | 是 | 受影响的快照字段路径，例如 `market.bars_1m`；没有具体字段时使用空字符串。 |
| `details` | object | 是 | 供程序诊断或补充展示的结构化信息，默认 `{}`，不得包含异常栈或 Provider 原始响应。 |

无法形成可用快照的情况必须使用 `operation_failed`，不得只写 warning。

行情范围与计算约束：

- `bars_1m` 只能包含目标日 `current_time` 及以前的真实分钟；预热数据不得混入分时图。
- `bars_5m` 包含开盘前预热序列和目标日截至当前的正式闭合 5 分钟 K；当前动态
  5 分钟 K 必须以 `closed: false` 标记，且不得进入 CZSC。
- `daily_bars` 包含历史日 K 和目标日截至当前的动态日 K；动态日 K 使用
  `closed: false`。
- `indicators.five_minute` 基于完整已加载 5 分钟历史计算，不以可见窗口为输入。
- `chan_analysis` 只使用截至当前已正式闭合的 5 分钟 K，并按 ADR 0008 执行 full
  project-level rebuild。
- `granularity` 为 `one_minute` 或 `five_minute`。降级到 5 分钟回放时，
  `bars_1m` 为空、`step_seconds` 为 300；React 根据 `granularity` 显示“5 分钟回放”。

时间字段约束：

- `start_time` 是目标交易日的第一个可回放游标边界，通常为 `09:30:00`，此时尚未
  消费目标日 K 线。`end_time` 由证券所属市场和目标交易日的交易日历确定，不由最后
  一根已下载 K 线决定；当前支持的沪深市场正常交易日为 `15:00:00`。
- `current_time` 是已经消费的输入前缀的闭区间上界。单步从 `start_time` 推进到下一
  根实际 K 的闭合时刻，不为午休创建游标点。
- `next_bar_time` 指向当前粒度下尚未消费的第一根实际 K 线：1 分钟模式指向下一根
  1 分钟 K，5 分钟模式指向下一根正式闭合 5 分钟 K；到达序列尾部后为 `null`。
  它只暴露下一个时间位置，不暴露未来行情值。

前后端一致性约束：

- React 根据 `session.state`、`current_time`、`next_bar_time`、`end_time` 和
  `granularity` 决定播放、单步与定位控件的显示和禁用状态；单步按钮仅在状态允许且
  `next_bar_time` 不为 `null` 时可用。Python 不返回控制按钮的布尔字段，但仍校验每条
  回放命令。
- `replay.playing` 必须与 `session.state` 一致：仅当 state 为 `playing` 时为 `true`。
- `session.revision` 必须与承载该快照的事件信封 `revision` 相等。

安全约束：

- 快照不得包含 Electron 连接参数、Python 内部对象、SQLite 路径或上游原始 payload。

## 7. Replay 状态和定位语义

```text
created → loading → ready ─────→ playing ↔ paused
                    └─────────→ paused
created/loading/ready/playing/paused ─────────────→ failed
created/loading/ready/playing/paused/failed ──────→ retired
```

- `begin_replay` 每次创建新 Session；不得恢复上一次 Replay 的日期、进度或派生状态。
- Session 创建后绑定 `symbol + trade_date`，不能在原实例上切换股票或日期。
- Session 进入 `ready` 前必须从本地 SQLite 和必要的网络补数准备好目标日完整输入
  序列，并保存在本次回放可直接读取的内存状态中；进入 `ready` 后，播放、单步和定位
  不按下一根 K 逐次请求网络。
- `end_time` 之前应有而缺失的行情必须在进入 `ready` 前先尝试补齐。1 分钟数据不能
  形成可靠回放、但正式 5 分钟数据可用时降级为 5 分钟回放；两种粒度都不能形成
  可靠回放时返回 `replay_data_unavailable`，不得缩短 `end_time` 或生成虚假 K 线。
- `ready` 表示目标日完整输入序列已经准备完成、尚未开始播放或执行游标操作的初始
  静止状态；
  `paused` 表示用户暂停，或单步/定位完成后的静止状态。两者都允许开始播放、单步和
  定位，但不是同一个状态值。
- `step_replay` 在 1 分钟模式推进到下一根实际 1 分钟 K；在降级模式推进到下一根
  实际闭合 5 分钟 K，不为午休或其他无交易时间生成空步骤。
- 当 `next_bar_time` 为 `null` 时，`step_replay` 是幂等 no-op：返回
  同步成功结果，省略 `operation_id`，不增加 revision，也不发布事件。
- `seek_replay` 自动暂停。向前或向后定位都以目标时点为闭区间上界。
- 向后定位丢弃旧管线实例，从开盘前预热状态顺序重放到目标时点。
- `step_replay` 和 `seek_replay` 都是游标操作。同一 Session 同时最多执行一个游标
  操作；开始任一游标操作前先进入 `paused`。
- 新的 `seek_replay` 采用 latest-wins：它使正在执行的旧 seek 或 step 失效，旧
  `operation_id` 的结果即使晚到也不得发布快照。
- 游标操作执行中收到 `step_replay` 时不排队，返回 `replay_busy`；用户可在当前操作
  完成后重试。游标操作执行中收到 `set_replay_playback(playing=true)` 同样返回
  `replay_busy`；`set_replay_playback(playing=false)` 始终可以接受。
- 当 Session 已处于 `ready` 或 `paused` 时，重复调用
  `set_replay_playback(playing=false)` 是幂等 no-op：返回同步成功结果，不增加
  revision，也不发布 `session_status` 或快照事件。
- `end_replay` 退休 Session；退休后所有命令返回 `session_retired`，所有晚到事件丢弃。
- Python 服务 generation 改变后，旧 Replay 明确丢失；Electron/React 不尝试恢复旧
  Session，只返回未开始的 Replay 状态。

## 8. 错误契约

错误 payload：

```json
{
  "error_code": "replay_data_unavailable",
  "category": "data",
  "severity": "error",
  "retryable": true,
  "affected_capability": "replay",
  "message": "没有可用的历史行情，无法开始回放",
  "request_id": "opaque-request-id",
  "operation_id": "opaque-operation-id",
  "details": {}
}
```

错误字段说明：

| 字段 | 类型 | 是否必填 | 含义 |
| --- | --- | --- | --- |
| `error_code` | string | 是 | 稳定的机器可读错误码，具体允许值见下表。 |
| `category` | string | 是 | 错误所属类别，用于统一处理相近错误。 |
| `severity` | string | 是 | 错误级别，当前固定为 `error`。 |
| `retryable` | boolean | 是 | `true` 表示在数据或服务条件改变后可以重试，不代表前端必须自动重试。 |
| `affected_capability` | string | 是 | 因错误而不可用或受影响的功能。 |
| `message` | string | 是 | 可以直接展示给用户的简短错误说明。 |
| `request_id` | string | 是 | 触发该错误的原请求编号。 |
| `operation_id` | string | 否 | 失败的后台操作编号；同步拒绝没有后台操作，因此省略。 |
| `details` | object | 是 | 脱敏后的结构化补充信息，默认 `{}`；不得包含异常栈、凭据、文件路径或上游原始响应。 |

历史行情回放功能使用以下错误码：

合法枚举：

- `category`：`validation`、`data`、`calculation`、`session`、`service`；
- `severity`：当前版本只允许 `error`；非阻塞情况使用 warning 结构；
- `affected_capability`：`symbol_selection`、`replay`、`intraday_chart`、
  `five_minute_chart`、`chan_analysis` 或 `service`。

HTTP/WebSocket 等传输异常不会以 `transport` category 暴露给 React；Electron main
统一将其映射为 `service` category 和稳定的服务错误。

同步拒绝通常包括 `invalid_request`、`symbol_not_found`、`invalid_trade_date`、
`session_not_found`、`session_retired`、`replay_busy` 和 `service_unavailable`。
异步失败包括 `replay_data_unavailable`、`calculation_failed` 和
`operation_superseded`。具体错误仍以操作实际执行阶段为准：同一错误码不得同时通过
同步响应和异步事件重复交付。

| 错误码 | category | severity | affected_capability | 含义 | 默认可重试 |
| --- | --- | --- | --- | --- | --- |
| `invalid_request` | `validation` | `error` | `symbol_selection` 或 `replay` | 字段、类型或时间格式无效 | 否 |
| `symbol_not_found` | `data` | `error` | `symbol_selection` | 无法解析标准证券 | 是 |
| `invalid_trade_date` | `validation` | `error` | `replay` | 日期不是可接受的回放目标 | 否 |
| `replay_data_unavailable` | `data` | `error` | `replay` | 1m 与正式 5m 数据都无法形成覆盖市场回放要求的可靠输入 | 是 |
| `session_not_found` | `session` | `error` | `replay` | Session 不存在或不属于当前 generation | 否 |
| `session_retired` | `session` | `error` | `replay` | Session 已结束 | 否 |
| `operation_superseded` | `session` | `error` | `replay` | 操作被更新的定位操作取代 | 否 |
| `replay_busy` | `session` | `error` | `replay` | 已有游标操作正在执行 | 是 |
| `calculation_failed` | `calculation` | `error` | `five_minute_chart` 或 `chan_analysis` | 指标或 CZSC 重建失败 | 是 |
| `service_unavailable` | `service` | `error` | `service` | Python 服务未就绪或正在重启 | 是 |

内部异常栈、凭据、文件路径和上游原始响应只写入脱敏技术日志，不进入错误 payload。

## 9. 确定性 Fixture 和验收门槛

历史行情回放功能至少准备两组仓库内 fixture：

1. `one_minute_replay`：跨多个交易日的 5 分钟预热数据，加一个完整目标交易日的
   1 分钟和正式 5 分钟数据；
2. `five_minute_fallback`：相同类型标的和交易日，但目标日缺少 1 分钟数据，只提供
   正式闭合 5 分钟数据。

fixture 不访问网络，时间戳严格递增，覆盖上午、午休、下午和跨日边界。验收测试必须
证明：

1. 相同输入前缀、配置和目标时点产生字节级稳定或经明确忽略字段后的等价快照；
2. 任意快照中的 bar、指标、CZSC、绘图原语和行情值都不晚于 `current_time`；
   绘图原语特指 `chan_analysis` 中由 `chantheory` `AnalysisResult.to_dict()` 输出的
   `plot_primitives`。
   `start_time`、`end_time` 和 `next_bar_time` 只描述回放时间位置，不得携带未来价格、
   成交量、指标或分析结果；
3. 动态未闭合 5 分钟 K 不改变 CZSC 输出；正式闭合 K 到来后才触发 full rebuild；
4. 向后定位后不存在未来 bar、指标、CZSC、信号或绘图原语残留；
5. 快速连续定位只发布最后一个 `operation_id` 的结果；
6. 相同或更旧 revision 被丢弃，revision 跳号由前端识别并触发完整快照重新基线，
   协议不依赖后端缺口通知事件；
7. 服务 generation 改变后旧 Session 和旧事件失效；
8. 1 分钟缺失时正确进入 5 分钟降级模式；两种粒度都不会为午休生成虚假步骤；
9. 快照替换是原子的，React 不展示部分计算结果；
10. `select_symbol`、指标 point、warning、行情 bar/quote 和错误枚举通过 Schema
    合约测试；
11. `session_status` 可以立即更新状态，但不能覆盖更高 revision 的完整快照；
12. 连续 seek、step 后 seek、游标繁忙时 step，以及到达末尾后的 step 行为符合
    第 7 节；
13. 同步拒绝不创建 `operation_id` 或事件，异步失败只通过一个带原
    `operation_id` 的 `operation_failed` 事件交付；
14. 各类事件严格按第 5 节规则携带或省略 `operation_id`。
15. `session_status.reason` 只使用登记值。
16. 任意可结束状态都能进入 `retired`。
17. 已处于 `ready` 或 `paused` 时重复暂停不增加 revision，也不发布事件。
18. `end_time` 来自证券市场和目标交易日的交易日历；行情缺失不会缩短该时间。
19. `next_bar_time` 始终指向当前粒度的下一根实际 K，或在序列尾部为 `null`。
20. React 不依赖 Python 按钮开关字段，也能从事实状态推导回放控件状态。

等价快照比较只允许忽略以下运行身份字段：事件信封中的 `service_generation`、
`operation_id`，以及 payload 中的 `session.session_id`。`request_id` 不进入工作台快照。
在相同命令序列下，`revision`、状态、游标、行情、指标、CZSC、warning
和所有业务时间戳都必须相等；payload 不得加入未列明的当前墙钟时间、随机标识或其他
易变字段。

## 10. 后续扩展

实时行情、普通指标增量、完整 CZSC 替换事件、模拟成交、真实成交和偏好将在各自垂直
切片中扩展。任何扩展都继续遵守本契约的 generation、Session、revision、完整快照
重新基线和稳定错误边界。
