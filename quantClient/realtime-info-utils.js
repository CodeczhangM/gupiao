(function exposeRealtimeInfoUtils(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.tailVolumeDisplay = api.tailVolumeDisplay;
  root.realtimeCacheState = api.realtimeCacheState;
  root.realtimeDataStatus = api.realtimeDataStatus;
  root.screeningDataTimeText = api.screeningDataTimeText;
  root.tailPremiumSelectionState = api.tailPremiumSelectionState;
  root.premiumRiskState = api.premiumRiskState;
  root.detailListText = api.detailListText;
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

  function realtimeCacheState(payload) {
    const source = payload || {};
    const updatedAt = source.cache_updated_at || source.data_updated_at || '--';
    if (source.cache_source === 'database') {
      return {
        text: '数据库快速结果',
        state: 'cached',
        detail: `缓存更新于 ${updatedAt}`,
      };
    }
    if (source.cache_source === 'memory') {
      return {
        text: '内存快速结果',
        state: 'cached',
        detail: `缓存更新于 ${updatedAt}`,
      };
    }
    if (source.cache_source === 'fresh') {
      return {
        text: '刚刚强制刷新',
        state: 'fresh',
        detail: `刷新于 ${updatedAt}`,
      };
    }
    return {
      text: '尚未查询',
      state: 'empty',
      detail: '可快速查看数据库结果或强制刷新行情',
    };
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

  function screeningDataTimeText(payload) {
    const source = payload || {};
    if (source.data_status === 'unavailable') {
      return '筛选数据时间 --';
    }
    const prefix = (
      source.data_current === false
      || source.data_status === 'stale'
    ) ? '备用缓存 · ' : '';
    if (source.data_as_of) {
      return `${prefix}筛选数据截至 ${source.data_as_of}`;
    }
    const tradeDate = (
      source.data_trade_date
      || source.base_trade_date
      || source.trade_date
    );
    if (!tradeDate) return '筛选数据时间 --';
    return `${prefix}筛选数据日 ${tradeDate}`;
  }

  function tailPremiumSelectionState(payload) {
    const state = (payload || {}).selection_state;
    if (state === 'live_tail_window') {
      return { text: '盘末动态候选', state: 'live' };
    }
    if (state === 'closed_final') {
      return { text: '收盘最终结果', state: 'fresh' };
    }
    return { text: '14:50前预观察', state: 'muted' };
  }

  function premiumRiskState(row) {
    const level = (row || {}).risk_level;
    if (level === '高') return 'risk';
    if (level === '中') return 'watch';
    if (level === '低') return 'strong';
    return 'muted';
  }

  function detailListText(value) {
    if (Array.isArray(value)) {
      return value.length ? value.join('；') : '--';
    }
    const text = String(value || '').trim();
    return text || '--';
  }

  return {
    detailListText,
    premiumRiskState,
    realtimeCacheState,
    realtimeDataStatus,
    screeningDataTimeText,
    tailPremiumSelectionState,
    tailVolumeDisplay,
  };
}));
