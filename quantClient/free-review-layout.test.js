'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = __dirname;
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'styles.css'), 'utf8');
const main = fs.readFileSync(path.join(root, 'main.js'), 'utf8');

[
  '自由复盘选股',
  'free-review-utils.js',
  '生成复盘数据',
  '重新生成',
  '导出 CSV',
  '板块总览',
  '财务覆盖率',
].forEach((text) => assert.ok(html.includes(text), `页面缺少：${text}`));

[
  ['dip', '超跌反转'],
  ['strong', '趋势突破'],
  ['first_limit', '主升浪启动'],
].forEach(([tab, label]) => {
  assert.doesNotMatch(
    html,
    new RegExp(`<button[^>]*activeTab === '${tab}'[^>]*>[^<]*${label}`),
    `导航中不应保留${label}`,
  );
  assert.doesNotMatch(
    html,
    new RegExp(`<section[^>]*activeTab === '${tab}'`),
    `不应保留${label}独立列表`,
  );
});

assert.match(html, /activeTab === 'free_review'/);
assert.match(html, /freeReviewFilters\.ranges\[metric\.key\]\.min/);
assert.match(html, /freeReviewVisibleMetrics/);
assert.match(css, /\.free-review-range-grid/);
assert.match(css, /\.free-review-table th:first-child/);
assert.match(css, /\.free-review-table th:nth-child\(4\)/);
assert.doesNotMatch(html, /class="stage-guide"/);
assert.match(main, /activeTab:\s*'free_review'/);
assert.match(main, /page_size/);
assert.match(main, /response\.blob\(\)/);
assert.match(
  main,
  /if \(meta\.ready === false\)/,
  '首次尚无快照时不应继续请求筛选结果',
);
[
  '快线周期',
  '慢线周期',
  '信号周期',
  '保存并重新计算',
].forEach((text) => assert.ok(html.includes(text), `MACD 设置缺少：${text}`));
assert.match(html, /v-model\.number="macdSettingsForm\.fast_period"/);
assert.match(html, /v-model\.number="macdSettingsForm\.slow_period"/);
assert.match(html, /v-model\.number="macdSettingsForm\.signal_period"/);
assert.match(main, /loadMacdSettings/);
assert.match(main, /saveMacdSettingsAndRecalculate/);
assert.ok(
  (html.match(/macdBasisText/g) || []).length >= 5,
  '自由复盘、实时信息、实时共振、隔夜和次日早盘都应显示 MACD 口径',
);

console.log('free-review layout regression ok');
