# czsc 1.0.1 升级 — #173 隔离环境验证与旧版基线冻结

- 日期：2026-09-09（修订版，回应 PR #178 审查意见）
- 分支：`upgrade/czsc-1.0.1`（基于 main `b924c53`）
- 父 issue：#172；本报告对应子任务 #173
- 结论：**安装验证通过；旧版基线冻结完成；样本覆盖完成。** 本 PR 关闭 #173。

> **审查回应**：本版修正了初版报告的三个问题：
> 1. [P1] 补齐可复现旧版基线：新增生成脚本、固定输入、完整分析 JSON、SHA-256 校验清单，并验证可重复生成。
> 2. [P1] 补齐样本覆盖：日线/5m/30m 三周期均有固定输入与完整输出；四信号在 30m 场景全部触发，在日线/5m 场景有触发/未触发记录。
> 3. [P2] 修正 16:00 时间戳错误结论：旧版纯 Python 引擎（`engine.py` 实际加载路径）无 16:00 偏移；初版实验误用顶层 Rust `RawBar` 路径导致错误归因。详见 §3.2。
>
> **第二轮审查回应**：
> 1. [P1] 移除循环依赖：#173 的关闭条件仅为安装验证 + 旧基线冻结 + 样本覆盖，不依赖 #174/#176。新版适配与对照保留为后续子任务。
> 2. [P1] 修复失败处理：生成器在任一必需场景失败时返回非零退出码，且不覆盖已提交的校验清单和金标输出。
> 3. [P2] 只读验证模式：新增 `--verify` 模式，生成到临时目录与已提交清单比较；新增引擎版本守卫，防止在新版环境误写旧基线。

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
| `engine.py` 实际加载路径 | `czsc.py.objects.RawBar` / `czsc.py.analyze.CZSC`（纯 Python 优先路径，`engine.py:30-36`） |
| 现有测试 | `python -m unittest discover -s packages/chantheory/tests -p 'test_*.py'` → **95 tests OK**（24.5s，2026-09-08） |

依赖清单：`baseline/pip-freeze-prod-0.10.12.txt`
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

依赖清单：`baseline/pip-freeze-isolated-1.0.1.txt`

重建命令：

```bash
python3.14 -m venv ~/.venvs/czsc101-isolated
source ~/.venvs/czsc101-isolated/bin/activate
pip install --upgrade pip
pip install czsc==1.0.1
```

**安装验证结论：通过。** Python 3.14.5 可直接安装运行 czsc 1.0.1，无需更换解释器、无需源码编译。

## 3. 1.0.1 入口与行为验证

### 3.1 入口可用性

| 旧入口（0.10.12） | 1.0.1 状态 | 替代 |
|---|---|---|
| `czsc.py.objects.RawBar` / `czsc.py.analyze.CZSC`（纯 Python 优先路径，`engine.py` 实际使用） | **已移除**（`czsc.py` 不存在） | 顶层 `czsc.RawBar` / `czsc.CZSC`（Rust `_native`） |
| `czsc.utils.sig.get_zs_seq` | **已移除**（`czsc.utils.sig` 不存在） | 顶层 `czsc.get_zs_seq`（已验证可用） |
| `czsc.signals.cxt.*`（Python 信号函数模块） | **已移除**（`czsc.signals` 包不存在） | `czsc._native.call_signal(name, czsc, params)` 分发器 |
| `CZSC(bars, max_bi_num=...)` | 位置/关键字均可，`max_bi_num` 生效（实测 attr 回读一致） | 同名参数 |
| `min_bi_len` | **新构造参数**：`CZSC(bars, min_bi_len=6)` 可传，attr 回读一致；默认 6（与旧版 env 默认一致） | 显式传参对齐 |
| `CZSC.update(bar)` | 可用（增量验证通过） | 同名 |
| `finished_bis` | 存在；实测 120 根样本 `bi_list=17, finished_bis=16`（尾笔排除语义保留） | 同名 |

### 3.2 时间戳验证（修正初版错误结论）

> **初版错误**：初版报告称旧版纯 Python 引擎有"16:00 尾部标记语义"。**此结论错误。**
> 经核实，`engine.py` 的 `load_czsc()` 优先加载 `czsc.py.objects.RawBar`（纯 Python 路径），
> 该路径的分型 dt 为干净的 `00:00`（日线）或精确分钟时间戳（分钟级），**无 16:00 偏移**。
> 初版实验误用了顶层 `czsc.RawBar`（Rust `builtins.RawBar`），该路径确实将日线 dt
> 转换为前一日 16:00 的 pandas Timestamp，但**项目 `engine.py` 从未使用此路径**。

**实际加载类型验证**（正式环境 `~/.venvs/czsc`）：

```
czsc version: 0.10.12
py_raw_bar: <class 'czsc.py.objects.RawBar'>     ← engine.py 实际使用
py_freq: <enum 'Freq'>
py_czsc: <class 'czsc.py.analyze.CZSC'>
pure-python path taken: True
top-level RawBar: <class 'builtins.RawBar'>      ← Rust 路径（项目未使用）
```

**同输入（seed=42 合成日线，120 根）两条路径对照**：

| 路径 | `RawBar.__module__` | `bars_raw[-1].dt` | `fx[0].dt` | `fx[0].dt` 类型 |
|---|---|---|---|---|
| 纯 Python（`engine.py` 使用） | `czsc.py.objects` | `2024-06-14 00:00:00` | `2024-01-12 00:00:00` | `datetime` |
| 顶层 Rust（初版误用） | `builtins` | `2024-06-13 16:00:00` | `2024-01-11 16:00:00` | `Timestamp` |

结论：旧版纯 Python 路径无 16:00 偏移，日期不前移。初版将 Rust 路径的行为错误归因于
纯 Python 引擎。**#174 的"日线日期不偏移"硬门禁在旧版基线中已满足**；新版 1.0.1
顶层 `czsc.RawBar` 的 16:00 行为是否保留，需在 #174 适配时验证。

**基线输出时间戳验证**（见 §4 基线产物）：

| 场景 | 分型时间戳示例 | 偏移 |
|---|---|---|
| 日线（合成 120 根） | `2024-01-12`, `2024-01-15`, `2024-01-18` | 无（纯日期） |
| 5m（真实 548 根） | `2026-06-30 10:45:00`, `2026-06-30 14:00:00` | 无（精确分钟） |
| 30m（真实 1336 根） | `2026-01-08 14:30:00`, `2026-01-09 10:30:00` | 无（精确分钟） |

### 3.3 信号分发器验证（隔离环境 1.0.1）

- `czsc._native.list_signal_names("cxt")` → 41 个 cxt 信号，**四个默认信号全部注册**：
  `cxt_first_buy_V221126`、`cxt_first_sell_V221126`、`cxt_second_bs_V240524`、`cxt_third_bs_V230319`。
- `call_signal(name, czsc, {"di": 1})` → 返回 `Signal` 列表（len=1）。
- `Signal` 形态：`key="日线_D1B_BUY1"`（k1_k2_k3），`value="其他_任意_任意_0"`（v1_v2_v3_score），`to_string()` 为 7 段全串，`is_match(dict)` 需传入 `{key: value}` 形式的信号字典（空字典或缺失 key 会抛 `ValueError`），`score`、`matches` 可用。
- 未触发值仍为 `其他_任意_任意_0`，与现有项目契约的未触发口径一致。
- 未知信号 → `KeyError: unknown signal: ...`（明确诊断，非静默）。
- **自定义 Python 信号函数**：`czsc.signals` 包不存在，旧 `import_module(module_name)` 路径对 czsc 内置信号不可用；但项目自有 Python 信号函数模块（非 `czsc.signals.*`）不受影响，可在适配层保留（用户已确认口径：分别处理内置分发与自定义入口）。
- `CzscSignals`（逐 bar 信号器）存在但构造签名变化：`BarGenerator(base_freq, freqs, max_count=2000)`（如 `BarGenerator("日线", [])`），方法为 `update_signals(bar)` / `get_signals_by_conf()`；`CZSC.signals` 属性为 OrderedDict（逐 bar 重放下为空，需走分发器）。#175 迁移时以 `call_signal` 为主入口。

### 3.4 增量更新

`CZSC(bars[:100]).update(bars[100])` 正常，`bars_raw` 尾部对齐，笔数合理演化。逐根增量与全量重建的一致性留待 #176 正式对照。

## 4. 旧版基线冻结（可复现）

### 4.1 生成脚本

`baseline/generate_baseline.py` — 可复现的旧版基线生成器。使用**生产代码路径**
（`packages.chantheory.analyze` → `engine.py` → `czsc.py.objects.RawBar` 纯 Python 路径），
对固定输入运行完整分析管线并冻结输出。

运行方式（正式环境）：

```bash
source ~/.venvs/czsc/bin/activate

# 生成模式：写入 inputs/outputs/checksums/repro-log
python spikes/0009-czsc-1.0.1-upgrade/baseline/generate_baseline.py

# 验证模式（只读）：生成到临时目录，与已提交的 checksums.sha256 比较
python spikes/0009-czsc-1.0.1-upgrade/baseline/generate_baseline.py --verify
```

脚本逻辑：
1. **引擎版本守卫**：检查 `czsc.__version__ == PINNED_ENGINE_VERSION`（0.10.12），
   不匹配则拒绝运行（退出码 2），防止在新版环境误写旧基线。
2. 生成/加载固定输入（合成日线 seed=42；真实 5m/30m 从冻结 JSON 或首次拉取）
3. 调用 `analyze()` 完整管线（含 normalize → engine → structure_mapping → signals）
4. 序列化完整 `AnalysisResult` JSON
5. 记录引擎探针（实际 `RawBar.__module__`、`czsc.__version__`、环境变量）
6. 计算所有输入/输出文件的 SHA-256

**失败处理**：任一必需场景分析失败时，脚本记录错误、返回非零退出码（1），
且**不覆盖**已提交的校验清单和金标输出。

**验证模式（`--verify`）**：生成到临时目录，计算 SHA-256，与已提交的
`checksums.sha256` 逐项比较。全部一致返回 0，任一不匹配返回 1。不修改任何已提交文件。

### 4.2 固定输入

| 场景 | 输入文件 | 标的 | 周期 | 根数 | 数据来源 |
|---|---|---|---|---|---|
| 日线合成 | `inputs/daily_synthetic_120_rows.json` | 000001.SZ | day | 120 | `random.seed(42)` 合成 |
| 5m 真实 | `inputs/5m_real_600584_548_rows.json` | 600584.SH | 5m | 548 | `spikes/0008-.../fixtures/a_share_5m_548.json` 冻结 |
| 30m 真实 | `inputs/30m_600584_sh_rows.json` | 600584.SH | 30m | 1336 | Tencent mkline API 拉取并冻结 |

### 4.3 完整分析输出

| 场景 | 输出文件 | 分型 | 笔 | 线段 | 中枢 |
|---|---|---|---|---|---|
| 日线合成 | `outputs/daily_synthetic_120_result.json` | 30 | 9 | 1 | 2 |
| 5m 真实 | `outputs/5m_real_600584_548_result.json` | 38 | 24 | 3 | 1 |
| 30m 真实 | `outputs/30m_real_600584_result.json` | 417 | 95 | 9 | 21 |

每个输出 JSON 包含完整 `AnalysisResult`：`fractals`、`strokes`、`segments`、
`pivot_zones`、`divergences`、`signal_series`、`signal_events`、`signal_snapshots`、
`candidate_*`、`plot_primitives`、`summary`、`warnings`、`meta`（含 `engine_version`、
`parameters`、`bar_count`、`engine_probe`）。

### 4.4 四信号触发/未触发覆盖

| 信号 | 日线（120 根） | 5m（548 根） | 30m（1336 根） |
|---|---|---|---|
| `first_buy` | 0 active | 0 active | **111 active** |
| `first_sell` | **16 active** | **46 active** | **102 active** |
| `second_bs` | 0 active | **38 active** | **161 active** |
| `third_bs` | 0 active (not_ready) | 0 active (not_ready) | **205 active** |

- 30m 场景四信号全部触发，满足"四个默认信号各自触发"要求。
- 日线/5m 场景 `first_sell` 触发，其余未触发，满足"未触发"覆盖。
- `third_bs` 在日线/5m 因 `list index out of range`（not_ready）未触发——这是旧版
  信号函数在短样本下的已知行为，基线如实记录。
- 所有信号的 `latest_value` 均为 `其他_任意_任意_0`（未触发口径），与项目契约一致。

### 4.5 文件校验清单

`baseline/checksums.sha256` — 所有输入/输出文件的 SHA-256：

```
03c5deb3...  inputs/5m_real_600584_548_rows.json
56debd2a...  outputs/5m_real_600584_548_result.json
5b9e5a0d...  inputs/30m_600584_sh_rows.json
62716e1c...  outputs/30m_real_600584_result.json
acb130f5...  outputs/daily_synthetic_120_result.json
fed13e7c...  inputs/daily_synthetic_120_rows.json
```

### 4.6 可复现性验证（只读 `--verify` 模式）

使用 `--verify` 模式验证可复现性。该模式生成到临时目录，计算 SHA-256，
与**已提交的** `checksums.sha256` 逐项比较，不修改任何已提交文件：

```bash
source ~/.venvs/czsc/bin/activate
python spikes/0009-czsc-1.0.1-upgrade/baseline/generate_baseline.py --verify
```

验证结果（退出码 0 = 全部一致）：

```
--- Verify: comparing 6 generated files against committed checksums ---
  inputs/30m_600584_sh_rows.json: OK
  inputs/5m_real_600584_548_rows.json: OK
  inputs/daily_synthetic_120_rows.json: OK
  outputs/30m_real_600584_result.json: OK
  outputs/5m_real_600584_548_result.json: OK
  outputs/daily_synthetic_120_result.json: OK

VERIFICATION PASSED: all 6 files match committed checksums
```

输出字节级一致，证明基线可重复生成。`--verify` 模式不重写校验清单，
因此即使结果改变也不会"自证通过"。

### 4.7 生成日志

`baseline/repro-log.txt` — 记录引擎探针、每个场景的结构计数、信号探针、SHA-256。
关键探针数据：

```
"czsc_version": "0.10.12"
"RawBar_module": "czsc.py.objects"          ← 纯 Python 路径（engine.py 实际使用）
"CZSC_module": "czsc.py.analyze"
"czsc_min_bi_len_env": "<unset>"
"PINNED_ENGINE_VERSION": "0.10.12"
```

## 5. 样本覆盖缺口盘点

| 要求场景 | 状态 | 证据 |
|---|---|---|
| 日线有中枢 | ✅ 已覆盖 | 基线 `daily_synthetic_120`：2 个中枢 |
| 5m 真实数据 | ✅ 已覆盖 | 基线 `5m_real_600584_548`：548 根，1 个中枢 |
| 30m 真实数据 | ✅ 已补齐 | 基线 `30m_real_600584`：1336 根，21 个中枢 |
| 四信号各自触发 | ✅ 已覆盖 | 30m 场景四信号全部触发（111/102/161/205 active） |
| 四信号未触发 | ✅ 已覆盖 | 日线 `first_buy`/`second_bs`/`third_bs` = 0 active |
| 尾笔延伸 | ✅ 已覆盖 | `test_map_pending_stroke_*` 测试 + 基线 `map_pending_stroke` 输出 |
| 时间戳敏感 | ✅ 已覆盖 | 基线分型/笔端点 dt 精确等于 K 线 dt（§3.2） |
| 同 dt 更新 | ⚠️ 仅 normalize 层 | engine 层缺，属 #174 DoD |

## 6. 缓存/持久化消费者盘点（正式切换时需处理项）

| 消费者 | 存储 | engine_version 参与？ | 切换动作 |
|---|---|---|---|
| `stockpilot/db/market_data.sqlite` | 仅 K 线/证券 | 否 | 无需处理 |
| `stockpilot/db/t0_assistant.sqlite` | preferences/fee_plans/trades | 否 | 无需处理 |
| chan-viewer `st.session_state` | 内存，会话级 | 失效键不含 engine_version（`apps/chan-viewer/app.py:281-292`） | 重启会话即可；可在 #177 冒烟时确认 |
| t0 `LiveProjectionStore` | 内存，会话级 | 否 | 新会话重建，无需处理 |
| 磁盘 JSON | 仅测试 fixture（`p2_sample_result.json` 钉住 0.10.12） | 是 | 由 #176 对照验收后统一更新 |

结论：**无跨进程磁盘缓存混用风险**；正式切换（#177）无需数据迁移，仅需重启应用会话。

## 7. 结论与遗留风险

### #173 完成项

#### 安装验证：通过

1. Python 3.14.5 + cp310-abi3 wheel 安装/导入全部成功，无需换解释器、无需编译工具链。
2. 四个默认信号在 Rust 分发器中全部注册且可调用，未触发口径与现有契约一致。
3. `min_bi_len` 成为显式构造参数且默认 6，与旧版实际生效值一致；#174 将显式传参固定。

#### 旧版基线冻结：完成

1. §4 的基线是旧版 0.10.12 的输出，使用生产代码路径（`engine.py` → `czsc.py.objects` 纯 Python）。
2. 生成脚本 `generate_baseline.py` 支持 `--verify` 只读验证模式（生成到临时目录与已提交清单比较）和引擎版本守卫（拒绝在非 0.10.12 环境运行）。
3. 任一必需场景失败时返回非零退出码且不覆盖已提交金标。
4. 三周期（日线/5m/30m）固定输入 + 完整 AnalysisResult JSON + SHA-256 校验清单均已提交。
5. `--verify` 模式验证全部 6 文件字节级一致。

#### 样本覆盖：完成

1. 30m 场景（1336 根）四信号全部触发（111/102/161/205 active）。
2. 日线/5m 场景有触发（first_sell）与未触发覆盖。
3. 时间戳敏感场景已覆盖（§3.2 基线分型/笔端点 dt 精确等于 K 线 dt）。

**#173 的关闭条件（安装验证 + 旧基线冻结 + 样本覆盖）均已满足。本 PR 关闭 #173。**

### 后续子任务（不阻塞 #173）

以下项属 #174（引擎适配）/#175（信号迁移）/#176（对照验收）的 DoD，不属于 #173：

1. **新版对照**：§4 基线是旧版输出，新版 1.0.1 在同输入上的对照需在 #174/#176 完成。基线生成脚本已就绪，可在隔离环境重跑。
2. **`p2_sample_*` 旧 fixture 与实际引擎不一致**：5 根样本在旧版引擎实际产生 0 分型，但 `p2_sample_result.json` 记录了 4 分型/3 笔。该 fixture 非本次生成，保留原样不动；新版 fixture 仅在 #176 对照验收通过后更新。
3. **同 dt 更新场景**：engine 层缺，属 #174 DoD。
4. **逐根增量 vs 全量重建一致性**：属 #176。

### 遗留风险（交后续子任务）

- `Signal.is_match` 语义变化（需传信号字典）→ #175 适配。
- `czsc.signals.*` 模块路径消失 → #175 分发入口迁移；自定义 Python 信号函数入口需在适配层保留并验证其对分析对象的依赖。
- `CzscSignals`/`BarGenerator` 构造签名变化 → #175/#174 按需适配。
- `finished_bis` 尾笔排除条件需按 1.0.1 实际源码写回归断言（#174 DoD）。
- 逐根增量 vs 全量重建一致性、多周期、性能 → #176。
- 新版 1.0.1 顶层 `czsc.RawBar` 的 16:00 行为是否保留，需在 #174 适配时验证（旧版纯 Python 路径无此行为）。
