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
assert.ok(html.includes('bottom_position_20d'));
assert.ok(html.includes('bottom_box_amplitude_20d'));
assert.ok(html.includes('bottom_limit_up_date'));
assert.ok(html.includes('bottom_pullback_pct'));
assert.ok(html.includes('bottom_breakout_days'));
assert.ok(html.includes('bottom_volume_expansion'));
assert.ok(html.includes('bottom_consolidation_reason'));
assert.ok(html.includes('bottom_filter_debug'));
assert.ok(html.includes('daily_candidate_count'));
assert.ok(html.includes('minute_loaded_count'));
assert.ok(html.includes('technical_confirmed_count'));
assert.ok(html.includes('final_output_count'));
assert.ok(main.includes('realtimeObservationRows'));
assert.ok(main.includes('realtimeTriggerRows'));
assert.ok(main.includes('realtimeLaunchRows'));
assert.ok(html.includes('缩量企稳观察'));
assert.ok(html.includes('底部首阳触发'));
assert.ok(html.includes('底部放量启动'));
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

// The separate next-morning module remains wired to its own endpoint.
assert.ok(html.includes('次日早盘跟进'));
assert.ok(main.includes('/morning-follow-monitor?limit=10'));

console.log('realtime tail premium layout regression ok');
