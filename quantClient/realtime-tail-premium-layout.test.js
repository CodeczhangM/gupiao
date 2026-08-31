const assert = require('assert');
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(
  path.join(__dirname, 'index.html'),
  'utf8',
);
const main = fs.readFileSync(
  path.join(__dirname, 'main.js'),
  'utf8',
);
const [stageHtml, overnightHtml = ''] = html.split(
  '<h3>盘末隔夜溢价 TOP20</h3>',
);

assert.ok(html.includes('盘末隔夜溢价 TOP20'));
assert.ok(html.includes('premium_score'));
assert.ok(html.includes('tail_score'));
assert.ok(html.includes('limit_score'));
assert.ok(html.includes('sector_score'));
assert.ok(html.includes('trend_score'));
assert.ok(html.includes('volume_score'));
assert.ok(html.includes('position_score'));
assert.ok(html.includes('risk_items'));
assert.ok(html.includes('buy_reasons'));
assert.ok(html.includes('base_premium_score'));
assert.ok(html.includes('market_environment_adjustment'));
assert.ok(html.includes('market_environment_reason'));
assert.ok(html.includes('next_day_plan'));
assert.ok(main.includes('/realtime-info?limit=20'));
assert.ok(html.includes('过滤调试'));
assert.ok(html.includes('tailPremiumDebugRows'));
assert.ok(html.includes('resonance_type'));
assert.ok(main.includes('realtimePositionCandidateRows'));
assert.ok(main.includes('position_candidates'));
assert.ok(html.includes('近期观察与建仓'));
assert.ok(html.includes('建仓等级'));
assert.ok(html.includes('板块/热点'));
assert.ok(!stageHtml.includes('v-for="stageTable in realtimeStageTables"'));
assert.ok(stageHtml.includes('<th>筹码峰</th>'));
assert.ok(stageHtml.includes('chipPeakDisplay(row)'));
assert.ok(stageHtml.includes('chip-washout'));
assert.ok(!overnightHtml.includes('chipPeakDisplay'));
assert.ok(!overnightHtml.includes('chip_peak_'));
assert.ok(!overnightHtml.includes('chip_washout_'));
assert.ok(!overnightHtml.includes('<th>筹码峰</th>'));
assert.ok(main.includes('realtimeTailPremiumDebug'));
assert.ok(main.includes('top_reasons'));
assert.ok(main.includes('debug=true'));
assert.ok(main.includes('/realtime-info/tail-premium?limit=20'));
assert.ok(main.includes('loadRealtimeTailPremium'));
assert.ok(html.includes('realtimeTailPremiumLoading'));
assert.ok(html.includes("loadRealtimeTailPremium(true, true)"));

// The separate next-morning module remains wired to its own endpoint.
assert.ok(html.includes('次日早盘跟进'));
assert.ok(main.includes('/morning-follow-monitor?limit=10'));

console.log('realtime tail premium layout regression ok');
