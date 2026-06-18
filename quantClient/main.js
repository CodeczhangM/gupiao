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

const StockTable = {
  props: {
    rows: { type: Array, default: () => [] },
    mode: { type: String, default: 'strong' },
  },
  methods: { formatNumber, signedClass, displayMetric, displayPercent },
  template: `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>代码</th>
            <th>名称</th>
            <th>行业</th>
            <th>收盘</th>
            <th>涨跌幅</th>
            <template v-if="mode === 'strong'">
              <th>总分</th>
              <th>行业排名</th>
              <th>命中原因</th>
              <th>20日强度</th>
              <th>底部区域</th>
              <th>60日跌幅</th>
              <th>放量上涨</th>
              <th>换手>8%</th>
              <th>量比>2</th>
              <th>站上20日线</th>
              <th>MACD金叉</th>
              <th>热点行业</th>
            </template>
            <template v-else>
              <th>换手率</th>
              <th>量比</th>
              <th>抄底原因</th>
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
            <td class="mono">{{ row.ts_code || '--' }}</td>
            <td>{{ row.name || '--' }}</td>
            <td>{{ row.industry || '--' }}</td>
            <td>{{ formatNumber(row.close) }}</td>
            <td :class="signedClass(row.pct_chg)">{{ formatNumber(row.pct_chg) }}%</td>
            <template v-if="mode === 'strong'">
              <td><strong>{{ formatNumber(row.score, 0) }}</strong></td>
              <td>{{ formatNumber(row.relative_strength_rank, 0) }}</td>
              <td>{{ row.strong_reason || '--' }}</td>
              <td>{{ formatNumber(row.strength20_score, 0) }}</td>
              <td>{{ formatNumber(row.in_bottom_area_score, 0) }}</td>
              <td>{{ formatNumber(row.ret60_oversold_score, 0) }}</td>
              <td>{{ formatNumber(row.volume_price_rise_score, 0) }}</td>
              <td>{{ formatNumber(row.turnover_active_score, 0) }}</td>
              <td>{{ formatNumber(row.volume_ratio_active_score, 0) }}</td>
              <td>{{ formatNumber(row.close_above_ma20_score, 0) }}</td>
              <td>{{ formatNumber(row.macd_golden_cross_score, 0) }}</td>
              <td>{{ formatNumber(row.hot_theme_score, 0) }}</td>
            </template>
            <template v-else>
              <td>{{ formatNumber(row.turnover_rate) }}%</td>
              <td>{{ formatNumber(row.volume_ratio) }}</td>
              <td>{{ row.dip_reason || '--' }}</td>
              <td :class="signedClass(row.high_drawdown)">{{ displayPercent(row.high_drawdown) }}</td>
              <td>{{ displayMetric(row.volume_shrink_rate) }}</td>
              <td>{{ displayMetric(row.ma20) }}</td>
              <td>{{ displayMetric(row.ma40) }}</td>
              <td>{{ row.support_line || '--' }}</td>
              <td>{{ formatNumber(row.score ?? row.dip_score) }}</td>
            </template>
          </tr>
          <tr v-if="rows.length === 0">
            <td :colspan="mode === 'strong' ? 17 : 14" class="empty">暂无数据</td>
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
  methods: { formatNumber, signedClass },
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
            <td>{{ row.strategy === 'strong' ? '优势' : '抄底' }}</td>
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
    BacktestTable,
  },
  data() {
    return {
      apiBase: initialApiBase(),
      activeTab: 'overview',
      includeAi: false,
      limit: 20,
      latest: {},
      reports: [],
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
      error: '',
      healthOk: false,
      healthText: '未连接',
    };
  },
  computed: {
    pageTitle() {
      const titles = {
        overview: '选股总览',
        strong: '优势股',
        dip: '抄底候选',
        sectors: '板块机会',
        reports: '历史报告',
        backtest: '策略回测',
        evaluation: 'AI 推荐评估',
      };
      return titles[this.activeTab] || '选股总览';
    },
    backtestRows() {
      return this.backtest && this.backtest.results ? this.backtest.results : [];
    },
  },
  mounted() {
    this.refreshAll();
  },
  methods: {
    formatNumber,
    signedClass,
    topRows(rows, limit) {
      return (rows || []).slice(0, limit);
    },
    listCount(rows) {
      return rows ? rows.length : 0;
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
        const [latest, reports] = await Promise.all([
          this.request('/reports/latest'),
          this.request(`/reports?limit=${this.limit}`),
        ]);
        this.latest = latest || {};
        this.reports = reports || [];
      } catch (error) {
        this.error = error.message;
      } finally {
        this.loading = false;
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
  },
}).mount('#app');
