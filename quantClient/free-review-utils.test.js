const assert = require('assert');
const {
  FREE_REVIEW_METRIC_GROUPS,
  normalizeFreeReviewQuery,
  saveFreeReviewPreset,
  loadFreeReviewPresets,
  freeReviewBuildState,
  validateMacdSettings,
  macdParameterLabel,
} = require('./free-review-utils.js');

const query = normalizeFreeReviewQuery({
  keyword: ' 半导体 ',
  industries: ['电子'],
  markets: ['主板', '创业板'],
  ranges: {
    total_score: { min: 0, max: '' },
    pe_ttm: { min: null, max: 35 },
    roe: { min: '', max: '' },
  },
  visible_columns: ['total_score', 'pe_ttm', 'roe'],
}, 2, 100, { by: 'pe_ttm', direction: 'asc' });

assert.equal(query.keyword, '半导体');
assert.deepEqual(query.industries, ['电子']);
assert.deepEqual(query.markets, ['主板', '创业板']);
assert.deepEqual(query.ranges.total_score, { min: 0 });
assert.deepEqual(query.ranges.pe_ttm, { max: 35 });
assert.equal(query.ranges.roe, undefined);
assert.deepEqual(query.visible_columns, ['total_score', 'pe_ttm', 'roe']);
assert.equal(query.page, 2);
assert.equal(query.page_size, 100);
assert.equal(query.sort_by, 'pe_ttm');
assert.equal(query.sort_direction, 'asc');
assert.equal(normalizeFreeReviewQuery({}, 1, 500, {}).page_size, 50);

const financialQuery = normalizeFreeReviewQuery({
  ranges: {
    financial_event_hit: { min: 1, max: '' },
    deducted_netprofit: { min: 50000000, max: '' },
    deducted_netprofit_growth: { min: 50, max: '' },
  },
  visible_columns: ['financial_event_score', 'deducted_netprofit'],
}, 1, 50, { by: 'financial_event_score', direction: 'desc' });

assert.deepEqual(financialQuery.ranges.financial_event_hit, { min: 1 });
assert.deepEqual(financialQuery.ranges.deducted_netprofit, { min: 50000000 });
assert.equal(financialQuery.sort_by, 'financial_event_score');
assert.ok(FREE_REVIEW_METRIC_GROUPS.some((group) => group.label === '财报事件'));

const data = {};
const storage = {
  getItem(key) { return data[key] || null; },
  setItem(key, value) { data[key] = value; },
};
saveFreeReviewPreset('低估值成长', { ranges: { pe_ttm: { max: 30 } } }, storage);
assert.equal(loadFreeReviewPresets(storage)[0].name, '低估值成长');
data.free_review_presets = '{broken';
assert.deepEqual(loadFreeReviewPresets(storage), []);

assert.equal(freeReviewBuildState({ status: 'pending' }).text, '等待构建');
assert.equal(freeReviewBuildState({ status: 'running', stage: 'financial' }).text, '同步财务指标');
assert.equal(freeReviewBuildState({ status: 'success', total_count: 5000 }).state, 'success');
assert.equal(freeReviewBuildState({ status: 'failed', error_message: '权限不足' }).state, 'failed');

assert.ok(FREE_REVIEW_METRIC_GROUPS.length >= 5);
assert.ok(FREE_REVIEW_METRIC_GROUPS.flatMap((group) => group.metrics)
  .some((metric) => metric.key === 'pe_ttm'));

assert.deepEqual(
  validateMacdSettings({ fast_period: 5, slow_period: 34, signal_period: 5 }),
  { valid: true, message: '' },
);
assert.equal(validateMacdSettings({
  fast_period: 5.5, slow_period: 34, signal_period: 5,
}).valid, false);
assert.equal(validateMacdSettings({
  fast_period: 34, slow_period: 34, signal_period: 5,
}).valid, false);
assert.equal(validateMacdSettings({
  fast_period: 1, slow_period: 34, signal_period: 5,
}).valid, false);
assert.equal(
  macdParameterLabel({ fast_period: 5, slow_period: 34, signal_period: 5 }),
  'MACD 5/34/5',
);

console.log('free-review utility regression ok');
