# 回退演练记录（合入前）

- 日期：2026-09-11
- 结果：**PASS**
- 机器可读：`rollback-drill.json`
- 演练 venv freeze：`pip-freeze-rollback-drill-0.10.12.txt`（演练环境快照，不是旧正式环境备份）

## 做了什么

在**新建**隔离 venv `~/.venvs/czsc-rollback-drill-177` 中执行：

1. `python3.14 -m venv`（Python 3.14.5）
2. `pip install czsc==0.10.12`（由 PyPI 解析依赖，顺带装入 `rs-czsc`）
3. 使用旧代码树 `/Users/jishen/development/stockpilot-wt-01012` @ `2883f34`（`pyproject.toml` pin `czsc==0.10.12`）
4. `python .../generate_baseline.py --verify`

`--verify` 对三个冻结场景生成临时输出，与已提交 `checksums.sha256` 比较：**6/6 OK**。

探针：`czsc 0.10.12`，`RawBar_module=czsc.py.objects`（纯 Python 路径），解释器 `/Users/jishen/.venvs/czsc-rollback-drill-177/bin/python`。

## 明确没有当作证据的东西

- 没有降级或改写正式 `~/.venvs/czsc`（该环境已是 1.0.1）。
- 没有把现成 `~/.venvs/czsc01012-bench`「能跑」当作恢复步骤已验证。
- 没有 `pip install -r baseline/pip-freeze-prod-0.10.12.txt`。该 freeze 只是当时正式 venv 的包清单快照，含大量无关包，**不能**当作完整旧环境锁。

## 复现限制

本演练证明的是：**旧代码 SHA + `czsc==0.10.12` + 冻结样本** 能复现 #173 金标校验和。  
它不证明可以用一份 freeze 在任意机器上一键重建 2026-09 的原正式 venv。
