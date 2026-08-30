const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = __dirname;
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const main = fs.readFileSync(path.join(root, 'main.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'styles.css'), 'utf8');
const rotationSection = html.match(/<div class="sector-rotation-stack">[\s\S]*?<section v-show="activeTab === '(?:cycle_watch|intraday_monitor)'"/)?.[0] || '';

assert(
  html.includes("activeTab === 'sector_rotation'"),
  'navigation should include sector_rotation tab',
);
assert(html.includes('明日轮动'), 'page should show 明日轮动 copy');
assert(html.includes('延续流入榜'), 'page should show continuation board');
assert(html.includes('轮动回流榜'), 'page should show rotation board');
assert(html.includes('进攻龙头'), 'page should show attack leaders');
assert(html.includes('补涨候选'), 'page should show catch-up candidates');
assert(
  html.includes('sector-rotation-stack'),
  'rotation boards should stack vertically instead of using a side-by-side grid',
);
assert(
  html.includes('rotation-stock-card'),
  'recommended stocks should render as detailed stock cards',
);
assert(
  html.includes('rotation-sector-card'),
  'rotation boards should render each sector as a readable card',
);
assert(
  rotationSection && !rotationSection.includes('<table>'),
  'sector rotation boards should avoid horizontal table scrolling',
);
assert(html.includes('量比'), 'stock cards should show volume ratio');
assert(html.includes('换手'), 'stock cards should show turnover rate');
assert(html.includes('成交额'), 'stock cards should show amount');
assert(
  html.includes('formatSectorRotationStockAmount(stock)'),
  'stock cards should format raw daily amount with the sector rotation amount helper',
);
assert(
  html.includes('sector-rotation-utils.js'),
  'index should load sector rotation utilities',
);
assert(main.includes('sectorRotation:'), 'main state should include sectorRotation');
assert(main.includes('loadSectorRotation'), 'main should include loadSectorRotation method');
assert(
  main.includes('formatSectorRotationStockAmount'),
  'main should include sector rotation stock amount formatter',
);
assert(
  main.includes('/sector-rotation/tomorrow'),
  'main should call sector rotation API',
);
assert(
  css.includes('.sector-rotation-page'),
  'css should include sector rotation page styles',
);
assert(
  !html.includes('sector-rotation-grid') && !css.includes('.sector-rotation-grid'),
  'sector rotation boards should not use the old grid class',
);
assert(
  !css.includes('.sector-rotation-board {\n  overflow-x: auto;'),
  'sector rotation boards should not rely on bottom horizontal scrollbars',
);

console.log('sector rotation layout ok');
