const assert = require('assert');
const {
  realtimeDataStatus,
  screeningDataTimeText,
  tailVolumeDisplay,
} = require('./realtime-info-utils.js');

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

console.log('realtime tail-volume display regression ok');
