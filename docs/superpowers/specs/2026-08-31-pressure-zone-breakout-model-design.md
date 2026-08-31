# 压力区与突破建仓模型重构设计

## 目标

在保留现有“近期观察与建仓”入口、日线候选池和兼容字段的前提下，将机械的单点突破价升级为规则化、可回测、可解释的压力区交易模型。模型回答三个问题：当前价格附近真正需要突破的压力在哪里，什么条件构成有效突破，以及这笔突破交易的风险收益是否值得参与。

本次不引入AI涨跌预测，不推翻涨停基因、历史共振、板块、量价、筹码、MACD和尾盘数据链路。MACD降为辅助项，压力结构、突破有效性、假突破风险和盈亏比成为核心。

## 现状与问题

当前 `extract_pullback_confirmation()` 使用涨停后窗口最后一根K线高点作为 `confirmation_price`，兜底使用平台最高价或前期最高价。它没有验证高点是否被反复交易、触碰后是否回落，也没有区分压力区、触发价和确认价。

当前突破成立条件主要是现价高出确认价0.5%，同时量比处于固定范围或成交额超过回踩均值。缺少收盘位置、上影线、尾盘站稳、VWAP和次日回落判断。`score_position_candidate()` 主要按总分将股票划入“立即建仓、等待突破建仓、观察建仓”，没有按距突破位1.5%、3%、5%建立硬边界，因此距离较远的股票也可能进入等待突破。

筹码数据缺失目前会产生零分，无法明确区分“结构较差”和“数据不可用”。单一综合分也混合了股票本身质量与当前买点质量。

## 总体架构

保留 `extract_pullback_confirmation()` 作为兼容入口，内部改为组合五个独立阶段：

1. 日线结构特征提取与压力候选生成。
2. ATR自适应聚类与主要压力区选择。
3. 支撑、触发、确认、失效和目标价交易计划构建。
4. 日线与可选分钟数据驱动的突破质量、假突破和盈亏比计算。
5. 股票质量、买点、突破、盈亏比和数据完整度分项评分及等级判定。

数据库日线负责全市场计算。日线预筛后只对前10只并发请求分钟数据，补充VWAP、尾盘站稳和盘中突破回落证据。分钟失败时保留日线结果并降低数据完整度，不阻断候选输出。

## 配置

新增 `position_strategy_settings` 模块及数据库单例配置，复用MACD配置的版本、短缓存、GET/PUT接口和缓存失效模式。默认值如下：

```json
{
  "pressure": {
    "history_days": 60,
    "structure_days": 20,
    "pivot_left_days": 2,
    "pivot_right_days": 2,
    "min_touches": 2,
    "cluster_pct": 1.0,
    "cluster_atr_factor": 0.35,
    "cluster_max_pct": 2.0,
    "volume_surge_ratio": 1.5,
    "rejection_lookahead_days": 5,
    "rejection_min_pct": 2.0,
    "rejection_atr_factor": 0.8
  },
  "breakout": {
    "trigger_pct": 0.1,
    "trigger_atr_factor": 0.05,
    "confirm_pct": 0.5,
    "confirm_atr_factor": 0.3,
    "volume_confirm_ratio": 1.3,
    "close_position_min": 0.68,
    "long_upper_shadow_ratio": 0.4
  },
  "distance": {
    "critical_pct": 1.5,
    "waiting_pct": 3.0,
    "observe_pct": 5.0
  },
  "risk_reward": {
    "minimum_ratio": 1.5,
    "good_ratio": 2.0,
    "excellent_ratio": 3.0
  },
  "network": {
    "enrichment_limit": 10,
    "workers": 5,
    "request_timeout_seconds": 6,
    "stage_budget_seconds": 15,
    "total_budget_seconds": 45
  }
}
```

配置更新递增版本号并清理位置候选缓存。版本号加入 `position_score_version()`，防止旧规则结果继续命中缓存。第一版提供后端配置接口，不增加首页配置表单。

## 压力候选生成

使用最近60个交易日日线计算ATR14，重点分析最近20日结构，涨停后结构使用最近10日。

### 平台局部高点

局部高点默认要求其高点不低于左右各2根K线。候选触碰后1至5日必须出现明显回落，回落幅度至少为 `max(2%, 0.8 × ATR百分比)`。同一价格区域至少2次有效触碰才构成平台压力，3次及以上提高强度。

### 放量冲高高点

当日成交量不低于前5日均量的1.5倍，并且收盘位置偏低、存在明显上影或随后1至5日明显回落时，将当日高点加入候选。放量冲高的权重高于普通局部高点。

### 涨停后结构

识别最近涨停后的炸板高点、次日冲高点、成交密集平台上沿和涨停后平台高点。涨停后结构候选获得额外强度，但仍需记录来源和日期，不因“涨停后”标签直接判定为当前主要压力。

### 筹码压力

筹码数据可用时，识别当前价格上方的主要筹码密集区及其上沿，并加入压力候选。需要扩展筹码字段以保留密集区上下沿，而不是只保存单一筹码峰。筹码不可用时记录 `chip_pressure_data_available=false` 和缺失原因，不按零分解释为筹码结构差。

### 最高价兜底

只有无法识别平台、放量、涨停后或筹码压力区时，才使用N日最高价作为低置信度候选，并在来源中明确标记“最高价兜底”。

## 压力聚类

候选点按照价格升序聚类。两个点在以下距离内合并：

```text
abs(price1 - price2) <= min(
    基准价 × 2%,
    max(0.35 × ATR, 基准价 × 1%)
)
```

每个聚类输出最低价、最高价、触碰日期、来源、成交量证据、回落证据和强度。压力区强度满分100：

- 触碰次数25
- 触碰后回落幅度20
- 放量冲高证据15
- 涨停后结构15
- 时间新鲜度10
- 筹码共振10
- 多来源重合5

## 主要压力区选择

选择函数同时考虑强度与距离，不单纯取最强区：

- 距当前价0%至3%的压力区获得最高适用性。
- 距离3%至5%降低适用性。
- 距离超过5%的压力区只保留为远端目标，不作为当前突破目标。
- 当前价刚越过的最近压力区保留，用于评估突破质量与假突破。
- 同等距离优先强度高、触碰多且更新的压力区。

输出所有压力区、选中区和明确的 `pressure_selection_reason`，包括未选中区域的距离及放弃原因。

## 交易计划价格

主要压力区输出 `pressure_low` 与 `pressure_high`。触发价和确认价分别计算：

```text
breakout_trigger = max(
    pressure_high × (1 + trigger_pct),
    pressure_high + trigger_atr_factor × ATR
)

breakout_confirm = max(
    pressure_high × (1 + confirm_pct),
    pressure_high + confirm_atr_factor × ATR
)
```

失效位默认取支撑区下沿1.5%下方与支撑区下沿减0.3倍ATR中的更低值。目标价优先使用上方下一个有效压力区；没有更高压力区时使用压力区与支撑区的结构高度推算，禁止用固定收益率或固定R倍数反推目标以美化盈亏比。

每只股票形成：`support_price`、`pressure_low`、`pressure_high`、`breakout_trigger`、`breakout_confirm`、`invalid_price`、`target_price`。

## 距离与突破状态

输出三个独立距离：

```text
distance_to_pressure_pct
distance_to_trigger_pct
distance_to_confirm_pct
```

首页重点展示距触发价。状态包括：

- `NOT_TRIGGERED`：尚未到触发价。
- `TOUCHING`：进入压力区但未越过触发价。
- `TRIGGERED`：超过触发价但尚未有效确认。
- `CONFIRMED`：达到确认价并通过有效性检查。
- `FAILED`：盘中突破但收盘跌回压力区。
- `OVEREXTENDED`：有效突破后明显远离确认价，需要等待回踩。

未突破显示“距触发价X%”；已突破显示“已突破+X%”，不再继续显示“等待突破”。

## 突破质量

只有进入压力区或发生突破后计算 `breakout_quality_score`；尚未触发时返回 `null` 和“未触发”，避免把缺少事件解释为零质量。

满分100：价格突破幅度25、成交量确认20、收盘位置15、上影线质量10、突破后保持时间10、尾盘站稳10、VWAP上方5、板块同步5。

质量标签：80至100强突破，65至79正常突破，50至64弱突破，低于50疑似假突破。

日线提供价格、成交量、收盘位置和上影证据；分钟数据提供保持时间、尾盘和VWAP。缺失分钟数据时相关项目标记不可用，并降低数据完整度，不直接按失败计分。可用项目评分需归一化，同时保留原始可用权重，防止低完整度结果伪装成高置信度。

## 假突破风险

`false_breakout_risk_score` 与突破质量分开计算。风险证据包括：收盘跌回压力区、收盘位置低于50%、无明显放量、长上影、异常高换手、同一区域连续失败、尾盘明显回落、跌破VWAP、突破后次日快速跌回压力区。

风险标签：0至24为LOW，25至49为MEDIUM，50至100为HIGH。HIGH不允许进入A+；收盘明显跌回压力区、放量跌破支撑或次日确认失败可直接进入X。

## 盈亏比

已确认股票使用当前价与确认价中的较高值作为计划入场价。临界突破和等待突破使用确认价。等待回踩使用计划回踩价。

```text
risk = entry_price - invalid_price
reward = target_price - entry_price
risk_reward_ratio = reward / risk
```

风险、收益必须为正，否则盈亏比无效。低于1.5不建议建仓，1.5至2为一般，2至3为良好，高于3为优秀。缺少入场、止损或目标时返回空值并降低数据完整度，不伪造数值。

## 分项评分

`stock_quality_score` 满分100：板块25、日线量价20、支撑与回踩20、涨停基因及历史共振15、筹码10、MACD辅助5、相对强弱5。

`entry_timing_score` 满分100：距触发价30、支撑是否守住20、突破阶段20、当日量价10、尾盘站稳10、假突破风险10。

`risk_reward_score` 按盈亏比区间映射。`data_confidence` 根据日线完整性、压力证据、板块、筹码和分钟数据分别计算。不可用指标从对应分项的可用权重中归一化，但数据完整度同步下降。

最终分数：

```text
final_score =
    stock_quality_score × 30%
    + entry_timing_score × 30%
    + breakout_quality_score × 20%
    + risk_reward_score × 15%
    + data_confidence × 5%
```

尚未触发时突破质量项不参与，并对其余可用总权重归一化；等级仍由距离和硬规则约束。MACD仅输出强、中、弱，作为辅助确认。

## 建仓等级

- `A+ 已确认，可考虑建仓`：CONFIRMED，突破质量不低于75，假突破风险非HIGH，盈亏比不低于1.5，数据完整度不低于70，支撑未失效。
- `A 临界突破`：距触发价不超过1.5%，压力结构有效，盈亏比不低于1.5，假突破风险非HIGH。
- `B+ 等待突破`：距触发价大于1.5%且不超过3%，支撑有效且股票质量达标。
- `B 等回踩`：已突破但明显远离确认价，或突破后尚未形成安全回踩，结构仍有效。
- `C 观察`：距触发价大于3%且不超过5%，或结构有效但数据完整度不足。
- `X 放弃`：距触发价超过5%、盈亏比低于1.5、支撑有效跌破、严重假突破或命中现有硬风险否决。

重点池只显示A+、A、B+、B、C中的前5至10只。X只出现在调试漏斗和具体过滤原因中。

## 网络增强与时间预算

数据库日线完成全量压力区、交易计划和日线初评分。过滤明显不合格结果后取前10只，以5线程并发请求当日分钟数据。单请求超时6秒，网络阶段软预算15秒，完整刷新软预算45秒。

分钟数据用于计算当日VWAP、突破后的保持时间、14:30后是否站稳和尾盘回落。请求失败或超时时取消未开始任务，保留日线结果，写入 `missing_data` 和 `fallback_warnings`。任何单只股票失败不得使整个候选接口失败。

## 输出契约与兼容

新增字段至少包括：

```json
{
  "support_price": 8.54,
  "pressure_low": 8.82,
  "pressure_high": 8.90,
  "breakout_trigger": 8.91,
  "breakout_confirm": 8.97,
  "invalid_price": 8.42,
  "target_price": 10.10,
  "distance_to_pressure_pct": 0.11,
  "distance_to_trigger_pct": 1.03,
  "distance_to_confirm_pct": 1.72,
  "breakout_state": "NOT_TRIGGERED",
  "breakout_quality_score": null,
  "breakout_quality_label": "未触发",
  "false_breakout_risk": "LOW",
  "stock_quality_score": 85,
  "entry_timing_score": 92,
  "risk_reward_score": 72,
  "risk_reward_ratio": 2.4,
  "data_confidence": 95,
  "final_score": 87,
  "build_position_level": "A",
  "build_position_status": "临界突破",
  "pressure_strength_score": 86,
  "pressure_sources": ["近20日3次局部高点聚类"],
  "pressure_selection_reason": "距现价1.03%，强度86，优先于远端压力",
  "pressure_zones": [],
  "breakout_evidence": [],
  "false_breakout_evidence": [],
  "missing_data": []
}
```

兼容期保留 `primary_support`、`confirmation_price`、`position_score` 和 `position_level`。它们分别映射到新模型的支撑价、确认价、最终分和中文等级状态。现有接口URL不变，Java代理继续透明转发。

## 页面

首页主表优先展示股票与等级、距触发价、支撑/压力区、触发/确认价、突破质量、假突破风险、盈亏比、板块强度、尾盘强度、评分摘要以及依据与待确认。

MACD显示强、中、弱并并入评分摘要。压力区候选、分项评分、选择原因和风险证据放入可展开详情，主表不堆叠所有技术字段。所有说明单元格允许换行并保留横向滚动。

## 调试与日志

每只股票返回所有压力区、选中及未选中原因、触碰日期、回落幅度、量比、触发与确认参数、突破质量分项、假突破证据、盈亏比计算和等级硬规则。普通模式不逐只刷控制台；异常进入 `fallback_warnings`，结构化证据通过调试响应输出。

## 验收标准

- 有效平台必须由至少2次接近高点及触碰后回落形成。
- ATR聚类在高低波动股票上均按配置工作，且区域宽度不超过2%。
- 平台、放量、涨停后和筹码来源可解释，最高价只作兜底。
- 当前价10元时，10.20元强度82的压力优先于12.50元强度90的远端压力。
- 压力区、触发价和确认价互相区分且满足递增关系。
- 距触发价超过5%的股票不进入重点池。
- 已突破股票不再显示“等待突破”，而进入有效性或回踩判断。
- 假突破风险和突破质量分别输出。
- 盈亏比低于1.5不得获得A+或A。
- 筹码或分钟数据缺失不会被解释为指标零分，并能降低数据完整度。
- 强制刷新正常目标低于45秒，网络增强不超过15秒，单只失败不阻断整体。
- 新旧字段在兼容期同时可用，现有接口路径不变。
- 压力识别、评分边界、网络降级、接口和页面均有回归测试。

