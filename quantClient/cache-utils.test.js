const assert = require('assert');
const { cacheProgressText, latestCacheRows } = require('./cache-utils.js');

const rows = latestCacheRows([
  { source_name: 'daily', trade_date: '20260212', updated_at: '2026-07-12 10:00:00' },
  { source_name: 'daily', trade_date: '20260711', updated_at: '2026-07-12 10:00:00' },
  { source_name: 'daily_basic', trade_date: '20260711', updated_at: '2026-07-12 10:00:00' },
]);

assert.equal(rows.find((row) => row.source_name === 'daily').trade_date, '20260711');
assert.equal(rows.find((row) => row.source_name === 'daily_basic').trade_date, '20260711');
assert.equal(rows.find((row) => row.source_name === 'stock_basic').trade_date, undefined);

assert.equal(
  cacheProgressText({ complete_dates: 118, target_days: 120, missing_dates: 2 }),
  '118/120 个完整交易日 · 还差 2 个'
);
assert.equal(
  cacheProgressText({ complete_dates: 120, target_days: 120, missing_dates: 0 }),
  '120/120 个完整交易日 · 已补齐'
);

console.log('cache latest-date regression ok');
