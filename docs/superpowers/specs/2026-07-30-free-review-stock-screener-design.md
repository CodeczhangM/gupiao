# 自由复盘选股设计

## 目标

将页面上的“超跌反转、趋势突破、主升浪启动”三个选股入口整合为一个“自由复盘选股”模块。模块使用最新完整交易日的全部合格 A 股，通过系统综合评分与自由条件筛选，提供板块总览、数千只股票分页查询和 CSV 导出。

旧三池继续在后端生成，供实时共振、回测、AI 评估和历史报告兼容，但不再作为主页面入口或统计卡展示。

## 股票范围

宽表覆盖最新完整交易日的全部 A 股，默认排除：

- 名称包含 ST、`*ST` 或退市整理标识的股票；
- 当日没有有效成交数据的停牌股票；
- 复盘交易日距上市日期不足 60 个自然日的股票；
- `list_status` 不是正常上市状态的股票。

亏损股继续保留，市盈率为负时标记“亏损”，估值得分为 0。

## 时间口径

- 行情、估值和技术指标使用最新完整交易日收盘数据。
- 技术指标最多读取最近 100 个完整交易日。
- 财务数据同步最近 8 个季度。
- 对任意复盘交易日，只允许使用 `ann_date <= trade_date` 的财务记录。
- 同一报告期存在多次更新时，选择复盘交易日前最新公告版本。
- 页面显示行情交易日、财务报告期、财务公告日、宽表生成时间和评分版本。

## 数据来源

### 已有行情缓存

复用：

- `market_daily`：开高低收、涨跌幅、成交量、成交额；
- `market_daily_basic`：换手率、自由换手率、量比、PE、PE-TTM、PB、PS、PS-TTM、股息率、总市值和流通市值；
- `stock_basic_cache`：代码、名称、地区、行业、市场、上市状态和上市日期。

`load_market_snapshot()` 需要扩充当前未返回的 `turnover_rate_f`、`ps`、`ps_ttm`、`dv_ratio`、`dv_ttm`、`area`、`market`、`list_status` 和 `list_date`。

### 财务指标

使用 Tushare `fina_indicator_vip` 按季度批量同步。该接口提供 ROE、ROA、ROIC、毛利率、净利率、资产负债率、经营现金流占营收、营收同比、净利润同比、扣非净利润同比等字段。接口权限要求与字段以 Tushare 官方文档为准：

<https://tushare.pro/document/2?doc_id=79>

本设计假设部署使用的 Tushare 账号具备 5000 积分和 `fina_indicator_vip` 权限。VIP 调用失败时任务记录明确错误，不自动切换为数千次逐股查询。

## 数据表

### `financial_indicator_cache`

按财报公告版本保存原始财务指标。

主键：

```text
(ts_code, end_date, ann_date)
```

索引：

```text
(end_date, ann_date)
(ts_code, ann_date)
```

字段至少包括：

```text
ts_code, ann_date, end_date, update_flag,
eps, dt_eps, cfps,
roe, roe_dt, roa, roic,
grossprofit_margin, netprofit_margin,
current_ratio, debt_to_assets,
ocf_to_or, q_ocf_to_sales,
tr_yoy, or_yoy, netprofit_yoy, dt_netprofit_yoy,
q_sales_yoy, q_netprofit_yoy, ocf_yoy,
basic_eps_yoy, rd_exp,
source_name, fetched_at
```

### `review_stock_snapshot`

每个交易日、每只股票、每个评分版本保存一行复盘宽表。

主键：

```text
(trade_date, ts_code, score_version)
```

主要字段分组：

- 标识：代码、名称、地区、行业、市场、上市日期、上市天数；
- 行情：开高低收、涨跌幅、成交量、成交额；
- 估值：PE、PE-TTM、PB、PS、PS-TTM、股息率、总市值、流通市值；
- 量价：换手率、自由换手率、量比、成交额行业排名、量能相对 MA5/10/20、价量状态；
- 趋势：MA5/10/20/30/60、均线距离、MA20/60 斜率、5/10/20/60 日收益、20/60 日高点回撤、区间位置；
- 动量：MACD DIF/DEA/柱体、KDJ K/D/J、RSI6/12/24、布林带位置和宽度、ATR 波动率；
- 财务：选中的报告期、公告日及财务缓存字段；
- 评分：趋势、量价、动量、估值、财务质量、财务成长、风险扣分、综合分；
- 状态：盈利标记、财务连续改善次数、数据完整度、缺失字段、生成时间。

用于常用筛选和排序的字段建立组合索引：

```text
(trade_date, score_version, total_score)
(trade_date, score_version, industry, total_score)
(trade_date, score_version, volume_ratio)
(trade_date, score_version, pe_ttm)
```

### 构建状态表

`review_snapshot_build` 保存任务状态：

```text
trade_date, score_version, status, stage,
total_count, processed_count, failed_count,
financial_coverage, started_at, completed_at, error_message
```

状态为 `pending`、`running`、`success` 或 `failed`。同一交易日和评分版本只允许一个构建任务。

## 指标计算

### 趋势分：20 分

- MA5、MA10、MA20、MA30、MA60；
- 收盘相对各均线距离；
- 均线多头排列；
- MA20、MA60 的近期斜率；
- 5、10、20、60 日收益；
- 20、60 日高点回撤；
- 60 日价格区间位置。

### 量价分：20 分

- 量比、换手率、自由换手率；
- 成交额和行业内成交额排名；
- 成交量相对 MA5、MA10、MA20；
- 放量上涨、缩量回调和价量背离；
- 量价指标采用合理上下限，极端值不会无限加分。

### 动量分：15 分

- MACD DIF、DEA、柱体及金叉状态；
- KDJ K、D、J 及交叉状态；
- RSI6、RSI12、RSI24；
- 布林带位置和宽度变化；
- ATR 波动率和近期突破状态。

### 估值分：15 分

- PE、PE-TTM、PB、PS、PS-TTM、股息率；
- 主要使用行业内百分位，同时设置绝对值异常保护；
- PE 为负时估值得分为 0；
- 金融行业使用行业内排名，避免与普通公司直接比较。

### 财务质量分：20 分

- ROE、扣非 ROE、ROA、ROIC；
- 毛利率、净利率；
- 经营现金流/营业收入、每股现金流；
- 流动比率和资产负债率；
- 银行、保险、证券不使用普通公司的流动比率和资产负债率惩罚。

### 财务成长分：10 分

- 营收同比；
- 归母净利润同比；
- 扣非净利润同比；
- 经营现金流同比；
- EPS 同比；
- 最近 8 季度连续改善次数。

### 风险扣分：最多 20 分

- 亏损和持续负现金流；
- 营收、利润持续负增长；
- 普通公司异常高负债；
- 高 ATR 波动；
- 近期大幅上涨后高位回撤；
- 放量下跌和价量背离。

最终分：

```text
total_score = clip(
    trend_score
    + volume_price_score
    + momentum_score
    + valuation_score
    + financial_quality_score
    + financial_growth_score
    - risk_penalty,
    0,
    100
)
```

缺失指标不重新放大其他权重，缺失项计 0，并通过 `data_completeness` 公开数据覆盖程度，避免缺数据股票获得虚高分。

## 构建流程

1. 读取最新完整交易日。
2. 确认 `market_daily` 和 `market_daily_basic` 均已完整同步。
3. 根据 `financial_indicator_cache` 找出最近 8 个季度缺口。
4. 调用 `fina_indicator_vip` 增量写入缺失季度。
5. 读取最近 100 个完整交易日行情。
6. 排除不合格股票。
7. 批量计算技术指标、行业百分位、财务趋势和评分。
8. 在单个数据库事务中写入指定交易日与评分版本的宽表。
9. 写入板块聚合结果或通过同一宽表实时聚合。
10. 将构建状态更新为成功并记录覆盖率。

同一交易日、同一评分版本的成功宽表默认直接复用。“重新生成”显式删除并重建该版本数据。

## 后端接口

Python 接口由 Spring 统一转发：

```text
POST /api/free-review/build
GET  /api/free-review/build-status
GET  /api/free-review/meta
POST /api/free-review/query
GET  /api/free-review/sectors
POST /api/free-review/export
```

Spring 对外路径增加 `/api/quant` 前缀。

`query` 请求包含：

```text
trade_date, score_version,
keyword, industries, areas, markets,
profit_state, volume_state, growth_state,
ranges, sort_by, sort_direction,
page, page_size, visible_columns
```

其中 `ranges` 只接受后端白名单中的数值字段。`sort_by` 同样使用字段白名单，SQL 不拼接任意前端输入。

查询响应包含：

```text
trade_date, score_version, generated_at,
total, page, page_size, rows,
financial_coverage, data_warnings
```

CSV 导出复用同一过滤器和排序器，保证导出结果与页面一致，并设置最大导出行数。

## 页面设计

### 导航与总览

- 删除三个旧池导航按钮；
- 新增“自由复盘选股”；
- 删除三个旧池统计卡；
- 显示交易日、股票总数、板块数、财务覆盖率、生成时间和评分版本。

### 板块总览

列出当天所有行业：

```text
行业、股票数、平均涨幅、上涨家数占比、
中位量比、平均换手率、平均 PE-TTM、平均综合分
```

点击行业卡或表格行会立即将行业加入股票筛选。

### 筛选器

按组折叠：

- 基础：关键字、行业、地区、市场、盈利状态；
- 评分：综合分和六个维度分；
- 量价：量比、换手率、自由换手率、成交额、量能状态；
- 估值：PE、PE-TTM、PB、PS、股息率、市值；
- 趋势：收益率、回撤、均线距离、价格位置；
- 动量：MACD、KDJ、RSI、布林、ATR；
- 财务：ROE、ROIC、毛利率、净利率、增长率、负债率、现金流；
- 风险：风险扣分、数据完整度和风险标签。

支持一键重置，并将命名筛选方案保存在浏览器 `localStorage`。第一版不在数据库保存个人方案。

### 股票表

- 固定股票代码、名称、行业和综合分；
- 指标列按分组切换，可自定义显示；
- 任意白名单列升降序；
- 每页 50、100 或 200 行；
- 点击股票打开现有个股技术详情；
- 导出当前条件下的 CSV。

## 错误处理

- VIP 权限不足：构建失败并显示明确权限说明，不执行逐股暴力回退；
- 财务季度部分失败：保留已写入季度，构建可继续，但状态和页面显示覆盖率与警告；
- 行情缓存未就绪：拒绝构建并提示先同步完整交易日；
- 单只股票指标计算失败：记录股票代码与原因，继续处理其余股票；
- 宽表未生成：查询接口返回可识别的未就绪状态，不现场计算数千只股票；
- 任务重复提交：返回当前运行任务而不是启动第二个任务；
- 数据库写入失败：事务回滚，不留下同一版本的半成品宽表；
- 前端保留上一次成功结果，并展示新任务错误，不用空表覆盖。

## 性能

- 财务数据按季度增量同步；
- 技术指标按股票分组批量计算；
- 宽表一次生成、多次查询；
- 所有查询必须分页；
- 板块统计与股票查询复用交易日和评分版本索引；
- 首次生成目标允许分钟级，普通筛选目标为数据库毫秒级到低百毫秒级；
- 页面不一次加载全市场完整 JSON。

## 兼容边界

- `quant_reports` 的 `strong`、`dip`、`first_limit` 和 `pools` 暂不删除；
- 原扫描、实时共振、次日跟进、实时信息、回测、AI 评估和历史报告接口继续工作；
- 仅主页面导航、统计卡和旧三池列表替换为自由复盘模块；
- 新模块使用独立服务和数据表，不把宽表逻辑继续堆入 `strategy.py`。

## 测试

### 单元测试

- 公告日在复盘日之后的财报不会被选择；
- 同报告期选择复盘日前最新版本；
- 8 季度趋势和连续改善次数；
- ST、退市整理、停牌和上市不足 60 天排除；
- 技术指标、行业百分位、评分边界和缺失值；
- 金融行业特殊风险规则；
- 数据完整度和风险扣分。

### 仓储与 API 测试

- 财务缓存幂等写入；
- 宽表事务覆盖和版本隔离；
- 构建任务互斥与状态进度；
- 查询字段白名单；
- 多条件筛选、排序和分页；
- 板块聚合与股票查询口径一致；
- CSV 与页面过滤结果一致。

### 回归测试

- 旧报告字段继续保存和读取；
- 实时共振、回测和历史接口不受影响；
- 页面旧入口和统计卡消失；
- 自由复盘筛选方案、分页、列选择和导出正常；
- 数据未就绪、部分财务缺失和构建失败均有明确状态。

## 验收标准

- 最新完整交易日的全部合格 A 股均能进入宽表；
- 页面展示全部行业并能点击筛选；
- 用户可组合估值、量价、技术、财务和风险条件；
- 综合评分和各维度分可解释；
- 财务数据不存在未来公告污染；
- 全市场查询始终分页，普通筛选无需重新计算；
- 原三个页面入口消失，但后端兼容功能通过回归测试。
