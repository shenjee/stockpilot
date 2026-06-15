# Phase 2 Tasks

## 目标

围绕开源 `czsc` 构建 Phase 2，不重复自研完整 Chan Theory 核心算法，而是在项目内建立 `chantheory` 适配层，完成输入标准化、结果统一、可视化输出与 `china-stock-analysis` 集成。

## 范围

- 底层引擎：`czsc`
- 项目核心交付物：`chantheory` 适配层
- 主输出：可视化结构结果
- 辅助输出：简短摘要与告警
- 调试界面：Streamlit 验证界面
- 集成目标：`skills/china-stock-analysis`

## 非目标

- [ ] Phase 2 不做完整 desktop app
- [ ] Phase 2 不从零实现完整 Chan Theory 核心
- [ ] Phase 2 不让上层直接依赖 `czsc` 原生对象
- [ ] Phase 2 不把缠论逻辑继续堆进日报脚本

## 执行原则

- [ ] 默认采用 `czsc` 作为底层引擎，但不让 skill、agent、UI 直接绑定其内部实现
- [ ] 所有项目侧逻辑统一沉淀在 `chantheory`，包括标准化、schema 映射、绘图数据、摘要和告警
- [ ] 先冻结输出契约，再做大范围集成
- [ ] 图形化输出优先，文本输出从属
- [ ] Streamlit 只作为调试与验算工具，不作为正式产品宿主

## 交付顺序

1. 先验证 `czsc` 是否适配当前项目环境与 A 股数据
2. 再冻结 `chantheory` 的适配层契约
3. 再建立 package 骨架与输入标准化能力
4. 再封装 `czsc` 并映射到统一 schema
5. 再冻结 `plot_primitives`、摘要与告警语义
6. 再建设 Streamlit 验证流程
7. 最后接入 `china-stock-analysis`
8. 收尾阶段补齐测试、性能检查与文档

## 核心交付物

- [x] 锁定并验证可用的 `czsc` 依赖策略
- [x] 建立 `packages/chantheory/` 适配层包
- [x] 冻结 Phase 2 对外 schema，统一使用 `snake_case`
- [x] 冻结 `plot_primitives` 绘图契约
- [x] 提供 Streamlit 调试与验算界面
- [ ] 提供 `china-stock-analysis` 的稳定接入路径
- [ ] 建立完整测试样例、回归基线与运行时预算

## P1：基础落地

### 目标

完成底层引擎验证、适配层契约冻结、包骨架搭建和最小可运行的数据流，为后续结构映射与可视化打基础。

### 交付物

- 已锁定的 `czsc` 版本
- `chantheory` 包骨架
- 输入标准化规则
- 第一版统一 schema

### 必须完成

- [x] 锁定 Phase 2 使用的 `czsc` 版本
- [x] 验证 `czsc` 的 Python 版本和安装约束
- [x] 验证当前 A 股 K 线数据能否转换为 `czsc` 输入对象
- [x] 验证项目所需周期是否被 `czsc` 正常支持
- [x] 检查现有本地数据字段是否足够支撑 `czsc`
- [x] 记录当前数据与 `czsc` 预期之间的差距
- [x] 明确 `chantheory` 是适配层而不是自研 Chan Theory 引擎
- [x] 定义 `chantheory` 对 skills、agents、apps 的公共 API
- [x] 冻结第一版统一输出契约：`fractals`、`strokes`、`segments`、`pivot_zones`、`divergences`
- [x] 冻结字段命名规范为 `snake_case`
- [x] 明确 `structure_alerts`、`candidate_buy_points`、`candidate_sell_points`
- [x] 明确 `plot_primitives`、`summary`、`warnings` 的职责边界
- [x] 明确错误处理和降级输出规则
- [x] 创建 `packages/chantheory/`
- [x] 添加 `__init__.py`
- [x] 添加 `normalize.py`
- [x] 添加 `adapters.py`
- [x] 添加 `schema.py`
- [x] 添加 `describe.py`
- [x] 添加 `plotting.py`
- [x] 添加 `config.py`
- [x] 添加最小 README
- [x] 定义项目标准 OHLCV 输入格式
- [x] 统一字段名、时间戳顺序、缺失值处理策略
- [x] 定义项目数据到 `czsc` 的周期映射
- [x] 定义适配层需要的包含关系与预处理策略
- [x] 准备合法、非法、边界输入样例
- [ ] 将复权方式纳入正式输入 schema 或 `meta`
- [ ] 明确交易日历、停牌和缺失 K 线的处理契约
- [ ] 明确分钟级 K 线聚合规则

### 完成标准

- [x] `czsc` 能在当前项目环境中稳定安装和调用
- [x] `chantheory` 包目录和基础模块已落地
- [x] 第一版 schema 和字段命名规则已冻结
- [x] 输入标准化规则足够支撑 day bar 封装开发
- [ ] 输入标准化规则覆盖复权、交易日历、停牌 / 缺失 K 线和分钟级聚合

## P2：结构输出与可视化

### 目标

完成 `czsc` 封装、结构结果映射、绘图契约定义、摘要与告警生成，并提供可视化验算手段。

### 交付物

- `czsc` 包装层
- 统一结构输出
- `plot_primitives`
- Streamlit 验证界面

### 必须完成

- [x] 构建项目 K 线到 `czsc` 输入对象的转换器
- [x] 封装稳定的 `czsc` 调用入口
- [x] 避免 `czsc` 内部类直接泄漏到上层
- [x] 把版本相关假设写入代码和文档
- [x] 把 `czsc` 结果映射为项目级 `fractals`
- [x] 把 `czsc` 结果映射为项目级 `strokes`
- [x] 把 `czsc` 结果映射或保守派生为项目级 `segments`，并记录 `mapping_strategy`
- [x] 把 `czsc` 结果映射为项目级 `pivot_zones`
- [x] 明确 `divergences` 在 Phase 2 的保守输出策略
- [x] 增加 symbol、timeframe、source、parameters、engine_version 等元数据
- [x] 明确 `candidate_buy_points`、`candidate_sell_points` 仅表示结构候选点，不等同于交易信号
- [x] 让 JSON 输出结构稳定可复用
- [x] 定义 `plot_primitives` 的点、线、框、标签、标记结构
- [x] 定义 K 线、Fractal、Stroke、Segment、Pivot Zone 的图层顺序
- [x] 定义颜色、样式和标注规范
- [x] 输出已知样例的 JSON 基线
- [x] 生成结构摘要，且保持短句化
- [x] 生成不足数据、结构不稳、转换异常等 `warnings`
- [x] 定义 agent 可读、日报可用的摘要文案
- [x] 创建 `apps/chan-streamlit/`
- [x] 添加 `app.py`
- [x] 添加 Streamlit README
- [x] 提供 symbol、timeframe、date range、参数控制项
- [x] 在 K 线图上叠加 `plot_primitives`
- [x] 添加 Fractal、Stroke、Segment、Pivot Zone、Divergence 的开关
- [x] 添加原始 JSON 检查视图
- [x] 添加 warnings 和 diagnostics 面板
- [x] 准备可视化验算样例数据

### 完成标准

- [x] `chantheory` 能输出稳定的结构结果和绘图原语
- [x] Streamlit 能完成结构叠加和结果核对
- [x] 摘要和告警能用于调试与日报接入
- [x] Phase 2 的视觉输出契约已基本稳定

## P3：业务接入与收口

### 目标

把 Phase 2 能力稳定接入 `china-stock-analysis`，并通过测试、性能、文档完成收口。

### 交付物

- skill 集成点
- 测试与回归样例
- 性能基线
- Phase 2 文档闭环

### 必须完成

- [ ] 在 `china-stock-analysis` 中增加 Phase 2 集成点
- [ ] 在调用前完成 K 线输入标准化
- [ ] skill 只调用 `chantheory`，不直接调用 `czsc`
- [ ] 把结构结果接入日报流水线
- [ ] 在日报中加入简短结构摘要
- [ ] 保证缠论分析失败时整份日报仍可降级输出
- [ ] 增加开关配置，可启停 Phase 2 分析
- [ ] 确认日报中只展示结构候选点，不把 candidate points 渲染为直接买卖指令
- [ ] 增加 normalization 和 schema conversion 单元测试
- [ ] 增加代表性 Chan Theory 结构回归样例
- [ ] 增加 `czsc` 结果映射测试
- [ ] 增加 `china-stock-analysis` 集成测试
- [ ] 增加 Streamlit 数据流 smoke tests
- [ ] 检查相同输入下输出是否具备确定性
- [ ] 测量真实 watchlist 规模下的运行耗时
- [ ] 定义日报执行可接受的运行时预算
- [ ] 仅在基线清楚后再做缓存或优化
- [ ] 验证短历史、缺失数据、部分结构情况下的表现
- [ ] 尽量把失败转为 warnings 而不是中断
- [ ] 记录最终 `czsc` 版本和依赖策略
- [ ] 记录 `chantheory` API 和统一 schema
- [ ] 记录 `plot_primitives` 契约
- [ ] 记录 Streamlit 验证流程
- [ ] 记录与 `china-stock-analysis` 的集成流
- [ ] 记录已知限制与 Phase 3 交接点
- [ ] 将 Phase 2 剩余数据口径 gaps 记录到用户可读文档：复权、交易日历、停牌 / 缺失 K 线、分钟级聚合

### 完成标准

- [ ] `china-stock-analysis` 已通过 `chantheory` 稳定消费 Phase 2 能力
- [ ] 关键测试、回归样例、运行时基线已具备
- [ ] 文档足够支撑后续实现和维护
- [ ] Phase 2 达到可验收状态

## 关键路径

- [x] 先验证并锁定 `czsc`
- [x] 再冻结 `chantheory` 适配层契约
- [x] 再完成 day bar 标准化与 `czsc` 封装
- [x] 再冻结统一 schema 和 `plot_primitives`
- [x] 再完成 Streamlit 验证界面
- [ ] 再接入 `china-stock-analysis`
- [ ] 最后补齐测试、性能与文档

## 验收标准

- [x] `czsc` 能在项目环境中稳定安装和使用
- [x] `chantheory` 对外暴露稳定的项目级 API 和 schema
- [x] 同一份样例 K 线输入能得到确定性的适配结果
- [x] `plot_primitives` 足以支撑 Phase 2 可视化验算
- [x] Streamlit 能渲染并检查主要结构结果
- [ ] `china-stock-analysis` 已通过 `chantheory` 接入，而非直接绑定 `czsc`
- [ ] Phase 2 的文本输出保持简短辅助，图形输出保持主导地位
- [ ] Phase 2 的结构候选买卖点不会被日报或 skill 输出升级为直接交易指令
- [ ] 复权、交易日历、停牌 / 缺失 K 线、分钟级聚合等数据口径 gaps 已有明确处理或限制说明
