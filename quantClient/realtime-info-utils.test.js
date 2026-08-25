const assert = require('assert');
const {
  realtimeCacheState,
  realtimeDataStatus,
  screeningDataTimeText,
  tailPremiumSelectionState,
  premiumRiskState,
  detailListText,
  marketRelativeText,
  historicalResilienceText,
  tailVolumeDisplay,
} = require('./realtime-info-utils.js');

assert.deepEqual(
  realtimeCacheState({
    cache_source: 'database',
    cache_updated_at: '2026-07-30 14:40:00',
  }),
  {
    text: '数据库快速结果',
    state: 'cached',
    detail: '缓存更新于 2026-07-30 14:40:00',
  },
);
assert.deepEqual(
  realtimeCacheState({
    cache_source: 'fresh',
    cache_updated_at: '2026-07-30 14:41:12',
  }),
  {
    text: '刚刚强制刷新',
    state: 'fresh',
    detail: '刷新于 2026-07-30 14:41:12',
  },
);

assert.deepEqual(
  tailVolumeDisplay({ tail_after_1430_available: true, tail_volume_ratio: 1.5 }, true),
  { text: '放量 1.50倍', state: 'strong' },
);
assert.deepEqual(
  tailVolumeDisplay({ tail_after_1430_available: true, tail_volume_ratio: 1.49 }, true),
  { text: '正常 1.49倍', state: 'muted' },
);
assert.deepEqual(
  tailVolumeDisplay({ tail_after_1430_available: false }, false),
  { text: '未到', state: 'muted' },
);
assert.deepEqual(
  tailVolumeDisplay({ tail_after_1430_available: false }, true),
  { text: '无数据', state: 'muted' },
);
assert.deepEqual(
  tailVolumeDisplay({ tail_after_1430_available: true, tail_volume_ratio: null }, true),
  { text: '无数据', state: 'muted' },
);

assert.deepEqual(
  realtimeDataStatus({
    data_status: 'stale',
    data_status_label: '备用缓存',
    data_updated_at: '2026-07-30 14:30:00',
    stale_age_seconds: 95,
  }),
  {
    text: '备用缓存',
    state: 'warning',
    detail: '数据时间 2026-07-30 14:30:00 · 已过期1分35秒',
  },
);
assert.deepEqual(
  realtimeDataStatus({
    data_status: 'live',
    data_status_label: '实时数据',
    data_updated_at: '2026-07-30 14:31:35',
    stale_age_seconds: 0,
  }),
  {
    text: '实时数据',
    state: 'live',
    detail: '数据时间 2026-07-30 14:31:35',
  },
);
assert.deepEqual(
  realtimeDataStatus({ data_status: 'unavailable' }),
  {
    text: '数据不可用',
    state: 'danger',
    detail: '当前没有可展示的实时或备用数据',
  },
);

assert.equal(
  screeningDataTimeText({
    data_as_of: '2026-07-30 14:42:00',
    data_trade_date: '20260730',
    data_current: true,
  }),
  '筛选数据截至 2026-07-30 14:42:00',
);
assert.equal(
  screeningDataTimeText({
    data_as_of: '2026-07-29 14:59:00',
    data_current: false,
  }),
  '备用缓存 · 筛选数据截至 2026-07-29 14:59:00',
);
assert.equal(
  screeningDataTimeText({
    data_as_of: null,
    data_trade_date: '20260729',
    data_current: false,
  }),
  '备用缓存 · 筛选数据日 20260729',
);
assert.equal(
  screeningDataTimeText({
    data_as_of: null,
    trade_date: '20260730',
    data_current: true,
  }),
  '筛选数据日 20260730',
);
assert.equal(
  screeningDataTimeText({}),
  '筛选数据时间 --',
);
assert.equal(
  screeningDataTimeText({
    data_status: 'unavailable',
    trade_date: '20260730',
    data_current: false,
  }),
  '筛选数据时间 --',
);

assert.deepEqual(
  tailPremiumSelectionState({ selection_state: 'waiting_tail_window' }),
  { text: '14:40前预观察', state: 'muted' },
);
assert.deepEqual(
  tailPremiumSelectionState({ selection_state: 'live_tail_window' }),
  { text: '盘末动态候选', state: 'live' },
);
assert.deepEqual(
  tailPremiumSelectionState({ selection_state: 'closed_final' }),
  { text: '收盘最终结果', state: 'fresh' },
);
assert.equal(premiumRiskState({ risk_level: '高' }), 'risk');
assert.equal(premiumRiskState({ risk_level: '中' }), 'watch');
assert.equal(premiumRiskState({ risk_level: '低' }), 'strong');
assert.equal(detailListText(['尾盘承接', '板块强势']), '尾盘承接；板块强势');
assert.equal(detailListText('等待确认'), '等待确认');
assert.equal(detailListText([]), '--');
assert.deepEqual(
  marketRelativeText({
    relative_strength: 2.2,
    market_resonance_label: '强于大盘',
    market_resonance_reason: '大盘 1.00%，个股 3.20%，相对强 2.20pct',
  }),
  {
    text: '强弱 +2.20pct · 强于大盘',
    title: '大盘 1.00%，个股 3.20%，相对强 2.20pct',
    state: 'strong',
  },
);
assert.deepEqual(
  marketRelativeText({
    relative_strength: -0.4,
    market_resonance_label: '弱于大盘',
  }),
  {
    text: '强弱 -0.40pct · 弱于大盘',
    title: '强弱 -0.40pct · 弱于大盘',
    state: 'weak',
  },
);
assert.deepEqual(
  marketRelativeText({}),
  {
    text: '强弱 --',
    title: '暂无相对大盘数据',
    state: 'muted',
  },
);
assert.deepEqual(
  historicalResilienceText({
    historical_resilience_score: 82.4,
    historical_resilience_label: '强抗跌',
    historical_resilience_reason: '近20日加权跑赢 +1.23pct',
  }),
  {
    text: '82分 · 强抗跌',
    title: '近20日加权跑赢 +1.23pct',
    state: 'strong',
  },
);
assert.deepEqual(
  historicalResilienceText({}),
  {
    text: '--',
    title: '暂无近20日抗跌力数据',
    state: 'muted',
  },
);

console.log('realtime tail-volume display regression ok');
