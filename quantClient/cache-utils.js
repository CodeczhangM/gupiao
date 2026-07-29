(function exposeCacheUtils(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.latestCacheRows = api.latestCacheRows;
  root.cacheProgressText = api.cacheProgressText;
}(typeof globalThis !== 'undefined' ? globalThis : this, function buildCacheUtils() {
  const sources = ['daily', 'daily_basic', 'stock_basic', 'moneyflow_ind_dc'];

  function latestCacheRows(rows) {
    const latest = new Map();
    (Array.isArray(rows) ? rows : []).forEach((row) => {
      if (!row || !row.source_name) return;
      const current = latest.get(row.source_name);
      const rowDate = String(row.trade_date || '');
      const currentDate = String((current && current.trade_date) || '');
      const rowTime = String(row.updated_at || row.completed_at || '');
      const currentTime = String((current && (current.updated_at || current.completed_at)) || '');
      if (!current || rowDate > currentDate || (rowDate === currentDate && rowTime > currentTime)) {
        latest.set(row.source_name, row);
      }
    });
    return sources.map((source) => ({ source_name: source, ...(latest.get(source) || {}) }));
  }

  function cacheProgressText(status) {
    const target = Number(status && (status.target_days || status.bootstrap_days || 120));
    const current = status && status.complete_dates !== undefined ? status.complete_dates : '--';
    const missing = Number(status && status.missing_dates);
    const suffix = Number.isFinite(missing) && missing <= 0
      ? '已补齐'
      : `还差 ${Number.isFinite(missing) ? missing : '--'} 个`;
    return `${current}/${target || 120} 个完整交易日 · ${suffix}`;
  }

  return { cacheProgressText, latestCacheRows };
}));
