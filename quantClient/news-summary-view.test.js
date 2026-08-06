const fs = require('fs');
const assert = require('assert');

const main = fs.readFileSync(`${__dirname}/main.js`, 'utf8');
const html = fs.readFileSync(`${__dirname}/index.html`, 'utf8');

for (const token of [
  'marketNewsSummary',
  'marketNewsLoading',
  'loadMarketNewsSummary',
  '/market-news-summary?market=all&limit=8',
]) {
  assert(main.includes(token), `main.js missing ${token}`);
}

for (const token of [
  "activeTab === 'market_news'",
  '消息面',
  'market-news-page',
  'news-sentiment-badge',
  'sentiment_tag',
  'sentiment_label',
  'summary_text',
  'focus_sectors',
  'ai_provider',
]) {
  assert(html.includes(token), `index.html missing ${token}`);
}

const styles = fs.readFileSync(`${__dirname}/styles.css`, 'utf8');
for (const token of [
  '.sentiment-positive',
  '.sentiment-negative',
  '.news-sentiment-badge',
]) {
  assert(styles.includes(token), `styles.css missing ${token}`);
}

console.log('market news summary view contract ok');
