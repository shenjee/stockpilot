# 定时任务配置

以下示例假设 stock agent workspace 位于 `~/.openclaw/workspace-stockpilot`，且 skill 安装在该 workspace 的 `skills/` 目录下：

```text
~/.openclaw/workspace-stockpilot/skills/china-stock-daily-tracker/
```

## 使用 cron 设置定时报告

### 方法1：系统 cron（推荐）

编辑 crontab：

```bash
crontab -e
```

添加以下内容：

```cron
# A股收盘简报 - 工作日 15:30
0 30 15 * * 1-5 cd ~/.openclaw/workspace-stockpilot && python3 skills/china-stock-daily-tracker/scripts/generate_report.py --type close >> reports/cron.log 2>&1

# A股复盘报告 - 工作日 20:30
0 30 20 * * 1-5 cd ~/.openclaw/workspace-stockpilot && python3 skills/china-stock-daily-tracker/scripts/generate_report.py --type review >> reports/cron.log 2>&1
```

### 方法2：OpenClaw 内置 cron

可以通过 OpenClaw 的 cron 功能设置：

```bash
# 收盘简报
openclaw cron add \
  --name "a-share-close-report" \
  --schedule "0 30 15 * * 1-5" \
  --command "cd ~/.openclaw/workspace-stockpilot && python3 skills/china-stock-daily-tracker/scripts/generate_report.py --type close"

# 复盘报告
openclaw cron add \
  --name "a-share-review-report" \
  --schedule "0 30 20 * * 1-5" \
  --command "cd ~/.openclaw/workspace-stockpilot && python3 skills/china-stock-daily-tracker/scripts/generate_report.py --type review"
```

## 手动测试

```bash
# 生成收盘简报
cd ~/.openclaw/workspace-stockpilot
python3 skills/china-stock-daily-tracker/scripts/generate_report.py --type close

# 生成复盘报告
python3 skills/china-stock-daily-tracker/scripts/generate_report.py --type review

# 强制生成（忽略交易日检查）
python3 skills/china-stock-daily-tracker/scripts/generate_report.py --type close --force
```

## 报告位置

生成的报告保存在：

```
~/.openclaw/workspace-stockpilot/reports/
├── daily_report_YYYYMMDD_close.md    # 收盘简报
└── daily_report_YYYYMMDD_review.md   # 复盘报告
```
