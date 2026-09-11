# #177 切换方案、回退演练与正式切换结果

父 issue：#172。合入前方案与隔离回退演练仍以本文 §1–§6 为准。  
正式运行验证已在 PR #179 合入后补齐，结论见 `switch-result-177.md`。

合并说明里不要写 `Closes #177` / `Closes #172`；也不要写 “Do not close #177”（GitHub 会把其中的 `close #177` 当成关闭关键字）。

---

## 1. 环境偏离（已锁定口径）

| 项 | 状态 |
|---|---|
| 正式 Python | **继续 3.14.5**，不换解释器 |
| `~/.venvs/czsc` | 在 #177 开始前 **已经是 czsc 1.0.1**（相对 #172「#177 才改正式环境」的提前偏离） |
| 原 0.10.12 正式 venv | **已被覆盖**，无法从当前正式环境导出完整旧备份 |
| 本轮是否把正式环境降回 0.10.12 再切一次 | **否** |

操作前快照（当前已偏离的正式环境，**不是**旧环境备份）：

- `formal-env-facts-pre-177.json`
- `pip-freeze-formal-current-1.0.1.txt`

旧环境恢复材料：

- `#173` `baseline/pip-freeze-prod-0.10.12.txt`（当时正式 venv 的 pip freeze **快照**）
- `~/.venvs/czsc01012-bench`（对照用旧 venv，**不能**仅凭它能运行就声称恢复步骤已验证）
- 已验证旧提交 `2883f34e956e49372f69c379b0ef45f8e63c929c`（worktree 示例：`stockpilot-wt-01012`；pin `czsc==0.10.12` + #173 冻结样本）。**合入后 `origin/main` 不再是回退目标。**

---

## 2. 依赖记录（不引入新锁文件体系）

「更新锁文件」修正为：**归档依赖快照与恢复说明**。

当前声明：

- `pyproject.toml`：`czsc==1.0.1`
- `wbt`：**传递依赖**，不写入直接依赖。正式环境实测 `wbt==0.9.0`（#173 隔离安装时为 0.8.2；`czsc 1.0.1` 要求 `wbt>=0.2.1`）

快照的复现限制：

1. `pip freeze` 含本机无关包，**不是**可完整重建该 venv 的锁。
2. `pip install -r pip-freeze-prod-0.10.12.txt` **不能**当作旧正式环境的一键恢复。
3. 可执行恢复是：检出已验证旧提交 `2883f34e956e49372f69c379b0ef45f8e63c929c` + `pip install czsc==0.10.12` + **该提交** `pyproject.toml` 声明的其余直接依赖，再用冻结样本验证。合入后 `origin/main` 的 pin 是 `czsc==1.0.1`，不能当回退代码。

---

## 3. 缓存 / 持久化（按 #173 盘点）

| 消费者 | 处理 |
|---|---|
| `stockpilot/db/market_data.sqlite` | 仅 K 线/证券，无 engine_version → 不迁移 |
| `stockpilot/db/t0_assistant.sqlite` | 偏好/费率/成交 → 不迁移 |
| chan-viewer `st.session_state` | 会话内存 → **重启 Streamlit 进程** |
| t0 `LiveProjectionStore` | 会话内存 → **重启 Electron/后端进程** |
| 测试 fixture JSON | #176 已按真实输出修正短样本；旧冻结基线保留 0.10.12 语义 |

合入后最小冒烟前：杀掉旧 Streamlit / Electron / PythonServiceHost，确认新结果来自 1.0.1（`engine_version` 与解释器路径写入冒烟记录）。无新增持久化风险，不加 cache-bust 代码。

---

## 4. `rs_czsc`

核查（正式 venv，2026-09-11）：

- `pip show rs_czsc`：`Required-by:` 空
- `czsc 1.0.1` 的 Requires **不含** `rs_czsc`（含 `wbt`）
- 本仓库生产路径不 import `rs_czsc`；实际入口为 `czsc._native`

口径：合入后的正式切换阶段**可以卸载**残留 `rs_czsc`，随后做依赖检查 + 最小冒烟。残留可导入本身不构成升级失败。本目录不在合入前改正式 venv。

---

## 5. 合入后正式执行清单（已完成）

PR #179 squash 合入 `e287379`。执行记录：`switch-result-177.md`、`formal-env-facts-post-177.json`、`formal-switch-probe.json`、`ui-smoke/`。

1. 合并 `upgrade/czsc-1.0.1` 一次进入 main（main pin 现为 `czsc==1.0.1`）。
2. 记录合并 SHA、解释器路径、`czsc.__version__`、`PINNED_ENGINE_VERSION`、`wbt`。
3. 卸载残留 `rs_czsc`，`pip check` 通过。
4. 重启 Live / Replay / chan-viewer 相关进程。
5. 最小冒烟：Live、Replay、chan-viewer 关键路径通过；单周期与 `analyze_multi_timeframe` 溯源均为 `1.0.1`。
6. 复用 #176 深度证据；本轮只卸载残留包并重跑最小冒烟 / 下游自动化，未改行为代码。
7. 切换结果写回本目录后，再关闭 #177 与 #172。

---

## 6. 回退步骤（可执行）

目标：旧代码 SHA + `czsc==0.10.12` + #173 冻结样本，并且 **Live / Replay / chan-viewer 实际加载这套组合**。

固定值（不要改成 `origin/main` 或 `~/.venvs/czsc`）：

```bash
OLD_SHA=2883f34e956e49372f69c379b0ef45f8e63c929c
OLD_ROOT="$HOME/development/stockpilot-wt-01012"   # 专用 worktree，不要复用已合入 1.0.1 的工作树
ROLLBACK_VENV="$HOME/.venvs/czsc-rollback"
ROLLBACK_PYTHON="$ROLLBACK_VENV/bin/python"
CURRENT_ROOT="$HOME/development/stockpilot"        # 仍停在 1.0.1 的仓库，只用来取 SHA / 跑本目录 helper
```

合入后 `git fetch origin && git switch --detach origin/main` 会落到 1.0.1；随后 `pip install -e ".[dev]"` 会按新 pin **再装回 1.0.1**。禁止这条路径。

### A. 检出已验证旧提交，并在安装前校验 pin

优先复用仍停在 `$OLD_SHA` 的 worktree；否则从当前仓库添加：

```bash
git -C "$CURRENT_ROOT" cat-file -e "${OLD_SHA}^{commit}" 2>/dev/null \
  || git -C "$CURRENT_ROOT" fetch origin "$OLD_SHA"
if [ ! -d "$OLD_ROOT/.git" ] && [ ! -e "$OLD_ROOT/.git" ]; then
  git -C "$CURRENT_ROOT" worktree add --detach "$OLD_ROOT" "$OLD_SHA"
fi
test "$(git -C "$OLD_ROOT" rev-parse HEAD)" = "$OLD_SHA"
grep -F 'czsc==0.10.12' "$OLD_ROOT/pyproject.toml"
grep -F 'PINNED_ENGINE_VERSION = "0.10.12"' "$OLD_ROOT/packages/chantheory/config.py"

# 也可从 1.0.1 树运行 helper（只读文件，不 import czsc；verify/probe 必须换恢复解释器）：
python3 "$CURRENT_ROOT/spikes/0009-czsc-1.0.1-upgrade/acceptance/verify_rollback.py" \
  --old-code-root "$OLD_ROOT" --check-pin-only
```

`HEAD`、`pyproject.toml` pin、`PINNED_ENGINE_VERSION` 任一项不是 0.10.12：**停止，不要安装。**

### B. 在独立恢复 venv 安装旧 pin

不要用 prod freeze 当完整锁，也不要激活 `~/.venvs/czsc`。

```bash
python3.14 -m venv "$ROLLBACK_VENV"
source "$ROLLBACK_VENV/bin/activate"
test "$(which python)" = "$ROLLBACK_PYTHON"
python -m pip install --upgrade pip
python -m pip install "czsc==0.10.12"
# 必须在旧代码树内安装，这样 .[dev] 解析的是 0.10.12 pin
python -m pip install -e "${OLD_ROOT}[dev]"
python -c "import czsc; assert czsc.__version__ == '0.10.12', czsc.__version__"
```

### C. 冻结样本 + 单/多周期版本探针

必须用 `$ROLLBACK_PYTHON`，且 `cwd` / 导入根为 `$OLD_ROOT`。新 adapter 会改写 provenance，checksum 对不上。

```bash
source "$ROLLBACK_VENV/bin/activate"
python "$CURRENT_ROOT/spikes/0009-czsc-1.0.1-upgrade/acceptance/verify_rollback.py" \
  --old-code-root "$OLD_ROOT" --probe-versions
```

通过条件：`generate_baseline.py --verify` 6/6；`czsc` / `PINNED_ENGINE_VERSION` / `analyze(5m)` / `analyze(30m)` / `analyze_multi_timeframe`（含各 level）均为 `0.10.12`；`chantheory.__file__` 落在 `$OLD_ROOT` 下。helper 若检测到 `~/.venvs/czsc` 会直接失败。

### D. 用旧代码目录 + 恢复环境启动应用

只停掉进程再按日常方式启动，仍会加载 `~/.venvs/czsc`（1.0.1）。chan-viewer 用 `__file__` 把 `packages/` 插到 `sys.path`；Electron 默认 `T0_PYTHON`/`PATH` 上的 python，且 `service.py` 以自身位置为仓库根。因此必须 **旧代码树 + 恢复解释器** 同时成立。

先结束仍占用正式环境的会话（Streamlit / Electron / `PythonServiceHost` / `backend/service.py`）。无磁盘 chan 缓存，不回放 1.0.1 分析结果。

```bash
# chan-viewer：绝对路径启动，与 §E pgrep 核验一致；不要 cd 后用相对脚本路径
test -x "$ROLLBACK_PYTHON"
"$ROLLBACK_PYTHON" -m streamlit run "$OLD_ROOT/apps/chan-viewer/app.py"

# T+0 Live / Replay：新 worktree 没有 node_modules，须先按该提交的锁文件安装
# 不要从 1.0.1 树 npm start，即使设置了 T0_PYTHON（会加载新 service.py / 新 packages）
cd "$OLD_ROOT/apps/t0-assistant"
test -f package-lock.json
npm ci
test -x "$ROLLBACK_PYTHON"
T0_PYTHON="$ROLLBACK_PYTHON" npm start
```

不要：`source ~/.venvs/czsc/bin/activate` 后启动；不要只改 `T0_PYTHON` 却在 1.0.1 目录 `npm start` / `streamlit run`。

### E. 启动后核验解释器与单/多周期版本

进程（解释器 + 代码目录）：

```bash
pgrep -lf 'streamlit run'        # 须含 $ROLLBACK_PYTHON 与 $OLD_ROOT/apps/chan-viewer/app.py
pgrep -lf 'backend/service.py'   # 须含 $ROLLBACK_PYTHON 与 $OLD_ROOT/apps/t0-assistant/backend/service.py
```

任一项仍指向 `~/.venvs/czsc` 或 1.0.1 工作树：**回退未生效，不要继续。**

版本：

- 再跑一次 §C 的 `--probe-versions`（同一 `$ROLLBACK_PYTHON` + `$OLD_ROOT`）。这覆盖 T0 pipeline 实际走的 `analyze(5m)` / `analyze(30m)`，以及 `analyze_multi_timeframe` 溯源。
- chan-viewer：摘要须出现 `czsc 0.10.12`；切换日线与分钟周期后再确认一次（`analyze_tracker_klines` 单周期路径）。
- T+0 Live 与 Replay：5m / 30m `chan_analysis.engine_version` 均为 `0.10.12`（payload 字段；Renderer 未必把版本画在界面上）。

隔离演练记录：`rollback-drill.md`。演练验证了 **SHA + 新 venv + 冻结样本 checksum**，**没有**把完整应用恢复当作已验证证据。应用启动命令以本节 D/E 为准。
