# czsc 1.0.2 升级 — #205 隔离对照与 pin 升级

- 日期：2026-11-25
- 分支：`upgrade/czsc-1.0.2`（基于 main `ecd8bbc`）
- Issue：#205（PR 使用 `Refs #205`，不合并）
- 结论：**对照通过；pin 已升级到 1.0.2；正式环境 `~/.venvs/czsc` 未切换。**

> 本目录是 #205 的交付物。上游 1.0.2 相对 1.0.1 包含 #347（重复分型去除）、#348、#349、#368（34 个信号 k3 迁移），维护者明确说明**不是兼容补丁**。本对照的目的就是在隔离环境里证明：对项目当前使用的四个默认信号和结构输出，1.0.2 与 1.0.1 在三个冻结样本上**逐字段一致**。

---

## 1. 两套环境

| 项 | reference（正式 venv） | candidate（隔离 venv） |
|---|---|---|
| 解释器 | `/Users/jishen/.venvs/czsc/bin/python` | `/Users/jishen/.venvs/czsc102-isolated/bin/python` |
| Python | 3.14.5 | 3.14.5 |
| 平台 | macOS-27.2.1-arm64 | macOS-27.2.1-arm64 |
| czsc | **1.0.1** | **1.0.2** |
| urllib3 | 2.7.0 | 2.8.0 |
| wbt | 0.9.0 | 0.9.2 |
| numpy | 2.5.0 | 2.5.5 |
| pandas | 3.0.3 | 3.1.1 |
| 安装命令 | （未执行任何 pip 操作） | `pip install -e .` → `pip install --only-binary=:all: 'czsc==1.0.2'` → `pip install -e .` |

隔离环境**只执行了 `pip install -e .`**（三次：初始安装、pin 改动后刷新元数据、最终验证），没有安装 `.[apps]`、`.[dev]` 或 akshare。完整包列表见 [`pip-freeze-isolated-1.0.2.txt`](./pip-freeze-isolated-1.0.2.txt)（49 个包）。

`pip check` 在隔离环境：pin 改动前报 `stockpilot 0.1.0 requires czsc==1.0.1`（预期，editable 元数据未刷新）；重新 `pip install -e .` 后 **`No broken requirements found.`**。因此**没有**在 `pyproject.toml` 新增 `urllib3>=2.8.0` 直接依赖——1.0.2 的 wheel 自带该要求，pip 已自动解析。

### 正式环境未改证明

对照前后各做一次只读 `pip freeze`：

```
~/.venvs/czsc/bin/python -m pip freeze > /tmp/czsc102/formal-freeze-before.txt   # 对照前
~/.venvs/czsc/bin/python -m pip freeze > /tmp/czsc102/formal-freeze-after.txt    # 全部完成后
diff formal-freeze-before.txt formal-freeze-after.txt   # 无输出
```

141 个包逐行一致；`czsc.__version__` 仍为 `1.0.1`。整次执行**没有**对 `~/.venvs/czsc` 执行任何 `pip install` / 升级 / 卸载。

---

## 2. 探针（1.0.2 隔离环境）

| 探针项 | 结果 |
|---|---|
| `czsc.__version__` | `1.0.2` |
| `czsc._native` 可导入 | 是 |
| `czsc.RawBar` / `czsc.Freq` / `czsc.CZSC` 顶层导出 | 是（与 1.0.1 相同的 `czsc._native` 路径） |
| `CZSC.__text_signature__` | `(bars_raw, max_bi_num=0, min_bi_len=0)` |
| `CZSC(bars).min_bi_len` 实例属性默认回读 | **6**（显式传 `min_bi_len=6` 结果一致；1.0.1 同样为 6） |
| `czsc.envs` 环境变量 `czsc_min_bi_len` / `czsc_max_bi_num` | 均未设置（两版相同） |

**`min_bi_len` 结论**：两版默认值都是 6，项目从未显式传参，对照脚本**不补参数**，保持与生产路径一致。

### 四个默认信号的 k3 迁移确认

通过 `czsc._native.call_signal(name, analyzer, params)` 解析（信号不是 `_native` 的直接属性）。**注册名（`signal_name`）两版都没变**，仍是 `cxt_first_buy_V221126` / `cxt_first_sell_V221126` / `cxt_second_bs_V240524` / `cxt_third_bs_V230319` 这一组；变的是 value 里的 k3 段：

| 项目信号 key | 注册名（不变） | 1.0.1 value 的 k3 | 1.0.2 value 的 k3 |
|---|---|---|---|
| `first_buy` | `cxt_first_buy_V221126` | `BUY1` | `BUY1V221126` |
| `first_sell` | `cxt_first_sell_V221126` | `SELL1` | `SELL1V221126` |
| `second_bs` | `cxt_second_bs_V240524` | `第二买卖点V240524` | 不变 |
| `third_bs` | `cxt_third_bs_V230319` | `BS3辅助V230319` | 不变 |

`BUY1` / `SELL1` 在 1.0.2 被 k3 迁移为带日期后缀的版本。项目 `DEFAULT_SIGNALS_CONFIG` 中的 `signal_key` 是项目自己的 key，不是上游注册名，**不需要改配置**；对照结果（§4）证实迁移后的 value 与 1.0.1 完全一致。

---

## 3. 对照方法

### 输入

三个冻结输入来自 `spikes/0009-czsc-1.0.1-upgrade/baseline/inputs/`（只读，未修改）：

| 场景 | 文件 | bars | SHA-256 前 8 位 |
|---|---|---|---|
| `daily_synthetic_120` | `daily_synthetic_120_rows.json` | 120 | `fed13e7c` |
| `5m_real_600584_548` | `5m_real_600584_548_rows.json` | 548 | `03c5deb3` |
| `30m_real_600584` | `30m_600584_sh_rows.json` | 1336 | `f60f5b73` |

`spikes/0009-.../baseline/outputs/` 是 0.10.12 金标，**没有**拿 1.0.2 去对它。

### 脚本

- [`run_frozen.py`](./run_frozen.py)：对三个冻结输入跑生产 `chantheory.analyze()`，输出完整 JSON + `run-manifest.json`（含输入/输出 SHA-256、引擎版本、环境信息）。
  - `--override-pin`：进程内重绑所有已加载 chantheory 模块的 `PINNED_ENGINE_VERSION`（扫描 `sys.modules` 中 `packages.chantheory*` / `chantheory*` 前缀，含 `packages.chantheory` 包本身、`config`、`engine`、`adapters`、`multi_timeframe`），**不改文件**。重绑后做 stale 自检，确保没有漏掉的模块。
  - 不传 `min_bi_len`，与生产路径一致。
- [`compare_1_0_1_vs_1_0_2.py`](./compare_1_0_1_vs_1_0_2.py)：驱动两个子进程分别用两个解释器跑 `run_frozen.py`，然后比较：
  - 分型 / 笔 / 线段 / 中枢的稳定字段（`STRUCTURE_FIELDS`）
  - 背驰 / 买卖点 / `plot_primitives` 的全字段（`CONTENT_FIELDS`）——不只比计数
  - 四个信号的 value / active / status 逐 snapshot 比较；`signal_events` 逐事件比较
  - warnings 的 code 集合
  - 被删分型归因：只有**直接前驱**（ref 序列中的上一个分型）本身被保留且同标记才算可归因；前驱也被删的，归为 unattributed
  - **不把 `engine_version` 字符串当结构差异**
  - **PASS 门**：上述任一集合有字段差异、出现 1.0.2 独有分型、有 unattributed 被删分型、信号有差异、warnings 不同 → `STOP`。注意这比 #205 原文"被删分型可归因则允许"**更严**：分型本身在 `HARD_COLLECTIONS` 里，任何分型差异都会 STOP，归因记录只用于说明差异性质。#347 真的删分型时，笔/线段几乎必然跟着变，本来就该停下来人工看。
  - 输出 [`comparison-report.json`](./comparison-report.json)

> **PASS 门经过负向测试。** 对 candidate 输出人为注入以下差异，脚本均判 STOP：分型 id 不变但 price 变、背驰 type 翻转、买卖点 price 变、`plot_primitive` y 变、删除前驱不同标记的分型、三个同标记连排删后两个（旧逻辑会误归因到隔一个的被删项）。

### 对照设计

两侧跑的是**同一份项目代码**（`git_sha` 均为 `ecd8bbc`，pin 均为 1.0.1），candidate 侧通过 `--override-pin` 在进程内放行 1.0.2。pin 的正式改动是在对照**通过之后**才做的。

> **不是严格单变量对照。** 两个 venv 除 czsc 外还有 numpy（2.5.0 → 2.5.5）、pandas（3.0.3 → 3.1.1）、wbt（0.9.0 → 0.9.2）、urllib3（2.7.0 → 2.8.0）的差异——这是新建隔离 venv 时 pip 解析出的最新版本，没有刻意对齐。输出仍然逐字段一致，所以结论只会更稳（差异被更多变量覆盖仍然为零），但实验本身不能声称"唯一变量是引擎版本"。

---

## 4. 对照结果

### 4.1 计数对照

| 场景 | 指标 | 1.0.1 | 1.0.2 | 一致 |
|---|---|---|---|---|
| daily_synthetic_120 | 分型 | 30 | 30 | ✅ |
| | 笔 | 9 | 9 | ✅ |
| | 线段 | 1 | 1 | ✅ |
| | 中枢 | 2 | 2 | ✅ |
| | 背驰 | 2 | 2 | ✅ |
| | 信号事件 | 4 | 4 | ✅ |
| 5m_real_600584_548 | 分型 | 38 | 38 | ✅ |
| | 笔 | 24 | 24 | ✅ |
| | 线段 | 3 | 3 | ✅ |
| | 中枢 | 1 | 1 | ✅ |
| | 背驰 | 2 | 2 | ✅ |
| | 信号事件 | 20 | 20 | ✅ |
| 30m_real_600584 | 分型 | 417 | 417 | ✅ |
| | 笔 | 95 | 95 | ✅ |
| | 线段 | 9 | 9 | ✅ |
| | 中枢 | 21 | 21 | ✅ |
| | 背驰 | 9 | 9 | ✅ |
| | 信号事件 | 220 | 220 | ✅ |

**被删分型：0。仅出现在 1.0.2 的分型：0。** 上游 #347 的重复分型去除在这三个样本上没有触发——样本中不存在相邻同标记重复分型。

### 4.2 信号 value 对照

四个默认信号在三个场景的全部 snapshot 上逐点比较：

| 场景 | snapshots | first_buy value 变化 | first_sell value 变化 | second_bs value 变化 | third_bs value 变化 | active 变化 |
|---|---|---|---|---|---|---|
| daily_synthetic_120 | 120 | 0 | 0 | 0 | 0 | 0 |
| 5m_real_600584_548 | 548 | 0 | 0 | 0 | 0 | 0 |
| 30m_real_600584 | 1336 | 0 | 0 | 0 | 0 | 0 |

**活跃信号（非 `其他`）在两版逐点相同。** 冻结输出里并非全部 snapshot 都是 `其他_任意_任意_0`——三个样本都有活跃信号：

| 样本 | 非 `其他` 的点数 | 出现过的 value |
|---|---|---|
| daily_synthetic_120 | 16 | `一卖_7笔_任意_0` |
| 5m_real_600584_548 | 84 | `一卖_9笔_任意_0`、`一卖_15笔_任意_0`、`二买_任意_任意_0`、`二卖_任意_任意_0` |
| 30m_real_600584 | 579 | 一买、一卖、二买、二卖、三买、三卖（含 `均线底分` / `均线新高` 等变体） |

这些 value 以及"是否以 `其他` 开头"的判定，两版逐点一致——这正是 #205 的通过条件。

### 4.3 结构字段对照

`structure_diffs` 在三个场景均为空 `{}`：分型、笔、线段、中枢的稳定字段逐条一致。

### 4.4 全量 JSON diff

对三个场景的完整输出 JSON 做逐行 diff，**每个场景只有 4 行不同**，全部是版本字符串：

1. `engine_version`（顶层）
2. `meta.engine_assumptions.engine_version`
3. `meta.engine_probe.installed_version`
4. summary 里 `czsc X mapped ...` 句中的版本号

其余所有字节一致。

### 4.5 warnings

| 场景 | warning codes（两版相同） |
|---|---|
| daily_synthetic_120 | `AMOUNT_DERIVED` |
| 5m_real_600584_548 | （无） |
| 30m_real_600584 | `AMOUNT_DERIVED` |

---

## 5. 通过 / 停止依据

按 #205 的判定规则逐条核对：

| 规则 | 结果 |
|---|---|
| 分型 / 笔 / 线段 / 中枢一致 | ✅ 三个场景计数和稳定字段全部一致 |
| 背驰 / 买卖点 / `plot_primitives` 一致 | ✅ 全字段一致（收紧 PASS 门后复核） |
| 四个默认信号 value 不变 | ✅ 2004 个 snapshot 逐点一致，含 679 个活跃（非 `其他`）点 |
| "是否以 `其他` 开头"不变 | ✅ |
| 唯一允许的结构差异（被删分型可归因） | 不适用——被删分型为 0 |
| `min_bi_len` 默认值 | ✅ 两版均 6，未补参数 |
| 1.0.1 计数与 #173/#175 记录相符 | ✅ 30/9、38/24、417/95 全部对上（对照前先验证，不符则停止） |

**判定：PASS。** 因此执行 pin 升级。

---

## 6. pin 升级后的改动

| 文件 | 改动 |
|---|---|
| `pyproject.toml` | `czsc==1.0.2` |
| `packages/chantheory/config.py` | `PINNED_ENGINE_VERSION = "1.0.2"`；`PINNED_ENGINE_REASON` 加 1.0.2 对照句；`MIN_BI_LEN_DEFAULT` 注释改为 1.0.x 并记录 1.0.2 复核为 6；`DEFAULT_SIGNALS_CONFIG` 注释加 k3 迁移说明；`get_engine_compatibility` 的 `supported_python` 改 1.0.2 |
| `packages/chantheory/engine.py` | 注释 1.0.1 → 1.0.x（逻辑未动） |
| `packages/chantheory/signals.py` | 模块 docstring 加 k3 迁移句；一处 docstring 1.0.1 → 1.0.x（逻辑未动） |
| `packages/chantheory/tests/fixtures/p2_sample_result.json` | 用 1.0.2 真实引擎重生成；**仅 4 处版本字符串变化**（`engine_version` ×2、`installed_version`、summary 版本句），键序不变；分型/笔/warning 仍为 0/0/`INSUFFICIENT_BARS` |
| `packages/chantheory/tests/test_czsc_5m_spike.py` | 7 处注释 1.0.1 → 1.0.x（描述当前引擎行为，1.0.2 上验证仍成立）；**bar 276 / 341 锁定断言未动** |
| `packages/chantheory/tests/test_upgrade_176_acceptance.py` | 计数注释补 1.0.2 复核记录；**计数 30/9、38/24、417/95 未动** |
| `packages/chantheory/tests/test_adapters.py` | 1 处 docstring 1.0.1 → 1.0.x；**mock 分型用例未动** |
| `packages/chantheory/README.md` | Status 段 Engine 行、Why this pin、Import note、Observed validation；两个 JSON 示例 `engine_version` |
| `docs/chan_theory_v0.1.md` | 示例 `engine_version`；锁定句 |
| `docs/t0assistant/t0_assistant_prd.md` | 锁定句改 1.0.2 并加本目录路径 |
| `CLAUDE.md` | 当前引擎行；`1.0.1 uses` → `1.0.x uses` |

**未改动**：`spikes/0009-czsc-1.0.1-upgrade/`（历史 spike，只读）、`.github/workflows/`、`packages/fundamentalscreener/`、公开 schema、`DEFAULT_SIGNALS_CONFIG` 的实际配置值。

### 测试（隔离 1.0.2 环境，pin 改动后）

```
python -m unittest discover -s packages/chantheory/tests -p 'test_*.py'      # 130 OK
python -m unittest discover -s packages/t0assistant/tests -p 'test_*.py'     # 617 OK (skipped=1, 原有)
python -m unittest discover -s apps/t0-assistant/tests -p 'test_*.py'        # 126 OK
```

`test_czsc_5m_spike.py` 的 bar 276 `一卖_9笔_任意_0` 和 bar 341 `二买_任意_任意_0` 锁定断言在真实 1.0.2 引擎上执行通过（verbose 模式确认）。`test_adapters.py` 的 5 根样本仍是 0 分型 / 0 笔。

---

## 7. 复现

```bash
# 对照（需要两个 venv：~/.venvs/czsc 装的是 1.0.1，~/.venvs/czsc102-isolated 装的是 1.0.2）
python spikes/0010-czsc-1.0.2-upgrade/compare_1_0_1_vs_1_0_2.py

# 只重跑单侧
~/.venvs/czsc/bin/python spikes/0010-czsc-1.0.2-upgrade/run_frozen.py \
    --out spikes/0010-czsc-1.0.2-upgrade/reference-1.0.1
~/.venvs/czsc102-isolated/bin/python spikes/0010-czsc-1.0.2-upgrade/run_frozen.py \
    --out spikes/0010-czsc-1.0.2-upgrade/candidate-1.0.2 --override-pin

# 复用已有输出只做比较
python spikes/0010-czsc-1.0.2-upgrade/compare_1_0_1_vs_1_0_2.py --skip-run
```

> 注意：pin 已升级后，`reference-1.0.1` 侧在 1.0.1 环境下会被 `assert_engine_version` 拒绝（pin 现在是 1.0.2）。复现 1.0.1 侧需要 `git checkout ecd8bbc -- packages/chantheory/config.py pyproject.toml` 临时回退，或直接使用已提交的 `reference-1.0.1/` 输出。

---

## 8. 正式环境切换（本 PR 不执行）

本 PR **不切换** `~/.venvs/czsc`。合并后由维护者手动执行：

```bash
source ~/.venvs/czsc/bin/activate
python -m pip install -e .
python -c "import czsc; assert czsc.__version__ == '1.0.2', czsc.__version__"
python -m unittest discover -s packages/chantheory/tests -p 'test_*.py'
```

`pip install -e .` 会把 czsc 从 1.0.1 升到 1.0.2，并把 urllib3 从 2.7.0 升到 ≥2.8.0（1.0.2 wheel 的要求）。
