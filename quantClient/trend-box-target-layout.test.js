const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = __dirname;
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const main = fs.readFileSync(path.join(root, 'main.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'styles.css'), 'utf8');

assert.match(
  html,
  /<button[^>]*activeTab === 'trend_box_target'[^>]*>[^<]*箱体目标/,
  'navigation should include trend_box_target tab',
);
assert.match(
  html,
  /<section[^>]*activeTab === 'trend_box_target'/,
  'trend-box target section should exist',
);
assert.match(html, /trendBoxForm\.tsCode/, 'stock code input should be bound');
assert.match(html, /trendBoxForm\.lookbackDays/, 'lookback input should be bound');
assert.match(html, /trendBoxForm\.autoDetect/, 'auto-detect switch should be bound');
assert.match(html, /trendBoxForm\.boxStart/, 'manual box start should be bound');
assert.match(html, /trendBoxForm\.boxEnd/, 'manual box end should be bound');
assert.match(html, /runTrendBoxTarget/, 'module should expose a run action');
assert.match(html, /trendBoxResult\.current_target/, 'current target should render');
assert.match(html, /trendBoxResult\.manual_box/, 'manual box analysis should render');
assert.match(html, /trendBoxBacktestSegments/, 'wave backtest table should render');
assert.match(html, /最近K线反转/, 'summary should describe recent-candle reversal detection');
assert.match(html, /横盘结束/, 'summary should describe sideways breakout completion');

assert.match(main, /trend_box_target:\s*'箱体目标'/, 'page title should include trend-box module');
assert.match(main, /trendBoxLoading:\s*false/, 'main state should include loading flag');
assert.match(main, /async runTrendBoxTarget\(\)/, 'main should include API loader');
assert.match(
  main,
  /\/stocks\/\$\{encodeURIComponent\(tsCode\)\}\/trend-box-target/,
  'API path should call trend-box backend endpoint',
);
assert.match(main, /auto_detect/, 'API request should pass auto_detect mode');
assert.match(main, /box_start/, 'API request should pass manual box start');
assert.match(main, /box_end/, 'API request should pass manual box end');
assert.match(css, /\.trend-box-page/, 'CSS should style trend-box module');
assert.match(css, /\.trend-box-summary/, 'CSS should style trend-box summary');
assert.match(css, /\.target-band/, 'CSS should style target band');
assert.match(
  css,
  /\.trend-box-table table\s*\{[^}]*table-layout:\s*auto;/s,
  'trend-box table should use auto layout so box dates are not ellipsized',
);
assert.match(
  css,
  /\.trend-box-table th,\s*\.trend-box-table td\s*\{[^}]*white-space:\s*nowrap;[^}]*overflow:\s*visible;[^}]*text-overflow:\s*clip;/s,
  'trend-box table cells should show full date ranges',
);

console.log('trend-box target layout regression ok');
