# 变更日志

StockPilot 的所有重要变更都会记录在这里。

## [1.2.0] - 2026-06-15

### 变更

- 将 Streamlit 调试应用重构为更小的 UI、service 和 chart 模块。
- 将股票分析报告流水线重构为 orchestrator、service、renderer、repository 和 CLI 模块。
- 将 app 侧与 report 侧的 K 线访问统一收口到共享的 `KLineDataService`。
- 将共享本地 K 线存储扩展为同时支持日线与分钟线周期。
- 将 app 和 skill 的测试移动到独立的 `tests/` 目录中，不再与生产模块混放。
- 加固 SQLite 连接生命周期管理，使本地 K 线存储可以干净关闭连接，避免 `ResourceWarning`。

## [1.0.0] - 2026-06-06

首次发布 StockPilot skill。

### 新增

- 新增可安装的 `china-stock-analysis` skill 目录结构。
- 新增中英文产品设计文档。
- 新增事实型 A 股日报工作流。
- 新增自选股与持仓配置支持。
- 新增本地 SQLite 日线 K 线缓存。
- 新增基础的指数、自选股、持仓、技术指标、策略规则和板块占位报告章节。
- 新增基础持仓字段，包括券商和买入订单记录。
- 新增私有工作区运行目录配置。
- 为日报新增完整技术指标输出：
  - MA5、MA10、MA20、MA60
  - MACD
  - KDJ
  - RSI
  - BOLL
  - 相对 20 日均量的成交量状态
  - 基于 5 日均量的量比
  - 换手率状态
  - 振幅
  - ATR14
- 新增对所有跟踪股票逐条输出结构化技术指标状态描述，而不再只显示前八行。
- 新增更丰富的 MACD 描述：零轴位置、红绿柱、放大或缩小、以及动能方向。
- 新增 BOLL 所在位置与波动宽度描述。
- 新增在无法计算换手率时明确输出“未配置流通股本”状态。
- 在 watchlist 和 portfolio 配置模板中新增可选 `float_shares` 示例。
- 新增 `scripts/market_data.py`，建立可插拔的行情 provider 边界。
- 新增腾讯财经作为默认行情 provider。
- 新增 `data_source.provider` 运行时配置支持。

### 变更

- 重构 `generate_report.py`，使报告生成不再直接拥有腾讯专属 HTTP 请求代码。
- 更新文档，以说明基于 provider 的行情数据架构。
- 将建议的脚本布局从 `fetch_kline.py` 更新为 `market_data.py`。
