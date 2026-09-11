# #177 正式切换结果

- 日期：2026-09-11
- 合入：PR [#179](https://github.com/shenjee/stockpilot/pull/179) squash → `e28737965a6362cfb09885ae32c26dd68faba14a`
- 正式代码：合并后的 `origin/main`
- 正式解释器：`/Users/jishen/.venvs/czsc/bin/python`（CPython 3.14.5）
- 结论：**正式切换验收通过。** 前四项 #173–#176 均已验收成立。

合入说明里的 “Do not close #177” 曾被 GitHub 当成 `close #177` 误关；已立即重开，本记录才是关闭依据。

---

## 1. 版本溯源

| 项 | 值 |
|---|---|
| merge SHA | `e28737965a6362cfb09885ae32c26dd68faba14a` |
| `pyproject.toml` | `czsc==1.0.1` |
| `PINNED_ENGINE_VERSION` | `1.0.1` |
| `czsc.__version__` | `1.0.1` |
| `wbt` | `0.9.0`（传递依赖） |
| `rs_czsc` | 已卸载；`pip show` 找不到；`import rs_czsc` → `ModuleNotFoundError` |
| `pip check` | No broken requirements found |
| 安装 | `pip install -e ".[dev]"`（editable `stockpilot==0.1.0`） |

单/多周期探针（冻结 #173 输入，正式 venv）：`analyze(5m/30m/day)`、`analyze_multi_timeframe` 及各 level、T+0 pipeline `_default_analyze_5m/_30m` 全部 `engine_version=1.0.1`。产物：`formal-switch-probe.json`。

依赖快照：`pip-freeze-formal-post-switch-1.0.1.txt`（本机快照，不是锁）。

---

## 2. `rs_czsc`

卸载前：`rs_czsc==0.1.26.post260402`，`Required-by:` 空；`czsc 1.0.1` Requires 不含它；仓库生产路径不 import。按方案卸载后 `pip check` 通过，入口仍为 `czsc._native`。

---

## 3. 进程重启与最小冒烟

无新增磁盘 chan 缓存，只重启会话内存。

| 表面 | 启动 | 关键路径 | `engine_version` |
|---|---|---|---|
| Live | Electron `PythonServiceHost`，`T0_PYTHON=~/.venvs/czsc/bin/python`，代码树 `e287379` | 选 600584、切 600000 再回来；不下单 | payload 5m/30m = `1.0.1` |
| Replay | 同上进程内切 Replay | 2026-07-14 开始、推进、回退、回 Live | payload 5m/30m = `1.0.1` |
| chan-viewer | 新进程：`$HOME/.venvs/czsc/bin/python -m streamlit run …/apps/chan-viewer/app.py --server.port 8502` | 600584 日线分析、切 5m/30m、切标的往返 | 摘要含 `czsc 1.0.1` |

证据：

- `ui-smoke/live/evidence.json`、`ui-smoke/replay/evidence.json`
- `ui-smoke/chan-viewer/ui_smoke_chan_viewer.json`（`engine_version_in_summary: true`）
- 下游自动化（合入后重跑，SHA `e287379`）：Live 动态 K、Replay e2e、Live↔Replay 生命周期、chan-viewer tests、`test_czsc_5m_spike` 全部 PASS。未改行为代码，未重跑 #176 对照/性能。

---

## 4. 缓存

按 #173 盘点：K 线/成交库无 `engine_version`；chan-viewer `st.session_state` 与 T0 `LiveProjectionStore` 为会话内存，已用新进程冒烟。不加 cache-bust 代码。

---

## 5. 回退

合入前隔离演练仍有效：旧 SHA `2883f34e956e49372f69c379b0ef45f8e63c929c` + 独立 venv + 冻结样本。步骤见同目录 `README.md` §6。本次正式冒烟通过，未执行回退。

---

## 6. 关闭规则

- 本记录通过后关闭 #177。
- #173 已在 PR #178 关闭；#174/#175/#176 已由 PR #179 关闭且升级分支验收记录仍成立。
- 前四项成立且本项完成后关闭 #172。
