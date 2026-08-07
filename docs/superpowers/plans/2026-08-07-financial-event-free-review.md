# Financial Event Free Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add financial-report event filters, sortable metrics, announcement price reaction, and sector event scoring to the existing free-review stock screener.

**Architecture:** Extend the existing `fina_indicator_vip` cache with `profit_dedt`, derive financial-event metrics inside free-review scoring, persist the new snapshot columns, and expose them through the existing free-review query/export UI. Keep the feature inside the existing free-review build and query path.

**Tech Stack:** Python 3, pandas, FastAPI, MySQL via PyMySQL-style cursors, unittest, Vue in `quantClient/main.js`, CommonJS utilities in `quantClient/free-review-utils.js`.

## Global Constraints

- Reuse `fina_indicator_vip`; do not create a separate financial-event data source in this version.
- Use point-in-time filtering with `ann_date <= trade_date`.
- Store boolean event flags as integer `0` or `1`.
- Default financial-event hit means deducted net profit >= `50000000` and growth >= `50`.
- Announcement returns are filterable and sortable but do not hard-filter by default.
- If `profit_dedt` coverage is unavailable, the free-review build must continue with warning data and zero event score.
- Do not add a separate financial-event page.

---

## File Structure

- `financial_cache.py`: extend financial indicator cache fields and schema migration for `profit_dedt`.
- `free_review_scoring.py`: derive deducted-profit growth, announcement reaction, financial event score, and sector financial event score.
- `free_review_service.py`: pass warnings when `profit_dedt` is unavailable.
- `free_review_repository.py`: persist/query/export new snapshot columns.
- `free_review_models.py`: allow new ranges and sort fields.
- `quantClient/free-review-utils.js`: add financial-event column metadata, query serialization, and build-state text if needed.
- `quantClient/main.js`: render financial-event filter group and presets in the existing free-review tab.
- `tests/test_financial_cache.py`: cache field and schema tests.
- `tests/test_free_review_scoring.py`: event derivation and snapshot tests.
- `tests/test_free_review_repository.py`: persistence/query field tests.
- `quantClient/free-review-utils.test.js` and `quantClient/free-review-layout.test.js`: frontend query and layout regression tests.

---

### Task 1: Extend Financial Indicator Cache With `profit_dedt`

**Files:**
- Modify: `financial_cache.py`
- Test: `tests/test_financial_cache.py`

**Interfaces:**
- Consumes: existing `sync_financial_indicators(query_loader, as_of_date, quarters=8)` and `load_financial_as_of(trade_date, periods=8)`.
- Produces: loaded financial frames include nullable numeric column `profit_dedt`.

- [ ] **Step 1: Write failing tests for field inclusion and migration**

Add to `tests/test_financial_cache.py`:

```python
    def test_financial_fields_include_profit_dedt(self):
        import financial_cache

        self.assertIn("profit_dedt", financial_cache.FINANCIAL_NUMERIC_FIELDS)
        self.assertIn("profit_dedt", financial_cache.FINANCIAL_FIELDS)

    def test_init_migrates_profit_dedt_column(self):
        import financial_cache

        cursor, connection = fake_connection()
        with (
            patch.object(financial_cache, "_schema_ready", False),
            patch("financial_cache.get_connection", return_value=connection),
        ):
            financial_cache.init_financial_cache()

        sql = "\n".join(call.args[0] for call in cursor.execute.call_args_list)
        self.assertIn("profit_dedt DOUBLE", sql)
        self.assertIn("ALTER TABLE financial_indicator_cache", sql)
        self.assertIn("ADD COLUMN profit_dedt DOUBLE NULL", sql)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python3 -m unittest tests.test_financial_cache.FinancialCacheTests -v`

Expected: FAIL because `profit_dedt` is not in `FINANCIAL_NUMERIC_FIELDS` and no migration SQL exists.

- [ ] **Step 3: Implement cache field and idempotent migration**

In `financial_cache.py`, add `"profit_dedt"` to `FINANCIAL_NUMERIC_FIELDS` near the other profit fields:

```python
    "tr_yoy", "or_yoy", "netprofit_yoy", "dt_netprofit_yoy",
    "q_sales_yoy", "q_netprofit_yoy", "ocf_yoy",
    "profit_dedt",
    "basic_eps_yoy", "rd_exp",
```

Add this statement after table creation in `init_financial_cache()`:

```python
        """ALTER TABLE financial_indicator_cache
            ADD COLUMN profit_dedt DOUBLE NULL""",
```

Wrap individual `cursor.execute(statement)` calls so duplicate-column migration is ignored:

```python
                for statement in statements:
                    try:
                        cursor.execute(statement)
                    except Exception as exc:
                        message = str(exc).lower()
                        duplicate_column = (
                            "duplicate column" in message
                            or "1060" in message
                        )
                        if "add column profit_dedt" in statement.lower() and duplicate_column:
                            continue
                        raise
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python3 -m unittest tests.test_financial_cache.FinancialCacheTests -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add financial_cache.py tests/test_financial_cache.py
git commit -m "feat: cache deducted net profit indicator"
```

---

### Task 2: Derive Financial Event Metrics And Announcement Reaction

**Files:**
- Modify: `free_review_scoring.py`
- Test: `tests/test_free_review_scoring.py`

**Interfaces:**
- Consumes: financial frame columns `ts_code`, `end_date`, `ann_date`, `update_flag`, `profit_dedt`; history frame columns `ts_code`, `trade_date`, `close`, `high`.
- Produces: `build_review_snapshot(...)` output columns `deducted_netprofit`, `deducted_netprofit_growth`, `financial_growth_basis`, `financial_statement_end_date`, `financial_statement_ann_date`, `announcement_return_3d`, `announcement_return_5d`, `announcement_return_10d`, `announcement_max_return_10d`, `financial_event_score`, `sector_financial_event_score`, and integer flags.

- [ ] **Step 1: Write failing tests for growth basis and announcement reaction**

Add helper financial rows to `tests/test_free_review_scoring.py`:

```python
def financial_event_fixture():
    return pd.DataFrame([
        {"ts_code": "600001.SH", "end_date": "20251231", "ann_date": "20260401", "update_flag": "1", "profit_dedt": 80_000_000, "roe": 10},
        {"ts_code": "600001.SH", "end_date": "20260331", "ann_date": "20260420", "update_flag": "1", "profit_dedt": 100_000_000, "roe": 11},
        {"ts_code": "600001.SH", "end_date": "20260630", "ann_date": "20260715", "update_flag": "1", "profit_dedt": 260_000_000, "roe": 12},
    ])
```

Add tests:

```python
    def test_build_snapshot_derives_financial_event_fields(self):
        import free_review_scoring

        result = free_review_scoring.build_review_snapshot(
            market_fixture(),
            history_fixture(),
            financial_event_fixture(),
            "20260730",
        )

        row = result.iloc[0]
        self.assertEqual(row["deducted_netprofit"], 160_000_000)
        self.assertEqual(row["financial_growth_basis"], "single_quarter_qoq")
        self.assertAlmostEqual(row["deducted_netprofit_growth"], 100.0)
        self.assertEqual(row["deducted_netprofit_threshold_hit"], 1)
        self.assertEqual(row["financial_growth_threshold_hit"], 1)
        self.assertEqual(row["financial_event_hit"], 1)
        self.assertEqual(row["financial_statement_end_date"], "20260630")
        self.assertEqual(row["financial_statement_ann_date"], "20260715")
        self.assertGreater(row["financial_event_score"], 50)
        self.assertGreaterEqual(row["sector_financial_event_score"], 0)

    def test_financial_event_handles_non_positive_previous_profit_conservatively(self):
        import free_review_scoring

        financial = pd.DataFrame([
            {"ts_code": "600001.SH", "end_date": "20260331", "ann_date": "20260420", "update_flag": "1", "profit_dedt": -10_000_000},
            {"ts_code": "600001.SH", "end_date": "20260630", "ann_date": "20260715", "update_flag": "1", "profit_dedt": 80_000_000},
        ])
        result = free_review_scoring.build_review_snapshot(
            market_fixture(),
            history_fixture(),
            financial,
            "20260730",
        )

        row = result.iloc[0]
        self.assertTrue(pd.isna(row["deducted_netprofit_growth"]))
        self.assertEqual(row["financial_growth_threshold_hit"], 0)
        self.assertEqual(row["financial_event_hit"], 0)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python3 -m unittest tests.test_free_review_scoring.FreeReviewScoringTests -v`

Expected: FAIL because new output columns do not exist.

- [ ] **Step 3: Add financial-event helper functions**

In `free_review_scoring.py`, add constants near `FINANCIAL_COLUMNS`:

```python
FINANCIAL_EVENT_COLUMNS = [
    "deducted_netprofit", "deducted_netprofit_growth",
    "financial_growth_basis",
    "deducted_netprofit_threshold_hit",
    "financial_growth_threshold_hit", "financial_event_hit",
    "financial_statement_end_date", "financial_statement_ann_date",
    "announcement_return_3d", "announcement_return_5d",
    "announcement_return_10d", "announcement_max_return_10d",
    "financial_event_score", "sector_financial_event_score",
]
```

Add helper signatures:

```python
def _period_month(end_date: str) -> int:
    return int(str(end_date)[4:6])

def _single_quarter_profit(group: pd.DataFrame) -> pd.DataFrame:
    data = group.sort_values("end_date").copy()
    data["profit_dedt_num"] = _numeric(data, "profit_dedt")
    data["year"] = data["end_date"].astype(str).str[:4]
    data["month"] = data["end_date"].astype(str).str[4:6].astype(int)
    data["single_quarter_profit"] = data["profit_dedt_num"]
    for year, year_group in data.groupby("year"):
        ordered = year_group.sort_values("month")
        prev_cumulative = ordered["profit_dedt_num"].shift(1)
        mask = ordered["month"].isin([6, 9, 12]) & prev_cumulative.notna()
        data.loc[ordered.index[mask], "single_quarter_profit"] = (
            ordered.loc[mask, "profit_dedt_num"] - prev_cumulative.loc[mask]
        )
    return data
```

Add `_growth_pct(current: float | None, previous: float | None) -> float | None` where non-positive previous values return `None`.

Add `_announcement_reaction(history_group: pd.DataFrame, ann_date: str) -> dict[str, float | None]` that sorts by `trade_date`, finds first index where `trade_date >= ann_date`, uses the previous close as baseline when available, and calculates 3/5/10 day close return plus 10 day max high return.

Add `_build_financial_events(financial: pd.DataFrame, history_groups: dict[str, pd.DataFrame]) -> pd.DataFrame` that groups by `ts_code`, deduplicates period rows by `end_date`, `ann_date`, and `update_flag`, chooses latest period, computes growth basis and flags, computes reaction, and returns one row per `ts_code`.

- [ ] **Step 4: Join event fields into snapshot and score**

Inside `build_review_snapshot(...)`, after merging latest financial indicators and before scoring rows:

```python
    financial_events = _build_financial_events(financial, history_groups)
    if not financial_events.empty:
        result = result.merge(financial_events, on="ts_code", how="left")
    else:
        for column in FINANCIAL_EVENT_COLUMNS:
            result[column] = None
```

After row-level `financial_event_score` exists, compute sector score before the final row loop or by mapping after event helper:

```python
    if "industry" in result and "financial_event_score" in result:
        sector_event = (
            result.dropna(subset=["industry"])
            .groupby("industry")
            .agg(
                avg_event=("financial_event_score", "mean"),
                hit_ratio=("financial_event_hit", "mean"),
            )
        )
        sector_event["sector_financial_event_score"] = (
            sector_event["avg_event"].fillna(0) * 0.7
            + sector_event["hit_ratio"].fillna(0) * 100 * 0.3
        ).clip(0, 100).round(2)
        result["sector_financial_event_score"] = result["industry"].map(
            sector_event["sector_financial_event_score"].to_dict()
        )
```

In total score calculation, add event contribution:

```python
        event_score = float(row.get("financial_event_score") or 0)
        sector_event_score = float(row.get("sector_financial_event_score") or 0)
        total = (
            sum(scores[key] for key in (...))
            - scores["risk_penalty"]
            + event_score * 0.12
            + sector_event_score * 0.06
        )
```

Add score reasons:

```python
        if int(row.get("financial_event_hit") or 0) == 1:
            reasons.append("财报扣非增长")
```

- [ ] **Step 5: Run scoring tests**

Run: `python3 -m unittest tests.test_free_review_scoring.FreeReviewScoringTests -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add free_review_scoring.py tests/test_free_review_scoring.py
git commit -m "feat: score financial report events"
```

---

### Task 3: Persist, Query, Sort, And Export Financial Event Fields

**Files:**
- Modify: `free_review_repository.py`
- Modify: `free_review_models.py`
- Modify: `free_review_service.py`
- Test: `tests/test_free_review_repository.py`
- Test: `tests/test_free_review_service.py`

**Interfaces:**
- Consumes: snapshot frames containing the Task 2 event columns.
- Produces: query/export APIs can filter and sort by event columns; build metadata warns on missing `profit_dedt`.

- [ ] **Step 1: Write failing model tests**

Add to the query model tests in `tests/test_free_review_repository.py` or create a small `tests/test_free_review_models.py` if no model test section exists:

```python
def test_free_review_query_accepts_financial_event_ranges_and_sort():
    from free_review_models import FreeReviewQuery

    query = FreeReviewQuery(
        ranges={
            "financial_event_hit": {"min": 1},
            "deducted_netprofit": {"min": 50_000_000},
            "deducted_netprofit_growth": {"min": 50},
            "announcement_return_5d": {"min": 0},
        },
        sort_by="financial_event_score",
    )

    assert query.sort_by == "financial_event_score"
    assert query.ranges["financial_event_hit"].min == 1
```

- [ ] **Step 2: Write failing repository persistence test**

Add a test that builds a one-row frame with event fields and asserts SQL includes the new columns:

```python
def test_replace_review_snapshot_persists_financial_event_columns():
    import free_review_repository

    frame = pd.DataFrame([{
        "ts_code": "600001.SH",
        "name": "财报强股",
        "industry": "制造",
        "deducted_netprofit": 80_000_000,
        "deducted_netprofit_growth": 60.0,
        "financial_growth_basis": "single_quarter_qoq",
        "deducted_netprofit_threshold_hit": 1,
        "financial_growth_threshold_hit": 1,
        "financial_event_hit": 1,
        "financial_statement_end_date": "20260630",
        "financial_statement_ann_date": "20260715",
        "announcement_return_3d": 6.0,
        "announcement_return_5d": 8.0,
        "announcement_return_10d": 12.0,
        "announcement_max_return_10d": 16.0,
        "financial_event_score": 82.0,
        "sector_financial_event_score": 75.0,
    }])
    cursor, connection = fake_connection()
    with (
        patch.object(free_review_repository, "_schema_ready", False),
        patch("free_review_repository.get_connection", return_value=connection),
    ):
        free_review_repository.replace_review_snapshot(
            "20260730",
            "free-review-v1-macd-5-34-5",
            frame,
        )

    sql = "\n".join(call.args[0] for call in cursor.execute.call_args_list)
    self.assertIn("financial_event_score", sql)
    self.assertIn("financial_event_hit", sql)
```

- [ ] **Step 3: Run tests to verify failure**

Run: `python3 -m unittest tests.test_free_review_repository -v`

Expected: FAIL because new fields are not allowed or persisted.

- [ ] **Step 4: Add repository/model fields**

In `free_review_repository.py`:

- Add text columns:

```python
    "financial_growth_basis",
    "financial_statement_end_date", "financial_statement_ann_date",
```

- Add integer columns:

```python
    "deducted_netprofit_threshold_hit",
    "financial_growth_threshold_hit", "financial_event_hit",
```

- Add numeric columns:

```python
    "deducted_netprofit", "deducted_netprofit_growth",
    "announcement_return_3d", "announcement_return_5d",
    "announcement_return_10d", "announcement_max_return_10d",
    "financial_event_score", "sector_financial_event_score",
```

Add indexes in schema creation:

```sql
            INDEX idx_review_financial_event
                (trade_date, score_version, financial_event_hit, financial_event_score),
            INDEX idx_review_financial_event_score
                (trade_date, score_version, financial_event_score)
```

In `free_review_models.py`, add all numeric event fields and integer flags to `ALLOWED_RANGE_FIELDS`, and add these sort fields to `ALLOWED_SORT_FIELDS`:

```python
    "financial_event_hit", "deducted_netprofit",
    "deducted_netprofit_growth", "announcement_return_3d",
    "announcement_return_5d", "announcement_return_10d",
    "announcement_max_return_10d", "financial_event_score",
    "sector_financial_event_score",
```

- [ ] **Step 5: Add service warning for missing `profit_dedt`**

In `free_review_service.py`, after loading financial data:

```python
        if "profit_dedt" not in financial.columns or financial["profit_dedt"].notna().mean() == 0:
            warnings.append("profit_dedt 扣非净利润字段无可用覆盖，财报事件分按 0 处理")
```

Guard for empty frames:

```python
        if financial is None or financial.empty:
            warnings.append("财务指标为空，财报事件分按 0 处理")
```

- [ ] **Step 6: Run backend repository/service tests**

Run:

```bash
python3 -m unittest tests.test_free_review_repository tests.test_free_review_service -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add free_review_repository.py free_review_models.py free_review_service.py tests/test_free_review_repository.py tests/test_free_review_service.py
git commit -m "feat: expose financial event review fields"
```

---

### Task 4: Add Financial Event Filters And Columns To The Frontend

**Files:**
- Modify: `quantClient/free-review-utils.js`
- Modify: `quantClient/main.js`
- Modify: `quantClient/styles.css`
- Test: `quantClient/free-review-utils.test.js`
- Test: `quantClient/free-review-layout.test.js`

**Interfaces:**
- Consumes: backend fields from Task 3.
- Produces: user can apply financial-event presets, ranges, column visibility, and sorting in the free-review UI.

- [ ] **Step 1: Write failing frontend utility tests**

In `quantClient/free-review-utils.test.js`, add:

```javascript
const financialQuery = normalizeFreeReviewQuery({
  ranges: {
    financial_event_hit: { min: 1, max: '' },
    deducted_netprofit: { min: 50000000, max: '' },
    deducted_netprofit_growth: { min: 50, max: '' },
  },
  visible_columns: ['financial_event_score', 'deducted_netprofit'],
}, 1, 50, { by: 'financial_event_score', direction: 'desc' });

assert.deepEqual(financialQuery.ranges.financial_event_hit, { min: 1 });
assert.deepEqual(financialQuery.ranges.deducted_netprofit, { min: 50000000 });
assert.equal(financialQuery.sort_by, 'financial_event_score');
assert.ok(FREE_REVIEW_METRIC_GROUPS.some(group => group.label === '财报事件'));
```

- [ ] **Step 2: Write failing layout test**

In `quantClient/free-review-layout.test.js`, assert the page contains financial-event controls:

```javascript
assert.ok(html.includes('财报事件'));
assert.ok(html.includes('financial_event_hit'));
assert.ok(html.includes('deducted_netprofit_growth'));
assert.ok(html.includes('financial_event_score'));
```

- [ ] **Step 3: Run frontend tests to verify failure**

Run:

```bash
node quantClient/free-review-utils.test.js
node quantClient/free-review-layout.test.js
```

Expected: FAIL because the new group and controls do not exist.

- [ ] **Step 4: Add column metadata**

In `quantClient/free-review-utils.js`, add a `财报事件` object to `FREE_REVIEW_METRIC_GROUPS`:

```javascript
{
  key: 'financial_event',
  label: '财报事件',
  metrics: [
    metric('financial_event_score', '财报事件分', 'score', { defaultVisible: true }),
    metric('sector_financial_event_score', '板块财报分', 'score'),
    metric('deducted_netprofit', '扣非净利润', 'money', { defaultVisible: true }),
    metric('deducted_netprofit_growth', '扣非增长%', 'percent', { defaultVisible: true }),
    metric('financial_event_hit', '财报命中', 'flag'),
    metric('financial_growth_basis', '增长口径', 'text', { filterable: false, sortable: false }),
    metric('financial_statement_ann_date', '公告日', 'date', { filterable: false }),
    metric('announcement_return_3d', '公告后3日%', 'percent'),
    metric('announcement_return_5d', '公告后5日%', 'percent'),
    metric('announcement_return_10d', '公告后10日%', 'percent'),
    metric('announcement_max_return_10d', '10日最高%', 'percent'),
  ],
},
```

Extend `freeReviewMetricValue(row, metric)` in `quantClient/main.js` before numeric formatting:

```javascript
      if (metric.format === 'flag') return Number(value) === 1 ? '是' : '否';
      if (metric.format === 'text' || metric.format === 'date') return String(value);
```

- [ ] **Step 5: Add free-review filter controls**

In `quantClient/main.js`, inside the free-review filters area, add a `details` section titled `财报事件` with:

```html
<details open>
  <summary>财报事件</summary>
  <div class="free-review-range-grid">
    <label>
      <span>财报命中</span>
      <div>
        <input v-model.number="freeReviewFilters.ranges.financial_event_hit.min" type="number" min="0" max="1" placeholder="1">
      </div>
    </label>
    <label>
      <span>扣非净利润</span>
      <div>
        <input v-model.number="freeReviewFilters.ranges.deducted_netprofit.min" type="number" placeholder="50000000">
      </div>
    </label>
    <label>
      <span>扣非增长%</span>
      <div>
        <input v-model.number="freeReviewFilters.ranges.deducted_netprofit_growth.min" type="number" placeholder="50">
      </div>
    </label>
    <label>
      <span>公告后5日%</span>
      <div>
        <input v-model.number="freeReviewFilters.ranges.announcement_return_5d.min" type="number" placeholder="0">
      </div>
    </label>
    <label>
      <span>财报事件分</span>
      <div>
        <input v-model.number="freeReviewFilters.ranges.financial_event_score.min" type="number" placeholder="60">
      </div>
    </label>
  </div>
</details>
```

Add a preset button:

```html
<button type="button" @click="applyFreeReviewFinancialEventPreset">财报事件命中</button>
```

Add method:

```javascript
applyFreeReviewFinancialEventPreset() {
  this.freeReviewFilters.ranges = {
    ...this.freeReviewFilters.ranges,
    financial_event_hit: { min: 1, max: '' },
    deducted_netprofit: { min: 50000000, max: '' },
    deducted_netprofit_growth: { min: 50, max: '' },
  };
  this.freeReviewSort = { by: 'financial_event_score', direction: 'desc' };
  this.loadFreeReview(true, true);
}
```

- [ ] **Step 6: Add compact financial input styles**

In `quantClient/styles.css`, add:

```css
.free-review-range-grid input[placeholder="50000000"] {
  min-width: 8rem;
}
```

- [ ] **Step 7: Run frontend tests**

Run:

```bash
node quantClient/free-review-utils.test.js
node quantClient/free-review-layout.test.js
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add quantClient/free-review-utils.js quantClient/main.js quantClient/styles.css quantClient/free-review-utils.test.js quantClient/free-review-layout.test.js
git commit -m "feat: add financial event review filters"
```

---

### Task 5: End-To-End Regression And Documentation Check

**Files:**
- Modify if needed: `README_BACKEND.md`
- No production code changes unless verification reveals a real issue.

**Interfaces:**
- Consumes: completed Tasks 1-4.
- Produces: verified backend and frontend regression pass for the financial-event free-review feature.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
python3 -m unittest tests.test_financial_cache tests.test_free_review_scoring tests.test_free_review_repository tests.test_free_review_service -v
```

Expected: PASS.

- [ ] **Step 2: Run focused frontend tests**

Run:

```bash
node quantClient/free-review-utils.test.js
node quantClient/free-review-layout.test.js
```

Expected: PASS.

- [ ] **Step 3: Run broader free-review API tests**

Run:

```bash
python3 -m unittest tests.test_free_review_api tests.test_free_review_scoring tests.test_free_review_repository tests.test_financial_cache -v
```

Expected: PASS.

- [ ] **Step 4: Update backend docs only if public setup changed**

If no new environment variables or manual setup steps were added, leave `README_BACKEND.md` unchanged. If a migration note is needed, add this text under the relevant cache/build section:

```markdown
Free-review builds now use `profit_dedt` from the existing `fina_indicator_vip` cache to score financial-report events. Existing databases are migrated automatically by `init_financial_cache()`.
```

- [ ] **Step 5: Commit verification docs if changed**

If `README_BACKEND.md` changed:

```bash
git add README_BACKEND.md
git commit -m "docs: note financial event review data"
```

If no docs changed, do not create an empty commit.

- [ ] **Step 6: Record final verification evidence**

Capture command names and PASS results for the final response:

```text
python3 -m unittest tests.test_financial_cache tests.test_free_review_scoring tests.test_free_review_repository tests.test_free_review_service -v
node quantClient/free-review-utils.test.js
node quantClient/free-review-layout.test.js
python3 -m unittest tests.test_free_review_api tests.test_free_review_scoring tests.test_free_review_repository tests.test_financial_cache -v
```

Expected: all PASS.
