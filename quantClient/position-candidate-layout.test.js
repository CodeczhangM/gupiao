const assert = require('assert');
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
const main = fs.readFileSync(path.join(__dirname, 'main.js'), 'utf8');
const [candidateHtml, overnightHtml = ''] = html.split('<h3>盘末隔夜溢价 TOP20</h3>', 2);

assert.ok(main.includes('realtimePositionCandidateRows'));
assert.ok(main.includes('position_candidates'));
assert.ok(candidateHtml.includes('近期观察与建仓'));
assert.ok(candidateHtml.includes('position_level'));
assert.ok(candidateHtml.includes('position_score'));
assert.ok(candidateHtml.includes('sector_hot_score'));
assert.ok(candidateHtml.includes('price_volume_score'));
assert.ok(candidateHtml.includes('macd_score'));
assert.ok(candidateHtml.includes('chip_peak_score'));
assert.ok(candidateHtml.includes('position_missing_confirmations'));
assert.ok(candidateHtml.includes('position_risk_items'));
assert.ok(candidateHtml.includes('过滤调试'));
assert.ok(candidateHtml.includes('positionFilterDebugRows'));
assert.ok(candidateHtml.includes('positionFilterDebugSamples'));
assert.ok(main.includes('realtimePositionDebug'));
assert.ok(main.includes('&debug=true'));
assert.ok(!candidateHtml.includes('v-for="stageTable in realtimeStageTables"'));
assert.ok(overnightHtml.includes('realtimeOvernightRows'));

console.log('position candidate layout regression ok');
