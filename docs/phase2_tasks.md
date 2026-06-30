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
7. 再补齐与 `czsc` 案例对比后的关键能力差异，但排除 HTML 导出
8. 再接入 `china-stock-analysis`
9. 收尾阶段补齐测试、性能检查与文档

## 核心交付物

- [x] 锁定并验证可用的 `czsc` 依赖策略
- [x] 建立 `packages/chantheory/` 适配层包
- [x] 冻结 Phase 2 对外 schema，统一使用 `snake_case`
- [x] 冻结 `plot_primitives` 绘图契约
- [x] 提供 Streamlit 调试与验算界面
- [ ] 提供 `china-stock-analysis` 的稳定接入路径
- [ ] 建立完整测试样例、回归基线与运行时预算

## 短期不准备做的重点

说明：以下事项不是永久不做，而是当前阶段不作为 Phase 2 的优先交付物。

- [ ] 暂不做独立 HTML 导出能力
  原因：当前主要使用场景仍是本地 Streamlit 调试和验算，HTML 导出更偏分享、归档和产品展示，不是短期主链路
- [ ] 暂不优先实现离线可分享案例页或研报嵌入页
  原因：当前 Phase 2 的首要目标仍是项目内验证与业务接入，而不是对外分发产物
- [ ] 暂不把所有 `czsc` 信号函数一并暴露为完整产品能力
  原因：短期更适合先补项目级通用信号承载结构，再逐步选择需要的信号接入，避免过早扩面
- [ ] 暂不把前端渲染技术切换本身作为独立里程碑
  原因：当前优先级是补齐信号、多周期、副图和交互能力；是否采用 `lightweight_charts` 应服务于这些能力，而不是反过来主导阶段目标

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
- [x] 创建 `apps/chan-viewer/`
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

## P3：差异点补齐与信号增强

### 目标

基于与 `czsc` 两篇案例的对比，补齐当前项目在信号表达、多周期联立、副图体系、交互验证和背驰输出上的关键差异，但不把 HTML 导出作为本阶段交付目标。

### 交付物

- 通用信号输出层与项目级 schema
- 多周期分析与联立验证视图
- 副图体系与信号回放能力
- 更完整的调试交互与背驰输出

### 执行顺序

#### P3.1：信号契约与分层

先补分析层契约，避免后续 UI 和多周期能力建立在不稳定的数据表达上。

- [x] 定义通用信号输出层的项目级 schema，如 `signal_series`、`signal_events`、`signal_snapshots` 或等价结构
- [x] 支持把 `signals_config` 作为显式输入透传到 `chantheory` 分析流程
- [x] 明确通用信号输出与 `candidate_buy_points` / `candidate_sell_points` 的分层关系
- [x] 保持一买、二买、三买和一卖、二卖、三卖的项目候选点映射，同时补充其历史触发、切换、失效的回放表达
- [x] 在候选买卖点之外，支持接入其他 `czsc` 信号函数体系，如 MA 分类、涨跌停、表里关系等可配置信号

#### P3.2：多周期与信号回放

在信号契约稳定后，再补多周期分析与联立能力，确保信号表达能跨周期复用。

- [x] 增加多周期分析入口，明确基础周期、上采样周期和结果组织方式
- [x] 明确多周期结果在项目 schema 中的承载方式，避免上层直接依赖 `czsc` 的 `BarGenerator` / `CzscTrader`
- [x] 在 Streamlit 中提供真正的多周期联立验证视图，而不是只支持单周期切换
- [x] 为信号回放、多周期输入和分钟级场景补充确定性基线和回归样例

#### P3.3：副图体系与交互验证

在已有信号层和多周期能力之上，补齐调试界面缺失的副图和交互能力。

- [x] 增加副图体系，至少覆盖 volume、MACD，并为 signal timeline 预留或落地承载方式
- [x] 支持查看当前 bar 的信号详情，而不只显示基础 OHLC 信息
- [x] 增强调试交互，逐步补齐多周期 tab、图层开关、图例控制、跨图联动和更完整的数据卡能力

#### P3.4：背驰与回归收口

最后补背驰定义与回归闭环，避免在前面信号和多周期能力还未稳定时过早冻结口径。

- [x] 冻结 `divergences` 的项目级定义与映射策略，不再长期固定为空列表
- [x] 为背驰结果补充可视化表达与回归样例
- [x] 为通用信号输出、多周期结果、副图体系和背驰映射补充 focused tests

### 排期建议

说明：这里采用“迭代 / 波次”排期，而不是绑定自然周。每一波次都以“前一波次的契约或基础能力稳定”为前提，再进入下一波次。

#### 第 1 波：先做，必须串行

目标：先把信号层的项目契约稳定下来，避免后续多周期、副图和交互返工。

- [x] 完成 `signal_series`、`signal_events`、`signal_snapshots` 或等价 schema 设计
- [x] 完成 `signals_config` 输入方式和分析入口设计
- [x] 完成通用信号输出与 `candidate_buy_points` / `candidate_sell_points` 的分层约定
- [x] 完成一买、二买、三买和一卖、二卖、三卖的历史触发 / 切换 / 失效回放表达
- [x] 先补最小测试集，保证 schema、序列输出和候选点分层不反复改动

交付门槛：

- [x] `chantheory` 已能稳定输出至少一版通用信号结果
- [x] 候选买卖点与底层信号结果的边界已明确
- [x] 后续多周期与 UI 不需要再回头改动信号 schema

#### 第 2 波：跟进，可局部并行

目标：在通用信号契约稳定后，建立多周期分析和信号回放主链路。

- [x] 完成多周期分析入口
- [x] 完成多周期结果在项目 schema 中的承载方式
- [x] 补分钟级与多周期场景的确定性样例
- [x] 在不破坏单周期路径的前提下，准备 Streamlit 多周期联立验证所需的数据接口

并行建议：

- [ ] 一条线处理 `chantheory` 的多周期分析与 schema 承载
- [ ] 另一条线准备多周期回归样例、分钟级样例和测试数据

交付门槛：

- [x] 相同输入下，多周期输出具备确定性
- [x] UI 层已能拿到可用于联立展示的多周期结果

#### 第 3 波：可并行推进的验证层增强

目标：把前两波的分析结果真正转化为可验证、可对照的调试界面能力。

- [x] 增加 volume、MACD 副图
- [x] 增加 signal timeline 的承载方式
- [x] 增加当前 bar 的信号详情视图
- [x] 增加多周期 tab、图层开关、图例控制、跨图联动和更完整的数据卡

并行建议：

- [ ] 一条线处理 Plotly / Streamlit 的副图与交互实现
- [ ] 另一条线补 tooltip 数据、当前 bar 信号详情和 signal timeline 数据适配

交付门槛：

- [x] Streamlit 已能做多周期联立验证
- [x] 主图、副图、当前 bar 信号详情三者口径一致

#### 第 4 波：最后收口

目标：在信号、多周期、副图和交互能力基本稳定后，再冻结背驰口径并补回归。

- [x] 冻结 `divergences` 的项目级定义与映射策略
- [x] 增加背驰的可视化表达
- [x] 增加背驰 focused tests 与回归样例
- [x] 补齐 P3 全阶段回归，确认新增能力没有破坏既有结构映射和候选买卖点输出

交付门槛：

- [x] `divergences` 不再长期为空
- [x] P3 全阶段回归通过

#### 可并行与依赖关系

- [ ] `P3.1` 必须先完成，`P3.2`、`P3.3`、`P3.4` 都依赖它
- [ ] `P3.2` 完成前，不应冻结 `P3.3` 的多周期交互方案
- [ ] `P3.3` 可在 `P3.2` 进入稳定阶段后并行推进
- [ ] `P3.4` 最好放在最后，避免背驰定义被前面未稳定的信号或多周期结果带偏
- [ ] 如果人力有限，优先级顺序为：`P3.1` > `P3.2` > `P3.3` > `P3.4`

### 必须完成

- [x] 增加通用信号输出层，不再只依赖 `candidate_buy_points` / `candidate_sell_points` 承载全部信号表达
- [x] 为通用信号输出定义项目级 schema，如 `signal_series`、`signal_events`、`signal_snapshots` 或等价结构
- [x] 支持把 `signals_config` 作为显式输入透传到 `chantheory` 分析流程
- [x] 明确通用信号输出与 `candidate_buy_points` / `candidate_sell_points` 的分层关系
- [x] 保持一买、二买、三买和一卖、二卖、三卖的项目候选点映射，同时补充其历史触发、切换、失效的回放表达
- [x] 在候选买卖点之外，支持接入其他 `czsc` 信号函数体系，如 MA 分类、涨跌停、表里关系等可配置信号
- [x] 增加多周期分析入口，明确基础周期、上采样周期和结果组织方式
- [x] 明确多周期结果在项目 schema 中的承载方式，避免上层直接依赖 `czsc` 的 `BarGenerator` / `CzscTrader`
- [x] 在 Streamlit 中提供真正的多周期联立验证视图，而不是只支持单周期切换
- [x] 增加副图体系，至少覆盖 volume、MACD，并为 signal timeline 预留或落地承载方式
- [x] 支持查看当前 bar 的信号详情，而不只显示基础 OHLC 信息
- [x] 增强调试交互，逐步补齐多周期 tab、图层开关、图例控制、跨图联动和更完整的数据卡能力
- [x] 冻结 `divergences` 的项目级定义与映射策略，不再长期固定为空列表
- [x] 为背驰结果补充可视化表达与回归样例
- [x] 为通用信号输出、多周期结果、副图体系和背驰映射补充 focused tests
- [x] 为信号回放、多周期输入和分钟级场景补充确定性基线和回归样例

### 完成标准

- [x] `chantheory` 能在结构结果之外输出稳定的项目级通用信号结果
- [x] 候选买卖点与底层信号结果已经分层，且两者关系清晰可复用
- [x] Streamlit 能完成多周期联立验证，并查看副图和当前 bar 信号详情
- [x] 至少一版 `divergences` 输出已落地，不再固定为空
- [x] P3 的新增能力仍通过 `chantheory` 暴露，不引入上层对 `czsc` 原生对象的直接依赖

## P4：业务接入与收口

### 目标

把前述 Phase 2 能力稳定接入 `china-stock-analysis`，并通过测试、性能、文档完成收口。

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
- [ ] 记录已知限制与 Phase 4 交接点
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
- [ ] 再补齐信号、多周期、副图、交互和背驰等关键差异点
- [ ] 再接入 `china-stock-analysis`
- [ ] 最后补齐测试、性能与文档

## 验收标准

- [x] `czsc` 能在项目环境中稳定安装和使用
- [x] `chantheory` 对外暴露稳定的项目级 API 和 schema
- [x] 同一份样例 K 线输入能得到确定性的适配结果
- [x] `plot_primitives` 足以支撑 Phase 2 可视化验算
- [x] Streamlit 能渲染并检查主要结构结果
- [ ] 项目已补齐通用信号、多周期联立、副图体系和当前 bar 信号详情等关键差异能力
- [ ] 候选买卖点与通用信号已完成分层表达，且不会混淆为直接交易指令
- [ ] `divergences` 已具备项目级稳定输出，不再长期为空
- [ ] `china-stock-analysis` 已通过 `chantheory` 接入，而非直接绑定 `czsc`
- [ ] Phase 2 的文本输出保持简短辅助，图形输出保持主导地位
- [ ] Phase 2 的结构候选买卖点不会被日报或 skill 输出升级为直接交易指令
- [ ] 复权、交易日历、停牌 / 缺失 K 线、分钟级聚合等数据口径 gaps 已有明确处理或限制说明
