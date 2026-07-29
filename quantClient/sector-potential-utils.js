(function exposeSectorPotentialUtils(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.sectorLeaderText = api.sectorLeaderText;
}(typeof globalThis !== 'undefined' ? globalThis : this, function buildSectorPotentialUtils() {
  function formatNumber(value, digits = 2) {
    if (value === null || value === undefined || value === '') return '--';
    const number = Number(value);
    if (Number.isNaN(number)) return value;
    return number.toLocaleString('zh-CN', {
      minimumFractionDigits: 0,
      maximumFractionDigits: digits,
    });
  }

  function formatYi(value) {
    const number = Number(value);
    if (Number.isNaN(number)) return '--';
    return number.toLocaleString('zh-CN', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  function sectorLeaderText(leader) {
    if (!leader) return '--';
    const identity = [];
    if (leader.name) identity.push(leader.name);
    if (leader.ts_code && leader.ts_code !== leader.name) identity.push(leader.ts_code);
    const parts = [identity.length ? identity.join(' ') : '--'];
    if (leader.pct_chg !== undefined && leader.pct_chg !== null) parts.push(`${formatNumber(leader.pct_chg)}%`);
    if (leader.total_mv_yuan !== undefined && leader.total_mv_yuan !== null) {
      parts.push(`${formatYi(Number(leader.total_mv_yuan) / 100000000)}亿`);
    }
    if (leader.pool_tag) parts.push(leader.pool_tag);
    if (leader.leader_reason) parts.push(leader.leader_reason);
    return parts.join(' · ');
  }

  return { sectorLeaderText };
}));
