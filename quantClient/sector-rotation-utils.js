(function exposeSectorRotationUtils(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.formatRotationMoney = api.formatRotationMoney;
  root.formatRotationPercent = api.formatRotationPercent;
  root.rotationSectorSummary = api.rotationSectorSummary;
  root.rotationStockText = api.rotationStockText;
}(typeof globalThis !== 'undefined' ? globalThis : this, function buildSectorRotationUtils() {
  function numberOrNull(value) {
    if (value === null || value === undefined || value === '') return null;
    const number = Number(value);
    return Number.isNaN(number) ? null : number;
  }

  function formatNumber(value, digits = 2, minDigits = 0) {
    const number = numberOrNull(value);
    if (number === null) return '--';
    return number.toLocaleString('zh-CN', {
      minimumFractionDigits: minDigits,
      maximumFractionDigits: digits,
    });
  }

  function formatRotationMoney(value) {
    const number = numberOrNull(value);
    if (number === null) return '--';
    return `${formatNumber(number / 100000000, 2, 2)}亿`;
  }

  function formatRotationPercent(value, digits = 2) {
    const number = numberOrNull(value);
    if (number === null) return '--';
    return `${formatNumber(number * 100, digits)}%`;
  }

  function rotationSectorSummary(row, mode) {
    if (!row) return '--';
    const scoreKey = mode === 'rotation'
      ? 'rotation_score'
      : 'continuation_score';
    const metrics = row.metrics || {};
    return [
      row.industry_name || '--',
      `分数 ${formatNumber(row[scoreKey], 1)}`,
      `今日 ${formatRotationMoney(metrics.net_amount_today)}`,
      `变化 ${formatRotationMoney(metrics.net_amount_change)}`,
      row.signal || '观察',
    ].join(' · ');
  }

  function rotationStockText(stock, scoreKey) {
    if (!stock) return '--';
    const identity = [];
    if (stock.name) identity.push(stock.name);
    if (stock.ts_code && stock.ts_code !== stock.name) {
      identity.push(stock.ts_code);
    }
    const parts = [identity.length ? identity.join(' ') : '--'];
    parts.push(`分 ${formatNumber(stock[scoreKey], 1)}`);
    if (stock.pct_chg !== undefined && stock.pct_chg !== null) {
      parts.push(`${formatNumber(stock.pct_chg)}%`);
    }
    if (stock.reason) parts.push(stock.reason);
    return parts.join(' · ');
  }

  return {
    formatRotationMoney,
    formatRotationPercent,
    rotationSectorSummary,
    rotationStockText,
  };
}));
