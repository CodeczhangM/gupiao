const fs = require('fs');
const assert = require('assert');

const main = fs.readFileSync(`${__dirname}/main.js`, 'utf8');
const html = fs.readFileSync(`${__dirname}/index.html`, 'utf8');

for (const token of [
  'cacheStatus', 'cacheLoading', 'cacheSyncing', 'cacheRows',
  'cacheSyncProgressTimer', 'cacheProgressTarget', 'cacheProgressPercent', 'cacheProgressText',
  'latest_complete_date',
  'startCacheProgressPolling', 'stopCacheProgressPolling',
  'loadCacheStatus', 'syncCache', "'/cache/status'", '/cache/sync', 'forceCurrent',
  'setInterval', 'clearInterval',
]) {
  assert(main.includes(token), `main.js missing ${token}`);
}

for (const token of [
  "activeTab === 'cache'", '数据缓存', '增量同步', '强制刷新当天',
  '更新进度', 'cache-progress-bar', 'cache-progress-fill', 'cacheProgressText',
  '数据源', '最新日期', '记录数', '错误信息', 'latest.cache_warnings',
  'sector-potential-utils.js',
]) {
  assert(html.includes(token), `index.html missing ${token}`);
}

console.log('cache view-model contract ok');
