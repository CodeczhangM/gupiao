const { createApp } = Vue;

function defaultApiBase() {
  if (window.location.protocol === 'file:') {
    return 'http://127.0.0.1:8080/api/quant';
  }
  return '/api/quant';
}

function initialApiBase() {
  const saved = localStorage.getItem('quant_api_base');
  if (window.location.protocol === 'file:' && (!saved || saved.startsWith('/'))) {
    return defaultApiBase();
  }
  return saved || defaultApiBase();
}

function formatNumber(value, digits = 2) {
  if (value === null || value === undefined || value === '') return '--';
  const number = Number(value);
  if (Number.isNaN(number)) return value;
  return number.toLocaleString('zh-CN', {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
}

function signedClass(value) {
  const number = Number(value);
  if (Number.isNaN(number)) return '';
  if (number > 0) return 'up';
  if (number < 0) return 'down';
  return '';
}

function displayMetric(value, fallback = '历史不足') {
  if (value === null || value === undefined || value === '') return fallback;
  return formatNumber(value);
}

function displayPercent(value, fallback = '历史不足') {
  if (value === null || value === undefined || value === '') return fallback;
  return `${formatNumber(value)}%`;
}

function displayMoney(value, fallback = '--') {
  if (value === null || value === undefined || value === '') return fallback;
  const number = Number(value);
  if (Number.isNaN(number)) return fallback;
  const abs = Math.abs(number);
  if (abs >= 100000000) return `${formatNumber(number / 100000000, 2)}亿`;
  if (abs >= 10000) return `${formatNumber(number / 10000, 2)}万`;
  return formatNumber(number, 2);
}

const StockTable = {
  props: {
    rows: { type: Array, default: () => [] },
    mode: { type: String, default: 'strong' },
    compact: { type: Boolean, default: false },
  },
  methods: {
    formatNumber,
    signedClass,
    displayMetric,
    displayPercent,
    confluenceText(row) {
      const count = row.breakout_confluence_count ?? '--';
      const score = row.breakout_confluence_score ?? '--';
      return `${count}项 / ${score}分`;
    },
    bollText(row) {
      const labels = [];
      if (row.boll_width_expand) labels.push('开口');
      if (row.close_near_boll_upper) labels.push('贴上轨');
      return labels.length ? labels.join('、') : '--';
    },
    kdjText(row) {
      return row.kdj_breakout_signal ? '金叉' : '--';
    },
    macdText(row) {
      const labels = [];
      if (row.macd_zero_axis_ready) labels.push('零轴');
      if (row.macd_cross_ready) labels.push('金叉');
      return labels.length ? labels.join('、') : '--';
    },
    breakoutSignalText(row) {
      const labels = [];
      const boll = this.bollText(row);
      if (boll !== '--') labels.push(`布林${boll}`);
      if (row.weekly_trend_state) labels.push(`周线${row.weekly_trend_state}`);
      const kdj = this.kdjText(row);
      if (kdj !== '--') labels.push(`KDJ${kdj}`);
      const macd = this.macdText(row);
      if (macd !== '--') labels.push(`MACD${macd}`);
      return labels.length ? labels.join(' · ') : '--';
    },
    compactColspan() {
      if (this.mode === 'reversal') return 7;
      if (this.mode === 'breakout') return 8;
      return 7;
    },
  },
  template: `
    <div class="table-wrap" :class="{ 'compact-table': compact }">
      <table>
        <thead>
          <tr v-if="compact">
            <th>代码</th>
            <th>名称</th>
            <th>涨跌幅</th>
            <th v-if="mode === 'reversal' || mode === 'breakout'" class="stage-col">阶段</th>
            <template v-if="mode === 'reversal'">
              <th>总分</th>
              <th>确认</th>
              <th>原因</th>
            </template>
            <template v-else-if="mode === 'breakout'">
              <th>状态</th>
              <th>共振</th>
              <th>信号</th>
              <th>评分</th>
            </template>
            <template v-else>
              <th>换手率</th>
              <th>命中</th>
              <th>评分</th>
            </template>
          </tr>
          <tr v-else>
            <th>代码</th>
            <th>名称</th>
            <th>行业</th>
            <th>收盘</th>
            <th>涨跌幅</th>
            <th v-if="mode === 'reversal' || mode === 'breakout'" class="stage-col">阶段</th>
            <template v-if="mode === 'reversal'">
              <th>总分</th>
              <th>确认项</th>
              <th>命中原因</th>
              <th>站上MA60</th>
              <th>MA60趋势</th>
              <th>RSI6&lt;75</th>
              <th>KDJ金叉</th>
              <th>MACD向上/零轴上</th>
              <th>量能&gt;MA5 1.5倍</th>
              <th>60日跌幅</th>
              <th>换手率</th>
            </template>
            <template v-else>
              <th>换手率</th>
              <th>量比</th>
              <th v-if="mode === 'breakout'">状态</th>
              <th v-if="mode === 'breakout'">共振</th>
              <th v-if="mode === 'breakout'">布林</th>
              <th v-if="mode === 'breakout'">周线</th>
              <th v-if="mode === 'breakout'">KDJ</th>
              <th v-if="mode === 'breakout'">MACD</th>
              <th v-if="mode === 'breakout'" class="cost-col">主力成本</th>
              <th>命中原因</th>
              <th>高点回撤</th>
              <th>缩量率</th>
              <th>MA20</th>
              <th>MA40</th>
              <th>守线</th>
              <th>评分</th>
            </template>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.ts_code + mode">
            <template v-if="compact">
              <td class="mono">{{ row.ts_code || '--' }}</td>
              <td>{{ row.name || '--' }}</td>
              <td :class="signedClass(row.pct_chg)">{{ formatNumber(row.pct_chg) }}%</td>
              <td v-if="mode === 'reversal' || mode === 'breakout'" class="stage-cell compact-stage">
                <strong v-if="row.trend_stage">{{ row.trend_stage }}</strong>
                <span v-else>--</span>
              </td>
              <template v-if="mode === 'reversal'">
                <td><strong>{{ formatNumber(row.score, 0) }}</strong></td>
                <td><strong>{{ formatNumber(row.reversal_indicator_count, 0) }}/6</strong></td>
                <td class="signal-cell">{{ row.strong_reason || '--' }}</td>
              </template>
              <template v-else-if="mode === 'breakout'">
                <td>{{ row.trade_state || row.breakout_status || '--' }}</td>
                <td><strong>{{ confluenceText(row) }}</strong></td>
                <td class="signal-cell">{{ breakoutSignalText(row) }}</td>
                <td>{{ formatNumber(row.score ?? row.dip_score) }}</td>
              </template>
              <template v-else>
                <td>{{ formatNumber(row.turnover_rate) }}%</td>
                <td class="signal-cell">{{ row.first_limit_reason || row.dip_reason || '--' }}</td>
                <td>{{ formatNumber(row.score ?? row.dip_score) }}</td>
              </template>
            </template>
            <template v-else>
              <td class="mono">{{ row.ts_code || '--' }}</td>
              <td>{{ row.name || '--' }}</td>
              <td>{{ row.industry || '--' }}</td>
              <td>{{ formatNumber(row.close) }}</td>
              <td :class="signedClass(row.pct_chg)">{{ formatNumber(row.pct_chg) }}%</td>
              <td v-if="mode === 'reversal' || mode === 'breakout'" class="stage-cell">
                <div v-if="row.trend_stage" class="stage-box">
                  <strong>{{ row.trend_stage }}</strong>
                  <span>{{ row.stage_label || '--' }}</span>
                  <em>{{ row.stage_action || '--' }}</em>
                </div>
                <span v-else>--</span>
              </td>
              <template v-if="mode === 'reversal'">
                <td><strong>{{ formatNumber(row.score, 0) }}</strong></td>
                <td><strong>{{ formatNumber(row.reversal_indicator_count, 0) }}/6</strong></td>
                <td>{{ row.strong_reason || '--' }}</td>
                <td>{{ row.ma60_above ? '是' : '否' }}</td>
                <td>{{ row.ma60_trend || '--' }}{{ row.ma60_decline_slowing ? '(放缓)' : '' }}</td>
                <td>{{ row.rsi6_below_75 ? '是' : '否' }}</td>
                <td>{{ row.kdj_golden_cross ? '是' : '否' }}</td>
                <td>{{ row.macd_trend_or_above_zero ? '是' : '否' }}</td>
                <td>{{ row.volume_above_ma5_1_5 ? '是' : '否' }}</td>
                <td :class="signedClass(row.ret60)">{{ formatNumber(row.ret60) }}%</td>
                <td>{{ formatNumber(row.turnover_rate) }}%</td>
              </template>
              <template v-else>
                <td>{{ formatNumber(row.turnover_rate) }}%</td>
                <td>{{ formatNumber(row.volume_ratio) }}</td>
                <td v-if="mode === 'breakout'">{{ row.trade_state || row.breakout_status || '--' }}</td>
                <td v-if="mode === 'breakout'"><strong>{{ confluenceText(row) }}</strong></td>
                <td v-if="mode === 'breakout'">{{ bollText(row) }}</td>
                <td v-if="mode === 'breakout'">{{ row.weekly_trend_state || '--' }}</td>
                <td v-if="mode === 'breakout'">{{ kdjText(row) }}</td>
                <td v-if="mode === 'breakout'">{{ macdText(row) }}</td>
                <td v-if="mode === 'breakout'" class="cost-cell">
                  <div v-if="row.main_cost_low !== undefined && row.main_cost_high !== undefined" class="cost-box">
                    <strong>{{ formatNumber(row.main_cost_low) }}~{{ formatNumber(row.main_cost_high) }}</strong>
                    <span>距离 {{ displayPercent(row.main_cost_distance_pct, '--') }} · {{ formatNumber(row.main_cost_score, 0) }}/10</span>
                  </div>
                  <span v-else>{{ row.main_cost_label || '--' }}</span>
                </td>
                <td>{{ row.breakout_reason || row.first_limit_reason || row.dip_reason || '--' }}</td>
                <td :class="signedClass(row.high_drawdown)">{{ displayPercent(row.high_drawdown) }}</td>
                <td>{{ displayMetric(row.volume_shrink_rate) }}</td>
                <td>{{ displayMetric(row.ma20) }}</td>
                <td>{{ displayMetric(row.ma40) }}</td>
                <td>{{ row.support_line || '--' }}</td>
                <td>{{ formatNumber(row.score ?? row.dip_score) }}</td>
              </template>
            </template>
          </tr>
          <tr v-if="rows.length === 0">
            <td :colspan="compact ? compactColspan() : (mode === 'reversal' ? 17 : (mode === 'breakout' ? 22 : 14))" class="empty">暂无数据</td>
          </tr>
        </tbody>
      </table>
    </div>
  `,
};

const SectorTable = {
  props: {
    rows: { type: Array, default: () => [] },
  },
  methods: {
    formatNumber,
    signedClass,
    strategyLabel(strategy) {
      const labels = {
        reversal: '超跌反转',
        breakout: '趋势突破',
        first_limit: '主升浪启动',
        strong: '趋势突破',
        dip: '超跌反转',
      };
      return labels[strategy] || strategy || '--';
    },
  },
  template: `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>板块</th>
            <th>平均涨跌幅</th>
            <th>股票数</th>
            <th>最低涨跌幅</th>
            <th>最高涨跌幅</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.industry_name">
            <td>{{ row.industry_name || '--' }}</td>
            <td :class="signedClass(row.avg_pct_chg)">{{ formatNumber(row.avg_pct_chg) }}%</td>
            <td>{{ row.stock_count || '--' }}</td>
            <td :class="signedClass(row.min_pct_chg)">{{ formatNumber(row.min_pct_chg) }}%</td>
            <td :class="signedClass(row.max_pct_chg)">{{ formatNumber(row.max_pct_chg) }}%</td>
          </tr>
          <tr v-if="rows.length === 0">
            <td colspan="5" class="empty">暂无数据</td>
          </tr>
        </tbody>
      </table>
    </div>
  `,
};

const SectorPotentialTable = {
  props: {
    rows: { type: Array, default: () => [] },
    compact: { type: Boolean, default: false },
  },
  data() {
    return {
      expandedRows: {},
    };
  },
  methods: {
    formatNumber,
    signedClass,
    leaderText(leader) {
      return sectorLeaderText(leader);
    },
    intradaySignalText(stock) {
      const labels = [];
      if (stock.macd_golden_cross_60m) labels.push(stock.macd_above_zero_60m ? '水上MACD金叉' : 'MACD金叉');
      else if (stock.macd_recent_golden_cross_60m) labels.push(stock.macd_above_zero_60m ? '水上MACD金叉延续' : 'MACD金叉延续');
      else if (stock.macd_bullish_60m) labels.push(stock.macd_above_zero_60m ? '水上MACD多头' : 'MACD多头');
      if (stock.kdj_golden_cross_60m) labels.push('KDJ金叉');
      else if (stock.kdj_recent_golden_cross_60m) labels.push('KDJ金叉延续');
      else if (stock.kdj_bullish_60m) labels.push('KDJ多头');
      return labels.length ? labels.join(' · ') : '--';
    },
    rowKey(row) {
      return row.industry_name || row.rank || '';
    },
    isExpanded(row) {
      return Boolean(this.expandedRows[this.rowKey(row)]);
    },
    toggleRow(row) {
      const key = this.rowKey(row);
      if (!key) return;
      this.expandedRows[key] = !this.expandedRows[key];
    },
    formatMoneyYi(value) {
      if (value === null || value === undefined || value === '') return '--';
      const number = Number(value);
      if (Number.isNaN(number) || number <= 0) return '--';
      return `${number.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}亿`;
    },
  },
  template: `
    <div class="table-wrap sector-potential-table" :class="{ 'compact-table': compact }">
      <table>
        <thead>
          <tr>
            <th>排名</th>
            <th>板块</th>
            <th>综合分</th>
            <th>短线</th>
            <th>波段</th>
            <th>信号</th>
            <th v-if="!compact">今日涨幅</th>
            <th v-if="!compact">上涨率</th>
            <th v-if="!compact">放量</th>
            <th v-if="!compact">RS20</th>
            <th v-if="!compact">RS60</th>
            <th>龙头股</th>
            <th v-if="!compact">理由</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="row in rows" :key="row.industry_name">
            <tr class="sector-expand-row" :class="{ expanded: isExpanded(row) }" @click="toggleRow(row)">
              <td>{{ row.rank || '--' }}</td>
              <td>
                <button class="expand-toggle" type="button" @click.stop="toggleRow(row)" :aria-expanded="isExpanded(row)">
                  {{ isExpanded(row) ? '-' : '+' }}
                </button>
                <strong>{{ row.industry_name || '--' }}</strong>
              </td>
              <td><strong>{{ formatNumber(row.potential_score) }}</strong></td>
              <td>{{ formatNumber(row.short_score) }}</td>
              <td>{{ formatNumber(row.swing_score) }}</td>
              <td><span class="signal-badge">{{ row.signal_type || '观察' }}</span></td>
              <td v-if="!compact" :class="signedClass(row.avg_pct_chg)">{{ formatNumber(row.avg_pct_chg) }}%</td>
              <td v-if="!compact">{{ formatNumber((row.up_ratio || 0) * 100, 0) }}%</td>
              <td v-if="!compact">{{ formatNumber(row.amount_expand_rate) }}倍</td>
              <td v-if="!compact" :class="signedClass(row.rs_20)">{{ formatNumber(row.rs_20) }}%</td>
              <td v-if="!compact" :class="signedClass(row.rs_60)">{{ formatNumber(row.rs_60) }}%</td>
              <td>
                <div class="leader-list">
                  <span v-for="leader in (row.leader_stocks || []).slice(0, compact ? 2 : 5)" :key="leader.ts_code">
                    {{ leaderText(leader) }}
                  </span>
                  <em v-if="!row.leader_stocks || row.leader_stocks.length === 0">暂无符合形态股票</em>
                </div>
              </td>
              <td v-if="!compact" class="reason-cell">{{ row.reason || '--' }}</td>
            </tr>
            <tr v-if="isExpanded(row)" class="sector-detail-row">
              <td :colspan="compact ? 7 : 13">
                <div class="sector-detail">
                  <div v-for="leader in (row.leader_stocks || [])" :key="leader.ts_code" class="sector-detail-stock">
                    <strong>{{ leader.name || '--' }}</strong>
                    <span>{{ leader.ts_code || '--' }}</span>
                    <b :class="signedClass(leader.pct_chg)">{{ formatNumber(leader.pct_chg) }}%</b>
                    <span>收盘 {{ formatNumber(leader.close) }}</span>
                    <span>换手 {{ formatNumber(leader.turnover_rate) }}%</span>
                    <span>量比 {{ formatNumber(leader.volume_ratio) }}</span>
                    <span>总市值 {{ formatMoneyYi(leader.total_mv_yuan ? Number(leader.total_mv_yuan) / 100000000 : null) }}</span>
                    <em>{{ leader.leader_reason || '--' }}</em>
                  </div>
                  <div v-if="!row.leader_stocks || row.leader_stocks.length === 0" class="empty-line">暂无满足总市值、多头向上、低位涨停、3日内回踩确认和箱体震荡的股票</div>
                  <div class="sector-detail-title">60分共振</div>
                  <div v-for="stock in (row.intraday_signal_stocks || [])" :key="'intraday-' + stock.ts_code" class="sector-detail-stock intraday-signal-stock">
                    <strong>{{ stock.name || '--' }}</strong>
                    <span>{{ stock.ts_code || '--' }}</span>
                    <b :class="signedClass(stock.pct_chg)">{{ formatNumber(stock.pct_chg) }}%</b>
                    <span>收盘 {{ formatNumber(stock.close) }}</span>
                    <span>换手 {{ formatNumber(stock.turnover_rate) }}%</span>
                    <span>量比 {{ formatNumber(stock.volume_ratio) }}</span>
                    <span>分数 {{ formatNumber(stock.intraday_signal_score) }}</span>
                    <span>{{ stock.next_day_bias || '数据不足' }} {{ stock.tail_strength_score ? formatNumber(stock.tail_strength_score) : '--' }}</span>
                    <em>{{ intradaySignalText(stock) }} · {{ stock.next_day_bias_reason || stock.trade_time_60m || '--' }}</em>
                  </div>
                  <div v-if="!row.intraday_signal_stocks || row.intraday_signal_stocks.length === 0" class="empty-line">暂无满足换手2%-10%、量比大于2和60分MACD金叉的股票</div>
                </div>
              </td>
            </tr>
          </template>
          <tr v-if="rows.length === 0">
            <td :colspan="compact ? 7 : 13" class="empty">暂无板块潜力数据</td>
          </tr>
        </tbody>
      </table>
    </div>
  `,
};

const BacktestTable = {
  props: {
    rows: { type: Array, default: () => [] },
  },
  methods: { formatNumber, signedClass },
  template: `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>策略</th>
            <th>选股日</th>
            <th>卖出日</th>
            <th>代码</th>
            <th>名称</th>
            <th>行业</th>
            <th>买入收盘</th>
            <th>卖出收盘</th>
            <th>收益</th>
            <th>评分</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, index) in rows" :key="row.strategy + row.trade_date + row.ts_code + index">
            <td>{{ strategyLabel(row.strategy) }}</td>
            <td>{{ row.trade_date || '--' }}</td>
            <td>{{ row.exit_date || '--' }}</td>
            <td class="mono">{{ row.ts_code || '--' }}</td>
            <td>{{ row.name || '--' }}</td>
            <td>{{ row.industry || '--' }}</td>
            <td>{{ formatNumber(row.entry_close) }}</td>
            <td>{{ formatNumber(row.exit_close) }}</td>
            <td :class="signedClass(row.return_pct)">{{ formatNumber(row.return_pct) }}%</td>
            <td>{{ formatNumber(row.score) }}</td>
          </tr>
          <tr v-if="rows.length === 0">
            <td colspan="10" class="empty">暂无回测结果</td>
          </tr>
        </tbody>
      </table>
    </div>
  `,
};

createApp({
  components: {
    StockTable,
    SectorTable,
    SectorPotentialTable,
    BacktestTable,
  },
  data() {
    return {
      apiBase: initialApiBase(),
      activeTab: 'free_review',
      includeAi: false,
      limit: 20,
      latest: {},
      reports: [],
      cacheStatus: {},
      cacheLoading: false,
      cacheSyncing: false,
      cacheSyncProgressTimer: null,
      loading: false,
      backtestLoading: false,
      backtestLookbackDays: 30,
      backtestHoldDays: 3,
      backtestLimit: 20,
      backtest: null,
      evaluationLoading: false,
      evaluationHoldDays: 3,
      evaluationReportLimit: 50,
      evaluationStockLimit: 20,
      aiEvaluation: null,
      tradeReviewLoading: false,
      tradeReview: null,
      intradayLoading: false,
      intradayRefreshMode: null,
      intradayMonitor: {},
      intradayAutoRefresh: false,
      intradayTimer: null,
      overnightLoading: false,
      overnightMonitor: {},
      overnightAutoRefresh: false,
      overnightTimer: null,
      realtimeInfoLoading: false,
      realtimeInfoRefreshMode: null,
      realtimeInfo: {},
      realtimeInfoAutoRefresh: false,
      realtimeInfoTimer: null,
      realtimePositionDebug: false,
      realtimeTailPremiumDebug: false,
      realtimeTailPremiumLoading: false,
      realtimeTailPremiumRefreshMode: null,
      cycleWatch: {
        stocks: [],
        confirmed_stocks: [],
        low_buy_stocks: [],
        watch_stocks: [],
        delayed_stocks: [],
        unread_alert_count: 0,
      },
      cycleWatchForm: {
        tsCode: '', note: '', plannedLowPrice: '', plannedHighPrice: '',
      },
      cycleWatchLoading: false,
      cycleWatchError: '',
      cycleWatchHistory: {},
      cycleWatchExpandedCode: null,
      marketNewsLoading: false,
      marketNewsSummary: {},
      trendBoxLoading: false,
      trendBoxResult: null,
      trendBoxForm: {
        tsCode: '',
        endTradeDate: '',
        lookbackDays: 120,
        autoDetect: true,
        boxStart: '',
        boxEnd: '',
      },
      sectorPotentialRefreshing: false,
      sectorPotentialTimer: null,
      sectorRotation: {
        trade_date: '',
        lookback_trade_dates: [],
        moneyflow_trade_dates: [],
        warnings: [],
        continuation_inflow: [],
        rotation_rebound: [],
      },
      sectorRotationLoading: false,
      freeReviewMeta: {},
      freeReviewBuild: {},
      freeReviewSectors: [],
      freeReviewResult: {
        items: [],
        total: 0,
        page: 1,
        page_size: 50,
        pages: 0,
      },
      freeReviewLoading: false,
      freeReviewBuildTimer: null,
      freeReviewPage: 1,
      freeReviewPageSize: 50,
      freeReviewSort: { by: 'total_score', direction: 'desc' },
      freeReviewFilters: {
        keyword: '',
        industries: [],
        areas: [],
        markets: [],
        profit_state: '',
        volume_state: '',
        growth_state: '',
        ranges: Object.fromEntries(
          FREE_REVIEW_METRIC_GROUPS
            .flatMap((group) => group.metrics)
            .filter((metric) => metric.filterable)
            .map((metric) => [metric.key, { min: '', max: '' }]),
        ),
        visible_columns: FREE_REVIEW_METRIC_GROUPS
          .flatMap((group) => group.metrics)
          .filter((metric) => metric.defaultVisible)
          .map((metric) => metric.key),
      },
      freeReviewPresets: loadFreeReviewPresets(localStorage),
      freeReviewPresetName: '',
      macdSettings: {
        fast_period: 5,
        slow_period: 34,
        signal_period: 5,
      },
      macdSettingsForm: {
        fast_period: 5,
        slow_period: 34,
        signal_period: 5,
      },
      macdSettingsSaving: false,
      reviewForm: {
        tsCode: '',
        buyDate: '',
        buyPrice: '',
        positionStatus: 'holding',
        sellDate: '',
        sellPrice: '',
        lossStatus: '',
        holdingNote: '',
      },
      error: '',
      healthOk: false,
      healthText: '未连接',
    };
  },
  computed: {
    pageTitle() {
      const titles = {
        overview: '选股总览',
        free_review: '自由复盘选股',
        strong: '趋势突破',
        dip: '超跌反转',
        first_limit: '主升浪启动',
        sectors: '板块机会',
        sector_potential: '板块潜力',
        sector_rotation: '明日轮动',
        intraday_monitor: '实时共振监控',
        overnight_monitor: '次日早盘跟进',
        realtime_info: '实时信息',
        cycle_watch: '周期关注',
        market_news: '消息面',
        trend_box_target: '箱体目标',
        reports: '历史报告',
        backtest: '策略回测',
        evaluation: 'AI 推荐评估',
        review: '交易复盘',
        cache: '数据缓存',
      };
      return titles[this.activeTab] || '选股总览';
    },
    backtestRows() {
      return this.backtest && this.backtest.results ? this.backtest.results : [];
    },
    cycleWatchGroupsView() {
      return cycleWatchGroups(this.cycleWatch.stocks || []);
    },
    cycleWatchConfirmedRows() {
      return this.cycleWatch.confirmed_stocks || this.cycleWatchGroupsView.confirmed;
    },
    cycleWatchLowBuyRows() {
      return this.cycleWatch.low_buy_stocks || this.cycleWatchGroupsView.lowBuy;
    },
    cycleWatchWatchingRows() {
      return this.cycleWatch.watch_stocks || this.cycleWatchGroupsView.watch;
    },
    cycleWatchDelayedRows() {
      return this.cycleWatch.delayed_stocks || this.cycleWatchGroupsView.delayed;
    },
    cycleWatchUnreadCount() {
      return Number(this.cycleWatch.unread_alert_count) || cycleWatchAlertCount(this.cycleWatch.stocks);
    },
    cycleWatchPanels() {
      return [
        { key: 'confirmed', title: '确认后介入', hint: '日线结构与60分钟确认共振', rows: this.cycleWatchConfirmedRows },
        { key: 'low-buy', title: '低吸提示', hint: '接近支撑，等待缩量企稳', rows: this.cycleWatchLowBuyRows },
        { key: 'watch', title: '继续观察', hint: '条件尚未齐备，暂不追高', rows: this.cycleWatchWatchingRows },
      ];
    },
    freeReviewBuildView() {
      return freeReviewBuildState(this.freeReviewBuild);
    },
    freeReviewMetricGroups() {
      return FREE_REVIEW_METRIC_GROUPS;
    },
    freeReviewVisibleMetrics() {
      const visible = new Set(this.freeReviewFilters.visible_columns || []);
      return FREE_REVIEW_METRIC_GROUPS
        .flatMap((group) => group.metrics)
        .filter((metric) => visible.has(metric.key));
    },
    freeReviewIndustryOptions() {
      return this.freeReviewSectors
        .map((row) => row.industry)
        .filter(Boolean);
    },
    sectorPotentialRows() {
      return Array.isArray(this.latest.sector_potential) ? this.latest.sector_potential : [];
    },
    intradayMonitorRows() {
      return Array.isArray(this.intradayMonitor.stocks) ? this.intradayMonitor.stocks : [];
    },
    intradayDataTimeText() {
      return screeningDataTimeText(this.intradayMonitor);
    },
    intradayCacheState() {
      return realtimeCacheState(this.intradayMonitor);
    },
    overnightMonitorRows() {
      return Array.isArray(this.overnightMonitor.stocks) ? this.overnightMonitor.stocks : [];
    },
    morningFollowDataState() {
      return morningFollowDataStatus(this.overnightMonitor);
    },
    realtimeIntradayRows() {
      return this.realtimeInfo && this.realtimeInfo.intraday && Array.isArray(this.realtimeInfo.intraday.stocks)
        ? this.realtimeInfo.intraday.stocks
        : [];
    },
    realtimePositionCandidateRows() {
      const intraday = this.realtimeInfo && this.realtimeInfo.intraday;
      return intraday && Array.isArray(intraday.position_candidates)
        ? intraday.position_candidates
        : [];
    },
    positionFilterDebugPayload() {
      const intraday = this.realtimeInfo && this.realtimeInfo.intraday;
      return intraday && intraday.position_filter_debug
        ? intraday.position_filter_debug
        : {};
    },
    positionFilterDebugRows() {
      return Array.isArray(this.positionFilterDebugPayload.top_reasons)
        ? this.positionFilterDebugPayload.top_reasons
        : [];
    },
    positionFilterDebugSamples() {
      return Array.isArray(this.positionFilterDebugPayload.samples)
        ? this.positionFilterDebugPayload.samples.slice(0, 20)
        : [];
    },
    positionDebugVisible() {
      return this.realtimePositionDebug || (
        (this.positionFilterDebugPayload.auto_expand === true || this.realtimePositionCandidateRows.length === 0)
        && this.positionFilterDebugPayload.source_count != null
      );
    },
    realtimeOvernightRows() {
      return this.realtimeInfo && this.realtimeInfo.overnight && Array.isArray(this.realtimeInfo.overnight.stocks)
        ? this.realtimeInfo.overnight.stocks
        : [];
    },
    realtimeInfoDataTimeText() {
      return screeningDataTimeText(this.realtimeInfo);
    },
    realtimeInfoStatus() {
      return realtimeDataStatus(this.realtimeInfo);
    },
    realtimeInfoCacheState() {
      return realtimeCacheState(this.realtimeInfo);
    },
    tailPremiumDebugPayload() {
      const overnight = this.realtimeInfo && this.realtimeInfo.overnight;
      return overnight && overnight.filter_debug ? overnight.filter_debug : {};
    },
    tailPremiumDebugStages() {
      return Array.isArray(this.tailPremiumDebugPayload.stages)
        ? this.tailPremiumDebugPayload.stages
        : [];
    },
    tailPremiumDebugRows() {
      return Array.isArray(this.tailPremiumDebugPayload.top_reasons)
        ? this.tailPremiumDebugPayload.top_reasons
        : [];
    },
    tailPremiumDebugSamples() {
      return Array.isArray(this.tailPremiumDebugPayload.samples)
        ? this.tailPremiumDebugPayload.samples.slice(0, 8)
        : [];
    },
    trendBoxBacktestSegments() {
      return this.trendBoxResult
        && this.trendBoxResult.wave_backtest
        && Array.isArray(this.trendBoxResult.wave_backtest.segments)
        ? this.trendBoxResult.wave_backtest.segments
        : [];
    },
    topShortSector() {
      const rows = [...this.sectorPotentialRows].sort((a, b) => Number(b.short_score || 0) - Number(a.short_score || 0));
      return rows.length ? rows[0].industry_name : '--';
    },
    topSwingSector() {
      const rows = [...this.sectorPotentialRows].sort((a, b) => Number(b.swing_score || 0) - Number(a.swing_score || 0));
      return rows.length ? rows[0].industry_name : '--';
    },
    cacheRows() {
      const rows = Array.isArray(this.cacheStatus.sources) ? this.cacheStatus.sources : [];
      return latestCacheRows(rows);
    },
    cacheLatestDate() {
      if (this.cacheStatus.latest_complete_date) return this.cacheStatus.latest_complete_date;
      const dates = this.cacheRows.map((row) => row.trade_date).filter(Boolean).sort();
      return dates.length ? dates[dates.length - 1] : '--';
    },
    cacheLatestTime() {
      const times = this.cacheRows.map((row) => row.updated_at || row.completed_at).filter(Boolean).sort();
      return times.length ? times[times.length - 1] : '--';
    },
    cacheOverallStatus() {
      if (!this.cacheRows.some((row) => row.status)) return '未初始化';
      if (this.cacheRows.some((row) => row.status === 'failed')) return '存在失败';
      if (this.cacheRows.some((row) => row.status === 'running')) return '同步中';
      return '正常';
    },
    cacheProgressTarget() {
      return Number(this.cacheStatus.target_days || this.cacheStatus.bootstrap_days || 120);
    },
    cacheProgressCurrent() {
      return Number(this.cacheStatus.complete_dates || 0);
    },
    cacheProgressPercent() {
      const target = this.cacheProgressTarget;
      if (!target) return 0;
      return Math.min(100, Math.round((this.cacheProgressCurrent / target) * 100));
    },
    cacheProgressText() {
      return cacheProgressText(this.cacheStatus);
    },
  },
  mounted() {
    this.refreshAll();
  },
  beforeUnmount() {
    this.stopIntradayMonitor();
    this.stopOvernightMonitor();
    this.stopRealtimeInfoMonitor();
    this.stopSectorPotentialPolling();
    this.stopFreeReviewBuildPolling();
  },
  watch: {
    activeTab(tab) {
      if (tab === 'sector_potential') this.startSectorPotentialPolling();
      else this.stopSectorPotentialPolling();
      if (tab !== 'realtime_info') this.stopRealtimeInfoMonitor();
      if (tab === 'free_review') this.loadFreeReview(false);
      if (tab === 'sector_rotation') this.loadSectorRotation(false);
    },
  },
  methods: {
    formatNumber,
    signedClass,
    displayMoney,
    macdBasisText(payload = {}) {
      const source = (
        payload && (
          payload.macd_fast_period != null
          || payload.fast_period != null
        )
      ) ? payload : this.macdSettings;
      return macdParameterLabel(source);
    },
    freeReviewMetricValue(row, metric) {
      const value = row ? row[metric.key] : null;
      if (value === null || value === undefined || value === '') return '--';
      if (metric.format === 'flag') return Number(value) === 1 ? '是' : '否';
      if (metric.format === 'text' || metric.format === 'date') return String(value);
      if (metric.format === 'percent') return `${formatNumber(value)}%`;
      if (metric.format === 'money') return displayMoney(value);
      if (metric.format === 'marketValue') {
        return `${formatNumber(Number(value) / 10000, 2)}亿`;
      }
      if (metric.format === 'ratio') return `${formatNumber(value)}倍`;
      return formatNumber(value);
    },
    isFreeReviewColumnVisible(key) {
      return (this.freeReviewFilters.visible_columns || []).includes(key);
    },
    toggleFreeReviewColumn(key) {
      const columns = this.freeReviewFilters.visible_columns || [];
      this.freeReviewFilters.visible_columns = columns.includes(key)
        ? columns.filter((item) => item !== key)
        : [...columns, key];
    },
    topRows(rows, limit) {
      return (rows || []).slice(0, limit);
    },
    listCount(rows) {
      return rows ? rows.length : 0;
    },
    rowsFor(pool, legacyKey) {
      if (this.latest && this.latest.pools && Array.isArray(this.latest.pools[pool])) {
        return this.latest.pools[pool];
      }
      return (this.latest && this.latest[legacyKey]) || [];
    },
    moneyflowTop(key) {
      const summary = this.latest && this.latest.moneyflow_summary;
      return summary && Array.isArray(summary[key]) ? summary[key] : [];
    },
    sectorRotationTopScore(key, scoreKey) {
      const rows = this.sectorRotation && Array.isArray(this.sectorRotation[key])
        ? this.sectorRotation[key]
        : [];
      if (!rows.length) return '--';
      return formatNumber(rows[0][scoreKey], 1);
    },
    formatSectorRotationMoney(value) {
      return formatRotationMoney(value);
    },
    formatSectorRotationPercent(value, digits) {
      return formatRotationPercent(value, digits);
    },
    formatSectorRotationStockAmount(stock) {
      if (!stock || stock.amount === null || stock.amount === undefined || stock.amount === '') return '--';
      const amount = Number(stock.amount);
      if (Number.isNaN(amount)) return '--';
      const amountYuan = Math.abs(amount) >= 10000000 ? amount : amount * 1000;
      return displayMoney(amountYuan);
    },
    rotationStockText(stock, scoreKey) {
      return rotationStockText(stock, scoreKey);
    },
    monitorSignalText(row) {
      const labels = [];
      if (row.macd_golden_cross_60m) labels.push(row.macd_above_zero_60m ? '水上MACD金叉' : 'MACD金叉');
      else if (row.macd_recent_golden_cross_60m) labels.push(row.macd_above_zero_60m ? '水上MACD延续' : 'MACD延续');
      else if (row.macd_bullish_60m) labels.push(row.macd_above_zero_60m ? '水上MACD多头' : 'MACD多头');
      if (row.kdj_golden_cross_60m) labels.push('KDJ金叉');
      else if (row.kdj_recent_golden_cross_60m) labels.push('KDJ延续');
      else if (row.kdj_bullish_60m) labels.push('KDJ多头');
      return labels.length ? labels.join(' · ') : '--';
    },
    isAfterClock(clock) {
      const [hour, minute] = String(clock).split(':').map(Number);
      const now = new Date();
      return now.getHours() * 60 + now.getMinutes() >= hour * 60 + minute;
    },
    tailReturnText(row) {
      if (row.tail_after_1430_available === true) return `${formatNumber(row.tail_return_after_1430)}%`;
      return this.isAfterClock('14:30') ? '无数据' : '未到';
    },
    tailVolumeText(row) {
      return tailVolumeDisplay(row, this.isAfterClock('14:30')).text;
    },
    tailVolumeBadgeClass(row) {
      return tailVolumeDisplay(row, this.isAfterClock('14:30')).state;
    },
    auctionReturnText(row) {
      if (row.tail_auction_available === true) return `${formatNumber(row.tail_auction_return)}%`;
      return this.isAfterClock('09:30') ? '无数据' : '未到';
    },
    tailPremiumSelectionText() {
      return tailPremiumSelectionState(this.realtimeInfo.overnight || {});
    },
    premiumRiskBadgeClass(row) {
      return premiumRiskState(row);
    },
    tailPremiumDetailText(value) {
      return detailListText(value);
    },
    marketRelativeText(row) {
      return marketRelativeText(row);
    },
    historicalResilienceText(row) {
      return historicalResilienceText(row);
    },
    chipPeakDisplay(row) {
      return globalThis.chipPeakDisplay(row);
    },
    monitorBadgeClass(value) {
      if (value === '主力抢筹' || value === '高开偏强') return 'strong';
      if (value === '低开风险' || value === '放量分歧') return 'risk';
      if (value === '冲高分歧' || value === '平开观察') return 'watch';
      return 'muted';
    },
    positionLevelBadgeClass(value) {
      if (value === 'A+') return 'strong';
      if (value === 'A' || value === 'B+') return 'watch';
      if (value === 'X') return 'risk';
      return 'muted';
    },
    falseBreakoutBadgeClass(value) {
      if (value === 'LOW') return 'strong';
      if (value === 'MEDIUM') return 'watch';
      if (value === 'HIGH') return 'risk';
      return 'muted';
    },
    monitorRowClass(row) {
      return {
        'monitor-strong': row.main_force_status === '主力抢筹' || row.next_day_bias === '高开偏强',
        'monitor-risk': row.main_force_status === '放量分歧' || row.next_day_bias === '低开风险',
      };
    },
    overnightBadgeClass(value) {
      if (value === '尾盘可买' || value === '隔夜高开优先') return 'strong';
      if (value === '不买' || value === '低开风险') return 'risk';
      if (value === '早盘冲高套利' || value === '轻仓观察' || value === '尾盘抢筹观察' || value === '盘中观察' || value === '尾盘观察') return 'watch';
      return 'muted';
    },
    morningFollowBadgeClass(value) {
      return morningFollowBadgeState(value);
    },
    morningFollowRemark(row) {
      return morningFollowRemarkText(row);
    },
    morningFollowRowClass(row) {
      return {
        'monitor-strong': row.follow_status === '可以跟进',
        'monitor-risk': row.follow_status === '放弃' || row.follow_status === '数据未就绪',
      };
    },
    sectorMacdBadgeClass(row) {
      if (row.sector_macd_water_golden_cross || row.sector_macd_status === '板块MACD水上走强') return 'strong';
      if (row.sector_macd_golden_cross || row.sector_macd_trending_up) return 'watch';
      return 'muted';
    },
    overnightRowClass(row) {
      return {
        'monitor-strong': row.buyable_tail_signal === '尾盘可买' || row.overnight_bias === '隔夜高开优先',
        'monitor-risk': row.buyable_tail_signal === '不买' || row.overnight_bias === '低开风险',
      };
    },
    isMarketAutoRefreshTime() {
      const now = new Date();
      const minutes = now.getHours() * 60 + now.getMinutes();
      return minutes >= 9 * 60 + 30 && minutes < 15 * 60;
    },
    strategyLabel(strategy) {
      const labels = {
        reversal: '超跌反转',
        breakout: '趋势突破',
        first_limit: '主升浪启动',
        strong: '趋势突破',
        dip: '超跌反转',
      };
      return labels[strategy] || strategy || '--';
    },
    trendBoxParam(key) {
      if (!this.trendBoxResult || !this.trendBoxResult.params) return '--';
      const value = this.trendBoxResult.params[key];
      if (Array.isArray(value)) return value.join('/');
      return value === null || value === undefined ? '--' : value;
    },
    segmentLabel(segment) {
      if (segment === 'base') return '底部箱体';
      if (String(segment || '').startsWith('relay')) {
        return `中继箱体${String(segment).replace('relay', '')}`;
      }
      return segment || '--';
    },
    resultClass(result) {
      if (result === '命中') return 'hit';
      if (result === '超出') return 'over';
      if (result === '未到') return 'under';
      return '';
    },
    saveApiBase() {
      localStorage.setItem('quant_api_base', this.apiBase || '/api/quant');
      this.refreshAll();
    },
    buildUrl(path) {
      const base = (this.apiBase || '/api/quant').replace(/\/$/, '');
      return `${base}${path}`;
    },
    async request(path, options = {}) {
      const response = await fetch(this.buildUrl(path), {
        headers: { 'Content-Type': 'application/json' },
        ...options,
      });
      const text = await response.text();
      let data = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        data = text;
      }
      if (!response.ok) {
        const detail = typeof data === 'object' ? (data.detail || data.error || JSON.stringify(data)) : data;
        throw new Error(detail || `HTTP ${response.status}`);
      }
      return data;
    },
    async checkHealth() {
      try {
        await this.request('/health');
        this.healthOk = true;
        this.healthText = '已连接';
      } catch (error) {
        this.healthOk = false;
        this.healthText = '连接失败';
      }
    },
    async refreshAll() {
      this.loading = true;
      this.error = '';
      try {
        await this.checkHealth();
        await this.loadMacdSettings(false);
        await this.loadCacheStatus(false);
        if (this.activeTab === 'free_review') {
          await this.loadFreeReview(false);
          return;
        }
        if (this.activeTab === 'sector_rotation') {
          await this.loadSectorRotation(false);
          return;
        }
        const [latest, reports] = await Promise.all([
          this.request('/reports/latest'),
          this.request(`/reports?limit=${this.limit}`),
        ]);
        this.latest = latest || {};
        this.reports = reports || [];
        if (this.activeTab === 'intraday_monitor') await this.loadIntradayMonitor(false, false);
        if (this.activeTab === 'overnight_monitor') await this.loadOvernightMonitor(false);
        if (this.activeTab === 'realtime_info') await this.loadRealtimeInfo(false);
        if (this.activeTab === 'cycle_watch') await this.loadCycleWatchlist(false);
        if (this.activeTab === 'market_news') await this.loadMarketNewsSummary(false);
      } catch (error) {
        this.error = error.message;
      } finally {
        this.loading = false;
      }
    },
    async loadCycleWatchlist(showLoading = true) {
      if (this.cycleWatchLoading) return;
      if (showLoading) this.cycleWatchLoading = true;
      this.cycleWatchError = '';
      try {
        this.cycleWatch = await this.request('/cycle-watchlist') || { stocks: [] };
      } catch (error) {
        this.cycleWatchError = error.message || '周期关注加载失败';
      } finally {
        this.cycleWatchLoading = false;
      }
    },
    async addCycleWatch() {
      this.cycleWatchLoading = true;
      this.cycleWatchError = '';
      try {
        const form = this.cycleWatchForm;
        const tsCode = normalizeCycleWatchInput(form.tsCode);
        const payload = {
          ts_code: tsCode,
          note: form.note.trim() || null,
          planned_low_price: form.plannedLowPrice === '' ? null : Number(form.plannedLowPrice),
          planned_high_price: form.plannedHighPrice === '' ? null : Number(form.plannedHighPrice),
        };
        if (payload.planned_low_price && payload.planned_high_price
            && payload.planned_low_price > payload.planned_high_price) {
          throw new Error('计划低价不能高于计划高价');
        }
        await addAndCheckCycleWatch(this.request.bind(this), payload);
        this.cycleWatchForm = { tsCode: '', note: '', plannedLowPrice: '', plannedHighPrice: '' };
        this.cycleWatchLoading = false;
        await this.loadCycleWatchlist();
      } catch (error) {
        this.cycleWatchError = error.message || '添加失败';
      } finally {
        this.cycleWatchLoading = false;
      }
    },
    async updateCycleWatch(tsCode, changes) {
      this.cycleWatchError = '';
      try {
        await this.request(`/cycle-watchlist/${encodeURIComponent(tsCode)}`, {
          method: 'PATCH', body: JSON.stringify(changes),
        });
        await this.loadCycleWatchlist();
      } catch (error) {
        this.cycleWatchError = error.message || '更新失败';
      }
    },
    async deleteCycleWatch(tsCode) {
      if (!window.confirm(`确认移除 ${tsCode} 的周期关注？历史评估记录将保留。`)) return;
      this.cycleWatchError = '';
      try {
        await this.request(`/cycle-watchlist/${encodeURIComponent(tsCode)}`, { method: 'DELETE' });
        if (this.cycleWatchExpandedCode === tsCode) this.cycleWatchExpandedCode = null;
        await this.loadCycleWatchlist();
      } catch (error) {
        this.cycleWatchError = error.message || '移除失败';
      }
    },
    async checkCycleWatch(tsCode = null) {
      this.cycleWatchLoading = true;
      this.cycleWatchError = '';
      try {
        const payload = {};
        if (tsCode) payload.ts_code = tsCode;
        this.cycleWatch = await this.request('/cycle-watchlist/check', {
          method: 'POST', body: JSON.stringify(payload),
        }) || { stocks: [] };
      } catch (error) {
        this.cycleWatchError = error.message || '检查入场时机失败';
      } finally {
        this.cycleWatchLoading = false;
      }
    },
    async loadCycleWatchHistory(tsCode) {
      if (this.cycleWatchExpandedCode === tsCode) {
        this.cycleWatchExpandedCode = null;
        return;
      }
      this.cycleWatchError = '';
      try {
        const response = await this.request(`/cycle-watchlist/${encodeURIComponent(tsCode)}/history?limit=30`);
        this.cycleWatchHistory[tsCode] = Array.isArray(response) ? response : (response.history || []);
        this.cycleWatchExpandedCode = tsCode;
      } catch (error) {
        this.cycleWatchError = error.message || '历史记录加载失败';
      }
    },
    async markCycleWatchAlertsRead() {
      try {
        await this.request('/cycle-watchlist/alerts/read', { method: 'POST', body: '{}' });
        await this.loadCycleWatchlist();
      } catch (error) {
        this.cycleWatchError = error.message || '提醒状态更新失败';
      }
    },
    async loadSectorRotation(force = false) {
      if (this.sectorRotationLoading && !force) return;
      this.sectorRotationLoading = true;
      this.error = '';
      try {
        this.sectorRotation = await this.request(
          `/sector-rotation/tomorrow?limit=${this.limit || 10}&stocks_per_sector=5`,
        );
      } catch (error) {
        this.error = error.message || '明日轮动加载失败';
      } finally {
        this.sectorRotationLoading = false;
      }
    },
    freeReviewQueryPayload() {
      return normalizeFreeReviewQuery(
        {
          ...this.freeReviewFilters,
          trade_date: this.freeReviewMeta.trade_date || undefined,
        },
        this.freeReviewPage,
        this.freeReviewPageSize,
        this.freeReviewSort,
      );
    },
    async loadMacdSettings(showError = true) {
      try {
        const settings = (
          await this.request('/indicator-settings/macd')
        ) || {};
        this.macdSettings = settings;
        this.macdSettingsForm = {
          fast_period: Number(settings.fast_period ?? 5),
          slow_period: Number(settings.slow_period ?? 34),
          signal_period: Number(settings.signal_period ?? 5),
        };
      } catch (error) {
        if (showError) this.error = error.message;
      }
    },
    async saveMacdSettingsAndRecalculate() {
      const validation = validateMacdSettings(this.macdSettingsForm);
      if (!validation.valid) {
        this.error = validation.message;
        return;
      }
      this.macdSettingsSaving = true;
      this.error = '';
      try {
        const result = await this.request('/indicator-settings/macd', {
          method: 'PUT',
          body: JSON.stringify(this.macdSettingsForm),
        });
        this.macdSettings = result.settings || this.macdSettings;
        this.macdSettingsForm = {
          fast_period: Number(this.macdSettings.fast_period),
          slow_period: Number(this.macdSettings.slow_period),
          signal_period: Number(this.macdSettings.signal_period),
        };
        this.freeReviewBuild = result.free_review_build || {};
        this.freeReviewMeta = {
          ...this.freeReviewMeta,
          ready: false,
          score_version: this.freeReviewBuild.score_version,
          ...this.macdSettings,
        };
        this.freeReviewResult = {
          items: [], total: 0, page: 1,
          page_size: this.freeReviewPageSize, pages: 0,
        };
        this.freeReviewSectors = [];
        this.intradayMonitor = {};
        this.overnightMonitor = {};
        this.realtimeInfo = {};
        if (['pending', 'running'].includes(this.freeReviewBuild.status)) {
          this.startFreeReviewBuildPolling();
        } else if (this.freeReviewBuild.status === 'queue_failed') {
          this.error = this.freeReviewBuild.error_message || '参数已保存，但复盘重建启动失败';
        }
      } catch (error) {
        this.error = error.message;
      } finally {
        this.macdSettingsSaving = false;
      }
    },
    async loadFreeReview(showError = true, resetPage = false) {
      if (this.freeReviewLoading) return;
      if (resetPage) this.freeReviewPage = 1;
      this.freeReviewLoading = true;
      try {
        const meta = await this.request('/free-review/meta');
        this.freeReviewMeta = meta || {};
        if (meta.ready === false) {
          this.freeReviewResult = {
            items: [],
            total: 0,
            page: 1,
            page_size: this.freeReviewPageSize,
            pages: 0,
          };
          this.freeReviewSectors = [];
          try {
            this.freeReviewBuild = (
              await this.request('/free-review/build-status')
            ) || {};
          } catch {
            this.freeReviewBuild = {};
          }
          return;
        }
        const payload = this.freeReviewQueryPayload();
        const [result, sectors] = await Promise.all([
          this.request('/free-review/query', {
            method: 'POST',
            body: JSON.stringify(payload),
          }),
          this.request(`/free-review/sectors?trade_date=${meta.trade_date}`),
        ]);
        this.freeReviewResult = result || this.freeReviewResult;
        this.freeReviewSectors = sectors && Array.isArray(sectors.items)
          ? sectors.items : [];
        this.freeReviewPage = Number(this.freeReviewResult.page || 1);
        this.freeReviewBuild = {
          status: 'success',
          stage: 'complete',
          total_count: meta.stock_count,
          financial_coverage: meta.financial_coverage,
        };
      } catch (error) {
        try {
          this.freeReviewBuild = (
            await this.request('/free-review/build-status')
          ) || {};
        } catch {
          this.freeReviewBuild = {};
        }
        if (showError) this.error = error.message;
      } finally {
        this.freeReviewLoading = false;
      }
    },
    async startFreeReviewBuild(force = false) {
      this.freeReviewLoading = true;
      this.error = '';
      try {
        this.freeReviewBuild = await this.request(
          `/free-review/build?force=${force}`,
          { method: 'POST' },
        );
        if (['pending', 'running'].includes(this.freeReviewBuild.status)) {
          this.startFreeReviewBuildPolling();
        } else if (this.freeReviewBuild.status === 'success') {
          this.freeReviewLoading = false;
          await this.loadFreeReview(false);
        }
      } catch (error) {
        this.error = error.message;
      } finally {
        this.freeReviewLoading = false;
      }
    },
    async pollFreeReviewBuild() {
      try {
        this.freeReviewBuild = (
          await this.request('/free-review/build-status')
        ) || {};
        if (this.freeReviewBuild.status === 'success') {
          this.stopFreeReviewBuildPolling();
          await this.loadFreeReview(false);
        } else if (this.freeReviewBuild.status === 'failed') {
          this.stopFreeReviewBuildPolling();
          this.error = this.freeReviewBuild.error_message || '自由复盘快照构建失败';
        }
      } catch (error) {
        this.stopFreeReviewBuildPolling();
        this.error = error.message;
      }
    },
    startFreeReviewBuildPolling() {
      this.stopFreeReviewBuildPolling();
      this.freeReviewBuildTimer = setInterval(
        () => this.pollFreeReviewBuild(),
        2000,
      );
    },
    stopFreeReviewBuildPolling() {
      if (this.freeReviewBuildTimer) {
        clearInterval(this.freeReviewBuildTimer);
        this.freeReviewBuildTimer = null;
      }
    },
    applyFreeReviewFilters() {
      this.loadFreeReview(true, true);
    },
    resetFreeReviewFilters() {
      this.freeReviewFilters = {
        keyword: '',
        industries: [],
        areas: [],
        markets: [],
        profit_state: '',
        volume_state: '',
        growth_state: '',
        ranges: Object.fromEntries(
          FREE_REVIEW_METRIC_GROUPS
            .flatMap((group) => group.metrics)
            .filter((metric) => metric.filterable)
            .map((metric) => [metric.key, { min: '', max: '' }]),
        ),
        visible_columns: FREE_REVIEW_METRIC_GROUPS
          .flatMap((group) => group.metrics)
          .filter((metric) => metric.defaultVisible)
          .map((metric) => metric.key),
      };
      this.freeReviewSort = { by: 'total_score', direction: 'desc' };
      this.loadFreeReview(true, true);
    },
    applyFreeReviewFinancialEventPreset() {
      this.freeReviewFilters.ranges = {
        ...this.freeReviewFilters.ranges,
        financial_event_hit: { min: 1, max: '' },
        deducted_netprofit: { min: 50000000, max: '' },
        deducted_netprofit_growth: { min: 50, max: '' },
      };
      this.freeReviewSort = { by: 'financial_event_score', direction: 'desc' };
      this.loadFreeReview(true, true);
    },
    saveCurrentFreeReviewPreset() {
      try {
        saveFreeReviewPreset(
          this.freeReviewPresetName,
          this.freeReviewFilters,
          localStorage,
        );
        this.freeReviewPresets = loadFreeReviewPresets(localStorage);
        this.freeReviewPresetName = '';
      } catch (error) {
        this.error = error.message;
      }
    },
    applyFreeReviewPreset(preset) {
      if (!preset || !preset.filters) return;
      const saved = JSON.parse(JSON.stringify(preset.filters));
      const ranges = Object.fromEntries(
        FREE_REVIEW_METRIC_GROUPS
          .flatMap((group) => group.metrics)
          .filter((metric) => metric.filterable)
          .map((metric) => [
            metric.key,
            {
              min: saved.ranges && saved.ranges[metric.key]
                ? (saved.ranges[metric.key].min ?? '') : '',
              max: saved.ranges && saved.ranges[metric.key]
                ? (saved.ranges[metric.key].max ?? '') : '',
            },
          ]),
      );
      this.freeReviewFilters = {
        keyword: '',
        industries: [],
        areas: [],
        markets: [],
        profit_state: '',
        volume_state: '',
        growth_state: '',
        visible_columns: [],
        ...saved,
        ranges,
      };
      this.loadFreeReview(true, true);
    },
    sortFreeReview(key) {
      if (this.freeReviewSort.by === key) {
        this.freeReviewSort.direction = this.freeReviewSort.direction === 'desc'
          ? 'asc' : 'desc';
      } else {
        this.freeReviewSort = { by: key, direction: 'desc' };
      }
      this.loadFreeReview(true, true);
    },
    changeFreeReviewPage(page) {
      const pages = Number(this.freeReviewResult.pages || 0);
      if (page < 1 || (pages && page > pages)) return;
      this.freeReviewPage = page;
      this.loadFreeReview(true);
    },
    async exportFreeReview() {
      try {
        const response = await fetch(this.buildUrl('/free-review/export'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this.freeReviewQueryPayload()),
        });
        if (!response.ok) {
          const message = await response.text();
          throw new Error(message || `HTTP ${response.status}`);
        }
        const blob = await response.blob();
        const disposition = response.headers.get('Content-Disposition') || '';
        const filenameMatch = disposition.match(
          /filename\*?=(?:UTF-8''|")?([^";]+)/i,
        );
        const filename = filenameMatch
          ? decodeURIComponent(filenameMatch[1].replace(/"$/, ''))
          : `free-review-${this.freeReviewMeta.trade_date || 'latest'}.csv`;
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(link.href);
      } catch (error) {
        this.error = error.message;
      }
    },
    async ensureCurrentMarketData() {
      await this.request('/cache/sync?forceCurrent=true&force_current=true', { method: 'POST' });
      const updated = await this.request('/scan/run', {
        method: 'POST',
        body: JSON.stringify({
          includeAi: false,
          limit: this.limit,
        }),
      });
      this.latest = updated || this.latest;
      await this.loadReports();
    },
    async loadIntradayMonitor(
      showError = true,
      ensureCurrent = false,
      forceRefresh = false,
    ) {
      this.intradayLoading = true;
      this.intradayRefreshMode = forceRefresh ? 'force' : 'quick';
      try {
        const forceQuery = forceRefresh ? '?force_refresh=true' : '';
        this.intradayMonitor = (
          await this.request(`/intraday-monitor${forceQuery}`)
        ) || {};
        if (ensureCurrent && this.intradayMonitor.data_current === false) {
          await this.ensureCurrentMarketData();
          this.intradayMonitor = (
            await this.request('/intraday-monitor?force_refresh=true')
          ) || {};
        }
        if (!this.intradayMonitor.auto_refresh_enabled && this.intradayAutoRefresh) {
          this.stopIntradayMonitor();
          this.intradayAutoRefresh = false;
        }
      } catch (error) {
        if (showError) this.error = error.message;
      } finally {
        this.intradayLoading = false;
        this.intradayRefreshMode = null;
      }
    },
    startIntradayMonitor() {
      this.stopIntradayMonitor();
      this.loadIntradayMonitor(false, false, false);
      this.intradayTimer = setInterval(() => {
        this.loadIntradayMonitor(false, false, false);
      }, 30000);
    },
    stopIntradayMonitor() {
      if (this.intradayTimer) {
        clearInterval(this.intradayTimer);
        this.intradayTimer = null;
      }
    },
    toggleIntradayMonitor() {
      if (this.intradayAutoRefresh) this.startIntradayMonitor();
      else this.stopIntradayMonitor();
    },
    async loadOvernightMonitor(showError = true) {
      this.overnightLoading = true;
      try {
        this.overnightMonitor = (await this.request('/morning-follow-monitor?limit=10')) || {};
        if (!this.overnightMonitor.auto_refresh && this.overnightAutoRefresh) {
          this.stopOvernightMonitor();
          this.overnightAutoRefresh = false;
        }
      } catch (error) {
        if (showError) this.error = error.message;
      } finally {
        this.overnightLoading = false;
      }
    },
    startOvernightMonitor() {
      this.stopOvernightMonitor();
      this.loadOvernightMonitor(false);
      this.overnightTimer = setInterval(() => {
        this.loadOvernightMonitor(false);
      }, 30000);
    },
    stopOvernightMonitor() {
      if (this.overnightTimer) {
        clearInterval(this.overnightTimer);
        this.overnightTimer = null;
      }
    },
    toggleOvernightMonitor() {
      if (this.overnightAutoRefresh) this.startOvernightMonitor();
      else this.stopOvernightMonitor();
    },
    async loadRealtimeInfo(showError = true, forceRefresh = false) {
      if (this.realtimeInfoLoading) return;
      this.realtimeInfoLoading = true;
      this.realtimeInfoRefreshMode = forceRefresh ? 'force' : 'quick';
      try {
        const forceQuery = forceRefresh ? '&force_refresh=true' : '';
        const debugQuery = this.realtimePositionDebug ? '&debug=true' : '';
        const loaded = (
          await this.request(`/realtime-info/position-candidates?limit=10${forceQuery}${debugQuery}`)
        ) || {};
        const existingOvernight = this.realtimeInfo && this.realtimeInfo.overnight;
        this.realtimeInfo = {
          ...loaded,
          overnight: existingOvernight || loaded.overnight || {},
        };
      } catch (error) {
        if (showError) this.error = error.message;
      } finally {
        this.realtimeInfoLoading = false;
        this.realtimeInfoRefreshMode = null;
      }
    },
    async loadRealtimeTailPremium(showError = true, forceRefresh = false) {
      if (this.realtimeTailPremiumLoading) return;
      this.realtimeTailPremiumLoading = true;
      this.realtimeTailPremiumRefreshMode = forceRefresh ? 'force' : 'quick';
      try {
        const forceQuery = forceRefresh ? '&force_refresh=true' : '';
        const debugQuery = this.realtimeTailPremiumDebug ? '&debug=true' : '';
        const overnight = (
          await this.request(`/realtime-info/tail-premium?limit=20${forceQuery}${debugQuery}`)
        ) || {};
        this.realtimeInfo = { ...(this.realtimeInfo || {}), overnight };
      } catch (error) {
        if (showError) this.error = error.message;
      } finally {
        this.realtimeTailPremiumLoading = false;
        this.realtimeTailPremiumRefreshMode = null;
      }
    },
    startRealtimeInfoMonitor() {
      this.stopRealtimeInfoMonitor();
      this.loadRealtimeInfo(false);
      this.realtimeInfoTimer = setInterval(() => {
        this.loadRealtimeInfo(false);
      }, 30000);
    },
    stopRealtimeInfoMonitor() {
      if (this.realtimeInfoTimer) {
        clearInterval(this.realtimeInfoTimer);
        this.realtimeInfoTimer = null;
      }
    },
    toggleRealtimeInfoMonitor() {
      if (this.realtimeInfoAutoRefresh) this.startRealtimeInfoMonitor();
      else this.stopRealtimeInfoMonitor();
    },
    async loadMarketNewsSummary(showError = true, forceRefresh = false) {
      this.marketNewsLoading = true;
      try {
        const forceQuery = forceRefresh ? '&force_refresh=true' : '';
        this.marketNewsSummary = (
          await this.request(`/market-news-summary?market=all&limit=8${forceQuery}`)
        ) || {};
      } catch (error) {
        if (showError) this.error = error.message;
      } finally {
        this.marketNewsLoading = false;
      }
    },
    async refreshSectorPotential() {
      this.sectorPotentialRefreshing = true;
      this.error = '';
      try {
        await this.ensureCurrentMarketData();
        if (this.activeTab === 'intraday_monitor') await this.loadIntradayMonitor(false, false);
      } catch (error) {
        this.error = error.message;
      } finally {
        this.sectorPotentialRefreshing = false;
      }
    },
    startSectorPotentialPolling() {
      this.stopSectorPotentialPolling();
      if (!this.isMarketAutoRefreshTime()) return;
      this.sectorPotentialTimer = setInterval(() => {
        if (this.activeTab === 'sector_potential' && this.isMarketAutoRefreshTime()) this.refreshSectorPotential();
        else this.stopSectorPotentialPolling();
      }, 30 * 60 * 1000);
    },
    stopSectorPotentialPolling() {
      if (this.sectorPotentialTimer) {
        clearInterval(this.sectorPotentialTimer);
        this.sectorPotentialTimer = null;
      }
    },
    async loadCacheStatus(showError = true) {
      this.cacheLoading = true;
      try {
        this.cacheStatus = (await this.request('/cache/status')) || {};
      } catch (error) {
        if (showError) this.error = error.message;
      } finally {
        this.cacheLoading = false;
      }
    },
    startCacheProgressPolling() {
      this.stopCacheProgressPolling();
      this.loadCacheStatus(false);
      this.cacheSyncProgressTimer = setInterval(() => {
        this.loadCacheStatus(false);
      }, 2000);
    },
    stopCacheProgressPolling() {
      if (this.cacheSyncProgressTimer) {
        clearInterval(this.cacheSyncProgressTimer);
        this.cacheSyncProgressTimer = null;
      }
    },
    async syncCache(forceCurrent = false) {
      this.cacheSyncing = true;
      this.error = '';
      this.startCacheProgressPolling();
      try {
        await this.request(`/cache/sync?forceCurrent=${forceCurrent}`, { method: 'POST' });
        await this.loadCacheStatus(false);
      } catch (error) {
        this.error = error.message;
      } finally {
        this.stopCacheProgressPolling();
        await this.loadCacheStatus(false);
        this.cacheSyncing = false;
      }
    },
    async loadReports() {
      this.loading = true;
      this.error = '';
      try {
        this.reports = await this.request(`/reports?limit=${this.limit}`);
      } catch (error) {
        this.error = error.message;
      } finally {
        this.loading = false;
      }
    },
    async runTrendBoxTarget() {
      this.trendBoxLoading = true;
      this.error = '';
      try {
        const tsCode = (this.trendBoxForm.tsCode || '').trim().toUpperCase();
        const endTradeDate = this.compactDate(this.trendBoxForm.endTradeDate || this.latest.trade_date || '');
        const lookbackDays = Number(this.trendBoxForm.lookbackDays || 120);
        const autoDetect = this.trendBoxForm.autoDetect !== false;
        const params = new URLSearchParams({
          end_trade_date: endTradeDate,
          lookback_days: String(lookbackDays),
          auto_detect: String(autoDetect),
        });
        if (!tsCode) throw new Error('请输入股票代码');
        if (!/^\d{8}$/.test(endTradeDate)) throw new Error('请输入8位结束交易日');
        if (!autoDetect) {
          const boxStart = this.compactDate(this.trendBoxForm.boxStart || '');
          const boxEnd = this.compactDate(this.trendBoxForm.boxEnd || '');
          if (!/^\d{8}$/.test(boxStart) || !/^\d{8}$/.test(boxEnd)) {
            throw new Error('请输入8位箱体开始日和结束日');
          }
          params.set('box_start', boxStart);
          params.set('box_end', boxEnd);
          this.trendBoxForm.boxStart = boxStart;
          this.trendBoxForm.boxEnd = boxEnd;
        }
        this.trendBoxResult = await this.request(
          `/stocks/${encodeURIComponent(tsCode)}/trend-box-target?${params.toString()}`,
        );
        this.trendBoxForm.tsCode = tsCode;
        this.trendBoxForm.endTradeDate = endTradeDate;
        this.activeTab = 'trend_box_target';
      } catch (error) {
        this.error = error.message;
      } finally {
        this.trendBoxLoading = false;
      }
    },
    async openReport(id) {
      this.loading = true;
      this.error = '';
      try {
        this.latest = await this.request(`/reports/${id}`);
        this.activeTab = 'overview';
      } catch (error) {
        this.error = error.message;
      } finally {
        this.loading = false;
      }
    },
    async runScan() {
      this.loading = true;
      this.error = '';
      try {
        this.latest = await this.request('/scan/run', {
          method: 'POST',
          body: JSON.stringify({
            includeAi: this.includeAi,
            limit: this.limit,
          }),
        });
        await this.loadReports();
        this.activeTab = 'overview';
      } catch (error) {
        this.error = error.message;
      } finally {
        this.loading = false;
      }
    },
    async runBacktest() {
      this.backtestLoading = true;
      this.error = '';
      try {
        this.backtest = await this.request('/backtest/run', {
          method: 'POST',
          body: JSON.stringify({
            lookbackDays: this.backtestLookbackDays,
            holdDays: this.backtestHoldDays,
            limit: this.backtestLimit,
          }),
        });
        this.activeTab = 'backtest';
      } catch (error) {
        this.error = error.message;
      } finally {
        this.backtestLoading = false;
      }
    },
    async runAiEvaluation() {
      this.evaluationLoading = true;
      this.error = '';
      try {
        this.aiEvaluation = await this.request(
          `/evaluation/ai?holdDays=${this.evaluationHoldDays}&reportLimit=${this.evaluationReportLimit}&stockLimit=${this.evaluationStockLimit}`
        );
        this.activeTab = 'evaluation';
      } catch (error) {
        this.error = error.message;
      } finally {
        this.evaluationLoading = false;
      }
    },
    summaryValue(strategy, key, suffix = '') {
      const value = this.summaryMetric(strategy, key);
      if (value === null || value === undefined) return '--';
      return `${formatNumber(value)}${suffix}`;
    },
    summaryMetric(strategy, key) {
      if (!this.backtest || !this.backtest.summary || !this.backtest.summary[strategy]) {
        return null;
      }
      return this.backtest.summary[strategy][key];
    },
    evaluationSummaryValue(group, key, suffix = '') {
      const value = this.evaluationSummaryMetric(group, key);
      if (value === null || value === undefined) return '--';
      return `${formatNumber(value)}${suffix}`;
    },
    evaluationSummaryMetric(group, key) {
      if (!this.aiEvaluation || !this.aiEvaluation.summary || !this.aiEvaluation.summary[group]) {
        return null;
      }
      return this.aiEvaluation.summary[group][key];
    },
    evaluationRanking(key) {
      if (!this.aiEvaluation || !this.aiEvaluation.ranking) {
        return [];
      }
      return this.aiEvaluation.ranking[key] || [];
    },
    compactDate(value) {
      return String(value || '').replace(/-/g, '');
    },
    reviewMetric(key) {
      return this.tradeReview && this.tradeReview.metrics ? this.tradeReview.metrics[key] : null;
    },
    async runTradeReview() {
      this.tradeReviewLoading = true;
      this.error = '';
      try {
        const form = this.reviewForm;
        const payload = {
          tsCode: form.tsCode.trim().toUpperCase(),
          buyDate: this.compactDate(form.buyDate),
          buyPrice: Number(form.buyPrice),
          positionStatus: form.positionStatus,
          lossStatus: form.lossStatus.trim(),
          holdingNote: form.holdingNote.trim(),
        };
        if (form.positionStatus === 'sold') {
          payload.sellDate = this.compactDate(form.sellDate);
          payload.sellPrice = Number(form.sellPrice);
        }
        this.tradeReview = await this.request('/trade-review/analyze', {
          method: 'POST',
          body: JSON.stringify(payload),
        });
        this.activeTab = 'review';
      } catch (error) {
        this.error = error.message;
      } finally {
        this.tradeReviewLoading = false;
      }
    },
  },
}).mount('#app');
