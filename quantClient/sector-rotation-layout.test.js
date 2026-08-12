const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = __dirname;
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const main = fs.readFileSync(path.join(root, 'main.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'styles.css'), 'utf8');

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
  html.includes('sector-rotation-utils.js'),
  'index should load sector rotation utilities',
);
assert(main.includes('sectorRotation:'), 'main state should include sectorRotation');
assert(main.includes('loadSectorRotation'), 'main should include loadSectorRotation method');
assert(
  main.includes('/sector-rotation/tomorrow'),
  'main should call sector rotation API',
);
assert(
  css.includes('.sector-rotation-page'),
  'css should include sector rotation page styles',
);

console.log('sector rotation layout ok');
