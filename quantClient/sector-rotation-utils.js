/**
 * @typedef {Object} SectorTrendData
 * @property {string} industry_name 板块名称
 * @property {string} trend_rating 综合评级 strong|bullish|watch|weak|avoid
 * @property {string} trend_rating_text 评级中文
 * @property {string} trend_desc 动态趋势说明
 * @property {{avg_pct_chg?: number, amount_today?: number}} metrics 当日行情
 * @property {{
 *   trend?: 'inflow'|'outflow'|'oscillation'|'unknown',
 *   trend_text?: string,
 *   net_amount_today?: number,
 *   net_amount_3d?: Array<number|null>,
 *   net_amount_sum_3d?: number|null,
 * }} capital_flow 资金流向
 * @property {{
 *   ready?: boolean,
 *   dif?: number, dea?: number, histogram?: number,
 *   previous_dif?: number, previous_dea?: number, previous_histogram?: number,
 *   status?: 'golden_cross'|'death_cross'|'critical'|null,
 *   status_text?: string,
 *   zero_axis?: 'above'|'below'|'crossing'|null,
 *   zero_axis_text?: string,
 *   histogram_trend?: string,
 *   histogram_trend_text?: string,
 * }} macd 周线 MACD 状态
 */
(function exposeSectorRotationUtils(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.formatRotationMoney = api.formatRotationMoney;
  root.formatRotationPercent = api.formatRotationPercent;
  root.rotationSectorSummary = api.rotationSectorSummary;
  root.rotationStockText = api.rotationStockText;
  root.signedRotationMoney = api.signedRotationMoney;
  root.capitalTrendArrow = api.capitalTrendArrow;
  root.capitalTrendClass = api.capitalTrendClass;
  root.trendRatingClass = api.trendRatingClass;
  root.macdTagList = api.macdTagList;
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

  function signedRotationMoney(value) {
    const number = numberOrNull(value);
    if (number === null) return '--';
    const sign = number > 0 ? '+' : '';
    return `${sign}${formatNumber(number / 100000000, 2, 2)}亿`;
  }

  function capitalTrendArrow(trend) {
    if (trend === 'inflow') return '↗';
    if (trend === 'outflow') return '↘';
    if (trend === 'oscillation') return '↔';
    return '·';
  }

  function capitalTrendClass(trend) {
    if (trend === 'inflow') return 'up';
    if (trend === 'outflow') return 'down';
    if (trend === 'oscillation') return 'muted';
    return 'muted';
  }

  function trendRatingClass(rating) {
    return `trend-badge is-${rating || 'watch'}`;
  }

  /**
   * 周线 MACD 展示标签：金叉/死叉/临界 + 零轴 + 柱体趋势。
   * 数据不足时返回单个“数据不足”标签，不做任何猜测。
   */
  function macdTagList(macd) {
    if (!macd || !macd.ready) {
      return [{ label: 'MACD 数据不足', kind: 'muted' }];
    }
    const tags = [];
    const statusKind = {
      golden_cross: 'up',
      death_cross: 'down',
      critical: 'muted',
    };
    if (macd.status_text) {
      tags.push({ label: `周线${macd.status_text}`, kind: statusKind[macd.status] || 'muted' });
    }
    const zeroKind = { above: 'up', below: 'down', crossing: 'muted' };
    if (macd.zero_axis_text) {
      tags.push({ label: macd.zero_axis_text, kind: zeroKind[macd.zero_axis] || 'muted' });
    }
    const histogramKind = {
      red_expand: 'up',
      green_expand: 'down',
      red_shrink: 'down',
      green_shrink: 'up',
    };
    if (macd.histogram_trend_text) {
      tags.push({
        label: macd.histogram_trend_text,
        kind: histogramKind[macd.histogram_trend] || 'muted',
      });
    }
    return tags;
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
    signedRotationMoney,
    capitalTrendArrow,
    capitalTrendClass,
    trendRatingClass,
    macdTagList,
  };
}));
