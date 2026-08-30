const assert = require('assert');
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
const main = fs.readFileSync(path.join(__dirname, 'main.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, 'styles.css'), 'utf8');

assert.match(html, /activeTab === 'cycle_watch'/);
assert.match(html, /周期关注/);
assert.match(main, /确认后介入/);
assert.match(main, /低吸提示/);
assert.match(main, /继续观察/);
assert.match(html, /cycle-watch-utils\.js/);
assert.match(html, /@submit\.prevent="addCycleWatch"/);
assert.match(html, /markCycleWatchAlertsRead/);
assert.match(main, /async loadCycleWatchlist\(/);
assert.match(main, /async addCycleWatch\(/);
assert.match(main, /async checkCycleWatch\(/);
assert.match(main, /async deleteCycleWatch\(/);
assert.match(main, /async loadCycleWatchHistory\(/);
assert.match(css, /\.cycle-watch-grid/);

console.log('cycle watch layout tests passed');
