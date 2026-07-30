(function exposeFreeReviewUtils(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  Object.assign(root, api);
}(typeof globalThis !== 'undefined' ? globalThis : this, function buildFreeReviewUtils() {
  const metric = (key, label, format = 'number', options = {}) => ({
    key,
    label,
    format,
    filterable: options.filterable !== false,
    sortable: options.sortable !== false,
    defaultVisible: options.defaultVisible === true,
  });

  const FREE_REVIEW_METRIC_GROUPS = [
    {
      key: 'score',
      label: '综合评分',
      metrics: [
        metric('total_score', '总分', 'score', { defaultVisible: true }),
        metric('trend_score', '趋势分', 'score', { defaultVisible: true }),
        metric('volume_price_score', '量价分', 'score', { defaultVisible: true }),
        metric('momentum_score', '动量分', 'score'),
        metric('valuation_score', '估值分', 'score'),
        metric('financial_quality_score', '财务质量分', 'score'),
        metric('financial_growth_score', '财务成长分', 'score'),
        metric('risk_penalty', '风险扣分', 'score', { defaultVisible: true }),
        metric('data_completeness', '数据完整度', 'percent'),
      ],
    },
    {
      key: 'market',
      label: '行情与估值',
      metrics: [
        metric('close', '收盘价', 'number', { filterable: false, defaultVisible: true }),
        metric('pct_chg', '涨跌幅', 'percent', { defaultVisible: true }),
        metric('amount', '成交额', 'money', { defaultVisible: true }),
        metric('turnover_rate', '换手率', 'percent', { defaultVisible: true }),
        metric('turnover_rate_f', '自由流通换手', 'percent'),
        metric('volume_ratio', '量比', 'ratio', { defaultVisible: true }),
        metric('pe_ttm', '市盈率TTM', 'number', { defaultVisible: true }),
        metric('pb', '市净率', 'number'),
        metric('ps_ttm', '市销率TTM', 'number'),
        metric('dv_ttm', '股息率TTM', 'percent'),
        metric('total_mv', '总市值', 'marketValue', { defaultVisible: true }),
        metric('circ_mv', '流通市值', 'marketValue'),
      ],
    },
    {
      key: 'trend',
      label: '趋势与位置',
      metrics: [
        metric('ret_5', '5日涨幅', 'percent'),
        metric('ret_10', '10日涨幅', 'percent'),
        metric('ret_20', '20日涨幅', 'percent', { defaultVisible: true }),
        metric('ret_60', '60日涨幅', 'percent'),
        metric('drawdown_20', '距20日高点', 'percent'),
        metric('drawdown_60', '距60日高点', 'percent'),
        metric('ma20_slope', 'MA20斜率', 'percent', { defaultVisible: true }),
        metric('ma60_slope', 'MA60斜率', 'percent'),
        metric('vol_ratio_ma5', '量/5日均量', 'ratio'),
        metric('vol_ratio_ma10', '量/10日均量', 'ratio'),
        metric('vol_ratio_ma20', '量/20日均量', 'ratio'),
      ],
    },
    {
      key: 'momentum',
      label: '动量与波动',
      metrics: [
        metric('macd_hist', 'MACD柱', 'number', { filterable: false }),
        metric('rsi6', 'RSI6', 'number'),
        metric('rsi12', 'RSI12', 'number', { defaultVisible: true }),
        metric('rsi24', 'RSI24', 'number'),
        metric('atr_pct', 'ATR波动率', 'percent'),
        metric('boll_position', '布林位置', 'ratio', { filterable: false }),
      ],
    },
    {
      key: 'quality',
      label: '财务质量',
      metrics: [
        metric('roe', 'ROE', 'percent', { defaultVisible: true }),
        metric('roe_dt', '扣非ROE', 'percent'),
        metric('roa', 'ROA', 'percent'),
        metric('roic', 'ROIC', 'percent', { defaultVisible: true }),
        metric('grossprofit_margin', '毛利率', 'percent'),
        metric('netprofit_margin', '净利率', 'percent'),
        metric('current_ratio', '流动比率', 'ratio'),
        metric('debt_to_assets', '资产负债率', 'percent', { defaultVisible: true }),
        metric('ocf_to_or', '经营现金流/营收', 'percent'),
      ],
    },
    {
      key: 'growth',
      label: '财务成长',
      metrics: [
        metric('tr_yoy', '营收同比', 'percent', { defaultVisible: true }),
        metric('netprofit_yoy', '净利润同比', 'percent', { defaultVisible: true }),
        metric('dt_netprofit_yoy', '扣非净利同比', 'percent'),
        metric('ocf_yoy', '经营现金流同比', 'percent'),
      ],
    },
  ];

  function cleanArray(value) {
    if (!Array.isArray(value)) return [];
    return value.map((item) => String(item).trim()).filter(Boolean);
  }

  function rangeBoundary(value) {
    if (value === '' || value === null || value === undefined) return undefined;
    const number = Number(value);
    return Number.isFinite(number) ? number : undefined;
  }

  function normalizeFreeReviewQuery(filters = {}, page = 1, pageSize = 50, sort = {}) {
    const ranges = {};
    Object.entries(filters.ranges || {}).forEach(([key, value]) => {
      const min = rangeBoundary(value && value.min);
      const max = rangeBoundary(value && value.max);
      if (min === undefined && max === undefined) return;
      ranges[key] = {};
      if (min !== undefined) ranges[key].min = min;
      if (max !== undefined) ranges[key].max = max;
    });
    const result = {
      page: Math.max(1, Number.parseInt(page, 10) || 1),
      page_size: [50, 100, 200].includes(Number(pageSize)) ? Number(pageSize) : 50,
      sort_by: sort.by || sort.sort_by || 'total_score',
      sort_direction: (sort.direction || sort.sort_direction) === 'asc' ? 'asc' : 'desc',
      ranges,
    };
    const keyword = String(filters.keyword || '').trim();
    if (keyword) result.keyword = keyword;
    ['industries', 'areas', 'markets', 'visible_columns'].forEach((key) => {
      const values = cleanArray(filters[key]);
      if (values.length) result[key] = values;
    });
    ['trade_date', 'score_version', 'profit_state', 'volume_state', 'growth_state']
      .forEach((key) => {
        if (filters[key]) result[key] = filters[key];
      });
    return result;
  }

  const PRESET_KEY = 'free_review_presets';

  function loadFreeReviewPresets(storage = root.localStorage) {
    if (!storage) return [];
    try {
      const parsed = JSON.parse(storage.getItem(PRESET_KEY) || '[]');
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  function saveFreeReviewPreset(name, filters, storage = root.localStorage) {
    if (!storage) return;
    const cleanName = String(name || '').trim();
    if (!cleanName) throw new Error('请输入方案名称');
    const presets = loadFreeReviewPresets(storage)
      .filter((item) => item && item.name !== cleanName);
    presets.unshift({
      name: cleanName,
      filters: JSON.parse(JSON.stringify(filters || {})),
      updated_at: new Date().toISOString(),
    });
    storage.setItem(PRESET_KEY, JSON.stringify(presets.slice(0, 20)));
  }

  function freeReviewBuildState(payload = {}) {
    const status = payload.status || 'idle';
    const stages = {
      queued: '等待构建',
      cache: '读取行情缓存',
      financial: '同步财务指标',
      scoring: '计算全市场评分',
      persisting: '写入筛选快照',
      complete: '构建完成',
      failed: '构建失败',
    };
    if (status === 'success') {
      return {
        state: 'success',
        text: '构建完成',
        detail: `${payload.total_count || 0} 只 · 财务覆盖 ${Math.round(Number(payload.financial_coverage || 0) * 100)}%`,
      };
    }
    if (status === 'failed') {
      return {
        state: 'failed',
        text: '构建失败',
        detail: payload.error_message || '请检查行情缓存与 Tushare 权限',
      };
    }
    if (status === 'pending' || status === 'running') {
      return {
        state: status,
        text: stages[payload.stage] || (status === 'pending' ? '等待构建' : '正在构建'),
        detail: `${payload.processed_count || 0}/${payload.total_count || 0}`,
      };
    }
    return { state: 'idle', text: '尚未构建', detail: '请生成最新完整交易日快照' };
  }

  return {
    FREE_REVIEW_METRIC_GROUPS,
    normalizeFreeReviewQuery,
    saveFreeReviewPreset,
    loadFreeReviewPresets,
    freeReviewBuildState,
  };
}));
