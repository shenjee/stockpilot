# Stock Pilot 安装与本机运行

本项目不依赖 OpenClaw。第一版采用常规方式安装：从 GitHub clone 源码，用 Python 标准库脚本运行，把私有配置、数据库和报告放在独立 workspace。

## 1. 安装源码

```bash
mkdir -p ~/development
cd ~/development
git clone https://github.com/shenjee/china-stock-daily-tracker.git
cd china-stock-daily-tracker
```

## 2. 准备私有工作区

```bash
mkdir -p "$HOME/Documents/Stock Pilot"
```

第一次运行会自动创建：

- `config/watchlist.yaml`
- `config/portfolio.yaml`
- `config/strategy_rules.yaml`
- `db/stockpilot.sqlite`
- `reports/`

## 3. 配置自选股和持仓

编辑：

```text
$HOME/Documents/Stock Pilot/config/watchlist.yaml
$HOME/Documents/Stock Pilot/config/portfolio.yaml
$HOME/Documents/Stock Pilot/config/strategy_rules.yaml
```

配置示例在：

```text
assets/config_templates/
```

真实持仓、成本价、本地数据库和报告都应只保存在 workspace，不要提交到 Git 仓库。

## 4. 手动生成报告

```bash
python3 scripts/generate_report.py \
  --workspace "$HOME/Documents/Stock Pilot" \
  --type close
```

历史日期测试：

```bash
python3 scripts/generate_report.py \
  --workspace "$HOME/Documents/Stock Pilot" \
  --date 2026-05-29 \
  --type close \
  --force
```

也可以使用环境变量：

```bash
export STOCKPILOT_WORKSPACE="$HOME/Documents/Stock Pilot"
python3 scripts/generate_report.py --type close
```

## 5. 定时运行

使用 macOS/Linux 常规 `cron`：

```cron
30 15 * * 1-5 cd ~/development/china-stock-daily-tracker && STOCKPILOT_WORKSPACE="$HOME/Documents/Stock Pilot" python3 scripts/generate_report.py --type close
30 20 * * 1-5 cd ~/development/china-stock-daily-tracker && STOCKPILOT_WORKSPACE="$HOME/Documents/Stock Pilot" python3 scripts/generate_report.py --type review
```

## 6. 输出位置

默认报告输出到：

```text
$HOME/Documents/Stock Pilot/reports/
```

本地 SQLite K 线库输出到：

```text
$HOME/Documents/Stock Pilot/db/stockpilot.sqlite
```
