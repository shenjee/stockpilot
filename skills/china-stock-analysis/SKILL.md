---
name: china-stock-analysis
description: "中国A股个股分析与行情报告。用于分析A股股票、股票名称、股票代码、个股行情、技术指标、自选股、持仓盈亏和指数概览；当用户请求分析某只A股或查看某只股票时优先使用。"
metadata:
  author: stock-pilot
  version: 1.2.0
  category: finance
  tags:
    - a-share
    - china-stock
    - stock-analysis
    - individual-stock
    - technical-indicators
    - portfolio
    - daily-report
    - market-data
    - A股
    - 股票分析
    - 个股分析
    - 技术指标
    - 持仓分析
    - 自选股
  requires: []
---

# 中国A股个股分析与行情报告

分析中国A股个股、指数、自选股和持仓股，生成事实型行情报告。不做买卖建议，只描述数据事实。

## 适用场景

当用户请求分析中国A股、个股、股票名称、股票代码、自选股、持仓股、行情、技术指标、每日复盘或收盘报告时，优先使用本 skill。

典型触发语句：

- 分析股票-厦门钨业
- 帮我看看 600549
- 分析一下贵州茅台
- 今天我的持仓怎么样
- 生成A股收盘报告
- 看一下自选股技术指标

也可被理解为原 `china-stock-daily-tracker`、A股分析、股票分析助手或个股行情分析。

## 数据来源

- 默认使用腾讯财经公开 API provider（零依赖，标准库实现）
- 行情数据请求封装在 `scripts/market_data.py`；在完整仓库里优先复用共享 `marketdata` 包，在独立 skill 安装里自动回退到随 skill 分发的兼容实现
- 本地配置：`config/watchlist.yaml`、`config/portfolio.yaml`

## 功能

1. **个股行情分析** - 按股票名称或代码分析中国A股行情数据
2. **主要指数行情** - 上证指数、深证成指、创业板指等9大指数
3. **自选股追踪** - 从watchlist.yaml读取，输出价格、涨跌幅、成交量
4. **持仓股监控** - 从portfolio.yaml读取，输出盈亏、仓位变化
5. **本地K线库** - 使用 SQLite 缓存自选股/持仓股历史日线
6. **技术指标状态** - MA、MACD、KDJ、RSI、BOLL、成交量、换手率、振幅、ATR
7. **经验策略规则** - 从 strategy_rules.yaml 读取并展示规则状态，做事实型触发检查
8. **板块轮动** - V1预留接口
9. **定时报告** - 收盘简报(15:30)、复盘报告(20:30)

## 使用方式

运行脚本位于本 skill 目录的 `scripts/generate_report.py`。

私有数据目录不是 skill 安装目录。默认使用当前 agent/project 的工作目录作为 `workspace`，并在 `workspace/stockpilot/` 下保存运行数据：

- `stockpilot/config/`
- `stockpilot/db/`
- `stockpilot/reports/`

```bash
cd <workspace-dir>
python3 <skill-dir>/scripts/generate_report.py --type close
```

特殊部署时可用 `CHINA_STOCK_ANALYSIS_WORKSPACE` 或本地 `china-stock-analysis.local.json` 覆盖 workspace；旧的 `CHINA_STOCK_DAILY_TRACKER_WORKSPACE` 和 `china-stock-daily-tracker.local.json` 仍兼容。`runtime_dir` 可在 JSON 配置中覆盖；`config_dir`、`db_dir`、`reports_dir` 默认相对 `runtime_dir` 解析，传入绝对路径时按绝对路径解析。行情源可通过 `data_source.provider` 配置，当前支持 `tencent`。

## 配置文件

### config/watchlist.yaml - 自选股列表

```yaml
watchlist:
  - code: "600519"
    name: "贵州茅台"
    tags: ["白酒", "核心资产"]
  - code: "300750"
    name: "宁德时代"
    tags: ["新能源", "创业板"]
```

### config/portfolio.yaml - 持仓记录

```yaml
portfolio:
  - code: "600111"
    market: "sh"
    name: "北方稀土"
    position: 1000        # 持仓数量
    cost_price: 25.50     # 成本价
    broker: "示例券商"     # 券商（可选）
    target_weight: 0.15   # 目标仓位占比(可选)
    buy_orders:           # 建仓/加仓记录（可选）
      - trade_date: "2026-06-01"
        trade_time: "09:35:00"
        buy_price: 25.50
        quantity: 1000
        amount: 25500.00
  - code: "000001"
    market: "sz"
    name: "平安银行"
    position: 2000
    cost_price: 12.30
```

### config/strategy_rules.yaml - 经验策略规则

```yaml
strategies:
  - name: "20日均线趋势过滤"
    category: "均线"
    status: "active"
    check: "below_ma20"
    text: "不上20日均线，不确认趋势条件"
```

## 报告输出

- 路径：`<workspace>/<runtime_dir>/reports/daily_report_YYYYMMDD.md`
- 格式：Markdown
- 内容：指数概览、自选股行情、持仓盈亏、技术指标状态、经验规则、板块数据状态

## 输出边界

本 skill 生成事实型分析：行情、涨跌幅、成交量、技术指标、持仓盈亏、策略规则触发状态。

- 不输出确定性买卖建议
- 不承诺收益
- 不替代投资决策

## 约束

- 仅使用Python标准库
- 非交易日跳过生成
- 所有判断基于数据事实
- 不输出任何买卖建议
