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

const StockTable = {
  props: {
    rows: { type: Array, default: () => [] },
    mode: { type: String, default: 'strong' },
  },
  methods: { formatNumber, signedClass },
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
            <th>换手率</th>
            <th>量比</th>
            <th>评分</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.ts_code + mode">
            <td class="mono">{{ row.ts_code || '--' }}</td>
            <td>{{ row.name || '--' }}</td>
            <td>{{ row.industry || '--' }}</td>
            <td>{{ formatNumber(row.close) }}</td>
            <td :class="signedClass(row.pct_chg)">{{ formatNumber(row.pct_chg) }}%</td>
            <td>{{ formatNumber(row.turnover_rate) }}%</td>
            <td>{{ formatNumber(row.volume_ratio) }}</td>
            <td>{{ formatNumber(row.score ?? row.dip_score) }}</td>
          </tr>
          <tr v-if="rows.length === 0">
            <td colspan="8" class="empty">暂无数据</td>
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

createApp({
  components: {
    StockTable,
    SectorTable,
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
      error: '',
      healthOk: false,
      healthText: '未连接',
    };
  },
  computed: {
    pageTitle() {
      const titles = {
        overview: '选股总览',
        strong: '强势股',
        dip: '抄底候选',
        sectors: '板块机会',
        reports: '历史报告',
      };
      return titles[this.activeTab] || '选股总览';
    },
  },
  mounted() {
    this.refreshAll();
  },
  methods: {
    topRows(rows, limit) {
      return (rows || []).slice(0, limit);
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
  },
}).mount('#app');
