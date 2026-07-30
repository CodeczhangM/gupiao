const assert = require('assert');
const {
  morningFollowBadgeState,
  morningFollowDataStatus,
  morningFollowRemarkText,
} = require('./morning-follow-utils.js');

assert.equal(morningFollowBadgeState('可以跟进'), 'strong');
assert.equal(morningFollowBadgeState('谨慎跟进'), 'watch');
assert.equal(morningFollowBadgeState('放弃'), 'risk');
assert.equal(morningFollowBadgeState('数据未就绪'), 'risk');
assert.equal(morningFollowBadgeState('等待9:35确认'), 'watch');
assert.equal(morningFollowBadgeState('等待确认'), 'watch');
assert.equal(morningFollowBadgeState('明日观察'), 'watch');
assert.equal(morningFollowBadgeState('宽松观察'), 'watch');
assert.equal(morningFollowBadgeState(''), 'muted');

assert.equal(
  morningFollowRemarkText({
    setup_tier_label: '宽松观察',
    tail_condition_notes: ['尾盘涨幅0.11%，低于严格下限0.15%'],
  }),
  '宽松观察 · 尾盘涨幅0.11%，低于严格下限0.15%',
);
assert.equal(
  morningFollowRemarkText({
    setup_tier_label: '严格候选',
    setup_tier_reason: '尾盘三项及观察分均达标',
  }),
  '严格候选 · 尾盘三项及观察分均达标',
);

assert.deepEqual(
  morningFollowDataStatus({
    data_status: 'stale',
    requested_candidate_trade_date: '20260730',
    candidate_trade_date: '20260729',
  }),
  {
    text: '备用缓存',
    state: 'watch',
    detail: '计划候选日 20260730 · 实际数据日 20260729',
  },
);
assert.deepEqual(
  morningFollowDataStatus({
    data_status: 'live',
    candidate_trade_date: '20260730',
  }),
  {
    text: '实时数据',
    state: 'strong',
    detail: '数据日 20260730',
  },
);

console.log('morning-follow utility regression ok');
