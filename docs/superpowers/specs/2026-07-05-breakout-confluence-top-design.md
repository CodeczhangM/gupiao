# 趋势突破 Top 共振增强设计

## 背景

当前“趋势突破 Top”复用 `pick_breakout_stocks` 的日线突破评分，重点覆盖均线多头、平台突破、量能、尾盘强度、板块强度和主力成本。用户希望 Top 更突出以下形态：

- 布林线上轨和下轨距离逐渐变大。
- 周线趋势上升，或处于横盘震荡但没有走坏。
- KDJ 金叉。
- MACD 转向 0 轴上方，将要出现金叉或刚开始金叉。

本设计选择“突破共振增强版”：保留现有趋势突破池的核心逻辑，在后端新增共振指标和评分，并在前端让这些指标成为“趋势突破 Top”的第一层信息。

## 范围

本次设计覆盖：

- 趋势突破扫描历史窗口从约 100 个交易日提升到 180 个交易日。
- 后端新增布林、周线、KDJ、MACD 共振指标。
- 趋势突破入池规则增加部分硬门槛，排序增加共振权重。
- 前端“趋势突破 Top”和“趋势突破列表”新增共振列和命中标签。
- 单元测试覆盖共振入池、拒绝条件和旧数据兼容。

不覆盖：

- 新增独立策略池。
- 单股详情图表改造。
- 交易执行或自动下单。

## 后端指标设计

在 `strategy.py` 中扩展历史统计逻辑，优先新增独立 helper，例如 `_build_breakout_confluence_stats(hist_df)`，再与现有 `_build_strong_history_stats(hist_df)` 的结果按 `ts_code` 合并。这样可以避免把突破共振指标散落进既有历史统计函数。

### 日线布林指标

基于每只股票按 `trade_date` 升序排列的日线：

- `boll_middle`: 20 日收盘均线。
- `boll_std`: 20 日收盘标准差，使用总体标准差口径。
- `boll_upper`: `boll_middle + 2 * boll_std`。
- `boll_lower`: `boll_middle - 2 * boll_std`。
- `boll_width`: `(boll_upper - boll_lower) / boll_middle`。
- `boll_width_expand`: 当前 `boll_width` 大于 5 日前，且近 3 日总体扩大。
- `close_near_boll_upper`: 收盘价高于中轨，且 `close >= boll_upper * 0.97`。
- `boll_breakout_ready`: `boll_width_expand && close_near_boll_upper`。

如果历史不足 25 个交易日，相关布林字段置空或 `False`，并不通过布林硬门槛。

### 周线趋势指标

用 180 日日线按自然周重采样为周线，取每周最后一个收盘价，计算：

- `weekly_ma5`
- `weekly_ma10`
- `weekly_ma10_slope`: 当前 `weekly_ma10` 与 3 周前的变化比例。
- `weekly_close_above_ma10`: 周收盘不低于 `weekly_ma10 * 0.98`。
- `weekly_trend_state`:
  - `上升`: `weekly_ma5 >= weekly_ma10` 且 `weekly_ma10_slope >= 0`。
  - `横盘`: `weekly_close_above_ma10` 且 `weekly_ma10_slope >= -0.03`。
  - `下降`: 不满足上升或横盘。
  - `数据不足`: 周线少于 12 根。
- `weekly_trend_ok`: `weekly_trend_state` 为 `上升` 或 `横盘`。

周线趋势是硬门槛，避免日线短期突破但大周期仍向下的股票进入 Top。

### KDJ 指标

按 9,3,3 口径计算 RSV、K、D、J：

- `kdj_golden_cross`: 当日 K 上穿 D。
- `kdj_recent_golden_cross`: 最近 3 日发生过 K 上穿 D，或当前 K > D 且 K/D 持续抬升。
- `kdj_breakout_signal`: `kdj_golden_cross || kdj_recent_golden_cross`。

KDJ 不作为硬门槛，作为共振加分项。这样可保留 MACD 更强但 KDJ 晚一天确认的候选。

### MACD 指标

沿用现有 MACD 参数 `EMA12/EMA26/DEA9`：

- `macd_dif`
- `macd_dea`
- `macd_bar`
- `macd_zero_axis_ready`: DIF 和 DEA 位于 0 轴上方，或 DIF 接近 0 轴且持续上行。接近 0 轴定义为 `dif >= -0.05 * close / 10`，实现时可以用价格归一化阈值避免高价股和低价股偏差。
- `macd_golden_cross`: 当日 DIF 上穿 DEA。
- `macd_cross_ready`: DIF 小于 DEA 但差值收敛，或最近 3 日刚金叉。
- `macd_breakout_signal`: `macd_golden_cross || macd_cross_ready`。

`macd_zero_axis_ready` 是硬门槛，`macd_breakout_signal` 是加分项。

## 入池与评分设计

保留当前趋势突破池的既有基础条件：

- `trend_upward`
- 非 `risk_reject`
- `overnight_premium_score >= breakout_entry_threshold`

新增硬门槛：

- `weekly_trend_ok`
- `boll_width_expand`
- `macd_zero_axis_ready`

新增共振评分 `breakout_confluence_score`，最高 20 分：

| 条件 | 分数 |
| --- | ---: |
| 布林开口扩大 | 4 |
| 收盘接近布林上轨且位于中轨上方 | 3 |
| 周线趋势上升 | 4 |
| 周线横盘不破位 | 2 |
| KDJ 金叉或近 3 日金叉 | 3 |
| MACD 将金叉或刚金叉 | 4 |

最终突破分：

```text
breakout_score = min(100, overnight_premium_score + breakout_confluence_score)
```

排序优先级调整为：

1. `breakout_confluence_score` 降序。
2. `breakout_confluence_count` 降序。
3. `breakout_score` 降序。
4. `sector_rank` 升序。
5. `relative_strength_rank` 升序。
6. `amount_yuan` 降序。
7. `volume_expand_rate` 降序。

`breakout_reason` 增加以下标签：

- `布林开口`
- `贴近上轨`
- `周线上升`
- `周线横盘`
- `KDJ金叉`
- `MACD将金叉`
- `MACD刚金叉`

## 数据流设计

`quant_service.run_quant_scan` 当前在强势基础池非空时拉取 100 日历史。改为：

```text
hist_days = 180 if not strong_base.empty else 40
```

`select_stock_pools(df, hist_df, core_inflow_sectors)` 继续向三个策略池传入同一份历史数据。超跌反转和主升浪启动可以继续只使用自己需要的近期窗口；新增逻辑只影响趋势突破池。

返回给前端的记录新增字段：

- `boll_width`
- `boll_width_expand`
- `close_near_boll_upper`
- `weekly_trend_state`
- `weekly_trend_ok`
- `kdj_breakout_signal`
- `macd_zero_axis_ready`
- `macd_cross_ready`
- `breakout_confluence_count`
- `breakout_confluence_score`

所有新增字段需要经过现有 `_clean_value`，保证 NaN 序列化为 `null`。

## 前端版面设计

`quantClient/main.js` 的 `StockTable` 在 `mode === 'breakout'` 时新增共振列：

- `共振`: 显示 `breakout_confluence_count` 和 `breakout_confluence_score`。
- `布林`: 显示 `开口`、`贴上轨` 或 `--`。
- `周线`: 显示 `上升`、`横盘`、`下降` 或 `数据不足`。
- `KDJ`: 显示 `金叉` 或 `--`。
- `MACD`: 显示 `零轴上/近零轴`、`将金叉/刚金叉`。

为了让 Top 更容易扫读，趋势突破表格列顺序调整为：

1. 代码
2. 名称
3. 行业
4. 收盘
5. 涨跌幅
6. 阶段
7. 共振
8. 布林
9. 周线
10. KDJ
11. MACD
12. 状态
13. 主力成本
14. 命中原因
15. 评分

原来的换手率、量比、高点回撤、缩量率、MA20、MA40、守线可以保留在列表页后半段，但总览 Top 中应优先展示共振信息。若暂不拆分 Top 表和列表表，则先把共振列插入到状态和命中原因前，保留旧列以降低改动风险。

旧报告缺少新增字段时，页面显示 `--`，不报错。

## 测试设计

在 `tests/test_advantage_stock_scoring.py` 增加或扩展 fixture：

- 构造 180 日日线，使布林宽度近 5 日扩大、收盘接近上轨、周线 MA5 >= MA10、KDJ 近 3 日金叉、MACD 近 0 轴并将金叉，断言股票进入 `pick_breakout_stocks`。
- 周线下降时，即使日线突破和量能满足，也不进入趋势突破池。
- 布林未开口时不进入趋势突破池。
- MACD 远低于 0 轴时不进入趋势突破池。
- 断言返回字段包含 `breakout_confluence_score`、`weekly_trend_state`、`boll_width_expand`、`macd_cross_ready`。

前端验证：

- 旧数据无新增字段时，`StockTable` 显示 `--`。
- 新数据有共振字段时，趋势突破行显示共振数、布林、周线、KDJ、MACD 状态。

## 风险与处理

- 拉取 180 日全市场历史会增加 Tushare 请求压力。先只在强势基础池非空时使用 180 日，沿用现有缓存和缺失日期补拉机制。
- 共振硬门槛可能导致候选数减少。保留当前 70 分阈值，但新增分数上限为 20，后续可根据真实扫描结果微调硬门槛。
- 周线由日线重采样，不依赖新接口，减少外部数据变化。
- MACD 近零轴阈值需要避免价格尺度偏差，实现时使用相对收盘价的归一化阈值。

## 验收标准

- 趋势突破 Top 中能直接看到布林开口、周线趋势、KDJ、MACD 共振状态。
- 满足用户描述形态的 fixture 可以进入趋势突破池并排在前列。
- 周线下降、布林未开口、MACD 远低于 0 轴的 fixture 被趋势突破池拒绝。
- 旧报告和缺失字段不会导致前端报错。
- 相关 Python 单元测试通过。
