'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = __dirname;
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'styles.css'), 'utf8');

const outerStart = html.indexOf(`<section v-show="activeTab === 'overnight_monitor'"`);
const internalStart = html.indexOf(`<section v-show="activeTab === 'realtime_info'"`);
const reportsStart = html.indexOf(`<section v-show="activeTab === 'reports'"`, internalStart);

assert(outerStart >= 0, '缺少次日早盘跟进区块');
assert(internalStart > outerStart, '缺少实时信息区块或区块顺序异常');
assert(reportsStart > internalStart, '缺少历史区块或区块顺序异常');

const outerSection = html.slice(outerStart, internalStart);
const internalSection = html.slice(internalStart, reportsStart);

assert.match(
  outerSection,
  /class="table-wrap intraday-monitor-table morning-follow-table"/,
  '外层次日早盘跟进表应使用独立样式类'
);
assert.doesNotMatch(
  internalSection,
  /morning-follow-table/,
  '实时信息区块不能使用外层早盘跟进样式类'
);
assert.match(
  html,
  /styles\.css\?v=20260731-tail-premium-v1/,
  '应更新样式缓存版本'
);

assert.match(
  css,
  /\.morning-follow-table table\s*\{[^}]*width:\s*100%;[^}]*min-width:\s*100%;[^}]*table-layout:\s*fixed;/s,
  '早盘跟进表应在内容区内使用固定布局'
);
assert.match(
  css,
  /\.morning-follow-table th,\s*\.morning-follow-table td\s*\{[^}]*height:\s*auto;[^}]*white-space:\s*normal;[^}]*overflow:\s*visible;[^}]*text-overflow:\s*clip;[^}]*overflow-wrap:\s*anywhere;/s,
  '早盘跟进单元格应允许换行且不使用省略号'
);
assert.match(
  css,
  /\.morning-follow-table \.monitor-reason-cell\s*\{[^}]*min-width:\s*0;[^}]*max-width:\s*none;/s,
  '备注列不应继承通用最小宽度'
);
assert.match(
  css,
  /\.morning-follow-table \.monitor-badge\s*\{[^}]*white-space:\s*normal;/s,
  '状态徽标应允许换行'
);

const widths = [8, 6, 5, 5, 4, 5, 5, 9, 4, 6, 5, 5, 8, 25];
widths.forEach((width, index) => {
  const column = index + 1;
  const rule = new RegExp(
    `\\.morning-follow-table th:nth-child\\(${column}\\),\\s*` +
      `\\.morning-follow-table td:nth-child\\(${column}\\)\\s*` +
      `\\{\\s*width:\\s*${width}%;\\s*\\}`,
    's'
  );
  assert.match(css, rule, `第 ${column} 列宽度应为 ${width}%`);
});

assert.strictEqual(
  widths.reduce((total, width) => total + width, 0),
  100,
  '列宽总和必须为 100%'
);

console.log('morning follow layout tests passed');
