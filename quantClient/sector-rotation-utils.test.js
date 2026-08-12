const assert = require('assert');
const {
  formatRotationMoney,
  formatRotationPercent,
  rotationSectorSummary,
  rotationStockText,
} = require('./sector-rotation-utils.js');

assert.strictEqual(formatRotationMoney(1230000000), '12.30亿');
assert.strictEqual(formatRotationMoney(null), '--');
assert.strictEqual(formatRotationPercent(0.723, 1), '72.3%');
assert.strictEqual(formatRotationPercent(null), '--');

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
