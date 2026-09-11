# #177 合入前切换方案与回退演练

父 issue：#172。本目录只覆盖**合入 main 之前**可完成的部分。  
正式运行验证、会话重启后的最小冒烟、以及是否卸载 `rs_czsc` 的执行结果，在合入后再补，**不得**用本目录关闭 #177 / #172。

合并 PR #179 时：**不要**写 `Closes #177` 或 `Closes #172`。

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
- main / worktree `stockpilot-wt-01012` @ `2883f34`（pin `czsc==0.10.12` + #173 冻结样本）

---

## 2. 依赖记录（不引入新锁文件体系）

「更新锁文件」修正为：**归档依赖快照与恢复说明**。

当前声明：

- `pyproject.toml`：`czsc==1.0.1`
- `wbt`：**传递依赖**，不写入直接依赖。正式环境实测 `wbt==0.9.0`（#173 隔离安装时为 0.8.2；`czsc 1.0.1` 要求 `wbt>=0.2.1`）

快照的复现限制：

1. `pip freeze` 含本机无关包，**不是**可完整重建该 venv 的锁。
2. `pip install -r pip-freeze-prod-0.10.12.txt` **不能**当作旧正式环境的一键恢复。
3. 可执行恢复是：检出旧代码（main / `2883f34`）+ `pip install czsc==0.10.12` + 项目声明的其余直接依赖，再用冻结样本验证。

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

## 5. 合入后正式执行清单（#177 未完成项）

1. 合并 `upgrade/czsc-1.0.1` 一次进入 main（此时 main pin 才变为 1.0.1）。
2. 记录合并 SHA、解释器路径、`czsc.__version__`、`PINNED_ENGINE_VERSION`、`wbt`。
3. 按需 `pip uninstall rs_czsc`，再 `pip check`。
4. 重启 Live / Replay / chan-viewer 相关进程。
5. 最小冒烟：Live、Replay、chan-viewer 各走关键路径；核验单周期与 `analyze_multi_timeframe` 溯源。
6. 复用 #176 深度证据；仅当本轮又改了行为/依赖时重跑受影响项。
7. 把切换结果写回本目录 / #177，**然后**才关闭 #177 与 #172。

---

## 6. 回退步骤（可执行）

目标：旧代码 + `czsc==0.10.12` + #173 冻结样本。

```bash
# A. 代码回到升级前 main（示例 SHA 以合入前 origin/main 为准）
git fetch origin
git switch --detach origin/main   # 合入前 pin 为 czsc==0.10.12

# B. 不要用 prod freeze 当完整锁。安装声明 pin：
python3.14 -m venv /path/to/czsc-rollback
source /path/to/czsc-rollback/bin/activate
python -m pip install --upgrade pip
python -m pip install "czsc==0.10.12"
python -m pip install -e ".[dev]"   # 该 checkout 的 pyproject 仍 pin 0.10.12

# C. 用冻结样本验证（必须在旧代码树内运行，版本守卫拒绝 1.0.1）
python spikes/0009-czsc-1.0.1-upgrade/baseline/generate_baseline.py --verify

# D. 重启应用会话；不回放 1.0.1 分析结果缓存（无磁盘 chan 缓存）
```

隔离演练记录：`rollback-drill.md`。演练使用**新建** venv `~/.venvs/czsc-rollback-drill-177`，不是现成 bench。
