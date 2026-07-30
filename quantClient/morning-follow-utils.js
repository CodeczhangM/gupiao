(function exposeMorningFollowUtils(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.morningFollowBadgeState = api.morningFollowBadgeState;
  root.morningFollowDataStatus = api.morningFollowDataStatus;
  root.morningFollowRemarkText = api.morningFollowRemarkText;
}(typeof globalThis !== 'undefined' ? globalThis : this, function buildMorningFollowUtils() {
  function morningFollowBadgeState(status) {
    if (status === '可以跟进') return 'strong';
    if (status === '放弃' || status === '数据未就绪') return 'risk';
    if (
      status === '谨慎跟进'
      || status === '等待9:35确认'
      || status === '等待确认'
      || status === '明日观察'
      || status === '宽松观察'
    ) return 'watch';
    return 'muted';
  }

  function morningFollowRemarkText(row) {
    const source = row || {};
    const label = source.setup_tier_label || '观察候选';
    const notes = Array.isArray(source.tail_condition_notes)
      ? source.tail_condition_notes.filter(Boolean)
      : [];
    const detail = notes.length
      ? notes.join('；')
      : (source.setup_tier_reason || '尾盘条件待确认');
    return `${label} · ${detail}`;
  }

  function morningFollowDataStatus(payload) {
    const source = payload || {};
    const actualDate = source.candidate_trade_date || '--';
    if (source.data_status === 'stale' || source.data_current === false) {
      const requestedDate = source.requested_candidate_trade_date || '--';
      return {
        text: source.data_status_label || '备用缓存',
        state: 'watch',
        detail: `计划候选日 ${requestedDate} · 实际数据日 ${actualDate}`,
      };
    }
    return {
      text: source.data_status_label || '实时数据',
      state: 'strong',
      detail: `数据日 ${actualDate}`,
    };
  }

  return {
    morningFollowBadgeState,
    morningFollowDataStatus,
    morningFollowRemarkText,
  };
}));
