(function cycleWatchUtilsModule(root) {
  const SH_PREFIXES = ['600', '601', '603', '605', '688', '689'];
  const SZ_PREFIXES = ['000', '001', '002', '003', '300', '301'];

  function normalizeCycleWatchInput(raw) {
    const value = String(raw || '').trim().toUpperCase();
    const match = value.match(/^(\d{6})(?:\.(SH|SZ))?$/);
    if (!match) throw new Error('股票代码必须为六位数字，可选带 .SH 或 .SZ 后缀');
    const digits = match[1];
    const supplied = match[2];
    const expected = SH_PREFIXES.some((prefix) => digits.startsWith(prefix))
      ? 'SH'
      : (SZ_PREFIXES.some((prefix) => digits.startsWith(prefix)) ? 'SZ' : null);
    if (!expected) throw new Error('暂仅支持沪深 A 股代码');
    if (supplied && supplied !== expected) throw new Error(`股票代码 ${digits} 的市场后缀应为 .${expected}`);
    return `${digits}.${expected}`;
  }

  function numberValue(value, fallback = 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function factor(row, key, fallback = 0) {
    return numberValue(row && row.factors && row.factors[key], fallback);
  }

  function cycleWatchGroups(rows) {
    const source = Array.isArray(rows) ? rows.slice() : [];
    const confirmed = source.filter((row) => row.status === 'confirmed').sort((left, right) => (
      numberValue(right.opportunity_score) - numberValue(left.opportunity_score)
      || factor(right, 'confirmation_count') - factor(left, 'confirmation_count')
    ));
    const lowBuy = source.filter((row) => row.status === 'low_buy').sort((left, right) => (
      factor(left, 'support_distance_pct', Number.POSITIVE_INFINITY)
      - factor(right, 'support_distance_pct', Number.POSITIVE_INFINITY)
      || factor(right, 'volume_contraction') - factor(left, 'volume_contraction')
      || numberValue(right.opportunity_score) - numberValue(left.opportunity_score)
    ));
    const watch = source.filter((row) => row.status === 'watch').sort(
      (left, right) => numberValue(right.opportunity_score) - numberValue(left.opportunity_score),
    );
    const delayed = source.filter((row) => row.status === 'data_delayed');
    return { confirmed, lowBuy, watch, delayed };
  }

  function cycleWatchAlertCount(rows) {
    return (Array.isArray(rows) ? rows : []).filter(
      (row) => row.is_new_alert && !row.alert_read,
    ).length;
  }

  const api = { normalizeCycleWatchInput, cycleWatchGroups, cycleWatchAlertCount };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) Object.assign(root, api);
}(typeof window !== 'undefined' ? window : globalThis));
