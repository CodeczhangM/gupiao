const assert = require('assert');
const {
  formatRotationMoney,
  formatRotationPercent,
  signedRotationMoney,
  capitalTrendArrow,
  capitalTrendClass,
  trendRatingClass,
  macdTagList,
  rotationSectorSummary,
  rotationStockText,
} = require('./sector-rotation-utils.js');

assert.strictEqual(formatRotationMoney(1230000000), '12.30亿');
assert.strictEqual(formatRotationMoney(null), '--');
assert.strictEqual(formatRotationPercent(0.723, 1), '72.3%');
assert.strictEqual(formatRotationPercent(null), '--');

assert.strictEqual(signedRotationMoney(1230000000), '+12.30亿');
assert.strictEqual(signedRotationMoney(-550000000), '-5.50亿');
assert.strictEqual(signedRotationMoney(0), '0.00亿');
assert.strictEqual(signedRotationMoney(null), '--');

assert.strictEqual(capitalTrendArrow('inflow'), '↗');
assert.strictEqual(capitalTrendArrow('outflow'), '↘');
assert.strictEqual(capitalTrendArrow('oscillation'), '↔');
assert.strictEqual(capitalTrendArrow('unknown'), '·');
assert.strictEqual(capitalTrendClass('inflow'), 'up');
assert.strictEqual(capitalTrendClass('outflow'), 'down');
assert.strictEqual(capitalTrendClass('oscillation'), 'muted');
assert.strictEqual(capitalTrendClass(undefined), 'muted');

assert.strictEqual(trendRatingClass('strong'), 'trend-badge is-strong');
assert.strictEqual(trendRatingClass('avoid'), 'trend-badge is-avoid');
assert.strictEqual(trendRatingClass(undefined), 'trend-badge is-watch');

assert.deepStrictEqual(macdTagList(null), [{ label: 'MACD 数据不足', kind: 'muted' }]);
assert.deepStrictEqual(macdTagList({ ready: false }), [{ label: 'MACD 数据不足', kind: 'muted' }]);
assert.deepStrictEqual(macdTagList({
  ready: true,
  status: 'golden_cross',
  status_text: '金叉',
  zero_axis: 'above',
  zero_axis_text: '零轴上方',
  histogram_trend: 'red_expand',
  histogram_trend_text: '红柱放大',
}), [
  { label: '周线金叉', kind: 'up' },
  { label: '零轴上方', kind: 'up' },
  { label: '红柱放大', kind: 'up' },
]);
assert.deepStrictEqual(macdTagList({
  ready: true,
  status: 'death_cross',
  status_text: '死叉',
  zero_axis: 'below',
  zero_axis_text: '零轴下方',
  histogram_trend: 'green_expand',
  histogram_trend_text: '绿柱放大',
}), [
  { label: '周线死叉', kind: 'down' },
  { label: '零轴下方', kind: 'down' },
  { label: '绿柱放大', kind: 'down' },
]);
assert.deepStrictEqual(macdTagList({
  ready: true,
  status: 'critical',
  status_text: '临界',
  zero_axis: 'crossing',
  zero_axis_text: '零轴附近',
  histogram_trend: 'green_shrink',
  histogram_trend_text: '绿柱收敛',
}), [
  { label: '周线临界', kind: 'muted' },
  { label: '零轴附近', kind: 'muted' },
  { label: '绿柱收敛', kind: 'up' },
]);

const sector = {
  industry_name: '机器人',
  continuation_score: 86.5,
  rotation_score: 54.2,
  signal: '连续流入放量扩散',
  metrics: {
    net_amount_today: 1230000000,
    net_amount_change: 550000000,
    up_ratio: 0.72,
  },
};
const summary = rotationSectorSummary(sector, 'continuation');
assert(summary.includes('机器人'));
assert(summary.includes('86.5'));
assert(summary.includes('12.30亿'));
assert(summary.includes('连续流入放量扩散'));

const stockText = rotationStockText({
  name: '示例股份',
  ts_code: '600001.SH',
  pct_chg: 3.21,
  attack_score: 78.9,
  reason: '量比活跃',
}, 'attack_score');
assert(stockText.includes('示例股份'));
assert(stockText.includes('600001.SH'));
assert(stockText.includes('78.9'));
assert(stockText.includes('量比活跃'));

console.log('sector rotation utils ok');
