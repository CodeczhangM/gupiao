# 实时共振相对大盘强弱设计

## 背景

当前“实时信息”里的实时共振主要从潜力板块中挑选个股，再叠加换手率、量比、涨跌幅、60 分钟信号和尾盘 1 分钟状态。这个逻辑擅长找“强板块里的强个股”，但没有显式比较个股和大盘的相对强弱。

新的目标是筛出两类股票：

- 大盘上涨时，个股涨得更多，而不是只跟随市场上涨。
- 大盘下跌时，个股跌幅很小，表现出明显抗跌。

## 大盘口径

“大盘”使用实时市场快照中的主板 A 股等权平均涨跌幅：

- 仅保留现有实时共振候选口径中的主板 A 股，排除创业板、科创板、北交所和 ST。
- 对有效 `pct_chg` 求等权平均，得到 `market_pct_chg`。
- 不新增指数行情数据源，避免实时依赖变复杂。

这个口径和当前候选池一致，能反映实时共振实际交易范围内的市场环境。

## 筛选规则

为每只候选股计算：

```text
relative_strength = stock_pct_chg - market_pct_chg
```

按市场状态分层筛选：

```text
大盘上涨：market_pct_chg >= 0.3
要求：stock_pct_chg >= market_pct_chg + 1.0，且 stock_pct_chg >= 1.5

大盘震荡：-0.3 < market_pct_chg < 0.3
要求：stock_pct_chg >= 1.0，且 relative_strength >= 1.0

大盘下跌：market_pct_chg <= -0.3
要求：stock_pct_chg >= market_pct_chg + 1.5，且 stock_pct_chg >= -0.5
```

这会替换实时共振预筛中的固定 `pct_chg >= 0.2` 条件。量比、换手率、主板过滤、ST 过滤和板块潜力过滤继续保留。

## 数据流

在 `realtime_info_service.py` 中新增独立的相对大盘辅助逻辑：

1. 从 `realtime_market` 计算 `market_pct_chg` 和市场状态标签。
2. 在 `_load_realtime_intraday_signal_bars()` 的候选预筛阶段附加 `market_pct_chg`、`relative_strength` 和 `market_resonance_label`。
3. 用分层规则替代固定涨幅阈值，保证逆势抗跌股票不会被误杀。
4. 后续 60 分钟信号、尾盘 1 分钟刷新、主力状态判断继续复用现有流程。
5. 最终结果行保留新增字段，前端可以直接展示或用于排序。

## 排序

最终排序仍保留现有主力状态、次日偏向和分钟信号优先级，并增加相对大盘强弱作为核心排序因子：

```text
realtime_relative_strength_score =
  relative_strength * 40
  + max(stock_pct_chg, 0) * 10
  + min(volume_ratio, 4) * 8
  + (10 if 2 <= turnover_rate <= 8 else 0)
```

推荐排序顺序：

1. `main_force_status == "主力抢筹"`
2. `next_day_bias == "高开偏强"`
3. `realtime_relative_strength_score`
4. `intraday_signal_score`
5. `volume_ratio`

## 输出字段

每条实时共振股票新增：

- `market_pct_chg`：实时大盘涨跌幅。
- `relative_strength`：个股涨跌幅减大盘涨跌幅。
- `market_resonance_label`：`强于大盘`、`逆势抗跌` 或 `震荡走强`。
- `market_resonance_reason`：例如 `大盘 -1.20%，个股 -0.20%，相对强 1.00pct`。
- `realtime_relative_strength_score`：相对强弱排序分。

## 边界与错误处理

- 如果无法计算大盘涨跌幅，保守退回原固定涨幅规则，避免实时共振直接为空。
- `pct_chg` 缺失的股票不参与相对大盘筛选。
- 大盘上涨和震荡场景要求个股红盘，避免弱跟涨进入结果。
- 大盘下跌场景允许个股小幅绿盘，但跌幅不能低于 `-0.5%`。
- 新字段只用于实时筛选和展示，不写回行情缓存。
- 结果缓存键需要纳入规则版本字符串，避免旧缓存和新筛选口径混用。

## 测试

新增或调整 `tests/test_realtime_info_service.py`：

1. 大盘上涨时，只保留明显跑赢大盘的股票，普通跟涨股票被过滤。
2. 大盘下跌时，小幅下跌但明显强于大盘的股票可以进入候选。
3. 大盘下跌时，跌幅接近或弱于大盘的股票被过滤。
4. 大盘震荡时，要求个股至少上涨 1% 且相对强度大于等于 1pct。
5. 大盘涨跌幅无法计算时，回退原有固定涨幅筛选，不影响实时共振可用性。
6. 结果行包含 `market_pct_chg`、`relative_strength`、`market_resonance_label`、`market_resonance_reason` 和 `realtime_relative_strength_score`。

验证命令：

```bash
env HOME=/tmp python3 -m unittest -v tests.test_realtime_info_service
```
