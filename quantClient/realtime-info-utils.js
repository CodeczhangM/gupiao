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
  root.marketRelativeText = api.marketRelativeText;
  root.historicalResilienceText = api.historicalResilienceText;
  root.chipPeakDisplay = api.chipPeakDisplay;
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
    return { text: '14:40前预观察', state: 'muted' };
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

  function marketRelativeText(row) {
    const source = row || {};
    const value = Number(source.relative_strength);
    if (!Number.isFinite(value)) {
      return {
        text: '强弱 --',
        title: '暂无相对大盘数据',
        state: 'muted',
      };
    }
    const label = String(source.market_resonance_label || '').trim();
    const signed = `${value >= 0 ? '+' : ''}${value.toFixed(2)}pct`;
    const text = `强弱 ${signed}${label ? ` · ${label}` : ''}`;
    return {
      text,
      title: source.market_resonance_reason || text,
      state: value >= 1 ? 'strong' : (value < 0 ? 'weak' : 'muted'),
    };
  }

  function historicalResilienceText(row) {
    const source = row || {};
    const score = Number(source.historical_resilience_score);
    if (!Number.isFinite(score)) {
      return {
        text: '--',
        title: '暂无近20日抗跌力数据',
        state: 'muted',
      };
    }
    const label = String(source.historical_resilience_label || '').trim();
    const rounded = Math.round(score);
    const text = `${rounded}分${label ? ` · ${label}` : ''}`;
    return {
      text,
      title: source.historical_resilience_reason || text,
      state: score >= 80 ? 'strong' : (score < 50 ? 'weak' : 'muted'),
    };
  }

  function chipPeakDisplay(row) {
    const source = row || {};
    const label = String(source.chip_washout_label || '筹码数据暂缺');
    const finiteNumber = (value) => {
      if (value === null || value === undefined || value === '') return null;
      const number = Number(value);
      return Number.isFinite(number) ? number : null;
    };
    const numberText = (value, digits = 2) => {
      const number = finiteNumber(value);
      return number === null ? '--' : number.toFixed(digits);
    };
    const scoreValue = finiteNumber(source.chip_washout_score);
    const peakPrice = numberText(source.chip_peak_price);
    const peakPercent = numberText(source.chip_peak_percent);
    const distanceValue = finiteNumber(source.chip_price_distance_pct);
    const distance = distanceValue !== null
      ? `${distanceValue >= 0 ? '+' : ''}${distanceValue.toFixed(2)}%`
      : '--';
    const concentration70 = numberText(source.chip_concentration_70_pct);
    const concentration90 = numberText(source.chip_concentration_90_pct);
    let state = 'muted';
    if (source.chip_build_position === true) state = 'strong';
    else if (label.includes('等待确认') || label === '筹码整理') state = 'watch';
    else if (label === '筹码结构偏弱') state = 'risk';

    return {
      label,
      score: scoreValue !== null ? `${Math.round(scoreValue)}分` : '--',
      peak: peakPrice === '--'
        ? '主峰 --'
        : `主峰 ${peakPrice} / ${peakPercent}% · 距峰 ${distance}`,
      concentration: `70% ${concentration70 === '--' ? '--' : `${concentration70}%`} · 90% ${concentration90 === '--' ? '--' : `${concentration90}%`}`,
      state,
      title: source.chip_washout_reason || '暂无有效筹码峰数据',
    };
  }

  return {
    chipPeakDisplay,
    detailListText,
    historicalResilienceText,
    marketRelativeText,
    premiumRiskState,
    realtimeCacheState,
    realtimeDataStatus,
    screeningDataTimeText,
    tailPremiumSelectionState,
    tailVolumeDisplay,
  };
}));
