const assert = require('assert');

const {
  normalizeCycleWatchInput,
  cycleWatchGroups,
  cycleWatchAlertCount,
  addAndCheckCycleWatch,
} = require('./cycle-watch-utils');

assert.strictEqual(normalizeCycleWatchInput('600000'), '600000.SH');
assert.strictEqual(normalizeCycleWatchInput('300750.sz'), '300750.SZ');
assert.throws(() => normalizeCycleWatchInput('920001'), /仅支持沪深/);
assert.throws(() => normalizeCycleWatchInput('600000.SZ'), /市场后缀/);

const rows = [
  { ts_code: '600001.SH', status: 'confirmed', opportunity_score: 82, factors: { confirmation_count: 2 } },
  { ts_code: '600002.SH', status: 'confirmed', opportunity_score: 82, factors: { confirmation_count: 3 } },
  { ts_code: '000001.SZ', status: 'low_buy', opportunity_score: 70, factors: { support_distance_pct: 1.5, volume_contraction: 0.8 } },
  { ts_code: '000002.SZ', status: 'low_buy', opportunity_score: 68, factors: { support_distance_pct: 0.5, volume_contraction: 0.6 } },
  { ts_code: '300001.SZ', status: 'watch', opportunity_score: 50 },
  { ts_code: '300002.SZ', status: 'watch', opportunity_score: 60 },
  { ts_code: '600003.SH', status: 'data_delayed', opportunity_score: 99 },
];
const groups = cycleWatchGroups(rows);
assert.deepStrictEqual(groups.confirmed.map((row) => row.ts_code), ['600002.SH', '600001.SH']);
assert.deepStrictEqual(groups.lowBuy.map((row) => row.ts_code), ['000002.SZ', '000001.SZ']);
assert.deepStrictEqual(groups.watch.map((row) => row.ts_code), ['300002.SZ', '300001.SZ']);
assert.deepStrictEqual(groups.delayed.map((row) => row.ts_code), ['600003.SH']);

assert.strictEqual(cycleWatchAlertCount([
  { is_new_alert: true, alert_read: false },
  { is_new_alert: true, alert_read: true },
  { is_new_alert: false, alert_read: false },
]), 1);

async function testAddChecksOnlyTheNewStock() {
  const calls = [];
  const request = async (url, options) => {
    calls.push({ url, options });
    if (url === '/cycle-watchlist') return { ts_code: '600199.SH' };
    return { stocks: [{ ts_code: '600199.SH', status: 'watch' }] };
  };

  const result = await addAndCheckCycleWatch(request, { ts_code: '600199.SH' });

  assert.deepStrictEqual(calls.map((call) => call.url), [
    '/cycle-watchlist',
    '/cycle-watchlist/check',
  ]);
  assert.deepStrictEqual(JSON.parse(calls[1].options.body), {
    ts_code: '600199.SH',
  });
  assert.strictEqual(result.stocks[0].ts_code, '600199.SH');
}

testAddChecksOnlyTheNewStock()
  .then(() => console.log('cycle watch utils tests passed'))
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
