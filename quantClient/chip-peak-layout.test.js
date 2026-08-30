const assert = require('assert');
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, 'styles.css'), 'utf8');
const [stageHtml, overnightHtml = ''] = html.split('<h3>盘末隔夜溢价 TOP20</h3>');

function declarationsFor(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  return match ? match[1] : '';
}

// Regression: the three stage tables must size the chip column by content;
// fixed equal-width columns truncate the peak and concentration details.
assert.ok(stageHtml.includes('intraday-stage-table'));
assert.ok(!overnightHtml.includes('intraday-stage-table'));
assert.match(declarationsFor('.intraday-stage-table table'), /table-layout:\s*auto/);
assert.match(declarationsFor('.intraday-stage-table .chip-washout'), /width:\s*300px/);
assert.match(declarationsFor('.intraday-stage-table .chip-washout'), /overflow:\s*visible/);
assert.match(declarationsFor('.chip-washout-detail'), /white-space:\s*normal/);

console.log('chip peak layout regression ok');
