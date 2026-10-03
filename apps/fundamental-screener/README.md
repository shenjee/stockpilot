# Fundamental Screener Streamlit Frontend

基本面量化工作台，用来浏览 `packages/fundamentalscreener` core/repository 的输出。

## 角色边界

- 仅做可视化与导航。
- 不重复实现板块轮动 / 公司排名 / 财务评分 / 估值评分 / 异常 flags 检测。
- 不生成研报，不输出买卖建议，不预测板块。
- 不向用户暴露 fixture、SQLite、数据库路径或 CLI 参数。

所有计算都来自 `packages/fundamentalscreener`，本 app 通过 `services/data_service.py`
调用 core 函数，按表格 / 折线图渲染。

## 启动

```bash
source ~/.venvs/czsc/bin/activate
streamlit run apps/fundamental-screener/app.py
```

依赖：

- `streamlit`
- `pandas`（仅折线图用）
- `packages/fundamentalscreener`

如缺失：

```bash
python -m pip install streamlit pandas
```

## 数据获取机制

产品界面默认使用真实市场数据：

- 数据源：同花顺行业板块，当前通过 AkShare 封装读取。
- 对照源：东方财富行业板块，仅用于开发对照和差异分析，不作为同花顺失败时的 fallback。
- 本地缓存：由应用内部管理，用于网络失败时保留最近可用结果。
- 用户动作：点击 `获取数据 / 运行分析` 后同步数据、质量检查并展示结果。

fixture 只用于测试和开发 smoke，不是产品数据源，不应出现在用户界面。

## 页面结构

- 顶部操作区：获取数据 / 运行分析、分析日期、数据状态。
- 顶部信息卡：日期 / 数据截止 / 分类口径 / 基准 / 板块数 / 质量状态。
- 板块归一化走势曲线（板块 + 基准）。
- 板块指标表（受侧边栏排序字段和 Top N 控制）。
- 板块下钻：
  - 公司排名表（按 `combined_score` 排序）。
  - 财务质量横向对比表。
  - 估值横向对比表。
- 公司 / 财务 / 估值的异常 flags + 估值 label 汇总。
- 数据质量 warnings 和板块详情 warnings 折叠显示。

## 开发计划

该小前端的产品功能和执行步骤见：

```text
docs/fundamental_screener_streamlit_frontend_plan.md
```

## 测试

```bash
source ~/.venvs/czsc/bin/activate
python -m unittest discover -s apps/fundamental-screener/tests -p 'test_*.py'
```

## 不做的事

- 不在 app 内复制排序、评分、异常检测算法。
- 不生成研报。
- 不输出买卖建议。
- 不把 fixture / SQLite 当作用户可选数据源。

## 财务历史数据限制

分析日期会约束财报的披露日期和当前数值版本可见日期。实时获取历史日期的数据
不能恢复当时版本；同值重复获取保持已有可见日，财报修订后的新值不用于旧日期。
当前源的披露日期为估算值，“质量问题”会说明披露依据、来源、批次与历史缺口。
已有可见性记录的估算披露以 info 展示，不会单独使当日筛选降级。
缺少证据的旧库和无法恢复的历史以 warning 展示并按既有规则限制优先研究分组，
仍可查看对比表。旧库的证据不足标记会持续保留，同值刷新不会解除。
具体保证和旧库处理见
[财报 PIT 说明](../../docs/fundamental_screener_mvp.md#1541-财报-pit-的最小保证与限制181)。

## 有息负债率数据限制

当前财务源未提供已核实的有息负债率，此项显示缺失。资产负债率可用时，
杠杆分仅由它计算，杠杆分量权重仍为 15%；整个杠杆分量缺失时，财务总分才
按剩余分量权重重新归一。
长期负债比率不能代替有息负债率。旧缓存中由该映射产生的值会自动忽略，
“质量问题”以 info 显示原因及所属公司/报告期；已屏蔽的旧值不会单独使
整份快照降级或清空优先研究分组，其他质量问题仍按原有规则处理。原始记录保留，无需删除缓存或全量重建。
详见 [来源、缓存范围与回归说明](../../docs/fundamental_screener_mvp.md#有息负债率来源与缓存隔离183)。
