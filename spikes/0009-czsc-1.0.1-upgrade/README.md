# czsc 1.0.1 升级 — #173 隔离环境验证与旧版基线冻结（Go/No-Go 报告）

- 日期：2026-09-08
- 分支：`upgrade/czsc-1.0.1`（基于 main `b924c53`）
- 父 issue：#172；本报告对应子任务 #173
- 结论：**GO**（Python 3.14.5 可直接安装运行 czsc 1.0.1，无需更换解释器、无需源码编译）

---

## 1. 正式环境记录（未修改）

| 项 | 值 |
|---|---|
| 解释器 | CPython 3.14.5（`/Users/jishen/.venvs/czsc/bin/python`） |
| 平台 | macOS-26.6.2-arm64（Apple Silicon, Mach-O） |
| czsc | 0.10.12（`pyproject.toml` pin `czsc==0.10.12`） |
| rs_czsc | 0.1.26.post260402（旧版附带，1.0.1 不再依赖） |
| `czsc_min_bi_len` 环境变量 | **未设置**（实测 `os.environ`） |
| 实际生效 `min_bi_len` | **6**（`czsc.envs.get_min_bi_len()` 默认值，来源：未设置环境变量 → 默认 6） |
| 实际生效 `max_bi_num`（env） | 50（`czsc.envs.get_max_bi_num()` 默认） |
| 项目参数 | `DEFAULT_PARAMETERS.max_bi_num=50`；分钟级 timeframe 经 `get_default_max_bi_num()` 提升为 500。项目代码从未设置 `min_bi_len`，旧版实际成笔长度阈值即默认 6 |
| 现有测试 | `python -m unittest discover -s packages/chantheory/tests -p 'test_*.py'` → **95 tests OK**（24.5s，2026-09-08） |

依赖清单：`spikes/0009-czsc-1.0.1-upgrade/pip-freeze-prod-0.10.12.txt`
（注：正式 venv 含大量与本项目无关的包；此清单为环境快照，非完全可复现的锁文件。）

重建命令（正式环境，仅记录，勿执行）：

```bash
python3.14 -m venv ~/.venvs/czsc
source ~/.venvs/czsc/bin/activate
pip install -e ".[apps,dev]"   # pyproject pin czsc==0.10.12
```

## 2. 隔离环境验证（czsc 1.0.1）

| 项 | 值 |
|---|---|
| 环境路径 | `/Users/jishen/.venvs/czsc101-isolated`（独立 venv，未触碰 `~/.venvs/czsc`） |
| 解释器 | CPython 3.14.5（与正式环境同版本） |
| 安装 | `pip install czsc==1.0.1` → 成功（exit 0） |
| wheel | `czsc-1.0.1-cp310-abi3-macosx_11_0_arm64.whl`（**cp310-abi3 稳定 ABI**，覆盖 Python ≥3.10，含 3.14；无需 rustc 源码编译） |
| `czsc._native` | 导入成功（`_native.abi3.so`） |
| `wbt` | 0.8.2（1.0.1 硬依赖，自动安装） |
| requires_python | `>=3.10` |

依赖清单：`spikes/0009-czsc-1.0.1-upgrade/pip-freeze-isolated-1.0.1.txt`

重建命令：

```bash
python3.14 -m venv ~/.venvs/czsc101-isolated
source ~/.venvs/czsc101-isolated/bin/activate
pip install --upgrade pip
pip install czsc==1.0.1
```

## 3. 1.0.1 入口与行为验证（最小实验，固定 seed=42 样本）

### 3.1 入口可用性

| 旧入口（0.10.12） | 1.0.1 状态 | 替代 |
|---|---|---|
| `czsc.py.objects.RawBar` / `czsc.py.analyze.CZSC`（纯 Python 优先路径） | **已移除**（`czsc.py` 不存在） | 顶层 `czsc.RawBar` / `czsc.CZSC`（Rust `_native`） |
| `czsc.utils.sig.get_zs_seq` | **已移除**（`czsc.utils.sig` 不存在） | 顶层 `czsc.get_zs_seq`（已验证可用） |
| `czsc.signals.cxt.*`（Python 信号函数模块） | **已移除**（`czsc.signals` 包不存在） | `czsc._native.call_signal(name, czsc, params)` 分发器 |
| `CZSC(bars, max_bi_num=...)` | 位置/关键字均可，`max_bi_num` 生效（实测 attr 回读一致） | 同名参数 |
| `min_bi_len` | **新构造参数**：`CZSC(bars, min_bi_len=6)` 可传，attr 回读一致；默认 6（与旧版 env 默认一致） | 显式传参对齐 |
| `CZSC.update(bar)` | 可用（增量验证通过） | 同名 |
| `finished_bis` | 存在；实测 120 根样本 `bi_list=17, finished_bis=16`（尾笔排除语义保留） | 同名 |

### 3.2 时间戳验证（#174 硬门禁的预检）

- **日线**：输入 `dt=2024-01-01..04-29`，`bars_raw[-1].dt` 与输入完全一致；全部 51 个分型 dt、17 笔端点 dt **精确等于** `bars_raw` 中某根 K 线的 dt（无 16:00 偏移）。
- **对照旧版**：旧版纯 Python 引擎 `fx[0].dt=2024-01-06 16:00:00`（旧版自身带 16:00 尾部标记语义）；新版分型 dt 为干净的 `00:00` 且精确对齐 K 线。**新版无日期偏移**。
- **5 分钟**：输入 `09:35..14:30`，`bars_raw[-1].dt` 与输入一致；首根差异为**预热丢弃**（见下），非偏移。
- **预热丢弃**：新旧版行为一致——`CZSC(bars)` 丢弃前 2 根（n=30/60/120 均为 dropped=2），`bars_raw` 首根从输入第 3 根开始。这是两版共同行为，非升级差异。

### 3.3 结构对照（同输入、同参数 min_bi_len=6 / max_bi_num=50）

| 指标 | 0.10.12（纯 Python） | 1.0.1（Rust） |
|---|---|---|
| 分型数 | 51 | 51 |
| 笔数 | 17 | 17 |
| 前 6 笔端点（dt, 方向, 价格） | — | **完全一致**（仅 dt 表示差异：旧版 `16:00` 尾标记 vs 新版对齐 K 线 dt） |
| `get_zs_seq(finished_bis)` | —（旧路径已移除） | 3 个中枢，sdt/edt/zg/zd 正常 |

### 3.4 信号分发器验证

- `czsc._native.list_signal_names("cxt")` → 41 个 cxt 信号，**四个默认信号全部注册**：
  `cxt_first_buy_V221126`、`cxt_first_sell_V221126`、`cxt_second_bs_V240524`、`cxt_third_bs_V230319`。
- `call_signal(name, czsc, {"di": 1})` → 返回 `Signal` 列表（len=1）。
- `Signal` 形态：`key="日线_D1B_BUY1"`（k1_k2_k3），`value="其他_任意_任意_0"`（v1_v2_v3_score），`to_string()` 为 7 段全串，`is_match(dict)` 需传入 `{key: value}` 形式的信号字典（空字典或缺失 key 会抛 `ValueError`），`score`、`matches` 可用。
- 未触发值仍为 `其他_任意_任意_0`，与现有项目契约的未触发口径一致。
- 未知信号 → `KeyError: unknown signal: ...`（明确诊断，非静默）。
- **自定义 Python 信号函数**：`czsc.signals` 包不存在，旧 `import_module(module_name)` 路径对 czsc 内置信号不可用；但项目自有 Python 信号函数模块（非 `czsc.signals.*`）不受影响，可在适配层保留（用户已确认口径：分别处理内置分发与自定义入口）。
- `CzscSignals`（逐 bar 信号器）存在但构造签名变化：`BarGenerator(base_freq, freqs, max_count=2000)`（如 `BarGenerator("日线", [])`），方法为 `update_signals(bar)` / `get_signals_by_conf()`；`CZSC.signals` 属性为 OrderedDict（逐 bar 重放下为空，需走分发器）。#175 迁移时以 `call_signal` 为主入口。

### 3.5 增量更新

`CZSC(bars[:100]).update(bars[100])` 正常，`bars_raw` 尾部对齐，笔数合理演化。逐根增量与全量重建的一致性留待 #176 正式对照。

## 4. 样本覆盖缺口盘点（#173 冻结基线范围）

现有 fixture 仅 `p2_sample_rows.json`（5 根日线，000001.SZ）+ `p2_sample_result.json`（4 分型/3 笔/1 线段/1 中枢，无信号层）。对照 #173 要求的缺口：

| 要求场景 | 现状 | 动作 |
|---|---|---|
| 日线有中枢 | p2 样本有 1 个 stroke 中枢 | 冻结保留 |
| 5m 真实数据 | `spikes/0008-.../fixtures/a_share_5m_548.json`（600584.SH，548 根） | 复用为基线输入（复制进 chantheory tests/fixtures，遵循“小型必要样本进仓库”） |
| 30m | **无真实样本** | 需补：经 marketdata 拉取固定历史窗口冻结为 JSON |
| 四信号触发/未触发 | 触发断言有（mock+真实 5m）；**未触发值断言有**；但无“各自触发”的专门样本 | 需补：构造/选取使各信号触发的窗口 |
| 尾笔延伸 | `test_map_pending_stroke_*` 覆盖 | 冻结进基线 |
| 同 dt 更新 | 仅 normalize 层；engine 层缺 | 需补（#174 DoD） |
| 时间戳敏感 | 5m 有；日线偏移断言缺 | 需补：基线断言 fx/bi dt 精确等于 K 线 dt |

基线冻结产物（本报告同目录）：
- `baseline/prod-env-facts.txt` — 正式环境实测记录
- `baseline/chantheory-baseline-95tests.txt` — 测试输出留档
- `baseline/pip-freeze-prod-0.10.12.txt` / `pip-freeze-isolated-1.0.1.txt`
- 旧金标 JSON 不动：`packages/chantheory/tests/fixtures/p2_sample_*` 保持原样；新版临时期望后续以 `*.c101.json` 后缀并存（不新增目录层级）。

## 5. 缓存/持久化消费者盘点（正式切换时需处理项）

| 消费者 | 存储 | engine_version 参与？ | 切换动作 |
|---|---|---|---|
| `stockpilot/db/market_data.sqlite` | 仅 K 线/证券 | 否 | 无需处理 |
| `stockpilot/db/t0_assistant.sqlite` | preferences/fee_plans/trades | 否 | 无需处理 |
| chan-viewer `st.session_state` | 内存，会话级 | 失效键不含 engine_version（`apps/chan-viewer/app.py:281-292`） | 重启会话即可；可在 #177 冒烟时确认 |
| t0 `LiveProjectionStore` | 内存，会话级 | 否 | 新会话重建，无需处理 |
| 磁盘 JSON | 仅测试 fixture（`p2_sample_result.json` 钉住 0.10.12） | 是 | 由 #176 对照验收后统一更新 |

结论：**无跨进程磁盘缓存混用风险**；正式切换（#177）无需数据迁移，仅需重启应用会话。

## 6. Go/No-Go 结论与遗留风险

**GO**：
1. Python 3.14.5 + cp310-abi3 wheel 安装/导入全部成功，无需换解释器、无需编译工具链。
2. 四个默认信号在 Rust 分发器中全部注册且可调用，未触发口径与现有契约一致。
3. 时间戳预检通过：日线/5m 无偏移，分型/笔端点精确对齐 K 线 dt。
4. 同参数下分型/笔数量与端点价格与旧版一致（初步，非正式对照）。
5. `min_bi_len` 成为显式构造参数且默认 6，与旧版实际生效值一致；#174 将显式传参固定。

**遗留风险（交后续子任务）**：
- `Signal.is_match` 语义变化（需传信号字典）→ #175 适配。
- `czsc.signals.*` 模块路径消失 → #175 分发入口迁移；自定义 Python 信号函数入口需在适配层保留并验证其对分析对象的依赖。
- `CzscSignals`/`BarGenerator` 构造签名变化 → #175/#174 按需适配。
- `finished_bis` 尾笔排除条件需按 1.0.1 实际源码写回归断言（#174 DoD）。
- 逐根增量 vs 全量重建一致性、多周期、性能 → #176。
- 本报告所有实验为最小验证样本（seed=42 合成数据），正式结论以 #174/#176 固定真实样本对照为准。
