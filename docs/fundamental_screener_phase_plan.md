# Fundamental Screener Phase Plan

## 1. 执行目标

本文档是 Fundamental Screener 的开发任务说明，供后续开发者或模型直接执行。目标是把 MVP 拆成严格的最小功能单元，按 Phase 逐步交付。

Fundamental Screener 是 StockPilot 的基本面量化筛选模块。它不是研报生成器，不输出买入/卖出建议，不预测下一个板块。它只做可量化指标、横向比较、排序、分组和风险标记。

第一条完整链路是：

```text
AkShare / 公开源
  -> 数据治理与 SQLite 本地缓存
  -> SqliteFundamentalRepository
  -> MarketSnapshot
  -> 板块轮动指标
  -> 板块内公司排序
  -> 财务质量对比
  -> 估值对比
  -> CLI JSON 输出
  -> Streamlit / skill / 日报消费
```

## 2. 执行规则

开发者必须遵守以下规则：

| 规则 | 要求 |
| --- | --- |
| 逐 Phase 执行 | 未完成当前 Phase 的 DoD，不进入下一 Phase |
| core 优先 | 先实现 `packages/fundamentalscreener/`，再做 app 或 skill |
| CLI 优先 | 每个可用能力必须先能通过 CLI 输出 JSON |
| UI 不承载算法 | Streamlit 只能调用 core/CLI，不允许重复实现计算逻辑 |
| skill 不承载算法 | skill 只能调用 CLI 或 core，不允许复制算法 |
| 输出可追溯 | 每个分数必须能回到原始指标 |
| 缺失可降级 | 数据缺失时输出 `warnings`，不要让整条命令崩溃 |
| 不做研报 | 不生成长文分析，不拼新闻故事 |
| 不做交易建议 | 不输出买入、卖出、加仓、清仓建议 |
| 不做板块预测 | 只展示历史和当前状态 |

## 3. 当前仓库事实

当前仓库路径：

```text
/Users/jishen/development/stockpilot
```

当前已有模块：

```text
packages/chantheory/              # 技术面缠论适配层
apps/chan-streamlit/              # 缠论调试 app
skills/china-stock-analysis/      # 当前 skill
stockpilot/db/market_data.sqlite  # 当前本地行情库
```

当前本地行情库只有：

```text
daily_klines
klines
```

因此 Fundamental Screener 的 Phase 0 和 Phase 1 必须支持 fixture/mock repository，不能假设已经存在完整的板块、财务、估值数据库。

Phase 0/1 的板块分类口径采用“口径无关，只定 schema”。不要在 Phase 0/1 绑定申万一级、中信一级或任何真实行业分类。`sector_id` 和 `sector_name` 只作为稳定字符串字段，fixture 使用概念板块式示例名即可，例如 `semiconductor` / `半导体`、`machinery` / `工程机械`。

后续接真实数据时，通过 `classification_system` 字段区分口径。第一版真实数据治理固定使用东方财富行业板块：

```text
em_industry # 东方财富行业板块，Phase 6 第一版真实数据口径
em_concept  # 东方财富概念板块，后续扩展
sw_l1       # 申万一级，后续扩展
citic_l1    # 中信一级，后续扩展
custom      # 用户自定义板块，后续扩展
```

## 4. 目标目录结构

Phase 0 必须创建：

```text
packages/fundamentalscreener/
├── __init__.py
├── schema.py
├── config.py
├── repositories.py
├── sector_rotation.py
├── company_ranking.py
├── financial_quality.py
├── valuation.py
├── screening.py
├── formatting.py
├── cli.py
└── tests/
    ├── fixtures/
    ├── test_schema.py
    ├── test_formatting.py
    └── test_cli.py
```

Phase 6 创建数据治理模块，Phase 7 才创建 Streamlit app：

```text
packages/fundamentalscreener/
├── data_sources/
│   └── akshare_source.py
├── sqlite_repository.py
├── sync.py
└── quality.py
```

```text
apps/fundamental-screener/
├── app.py
├── README.md
├── services/
└── tests/
```

不要在 Phase 0-5 创建 Streamlit 页面，除非用户明确要求提前做临时可视化。Streamlit 不属于数据治理模块，不能直接抓取 AkShare、东方财富或腾讯基本面数据。

Phase 0 必须创建完整目录结构中列出的所有 core 模块文件。暂未实现业务逻辑的模块使用空 stub，文件内容至少包含模块 docstring 和 `__all__ = []`。这样做是为了让目录结构、导入路径和后续 Phase 的落点从一开始稳定。

## 5. Phase 0 Fixture 规格

`packages/fundamentalscreener/tests/fixtures/minimal_market.json` 必须采用“板块 + 公司极简骨架”，不要使用完全空壳，也不要只放板块层预计算结果。

原因：

- Phase 0 需要验证 schema、repository、CLI 和 JSON 输出契约。
- Phase 1 会继续复用同一个 fixture 做板块收益、相对大盘、成交额变化、上涨家数占比和 chart series 计算。
- Phase 2 会继续复用同一个 fixture 做板块内公司排名。
- 完全空壳无法验证数据路径，后续每个 Phase 都会重新改 fixture。
- 只放预计算板块指标会绕过计算逻辑，不利于 Phase 1 测试。

### 5.1 Fixture 最小内容

Fixture 必须包含：

| 数据 | 最小数量 | 用途 |
| --- | ---: | --- |
| 基准指数 | 1 个 | 计算相对大盘和绘制基准线 |
| 板块 | 2 个 | 验证板块排序、状态和 Top N |
| 每个板块公司 | 2-3 家 | 验证上涨家数占比、成交额、公司排名 |
| 日线数据 | 至少 61 个交易日 | 支持 1/5/20/60 日收益计算 |
| 公司行情 | 每家公司至少 61 条 | 支持板块聚合和公司排序 |
| 板块成分关系 | 每家公司归属一个板块 | 支持 `companies --sector` |

### 5.2 Fixture 推荐结构

```json
{
  "date": "2026-06-19",
  "classification_system": "concept",
  "benchmark": {
    "id": "hs300",
    "name": "沪深300",
    "daily": [
      {
        "date": "2026-03-20",
        "close": 100.0,
        "turnover_amount": 100000000000.0
      }
    ]
  },
  "sectors": [
    {
      "sector_id": "semiconductor",
      "sector_name": "半导体",
      "constituents": ["002371", "600584"],
      "daily": [
        {
          "date": "2026-03-20",
          "close": 100.0,
          "turnover_amount": 10000000000.0
        }
      ]
    },
    {
      "sector_id": "machinery",
      "sector_name": "工程机械",
      "constituents": ["000001", "000002"],
      "daily": [
        {
          "date": "2026-03-20",
          "close": 100.0,
          "turnover_amount": 8000000000.0
        }
      ]
    }
  ],
  "companies": [
    {
      "code": "002371",
      "name": "示例公司A",
      "sector_id": "semiconductor",
      "market_cap": 120000000000.0,
      "daily": [
        {
          "date": "2026-03-20",
          "close": 10.0,
          "turnover_amount": 1000000000.0,
          "turnover_rate": 0.02
        }
      ]
    }
  ]
}
```

实际 fixture 中的 `daily` 数组必须补足至少 61 个交易日。日期可以是连续工作日或手工构造的交易日序列，但必须按日期升序排列。

### 5.3 Phase 0 对 Fixture 的使用边界

Phase 0 只要求：

- [ ] `FixtureRepository` 能读取 `date`、`benchmark`、`sectors`、`companies`。
- [ ] CLI 能通过 `--fixture` 加载该文件。
- [ ] `sectors --fixture ... --format json` 能返回稳定 JSON 框架。
- [ ] 测试能验证 fixture 被读取，而不是完全忽略。

Phase 0 不要求：

- [ ] 不要求计算真实 `return_1d`、`return_5d`、`return_20d`、`return_60d`。
- [ ] 不要求计算真实 `relative_return`。
- [ ] 不要求计算真实 `rising_stock_ratio`。
- [ ] 不要求计算真实 `chart_series`。

这些计算从 Phase 1 开始实现。

## 6. CLI 规范

在项目尚无正式 packaging 之前，稳定入口使用：

```bash
python -m packages.fundamentalscreener.cli <command> [options]
```

最终可以再升级为：

```bash
stockpilot fundamental <command> [options]
```

Phase 0 的 CLI 入口和依赖策略必须严格按以下规则执行：

| 项 | 规则 |
| --- | --- |
| CLI 入口 | 只使用 `python -m packages.fundamentalscreener.cli ...` |
| 根命令 | Phase 0 不实现 `stockpilot fundamental ...` |
| 依赖 | Phase 0 只使用 Python 标准库 |
| 禁止 | Phase 0 不新增 pandas、numpy、click、typer、rich 等依赖 |
| 参数解析 | 使用标准库 `argparse` |
| JSON 输出 | 使用标准库 `json` |
| CSV 输出 | 后续需要时使用标准库 `csv` |
| 日期处理 | 使用标准库 `datetime` |
| 文件读取 | 使用标准库 `pathlib` 和 `json` |

Phase 1+ 如确实需要新增轻量依赖，必须先说明用途、替代方案和影响范围，并由用户确认。即使后续引入依赖，CLI 的稳定调用方式仍应保持兼容：

```bash
python -m packages.fundamentalscreener.cli ...
```

第一版 CLI 命令：

```text
sectors
sector-detail
companies
financials
valuations
screen
```

统一参数：

| 参数 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--date` | 否 | 最近可用交易日 | 分析日期，格式 `YYYY-MM-DD` |
| `--format` | 否 | `json` | `json`、`markdown`、`csv` |
| `--top` | 否 | `20` | 返回条数 |
| `--sort` | 否 | 命令默认列 | 排序字段 |
| `--fixture` | 否 | 空 | 使用 fixture 数据文件，Phase 0/1 必须支持 |
| `--classification-system` | 否 | `concept` | 板块分类口径，Phase 0/1 只保留字段和参数 |

命令专用参数：

| 命令 | 参数 |
| --- | --- |
| `sectors` | `--classification-system`、`--benchmark`、`--periods`、`--sort`、`--top` |
| `sector-detail` | `--sector`、`--classification-system`、`--benchmark`、`--periods` |
| `companies` | `--sector`、`--classification-system`、`--top`、`--sort` |
| `financials` | `--codes`、`--sort` |
| `valuations` | `--codes`、`--sort` |
| `screen` | `--sector-top`、`--company-top`、`--benchmark` |

`--classification-system` 在 Phase 0/1 默认为 `concept`。Phase 0/1 不需要接入真实申万/中信分类，只需要保留参数和输出字段。

命令示例：

```bash
python -m packages.fundamentalscreener.cli sectors \
  --date 2026-06-19 \
  --benchmark hs300 \
  --periods 1,5,20,60 \
  --sort return_1d \
  --top 20 \
  --format json
```

```bash
python -m packages.fundamentalscreener.cli companies \
  --sector 半导体 \
  --date 2026-06-19 \
  --sort combined_score \
  --top 10 \
  --format markdown
```

```bash
python -m packages.fundamentalscreener.cli screen \
  --date 2026-06-19 \
  --benchmark hs300 \
  --sector-top 10 \
  --company-top 5 \
  --format json
```

## 7. JSON 输出契约

CLI 的 `json` 输出必须稳定。字段使用 `snake_case`。所有 JSON 输出顶层都应包含统一的 `snapshot` 对象，用于向 CLI、Streamlit 和 skill 透传快照血缘与质量状态。

```json
{
  "snapshot": {
    "snapshot_id": "",
    "analysis_date": "",
    "data_cutoff": "",
    "data_quality_status": "ok",
    "source_set": {},
    "fetch_run_id": "",
    "quality_report_id": "",
    "config_version": "",
    "formula_version": "",
    "generated_at": ""
  }
}
```

### 7.1 sectors 输出

```json
{
  "snapshot": {
    "snapshot_id": "snapshot-20260619-001",
    "analysis_date": "2026-06-19",
    "data_cutoff": "2026-06-19",
    "data_quality_status": "ok",
    "source_set": {"sector": "akshare_em", "quote": "tencent"},
    "fetch_run_id": "fetch-20260619-001",
    "quality_report_id": "quality-20260619-001",
    "config_version": "fundamental-screener-config-v1",
    "formula_version": "fundamental-screener-formula-v1",
    "generated_at": "2026-06-20T15:00:00+08:00"
  },
  "command": "sectors",
  "date": "2026-06-19",
  "classification_system": "concept",
  "benchmark": "hs300",
  "sort": "return_1d",
  "periods": [1, 5, 20, 60],
  "sectors": [
    {
      "sector_id": "semiconductor",
      "sector_name": "半导体",
      "classification_system": "concept",
      "return_1d": 0.021,
      "return_5d": 0.084,
      "return_20d": 0.152,
      "return_60d": 0.223,
      "relative_return": 0.068,
      "turnover_amount_change": 0.35,
      "market_turnover_share": 0.08,
      "rising_stock_ratio": 0.78,
      "rank_change_5d": 5,
      "state": "strong",
      "score": 82.0,
      "warnings": []
    }
  ],
  "chart_series": [
    {
      "series_id": "semiconductor",
      "series_name": "半导体",
      "type": "sector",
      "points": [
        {"date": "2026-06-01", "value": 100.0},
        {"date": "2026-06-19", "value": 115.2}
      ]
    },
    {
      "series_id": "hs300",
      "series_name": "沪深300",
      "type": "benchmark",
      "points": []
    }
  ],
  "warnings": []
}
```

### 7.2 companies 输出

```json
{
  "snapshot": {
    "snapshot_id": "snapshot-20260619-001",
    "analysis_date": "2026-06-19",
    "data_cutoff": "2026-06-19",
    "data_quality_status": "ok",
    "source_set": {"sector": "akshare_em", "quote": "tencent", "financial": "akshare_em"},
    "fetch_run_id": "fetch-20260619-001",
    "quality_report_id": "quality-20260619-001",
    "config_version": "fundamental-screener-config-v1",
    "formula_version": "fundamental-screener-formula-v1",
    "generated_at": "2026-06-20T15:00:00+08:00"
  },
  "command": "companies",
  "date": "2026-06-19",
  "classification_system": "concept",
  "sector_id": "semiconductor",
  "sector_name": "半导体",
  "sort": "combined_score",
  "companies": [
    {
      "code": "002371",
      "name": "示例公司",
      "market_cap": 120000000000.0,
      "turnover_amount": 3500000000.0,
      "turnover_rate": 0.032,
      "sector_return_rank": 5,
      "leader_score": 80.0,
      "attention_score": 75.0,
      "financial_quality_score": null,
      "valuation_score": null,
      "combined_score": 77.5,
      "group": "watch",
      "flags": [],
      "warnings": []
    }
  ],
  "warnings": []
}
```

### 7.3 financials 输出

`financials` 按股票代码查询，不绑定板块分类口径，顶层不需要 `classification_system`。

```json
{
  "snapshot": {
    "snapshot_id": "snapshot-20260619-001",
    "analysis_date": "2026-06-19",
    "data_cutoff": "2026-06-19",
    "data_quality_status": "ok",
    "source_set": {"financial": "akshare_em"},
    "fetch_run_id": "fetch-20260619-001",
    "quality_report_id": "quality-20260619-001",
    "config_version": "fundamental-screener-config-v1",
    "formula_version": "fundamental-screener-formula-v1",
    "generated_at": "2026-06-20T15:00:00+08:00"
  },
  "command": "financials",
  "date": "2026-06-19",
  "companies": [
    {
      "code": "002371",
      "name": "示例公司",
      "revenue_yoy": 0.18,
      "net_profit_yoy": 0.25,
      "deducted_net_profit_yoy": 0.21,
      "gross_margin": 0.36,
      "net_margin": 0.12,
      "roe": 0.14,
      "operating_cashflow_to_profit": 1.2,
      "free_cashflow": 1000000000.0,
      "debt_to_asset": 0.42,
      "interest_bearing_debt_ratio": 0.18,
      "accounts_receivable_yoy": 0.10,
      "inventory_yoy": 0.08,
      "score": 79.0,
      "abnormal_flags": [],
      "warnings": []
    }
  ],
  "warnings": []
}
```

### 7.4 valuations 输出

`valuations` 按股票代码查询，不绑定板块分类口径，顶层不需要 `classification_system`。

```json
{
  "snapshot": {
    "snapshot_id": "snapshot-20260619-001",
    "analysis_date": "2026-06-19",
    "data_cutoff": "2026-06-19",
    "data_quality_status": "ok",
    "source_set": {"valuation": "akshare_em"},
    "fetch_run_id": "fetch-20260619-001",
    "quality_report_id": "quality-20260619-001",
    "config_version": "fundamental-screener-config-v1",
    "formula_version": "fundamental-screener-formula-v1",
    "generated_at": "2026-06-20T15:00:00+08:00"
  },
  "command": "valuations",
  "date": "2026-06-19",
  "companies": [
    {
      "code": "002371",
      "name": "示例公司",
      "pe": 28.0,
      "pb": 3.2,
      "ps": 4.5,
      "peg": 1.1,
      "dividend_yield": 0.012,
      "pe_percentile": 0.45,
      "pb_percentile": 0.52,
      "industry_valuation_position": "mid",
      "score": 72.0,
      "label": "fair",
      "warnings": []
    }
  ],
  "warnings": []
}
```

### 7.5 screen 输出

`candidates` 的每个分组（`priority` / `watch` / `cautious`）包含若干 candidate 对象。candidate 在 `company_ranking` 输出的 `CompanyEntry` 基础上追加 `sector_id` / `sector_name`、并把硬约束（`valuation.label == "not_applicable"` ⇒ 降级到 `cautious`）后的最终 `group` 回写到字段本身，保证字段 `group` 始终与所在 bucket 一致。`flags` 透传自 `FinancialEntry.abnormal_flags`（例如 `weak_cashflow`、`receivable_growth_risk`、`high_debt`）。`financial` / `valuation` 子对象分别复用 `FinancialEntry.to_dict()` 与 `ValuationEntry.to_dict()`，供下游解释每个分数的来源（DoD：所有分数可追溯）。

```json
{
  "snapshot": {
    "snapshot_id": "snapshot-20260619-001",
    "analysis_date": "2026-06-19",
    "data_cutoff": "2026-06-19",
    "data_quality_status": "ok",
    "source_set": {"sector": "akshare_em", "quote": "tencent", "financial": "akshare_em", "valuation": "akshare_em"},
    "fetch_run_id": "fetch-20260619-001",
    "quality_report_id": "quality-20260619-001",
    "config_version": "fundamental-screener-config-v1",
    "formula_version": "fundamental-screener-formula-v1",
    "generated_at": "2026-06-20T15:00:00+08:00"
  },
  "command": "screen",
  "date": "2026-06-19",
  "classification_system": "concept",
  "benchmark": "hs300",
  "selected_sectors": [],
  "candidates": {
    "priority": [],
    "watch": [
      {
        "code": "002371",
        "name": "示例公司",
        "sector_id": "BK0001",
        "sector_name": "示例板块",
        "market_cap": 1.2e10,
        "turnover_amount": 3.4e8,
        "turnover_rate": 0.025,
        "sector_return_rank": 3,
        "leader_score": 78.0,
        "attention_score": 65.0,
        "financial_quality_score": 70.0,
        "valuation_score": 72.0,
        "combined_score": 71.5,
        "group": "watch",
        "flags": ["weak_cashflow"],
        "warnings": [],
        "financial": {
          "code": "002371",
          "name": "示例公司",
          "revenue_yoy": 0.18,
          "net_profit_yoy": 0.22,
          "deducted_net_profit_yoy": 0.20,
          "gross_margin": 0.35,
          "net_margin": 0.12,
          "roe": 0.14,
          "operating_cashflow_to_profit": 0.6,
          "free_cashflow": 1.2e8,
          "debt_to_asset": 0.55,
          "interest_bearing_debt_ratio": 0.30,
          "accounts_receivable_yoy": 0.4,
          "inventory_yoy": 0.15,
          "score": 70.0,
          "abnormal_flags": ["weak_cashflow"],
          "warnings": []
        },
        "valuation": {
          "code": "002371",
          "name": "示例公司",
          "pe": 28.0,
          "pb": 3.2,
          "ps": 4.5,
          "peg": 1.1,
          "dividend_yield": 0.012,
          "pe_percentile": 0.45,
          "pb_percentile": 0.52,
          "industry_valuation_position": "mid",
          "score": 72.0,
          "label": "fair",
          "warnings": []
        }
      }
    ],
    "cautious": []
  },
  "warnings": []
}
```

注：上例为结构示意，仅在 `watch` 中展示一条 candidate；硬约束触发时同样的 candidate 会出现在 `cautious` 桶里，且 `group` 字段会被覆盖为 `"cautious"`。

## 8. 术语和枚举

### 8.1 板块状态

| 值 | 中文展示 | 含义 |
| --- | --- | --- |
| `strong` | 当前强势 | 短中期涨幅靠前，相对大盘为正 |
| `improving` | 正在增强 | 近 5 日排名提升，成交额变化为正 |
| `overheated` | 高位过热 | 近 20/60 日涨幅分位高，短期涨幅过快 |
| `low_level_active` | 低位异动 | 近 60 日不强，近 5 日和成交额改善 |
| `neutral` | 普通 | 不满足以上规则 |

### 8.2 候选分组

| 值 | 中文展示 | 含义 |
| --- | --- | --- |
| `priority` | 优先研究 | 板块强，公司综合分高，财务和估值没有明显硬伤 |
| `watch` | 继续观察 | 有亮点，但存在估值、现金流、数据缺失或趋势问题 |
| `cautious` | 谨慎/排除 | 财务异常明显、估值过高或流动性不足 |

### 8.3 估值标签

| 值 | 中文展示 |
| --- | --- |
| `low_need_quality_check` | 偏低但需确认质量 |
| `fair` | 合理 |
| `expensive_but_supported` | 偏贵但成长可支撑 |
| `expensive` | 明显偏贵 |
| `not_applicable` | 不适用 |

## 9. 排序规则

`sectors` 默认排序：

```text
sort = return_1d desc
```

支持排序字段：

```text
return_1d
return_5d
return_20d
return_60d
relative_return
turnover_amount_change
rising_stock_ratio
score
```

不作为默认主要排序列：

```text
rank_change_5d
state
```

相对大盘定义：

```text
relative_return = sector_period_return - benchmark_period_return
```

它是正/负百分比，不是布尔值。

## 10. 模块职责

| 文件 | 必须提供 | 禁止 |
| --- | --- | --- |
| `schema.py` | dataclass 或等价结构、`to_dict()`/序列化辅助 | 读取数据库、计算分数 |
| `config.py` | 默认周期、权重、阈值、排序字段、枚举常量 | 读取用户私有配置 |
| `repositories.py` | Repository 接口、FixtureRepository、后续 SQLiteRepository 占位 | 写业务评分逻辑 |
| `sector_rotation.py` | 板块收益、相对大盘、成交额变化、上涨家数占比、状态、chart series | 处理公司财务 |
| `company_ranking.py` | 板块内公司排名、规模分、资金关注分、综合分占位 | 三张表计算 |
| `financial_quality.py` | 财务指标、财务评分、异常 flags | 估值计算 |
| `valuation.py` | 估值指标、历史分位、行业位置、标签、估值分 | DCF |
| `screening.py` | 编排各模块，输出 `screen` 结果 | CLI 参数解析、UI |
| `formatting.py` | JSON、Markdown、CSV 输出 | 业务计算 |
| `cli.py` | argparse、命令分发、退出码 | 具体算法 |

## 11. 测试要求

每个 Phase 至少要有 focused tests。优先使用 `unittest`，保持和当前仓库风格一致。

建议测试命令：

```bash
python -m unittest discover -s packages/fundamentalscreener/tests -p 'test_*.py'
```

Phase 0 必须通过：

```bash
python -m unittest packages.fundamentalscreener.tests.test_schema
python -m unittest packages.fundamentalscreener.tests.test_formatting
python -m unittest packages.fundamentalscreener.tests.test_cli
```

CLI smoke test：

```bash
python -m packages.fundamentalscreener.cli sectors --fixture packages/fundamentalscreener/tests/fixtures/minimal_market.json --format json
```

## 12. Phase 0：骨架、契约、空 CLI

### 目标

创建包骨架，冻结 schema、配置、输出字段和 CLI 命令。Phase 0 不接真实行情、财务、估值数据。

### 必须创建的文件

```text
packages/fundamentalscreener/__init__.py
packages/fundamentalscreener/schema.py
packages/fundamentalscreener/config.py
packages/fundamentalscreener/repositories.py
packages/fundamentalscreener/sector_rotation.py
packages/fundamentalscreener/company_ranking.py
packages/fundamentalscreener/financial_quality.py
packages/fundamentalscreener/valuation.py
packages/fundamentalscreener/screening.py
packages/fundamentalscreener/formatting.py
packages/fundamentalscreener/cli.py
packages/fundamentalscreener/tests/fixtures/minimal_market.json
packages/fundamentalscreener/tests/test_schema.py
packages/fundamentalscreener/tests/test_formatting.py
packages/fundamentalscreener/tests/test_cli.py
```

### 必须实现

- [ ] `schema.py` 定义本文档第 7 节对应的结果结构。
- [ ] `config.py` 定义默认周期 `[1, 5, 20, 60]`。
- [ ] `config.py` 定义默认排序字段 `return_1d`。
- [ ] `config.py` 定义默认基准 `hs300`。
- [ ] `config.py` 定义默认输出格式 `json`。
- [ ] `repositories.py` 定义 `FixtureRepository`，从 JSON fixture 读取数据。
- [ ] `sector_rotation.py`、`company_ranking.py`、`financial_quality.py`、`valuation.py`、`screening.py` 创建为空 stub，至少包含模块 docstring 和 `__all__ = []`。
- [ ] `formatting.py` 支持 JSON 输出。
- [ ] `cli.py` 支持 `sectors`、`sector-detail`、`companies`、`financials`、`valuations`、`screen` 六个子命令。
- [ ] 六个子命令都能返回合法 JSON 框架。
- [ ] 未实现的真实计算字段可以为空数组、`null` 或空 `warnings`，但字段名必须稳定。

### Phase 0 禁止

- [ ] 不接 Streamlit。
- [ ] 不接 skill。
- [ ] 不读取真实 SQLite。
- [ ] 不实现复杂评分。
- [ ] 不加入新第三方依赖，除非用户明确同意。

### DoD

- [ ] `python -m packages.fundamentalscreener.cli sectors --fixture packages/fundamentalscreener/tests/fixtures/minimal_market.json --format json` 返回合法 JSON。
- [ ] JSON 顶层含 `command`、`date`、`warnings`。
- [ ] `python -m unittest packages.fundamentalscreener.tests.test_schema` 通过。
- [ ] `python -m unittest packages.fundamentalscreener.tests.test_formatting` 通过。
- [ ] `python -m unittest packages.fundamentalscreener.tests.test_cli` 通过。
- [ ] `python -m unittest discover -s packages/fundamentalscreener/tests -p 'test_*.py'` 通过。

## 13. Phase 1：板块轮动 CLI

### 目标

实现板块轮动核心指标。数据源先使用 fixture repository；如果接真实数据，必须保持 fixture 测试不变。

### 必须实现

- [ ] `sector_rotation.py`。
- [ ] 计算 `return_1d`、`return_5d`、`return_20d`、`return_60d`。
- [ ] 计算 `relative_return`。
- [ ] 计算 `turnover_amount_change`。
- [ ] 计算 `market_turnover_share`。
- [ ] 计算 `rising_stock_ratio`。
- [ ] 计算 `rank_change_5d`。
- [ ] 输出 `state`。
- [ ] 输出 `score`。
- [ ] 输出 `chart_series`，包含板块归一化走势和基准线。
- [ ] `sectors` 支持 `--sort return_1d|return_5d|return_20d|return_60d|relative_return|turnover_amount_change|rising_stock_ratio|score`。
- [ ] `sector-detail` 支持 `--sector`。
- [ ] `formatting.py` 支持 Markdown 输出。

### 状态规则第一版

用简单规则，不要过度优化：

| 状态 | 规则 |
| --- | --- |
| `strong` | `return_5d > 0` 且 `return_20d > 0` 且 `relative_return > 0` |
| `low_level_active` | `return_60d <= 0` 且 `return_5d > 0` 且 `turnover_amount_change > 0` |
| `improving` | `return_5d > 0` 且 `turnover_amount_change > 0` 且不满足 `strong`、不满足 `low_level_active` |
| `overheated` | `return_20d` 在样本前 20% 且 `return_5d` 在样本前 20% |
| `neutral` | 其他 |

如果多个规则同时命中，优先级：

```text
overheated -> strong -> low_level_active -> improving -> neutral
```

`low_level_active` 放在 `improving` 之前，是为了让"60 日仍弱、近 5 日上涨并放量"的早期低位信号不被通用的 `improving` 抢占。

### DoD

- [ ] `sectors --sort return_1d --format json` 正常。
- [ ] `sectors --sort return_5d --format markdown` 正常。
- [ ] `relative_return` 是可正可负的小数，例如 `0.068` 表示 `+6.8%`。
- [ ] `rank_change_5d` 和 `state` 展示但不作为默认排序。
- [ ] 单元测试覆盖排序、相对大盘、状态优先级。

## 14. Phase 2：板块内公司排名

### 目标

给定一个板块，输出该板块内公司排名。Phase 2 可以暂时没有真实财务和估值分，但字段必须保留。

### 必须实现

- [x] `company_ranking.py`。
- [x] `companies --sector <sector>`。
- [x] 输出公司 `code`、`name`、`market_cap`、`turnover_amount`、`turnover_rate`、`sector_return_rank`。
- [x] 计算 `leader_score`。
- [x] 计算 `attention_score`。
- [x] `financial_quality_score` 和 `valuation_score` 在未接入时输出 `null`。
- [x] 计算 `combined_score`。
- [x] 输出 `group`：`priority`、`watch`、`cautious`。
- [x] 支持 `--top`。
- [x] 支持 JSON、Markdown、CSV。

### 第一版评分

```text
combined_score =
  leader_score * 0.4
+ attention_score * 0.6
```

Phase 3/4 接入后升级为（已在 `combined_score 升级` 小步骤中落地）：

```text
combined_score =
  leader_score * 0.2
+ attention_score * 0.2
+ financial_quality_score * 0.35
+ valuation_score * 0.25
```

实现说明：

- `companies` 命令在板块内部一次性调用 `compute_financial_quality` 和
  `compute_valuation` 补齐 fin/val 分数，无需调用方手动传入；财务分仍按
  cohort 归一化（一次板块内的统一归一化优于按公司各自跑一次）。
- 单个分量缺失（例如某家公司没有估值数据）→ 按可用权重重新归一，避免
  整体打成 0；`fin/val` 同时缺失时自动退化为 Phase 2 的 leader+attention。
- fin/val 子命令的 `missing_field` warnings 不会被堆叠到 `companies` 视图，
  保持单一职责（细节请去对应子命令查看）。
- 分组阈值（`priority >= 70 / watch >= 50 / cautious`）本轮保持不变，
  会在 Phase 5 编排里再配合 flags / 硬伤一起重新评估。

### DoD

- [x] `companies --sector <sector> --top 10 --format json` 正常。
- [x] 每家公司都有原始指标和分项分数。
- [x] 未接入财务/估值时不能崩溃。
- [x] 不输出买入/卖出建议。

## 15. Phase 3：财务质量对比

### 目标

实现三张表关键数字的横向对比和异常检测。第一版只做量化指标，不做文字研报。

### 必须实现

- [x] `financial_quality.py`。
- [x] `financials --codes <comma_separated_codes>`。
- [x] 输出 `revenue_yoy`、`net_profit_yoy`、`deducted_net_profit_yoy`。
- [x] 输出 `gross_margin`、`net_margin`、`roe`。
- [x] 输出 `operating_cashflow_to_profit`、`free_cashflow`。
- [x] 输出 `debt_to_asset`、`interest_bearing_debt_ratio`。
- [x] 输出 `accounts_receivable_yoy`、`inventory_yoy`。
- [x] 输出 `score`。
- [x] 输出 `abnormal_flags`。

### 异常规则第一版

| flag | 规则 |
| --- | --- |
| `weak_cashflow` | `net_profit_yoy > 0` 且 `operating_cashflow_to_profit < 0.5` |
| `receivable_growth_risk` | `accounts_receivable_yoy > revenue_yoy + 0.2` |
| `inventory_growth_risk` | `inventory_yoy > revenue_yoy + 0.2` |
| `high_debt` | `debt_to_asset > 0.7` |
| `gross_margin_decline` | fixture 或数据源提供同比下降字段时启用，否则跳过 |
| `weak_core_profit` | `deducted_net_profit_yoy < net_profit_yoy - 0.2` |

### DoD

- [x] `financials --codes 002371,600584 --format json` 正常。
- [x] 缺失数据输出 `warnings`，不导致整条命令失败。
- [x] 测试覆盖至少 3 个异常 flag。

## 16. Phase 4：估值对比

### 目标

实现相对估值，不做 DCF。

### 必须实现

- [x] `valuation.py`。
- [x] `valuations --codes <comma_separated_codes>`。
- [x] 输出 `pe`、`pb`、`ps`、`peg`、`dividend_yield`。
- [x] 输出 `pe_percentile`、`pb_percentile`。
- [x] 输出 `industry_valuation_position`。
- [x] 输出 `score`。
- [x] 输出 `label`。

### 标签规则第一版

| label | 规则 |
| --- | --- |
| `not_applicable` | `pe`、`pb`、`pe_percentile`、`pb_percentile` 任一关键字段缺失或不适用 |
| `expensive_but_supported` | `pe_percentile > 0.80` 或 `pb_percentile > 0.80`，且 `peg` 提供且 `peg <= 1.5` |
| `expensive` | `pe_percentile > 0.80` 或 `pb_percentile > 0.80`，且 `peg` 缺失或 `peg > 1.5` |
| `low_need_quality_check` | `pe_percentile < 0.35` 或 `pb_percentile < 0.35` |
| `fair` | `0.35 <= pe_percentile <= 0.70` 且 `0.35 <= pb_percentile <= 0.70` |

说明：`expensive_but_supported` 是 `expensive` 的"成长可支撑"降级解释，
两者互斥（同一公司只可能命中其中之一）。多规则命中时优先级：

```text
not_applicable -> expensive_but_supported -> expensive -> low_need_quality_check -> fair
```

### DoD

- [x] `valuations --codes 002371,600584 --format json` 正常。
- [x] 不适用或缺失指标输出 `not_applicable` 和 `warnings`。
- [x] 不实现 DCF。

## 17. Phase 5：完整筛选编排

### 目标

把板块、公司、财务和估值串成完整筛选链路。

### 必须实现

- [x] `screening.py`。
- [x] `screen --sector-top <N> --company-top <N>`。
- [x] 自动取 Top N 板块。
- [x] 对每个板块取 Top N 公司。
- [x] 补齐候选公司的财务质量和估值。
- [x] 使用 Phase 2 升级后的综合分公式。
- [x] 输出 `selected_sectors`。
- [x] 输出 `candidates.priority`、`candidates.watch`、`candidates.cautious`。
- [x] 输出完整原始指标、分项分数、flags、warnings。

### Supplement

- [x] Phase 5 接入估值结果时，`label=not_applicable` 必须优先作为硬约束处理。即使 Phase 4 仍会在部分估值字段缺失时按可用分量计算 `score`，筛选编排也不能只按 `score` 排名而忽略 `not_applicable`。

### DoD

- [x] `screen --sector-top 10 --company-top 5 --format json` 正常。
- [x] JSON 可直接供 skill 调用。
- [x] 分组逻辑可解释。
- [x] 所有分数可追溯。

## 18. Phase 6：数据治理与真实数据接入

### 目标

把测试 fixture 升级为独立的数据治理模块。真实数据必须先经过采集、SQLite 本地缓存、标准化和质量检查，再由 repository 组装为 `MarketSnapshot`。Streamlit、skill 和 CLI 不直接联网抓基本面数据。

第一版真实板块口径固定为：

```text
classification_system = "em_industry"
```

### 数据链路

```text
AkShare / 公开源
  -> SQLite 本地缓存
  -> 数据质量检查
  -> SqliteFundamentalRepository
  -> MarketSnapshot
  -> core
  -> CLI / Streamlit / Skill
```

### 数据源边界

| 数据 | 第一版主源 | 说明 |
| --- | --- | --- |
| 行业板块列表 | AkShare 封装的东方财富行业板块 | 固定 `em_industry`，不混用概念、申万、中信 |
| 行业板块成分 | AkShare 封装的东方财富行业成分 | 生成板块-股票关系 |
| 板块历史行情 | AkShare 封装的东方财富板块行情 | 支撑 1/5/20/60 日收益和 chart series |
| 个股行情/K线 | 现有腾讯财经 provider | 继续只做行情和技术数据补充 |
| 公司日度快照 | AkShare 东方财富实时行情，必要时腾讯补充 | 市值、PE、PB、PS、成交额、换手率 |
| 财务指标 | AkShare 公开源 | ROE、毛利率、净利率、成长、现金流、负债等 |
| 新闻/公告/研报 | 暂不接入 | 第二阶段再考虑文本数据 |
| Tushare | 暂不考虑 | 需要 token/充值，不作为免费 MVP 主路径 |

腾讯财经不扩展成基本面 provider。东方财富裸接口可以作为后续 fallback，但第一版不作为主实现，避免字段变化、限流和网络断开影响核心链路。

### 必须实现

- [ ] 新增数据源抽象，至少覆盖 `list_sectors()`、`get_sector_constituents()`、`get_sector_daily()`、`get_benchmark_daily()`、`get_stock_universe()`、`get_company_daily_snapshot()`、`get_company_valuation_history()`、`get_financial_metrics()`。
- [ ] 新增 AkShare 数据源实现，第一版只支持 `em_industry`。
- [ ] 新增 SQLite 初始化和同步脚本。
- [ ] 新增 `SqliteFundamentalRepository`，从 SQLite 组装现有 `MarketSnapshot`。
- [ ] 新增数据质量检查和质量报告。
- [ ] 保留 `FixtureRepository` 作为测试和 fallback，不把 fixture 当真实数据源。
- [ ] 同步失败写入 `data_fetch_log`，不能破坏已有可用缓存。

### 数据源抽象

真实数据源应只负责读取外部数据并返回标准化前的结构化结果；SQLite 写入、质量检查和 `MarketSnapshot` 组装属于同步与 repository 层。

| 方法 | 用途 |
| --- | --- |
| `list_sectors(classification_system)` | 获取行业板块列表 |
| `get_sector_constituents(sector_id, as_of_date)` | 获取板块成分 |
| `get_sector_daily(sector_id, start_date, end_date)` | 获取板块历史行情 |
| `get_benchmark_daily(benchmark, start_date, end_date)` | 获取基准指数历史行情 |
| `get_stock_universe(as_of_date)` | 获取股票池、市场、上市状态 |
| `get_company_daily_snapshot(trade_date)` | 获取公司日度行情与交易快照 |
| `get_company_valuation_history(codes, start_date, end_date)` | 获取或生成估值历史 |
| `get_financial_metrics(codes, as_of_date)` | 获取 point-in-time 可用的财务指标 |

### SQLite 表

第一版建议创建：

| 表 | 作用 |
| --- | --- |
| `stocks` | 股票代码、名称、市场、上市状态 |
| `sectors` | 板块代码、名称、分类口径、来源 |
| `sector_constituents` | 板块-股票关系，带 `source` 和 `as_of_date` |
| `sector_daily_bars` | 板块历史行情 |
| `company_daily_snapshot` | 公司日度行情与交易快照，长期保存市值、收盘价、成交额、换手率、涨跌幅等 |
| `company_valuation_history` | 公司日度估值历史，长期保存 PE、PB、PS、股息率等，用于本地计算历史分位 |
| `financial_metrics` | 公司财务指标，按报告期和披露日保存 ROE、毛利率、净利率、成长、现金流、负债等 |
| `data_fetch_log` | 来源、任务、时间、成功/失败、错误信息、行数、`fetch_run_id` |

关键表必须带可追溯字段：

| 字段 | 适用表 | 说明 |
| --- | --- | --- |
| `source` | 所有采集表 | 数据源，例如 `akshare_em`、`tencent` |
| `fetch_run_id` | 所有采集表 | 一次同步任务的唯一 ID，用于追溯批次 |
| `source_updated_at` | 所有采集表 | 来源数据更新时间，无法获得时记录抓取时间 |
| `created_at` / `updated_at` | 所有本地表 | 本地写入和更新审计 |
| `raw_field_name` | 标准化映射表或质量报告 | 外部字段名，便于定位字段变化 |

`financial_metrics` 至少需要以下时间字段：

| 字段 | 说明 |
| --- | --- |
| `report_period` | 财报报告期，例如 `2026Q1`、`2025A` |
| `period_end_date` | 报告期截止日 |
| `disclosure_date` | 公告或披露日期，用于避免未来函数 |
| `period_type` | `quarterly | semiannual | annual | ttm` |
| `as_of_date` | 当前记录对分析系统可见的日期 |

`company_valuation_history` 至少需要 `trade_date`、`code`、`market`、`pe`、`pb`、`ps`、`dividend_yield`、`source`、`fetch_run_id`。PE/PB 历史分位应基于本地保存的日度估值历史计算，而不是只保存最新快照。

### 标准化规则

- [ ] 百分比统一转成小数，例如 `0.18` 表示 18%。
- [ ] 金额统一为明确单位，优先使用元。
- [ ] 日期统一为 `YYYY-MM-DD`。
- [ ] 股票代码、市场和板块成分必须能稳定对齐。
- [ ] 财务/估值缺失写入 warnings，不静默吞掉。
- [ ] 估值分位优先基于本地历史 PE/PB 计算，不直接信任外部返回。
- [ ] 财务数据必须按 `analysis_date` 做 point-in-time 过滤：只能使用 `disclosure_date <= analysis_date` 且 `as_of_date <= analysis_date` 的记录。
- [ ] 同一公司同一报告期出现多源或多版本数据时，必须保留来源和批次信息，并由 repository 按明确优先级选择当前有效记录。
- [ ] 负 PE、亏损公司、停牌、ST、退市风险、缺失估值字段必须显式标记，不应用 0 替代。
- [ ] `MarketSnapshot.date` 是分析日期，不等于财报报告期，也不等于采集时间。

### 质量检查

- [ ] 板块必须有 `sector_id`、`sector_name`、`classification_system`。
- [ ] 每个板块至少有成分股。
- [ ] benchmark 必须有历史行情。
- [ ] 板块日线至少覆盖 60 个交易日。
- [ ] 公司 code 必须能在 companies、financials、valuations 中对齐。
- [ ] 网络采集失败时，CLI/UI/skill 可以读取最近一次成功缓存并输出 warnings。
- [ ] 行情和估值数据的 `trade_date` 应为最近可用交易日，过旧时输出 stale warning。
- [ ] 行业板块成分覆盖率应达到配置阈值，低于阈值时降级或阻断。
- [ ] 财务指标覆盖率应在质量报告中展示，例如候选公司中多少比例有可用最新财报。
- [ ] `data_fetch_log` 应记录成功/失败、行数、耗时、错误摘要和是否使用缓存。

质量检查需要区分严重级别：

| 级别 | 示例 | 处理 |
| --- | --- | --- |
| `error` | 无 benchmark、板块日线不足 60 个交易日、核心表不存在 | 阻断生成 `MarketSnapshot` |
| `warning` | 部分公司缺财务或估值、个别板块成分缺行情 | 输出 warnings，保留可用数据 |
| `info` | 使用最近一次缓存、部分字段来自 fallback 源 | 展示数据来源和新鲜度 |

### 快照、血缘与质量状态契约

- [ ] 每次生成 `MarketSnapshot` 必须产生 `snapshot_id`，并记录 `analysis_date`、`data_cutoff`、`source_set`、`fetch_run_id`、`quality_report_id`、`config_version`、`formula_version`、`generated_at`。
- [ ] CLI/JSON、Streamlit 和 skill 消费结果时，应能展示或透传快照血缘信息。
- [ ] 所有时变数据必须按 `analysis_date` 截断：行情/估值使用 `trade_date <= analysis_date`，板块成分、股票池、上市状态使用 `as_of_date <= analysis_date` 或 `valid_from <= analysis_date < valid_to`，财务数据使用 `availability_date <= analysis_date`。
- [ ] `availability_date` 通常取 `disclosure_date` / `announcement_date` 中可证明市场可见的日期。
- [ ] `source_set` 记录本次快照实际使用的数据源集合及其角色，例如 `sector=akshare_em`、`quote=tencent`、`financial=akshare_em`。
- [ ] 质量状态统一为 `ok | degraded | stale | invalid`，至少应有 snapshot 级状态；必要时可附带 sector/company/metric 级别的局部状态。
- [ ] `degraded` / `stale` 数据可以展示和解释，但不得进入 `priority` 候选组；`invalid` 数据不得生成 `MarketSnapshot` 或综合评分。
- [ ] 估值分位配置必须版本化，并纳入 `config_version` 或 `formula_version`，第一版至少覆盖窗口长度、最小有效样本数、非正 PE 和亏损公司的处理规则。

### 禁止

- [ ] 不让 Streamlit 直接调用 AkShare、东方财富或腾讯基本面接口。
- [ ] 不让 skill 直接抓基本面数据。
- [ ] 不把 AkShare、SQLite 或腾讯接口细节泄漏到 core 算法。
- [ ] 不在第一版混用 `em_industry` 和 `em_concept`。
- [ ] 不接新闻、公告、研报。

### DoD

- [ ] 可以同步 `em_industry` 行业板块列表、成分股和板块历史行情到 SQLite。
- [ ] 可以同步股票池、公司日度快照、估值历史和财务指标到 SQLite。
- [ ] 财务指标按 `analysis_date` 做 point-in-time 过滤，不读取分析日之后才披露的数据。
- [ ] 可以基于本地 `company_valuation_history` 计算 PE/PB 历史分位。
- [ ] 可以从 SQLite 读取并组装 `MarketSnapshot`。
- [ ] `MarketSnapshot` 可被现有 sector/company/financial/valuation/screening core 消费。
- [ ] 同步过程输出质量报告和 `data_fetch_log`。
- [ ] 网络失败时可降级读取最近一次成功缓存。

## 19. Phase 7：Streamlit MVP

### 目标

在 core/CLI 和数据治理边界稳定后做独立 Streamlit 数据工作台。Streamlit 只消费 repository、snapshot 或 core/CLI 输出，不承担采集、标准化或评分算法。

当前已完成的 Streamlit 项属于基于 fixture/core 的界面验证。接入真实数据前仍必须完成 Phase 6；Phase 7 的真实数据验收以“读取 Phase 6 的 SQLite/repository，并展示数据日期、来源和质量 warnings”为准。

### 必须实现

- [x] 创建 `apps/fundamental-screener/app.py`。
- [x] 创建 `apps/fundamental-screener/README.md`。
- [x] 通过 core 或 CLI 获取 `sectors` 结果。
- [x] 绘制板块归一化走势曲线和基准线。
- [x] 展示板块指标结果表。
- [x] 支持表格点击/选择板块。
- [x] 展示板块内公司排名。
- [x] 展示财务质量横向对比。
- [x] 展示估值横向对比。
- [x] 展示异常 flags。
- [ ] 接入 Phase 6 的 SQLite/repository 数据源作为真实数据入口。
- [ ] 页面展示数据日期、来源和 warnings。

### 禁止

- [x] 不在 Streamlit 中复制排序、评分、异常检测算法。
- [x] 不生成研报。
- [x] 不输出买卖建议。
- [ ] 不在 Streamlit 中直接联网抓基本面数据。

### DoD

- [x] `streamlit run apps/fundamental-screener/app.py` 可启动。
- [x] 页面能完成“板块 -> 公司 -> 财务/估值”的浏览。
- [x] 页面数据来自 core/CLI 输出。
- [ ] 页面可读取 Phase 6 的真实数据缓存。
- [ ] 页面明确展示数据日期和质量 warnings。

## 20. Phase 8：Skill 和日报集成

### 目标

让 `skills/china-stock-analysis` 调用 Fundamental Screener，把基本面量化结果接入自选股和持仓场景。skill 只消费 CLI/core/repository 输出，不直接实现数据治理。

### 必须实现

- [ ] skill 通过 CLI 或 core 调用 Fundamental Screener。
- [ ] 自选股日报展示股票所属板块状态。
- [ ] 持仓报告展示板块相对强弱。
- [ ] 持仓报告展示财务质量分和估值标签。
- [ ] 输出表格和短标签。
- [ ] 基本面调用失败时，原有日报仍可生成。

### 禁止

- [ ] 不在 skill 内复制 Fundamental Screener 算法。
- [ ] 不在 skill 内直接抓 AkShare、东方财富或腾讯基本面数据。
- [ ] 不生成长篇基本面研报。
- [ ] 不输出买卖建议。

### DoD

- [ ] skill 可以读取 CLI JSON。
- [ ] 自选股/持仓报告出现基本面量化表格。
- [ ] Fundamental Screener 异常时，报告输出 warnings，但不中断原有技术面内容。

## 21. 第一批开发任务

如果从零开始编码，只执行以下任务，不要越界：

1. 创建 `packages/fundamentalscreener/`。
2. 创建 `schema.py`、`config.py`、`repositories.py`、`formatting.py`、`cli.py`。
3. 创建 `sector_rotation.py`、`company_ranking.py`、`financial_quality.py`、`valuation.py`、`screening.py` 空 stub。
4. 创建 `packages/fundamentalscreener/tests/fixtures/minimal_market.json`。
5. 创建 `test_schema.py`、`test_formatting.py`、`test_cli.py`。
6. 让 `sectors --fixture ... --format json` 返回稳定空结构。
7. 跑通 Phase 0 测试。

不要在第一批任务中实现真实板块计算、Streamlit、skill 集成、财务、估值。

## 22. 开发完成后必须汇报

每次完成一个 Phase，最终回复必须包含：

- 修改了哪些文件。
- 实现了哪些命令。
- 跑了哪些测试命令。
- 哪些能力还没有做。
- 是否有数据缺失或降级。

如果某个 Phase 做不到，不要跳过，要说明阻塞原因。
