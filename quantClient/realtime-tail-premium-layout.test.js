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
assert.ok(html.includes('next_day_plan'));
assert.ok(main.includes('/realtime-info?limit=20'));

// The separate next-morning module remains wired to its own endpoint.
assert.ok(html.includes('次日早盘跟进'));
assert.ok(main.includes('/morning-follow-monitor?limit=10'));

console.log('realtime tail premium layout regression ok');
