const assert = require('assert');
const { sectorLeaderText } = require('./sector-potential-utils.js');

const text = sectorLeaderText({
  ts_code: '600001.SH',
  name: '示例股份',
  pct_chg: 3.21,
  total_mv_yuan: 25_000_000_000,
  leader_reason: '总市值200亿以上、多头向上、低位涨停、3日内回踩确认、箱体震荡',
  pool_tag: '趋势突破',
});

assert(text.includes('示例股份'), 'leader text should include stock name');
assert(text.includes('600001.SH'), 'leader text should include stock code');
assert(text.includes('3.21%'), 'leader text should include percent change');
assert(text.includes('250.00亿'), 'leader text should include total market value');
assert(text.includes('低位涨停'), 'leader text should include leader reason');
assert(text.includes('趋势突破'), 'leader text should include pool tag');

console.log('sector potential leader text ok');
