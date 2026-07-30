(function exposeRealtimeInfoUtils(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.tailVolumeDisplay = api.tailVolumeDisplay;
  root.realtimeDataStatus = api.realtimeDataStatus;
}(typeof globalThis !== 'undefined' ? globalThis : this, function buildRealtimeInfoUtils() {
  function tailVolumeDisplay(row, isAfter1430) {
    if (!row || row.tail_after_1430_available !== true) {
      return {
        text: isAfter1430 ? '无数据' : '未到',
        state: 'muted',
      };
    }
    const rawRatio = row.tail_volume_ratio;
    if (rawRatio === null || rawRatio === undefined || rawRatio === '') {
      return { text: '无数据', state: 'muted' };
    }
    const ratio = Number(rawRatio);
    if (!Number.isFinite(ratio)) {
      return { text: '无数据', state: 'muted' };
    }
    const expanded = ratio >= 1.5;
    return {
      text: `${expanded ? '放量' : '正常'} ${ratio.toFixed(2)}倍`,
      state: expanded ? 'strong' : 'muted',
    };
  }

  function formatAge(seconds) {
    const safeSeconds = Math.max(0, Math.floor(Number(seconds) || 0));
    const minutes = Math.floor(safeSeconds / 60);
    const remaining = safeSeconds % 60;
    if (minutes <= 0) return `${remaining}秒`;
    return `${minutes}分${remaining}秒`;
  }

  function realtimeDataStatus(payload) {
    const source = payload || {};
    if (source.data_status === 'stale') {
      return {
        text: source.data_status_label || '备用缓存',
        state: 'warning',
        detail: `数据时间 ${source.data_updated_at || '--'} · 已过期${formatAge(source.stale_age_seconds)}`,
      };
    }
    if (source.data_status === 'live') {
      return {
        text: source.data_status_label || '实时数据',
        state: 'live',
        detail: `数据时间 ${source.data_updated_at || '--'}`,
      };
    }
    return {
      text: '数据不可用',
      state: 'danger',
      detail: '当前没有可展示的实时或备用数据',
    };
  }

  return { realtimeDataStatus, tailVolumeDisplay };
}));
