# Free Review Stock Screener Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the three legacy stock-pool pages with a database-backed free-review screener covering every eligible A-share on the latest complete trading day.

**Architecture:** Incrementally cache eight quarters of Tushare VIP financial indicators, calculate a versioned per-stock review snapshot once per complete trading day, and serve whitelisted SQL filters, sector aggregates, pagination, and CSV exports. Keep legacy report fields and strategy calculations for realtime, backtest, evaluation, and historical compatibility while the Vue page switches to the new screener.

**Tech Stack:** Python 3.10, pandas, Tushare Pro VIP, PyMySQL, FastAPI/Pydantic, MySQL, Java 17, Spring Boot RestClient, Vue 3 static frontend, Node.js assertion tests, Python unittest, Maven.

## Global Constraints

- Use the latest complete trading day; never calculate the free-review universe from partial intraday data.
- Cover all eligible A-shares, excluding ST/`*ST`, delisting-status stocks, suspended rows, non-listed stocks, and listings younger than 60 calendar days.
- Include eligible `.SH`, `.SZ`, and `.BJ` listings; do not reuse the legacy main-board-only filter.
- Keep loss-making stocks, label them as loss-making, and assign zero valuation score.
- Sync the latest eight calendar quarters with `fina_indicator_vip`; do not fall back to thousands of per-stock requests.
- For historical review, only use financial rows where `ann_date <= trade_date`; for duplicate report periods choose the latest eligible announcement.
- Preserve `quant_reports.strong`, `dip`, `first_limit`, and `pools` and all existing scan, realtime, backtest, evaluation, and history behavior.
- Use `SCORE_VERSION = "free-review-v1"` for the first materialized snapshot version.
- Missing metrics score zero and never cause remaining weights to be renormalized.
- All stock queries are paginated; allowed page sizes are 50, 100, and 200.
- Filter and sort fields come from server-side whitelists; never interpolate arbitrary client field names into SQL.
- The frontend stores named filter presets only in `localStorage`; do not add user-account persistence.

## File Structure

- `financial_cache.py`: financial schema, eight-quarter period calculation, VIP incremental synchronization, and point-in-time financial selection.
- `free_review_scoring.py`: universe eligibility, technical/fundamental metrics, sector percentiles, dimension scores, risk penalties, and score explanations.
- `free_review_repository.py`: review snapshot/build schemas, transactional replacement, build status, whitelisted SQL query, sector aggregation, metadata, and CSV row iteration.
- `free_review_service.py`: background build orchestration and public service functions used by FastAPI.
- `free_review_models.py`: Pydantic request models for ranges, paginated queries, and exports.
- `app.py`: Python free-review endpoints.
- `market_cache.py`: expose every daily-basic and stock-basic field required by the screener.
- `quantServer/.../FreeReviewQueryRequest.java`: Spring JSON forwarding DTO.
- `QuantPythonClient.java`, `QuantController.java`: Spring API forwarding, including CSV response headers.
- `quantClient/free-review-utils.js`: metric definitions, query normalization, local preset helpers, and display formatting.
- `quantClient/main.js`, `index.html`, `styles.css`: free-review page state, requests, filters, table, sector summary, build progress, and removal of legacy pool navigation.

---

### Task 1: Expand market snapshots and add financial cache storage

**Files:**
- Create: `financial_cache.py`
- Modify: `market_cache.py`
- Create: `tests/test_financial_cache.py`
- Modify: `tests/test_market_cache.py`

**Interfaces:**
- Consumes: `database.get_connection()`, `data_service._query_tushare(api_name, **kwargs)`.
- Produces:
  - `quarter_periods(as_of_date: str, count: int = 8) -> list[str]`
  - `init_financial_cache() -> None`
  - `sync_financial_indicators(query_loader, as_of_date: str, quarters: int = 8) -> dict`
  - `load_financial_as_of(trade_date: str, periods: int = 8) -> pandas.DataFrame`
  - expanded `market_cache.load_market_snapshot(trade_date) -> pandas.DataFrame`

- [ ] **Step 1: Write failing market snapshot and financial schema tests**

Add tests proving that the market snapshot SQL returns:

```python
{
    "turnover_rate_f", "ps", "ps_ttm", "dv_ratio", "dv_ttm",
    "area", "market", "list_status", "list_date",
}
```

Add a schema test asserting these tables/keys:

```text
financial_indicator_cache:
  PRIMARY KEY (ts_code, end_date, ann_date)
  INDEX idx_financial_period (end_date, ann_date)
  INDEX idx_financial_code_announcement (ts_code, ann_date)

financial_cache_sync:
  PRIMARY KEY (source_name, end_date)
```

Use mocked `get_connection()` cursors, matching the repository test pattern in `tests/test_realtime_cache.py`.

- [ ] **Step 2: Run the tests to verify RED**

Run:

```bash
python3 -m unittest \
  tests.test_financial_cache \
  tests.test_market_cache
```

Expected: imports or assertions fail because `financial_cache.py` and the expanded snapshot fields do not exist.

- [ ] **Step 3: Implement the cache schema and expanded snapshot**

Create `financial_indicator_cache` with these numeric columns:

```text
eps, dt_eps, cfps,
roe, roe_dt, roa, roic,
grossprofit_margin, netprofit_margin,
current_ratio, debt_to_assets,
ocf_to_or, q_ocf_to_sales,
tr_yoy, or_yoy, netprofit_yoy, dt_netprofit_yoy,
q_sales_yoy, q_netprofit_yoy, ocf_yoy,
basic_eps_yoy, rd_exp
```

Include `update_flag`, `source_name`, and `fetched_at`. Create `financial_cache_sync` with `status`, `row_count`, timestamps, and `error_message`.

Change `load_market_snapshot()` to select:

```sql
b.turnover_rate_f, b.ps, b.ps_ttm, b.dv_ratio, b.dv_ttm,
s.area, s.market, s.list_status, s.list_date
```

in addition to its existing columns.

- [ ] **Step 4: Implement quarter calculation**

`quarter_periods("20260730", 8)` must return:

```python
[
    "20260630", "20260331", "20251231", "20250930",
    "20250630", "20250331", "20241231", "20240930",
]
```

Quarter periods are calendar quarter ends not later than `as_of_date`.

- [ ] **Step 5: Run the tests to verify GREEN**

Run the Step 2 command.

Expected: all financial-cache and market-cache tests pass.

- [ ] **Step 6: Commit**

```bash
git add financial_cache.py market_cache.py \
  tests/test_financial_cache.py tests/test_market_cache.py
git commit -m "feat: add financial indicator cache"
```

---

### Task 2: Implement Tushare VIP incremental financial synchronization

**Files:**
- Modify: `financial_cache.py`
- Modify: `tests/test_financial_cache.py`

**Interfaces:**
- Consumes: `quarter_periods()`, caller-supplied `query_loader("fina_indicator_vip", period=period, fields=fields)`.
- Produces: `sync_financial_indicators()` metadata:

```python
{
    "source_name": "fina_indicator_vip",
    "periods": ["20260630", "..."],
    "synced_periods": 2,
    "cached_periods": 6,
    "row_count": 42150,
    "failed_periods": [],
    "financial_coverage": 0.98,
}
```

- [ ] **Step 1: Write failing synchronization tests**

Cover:

1. already-complete periods do not call Tushare;
2. missing periods call `fina_indicator_vip` once each;
3. duplicate `(ts_code, end_date, ann_date)` rows use an upsert;
4. one failed period is recorded as `failed` while successful periods remain cached;
5. a permission error containing `5000` or `权限` is surfaced and does not trigger `fina_indicator`;
6. an empty period is recorded as failed, not complete.

- [ ] **Step 2: Run the focused tests to verify RED**

```bash
python3 -m unittest tests.test_financial_cache
```

Expected: synchronization behavior assertions fail.

- [ ] **Step 3: Implement incremental synchronization**

Use the exact Tushare field list:

```python
FINANCIAL_FIELDS = ",".join([
    "ts_code", "ann_date", "end_date", "update_flag",
    "eps", "dt_eps", "cfps", "roe", "roe_dt", "roa", "roic",
    "grossprofit_margin", "netprofit_margin",
    "current_ratio", "debt_to_assets",
    "ocf_to_or", "q_ocf_to_sales",
    "tr_yoy", "or_yoy", "netprofit_yoy", "dt_netprofit_yoy",
    "q_sales_yoy", "q_netprofit_yoy", "ocf_yoy",
    "basic_eps_yoy", "rd_exp",
])
```

For each missing quarter:

1. mark `financial_cache_sync` running;
2. request `fina_indicator_vip(period=period, fields=FINANCIAL_FIELDS)`;
3. normalize non-finite values to `None`;
4. batch upsert;
5. mark complete only after a non-empty committed write.

- [ ] **Step 4: Implement point-in-time loading**

`load_financial_as_of("20260730", 8)` must:

1. load the eight target report periods;
2. discard `ann_date > "20260730"`;
3. for each `(ts_code, end_date)`, retain the row with the latest `ann_date`, preferring `update_flag == "1"` when dates tie;
4. return all eligible period rows so the scoring layer can calculate improvement counts.

- [ ] **Step 5: Run focused and repository tests**

```bash
python3 -m unittest \
  tests.test_financial_cache \
  tests.test_market_cache \
  tests.test_realtime_cache
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add financial_cache.py tests/test_financial_cache.py
git commit -m "feat: sync eight quarters of financial data"
```

---

### Task 3: Build the free-review scoring engine

**Files:**
- Create: `free_review_scoring.py`
- Create: `tests/test_free_review_scoring.py`

**Interfaces:**
- Consumes:
  - market snapshot columns from Task 1;
  - 100-day history from `market_cache.load_recent_daily()`;
  - point-in-time rows from `load_financial_as_of()`.
- Produces:
  - `SCORE_VERSION = "free-review-v1"`
  - `eligible_universe(market: DataFrame, trade_date: str) -> DataFrame`
  - `build_review_snapshot(market, history, financial, trade_date) -> DataFrame`

- [ ] **Step 1: Write failing eligibility tests**

Use fixtures covering:

```text
正常上市 100 天且有成交 -> included
ST / *ST / 退市整理 -> excluded
list_status != L -> excluded
vol <= 0 or amount <= 0 -> excluded
listed for 59 days -> excluded
loss-making PE -> included with profit_state="loss"
```

- [ ] **Step 2: Write failing point-in-time and metric tests**

Build deterministic 100-day price/volume fixtures and assert:

- MA5/10/20/30/60;
- 5/10/20/60-day returns;
- 20/60-day drawdowns and 60-day position;
- volume-to-MA5/10/20 ratios;
- MACD DIF/DEA/histogram;
- KDJ, RSI6/12/24, Bollinger position/width, ATR percentage;
- financial latest report metadata and eight-quarter improvement counts.

Use approximate numeric assertions with explicit tolerances.

- [ ] **Step 3: Write failing scoring tests**

Assert exact boundaries:

```python
0 <= total_score <= 100
dimension score sum before risk == 100 maximum
missing metric contributes 0
negative PE produces valuation_score == 0
data completeness does not alter score weights
risk_penalty <= 20
```

Assert sector-percentile scoring by constructing two industries with intentionally different PE distributions. Assert banks, insurers, and securities do not receive ordinary-company current-ratio/debt penalties.

- [ ] **Step 4: Run tests to verify RED**

```bash
python3 -m unittest tests.test_free_review_scoring
```

Expected: module import fails.

- [ ] **Step 5: Implement eligibility and raw metrics**

Keep the module independent from database access. Use vectorized pandas operations and `groupby("ts_code")` for historical metrics. Emit stable snake-case columns, including:

```text
ret_5, ret_10, ret_20, ret_60,
drawdown_20, drawdown_60, position_60,
vol_ratio_ma5, vol_ratio_ma10, vol_ratio_ma20,
ma5, ma10, ma20, ma30, ma60,
ma20_slope, ma60_slope,
macd_dif, macd_dea, macd_hist,
kdj_k, kdj_d, kdj_j,
rsi6, rsi12, rsi24,
boll_position, boll_width, atr_pct
```

- [ ] **Step 6: Implement financial trends and six dimension scores**

Use:

```text
trend_score              0..20
volume_price_score       0..20
momentum_score           0..15
valuation_score          0..15
financial_quality_score  0..20
financial_growth_score   0..10
risk_penalty             0..20
total_score              0..100
```

Create `score_reasons` and `risk_flags` as JSON-compatible lists and `missing_fields` as a sorted list. `data_completeness` is the percentage of the scoring inputs that are non-null.

- [ ] **Step 7: Run focused tests to verify GREEN**

Run the Step 4 command.

Expected: all scoring tests pass.

- [ ] **Step 8: Commit**

```bash
git add free_review_scoring.py tests/test_free_review_scoring.py
git commit -m "feat: score free review stock universe"
```

---

### Task 4: Add snapshot repository, build status, and background orchestration

**Files:**
- Create: `free_review_repository.py`
- Create: `free_review_service.py`
- Create: `tests/test_free_review_repository.py`
- Create: `tests/test_free_review_service.py`

**Interfaces:**
- Consumes: Tasks 1–3 plus `market_cache.get_complete_dates(1)`, `load_market_snapshot()`, and `load_recent_daily()`.
- Produces:
  - `init_free_review_schema() -> None`
  - `replace_review_snapshot(trade_date, score_version, frame) -> None`
  - `save_build_status(payload: dict) -> None`
  - `load_build_status(trade_date=None, score_version=SCORE_VERSION) -> dict | None`
  - `start_free_review_build(force: bool = False) -> dict`
  - `build_free_review_snapshot(trade_date: str, force: bool = False) -> dict`

- [ ] **Step 1: Write failing schema and transaction tests**

Assert creation of:

```text
review_stock_snapshot
review_snapshot_build
```

The snapshot primary key is `(trade_date, ts_code, score_version)`. Test that replacement deletes only the same trade date/version and inserts the new frame in one transaction. A simulated insert failure must roll back without deleting the previous successful snapshot.

- [ ] **Step 2: Write failing build-state tests**

Test state progression:

```text
pending -> running/cache -> running/financial ->
running/scoring -> running/persisting -> success
```

Test:

- a second concurrent start returns the existing running task;
- a successful existing snapshot returns immediately unless `force=True`;
- a missing complete market date fails with `行情缓存中没有完整交易日`;
- a VIP permission failure marks the task failed with its permission message;
- an ordinary single-quarter sync failure becomes a warning and the build continues with cached eligible financial rows;
- per-stock scoring failures increment `failed_count` without discarding successful rows.

- [ ] **Step 3: Run tests to verify RED**

```bash
python3 -m unittest \
  tests.test_free_review_repository \
  tests.test_free_review_service
```

Expected: modules do not exist.

- [ ] **Step 4: Implement schemas and transactional persistence**

Use typed numeric MySQL columns for every filterable/sortable metric and `LONGTEXT` JSON for:

```text
score_reasons, risk_flags, missing_fields
```

Add indexes:

```text
(trade_date, score_version, total_score)
(trade_date, score_version, industry, total_score)
(trade_date, score_version, volume_ratio)
(trade_date, score_version, pe_ttm)
```

Serialize non-finite pandas values as `NULL`.

- [ ] **Step 5: Implement synchronous build**

`build_free_review_snapshot()` performs:

1. resolve latest complete trade date;
2. load and validate market/history;
3. sync eight financial quarters;
4. load point-in-time financial rows;
5. calculate the snapshot;
6. replace the version transactionally;
7. record total count, failed count, financial coverage, and timestamps.

- [ ] **Step 6: Implement background start**

Use one process-local lock plus persisted `running` state. Start a daemon `threading.Thread` only when no same-version task is running. Return immediately:

```python
{
    "trade_date": "20260730",
    "score_version": "free-review-v1",
    "status": "pending",
    "stage": "queued",
}
```

Database state is authoritative after process restart.

- [ ] **Step 7: Run focused tests to verify GREEN**

Run the Step 3 command.

Expected: all repository and service tests pass.

- [ ] **Step 8: Commit**

```bash
git add free_review_repository.py free_review_service.py \
  tests/test_free_review_repository.py tests/test_free_review_service.py
git commit -m "feat: materialize free review snapshots"
```

---

### Task 5: Add whitelisted queries, sector aggregation, metadata, and CSV

**Files:**
- Modify: `free_review_repository.py`
- Modify: `free_review_service.py`
- Create: `free_review_models.py`
- Modify: `tests/test_free_review_repository.py`
- Modify: `tests/test_free_review_service.py`

**Interfaces:**
- Produces:
  - `ReviewRange(min: float | None, max: float | None)`
  - `FreeReviewQuery(...)`
  - `query_free_review(request: FreeReviewQuery) -> dict`
  - `free_review_sectors(trade_date=None) -> dict`
  - `free_review_meta(trade_date=None) -> dict`
  - `export_free_review_csv(request: FreeReviewQuery) -> tuple[str, bytes]`

- [ ] **Step 1: Write failing whitelist and pagination tests**

Allowed numeric ranges include:

```text
total_score, trend_score, volume_price_score, momentum_score,
valuation_score, financial_quality_score, financial_growth_score,
risk_penalty, data_completeness,
pct_chg, amount, turnover_rate, turnover_rate_f, volume_ratio,
pe, pe_ttm, pb, ps, ps_ttm, dv_ttm, total_mv, circ_mv,
ret_5, ret_10, ret_20, ret_60, drawdown_20, drawdown_60,
vol_ratio_ma5, vol_ratio_ma10, vol_ratio_ma20,
ma20_slope, ma60_slope, rsi6, rsi12, rsi24, atr_pct,
roe, roe_dt, roa, roic, grossprofit_margin, netprofit_margin,
current_ratio, debt_to_assets, ocf_to_or,
tr_yoy, netprofit_yoy, dt_netprofit_yoy, ocf_yoy
```

Test keyword, multi-industry/area/market, profit state, range filters, sort direction, page sizes, out-of-range page, and total count. Invalid filter/sort fields must raise `ValueError`.

- [ ] **Step 2: Write failing sector and CSV consistency tests**

Sector rows must contain:

```text
industry, stock_count, avg_pct_chg, up_ratio,
median_volume_ratio, avg_turnover_rate, avg_pe_ttm, avg_total_score
```

CSV must use the exact same where clause and ordering as page queries and quote Chinese text correctly with UTF-8 BOM.

- [ ] **Step 3: Run tests to verify RED**

```bash
python3 -m unittest \
  tests.test_free_review_repository \
  tests.test_free_review_service
```

Expected: query/export methods are missing.

- [ ] **Step 4: Implement Pydantic models and SQL compiler**

`FreeReviewQuery` defaults:

```python
page = 1
page_size = 50
sort_by = "total_score"
sort_direction = "desc"
```

Clamp page to at least 1 and accept page sizes only from `{50, 100, 200}`. Compile conditions with `%s` parameters. Only the already-validated column identifiers may be interpolated.

- [ ] **Step 5: Implement metadata, sectors, and CSV**

Metadata returns:

```text
trade_date, score_version, generated_at, stock_count,
sector_count, financial_coverage, available_filters, data_warnings
```

CSV export is capped at 10,000 rows and returns filename:

```text
free-review-<trade_date>.csv
```

- [ ] **Step 6: Run focused tests to verify GREEN**

Run the Step 3 command.

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add free_review_models.py free_review_repository.py \
  free_review_service.py tests/test_free_review_repository.py \
  tests/test_free_review_service.py
git commit -m "feat: query and export free review stocks"
```

---

### Task 6: Expose FastAPI endpoints

**Files:**
- Modify: `app.py`
- Create: `tests/test_free_review_api.py`

**Interfaces:**
- Produces:

```text
POST /api/free-review/build?force=false
GET  /api/free-review/build-status
GET  /api/free-review/meta
POST /api/free-review/query
GET  /api/free-review/sectors
POST /api/free-review/export
```

- [ ] **Step 1: Write failing API tests**

Patch service functions and assert:

- build forwards `force`;
- status/meta/sectors return service payloads;
- query parses a body and forwards `FreeReviewQuery`;
- invalid whitelist input maps to HTTP 422;
- not-ready `LookupError` maps to HTTP 404;
- build/upstream failure maps to HTTP 502;
- export returns `text/csv; charset=utf-8` and an attachment filename.

- [ ] **Step 2: Run API tests to verify RED**

```bash
python3 -m unittest tests.test_free_review_api
```

Expected: routes return 404 or imports fail.

- [ ] **Step 3: Implement routes**

Import service functions lazily at module import as existing services do. Use `StreamingResponse(iter([content]))` for CSV and:

```python
headers={
    "Content-Disposition": f'attachment; filename="{filename}"',
}
```

Log build and query failures with endpoint-specific Chinese messages.

- [ ] **Step 4: Run focused and full Python tests**

```bash
python3 -m unittest tests.test_free_review_api
python3 -m unittest discover -s tests -p 'test_*.py'
```

Expected: all Python tests pass.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_free_review_api.py
git commit -m "feat: expose free review api"
```

---

### Task 7: Forward free-review APIs through Spring

**Files:**
- Create: `quantServer/quantServer/src/main/java/com/codec/quantserver/dto/FreeReviewRange.java`
- Create: `quantServer/quantServer/src/main/java/com/codec/quantserver/dto/FreeReviewQueryRequest.java`
- Modify: `quantServer/quantServer/src/main/java/com/codec/quantserver/service/QuantPythonClient.java`
- Modify: `quantServer/quantServer/src/main/java/com/codec/quantserver/controller/QuantController.java`
- Modify: `quantServer/quantServer/src/test/java/com/codec/quantserver/controller/QuantControllerTest.java`

**Interfaces:**
- Consumes: Python endpoints from Task 6.
- Produces: identical Spring paths under `/api/quant/free-review/...`.

- [ ] **Step 1: Write failing controller forwarding tests**

Assert:

- build forwards `force`;
- GET status/meta/sectors call matching client methods;
- query forwards the request body;
- export preserves `Content-Type` and `Content-Disposition`.

- [ ] **Step 2: Run Java tests to verify RED**

```bash
mvn -q -f quantServer/quantServer/pom.xml \
  -Dtest=QuantControllerTest test
```

Expected: DTO/client methods and controller routes are missing.

- [ ] **Step 3: Implement DTOs and JSON forwarding**

`FreeReviewQueryRequest` contains:

```text
String tradeDate
String scoreVersion
String keyword
List<String> industries
List<String> areas
List<String> markets
String profitState
String volumeState
String growthState
Map<String, FreeReviewRange> ranges
String sortBy
String sortDirection
Integer page
Integer pageSize
List<String> visibleColumns
```

Annotate every camel-case Java property with its snake-case JSON name, including:

```java
@JsonProperty("trade_date")
private String tradeDate;

@JsonProperty("score_version")
private String scoreVersion;

@JsonProperty("profit_state")
private String profitState;

@JsonProperty("sort_by")
private String sortBy;

@JsonProperty("sort_direction")
private String sortDirection;

@JsonProperty("page_size")
private Integer pageSize;

@JsonProperty("visible_columns")
private List<String> visibleColumns;
```

Forward JSON bodies unchanged except safe defaults for missing page/page size.

- [ ] **Step 4: Implement CSV proxy**

The client returns `ResponseEntity<byte[]>`; the controller copies content type and content disposition to its response. Do not decode/re-encode CSV as a Java `String`.

- [ ] **Step 5: Run focused and full Java tests**

```bash
mvn -q -f quantServer/quantServer/pom.xml \
  -Dtest=QuantControllerTest test
mvn -q -f quantServer/quantServer/pom.xml test
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add quantServer/quantServer/src/main/java \
  quantServer/quantServer/src/test/java/com/codec/quantserver/controller/QuantControllerTest.java
git commit -m "feat: proxy free review api"
```

---

### Task 8: Add frontend utilities and view-model behavior

**Files:**
- Create: `quantClient/free-review-utils.js`
- Create: `quantClient/free-review-utils.test.js`
- Modify: `quantClient/main.js`

**Interfaces:**
- Consumes: Spring endpoints from Task 7.
- Produces:
  - `FREE_REVIEW_METRIC_GROUPS`
  - `normalizeFreeReviewQuery(filters, page, pageSize, sort) -> object`
  - `saveFreeReviewPreset(name, filters, storage) -> void`
  - `loadFreeReviewPresets(storage) -> object[]`
  - `freeReviewBuildState(payload) -> {state, text, detail}`

- [ ] **Step 1: Write failing JavaScript utility tests**

Test:

- empty min/max values are omitted;
- zero remains a valid range boundary;
- visible columns and multi-select arrays survive normalization;
- page sizes outside 50/100/200 become 50;
- presets round-trip Chinese names through a fake `localStorage`;
- corrupt preset JSON returns an empty list;
- build states map pending/running/success/failed to Chinese text.

- [ ] **Step 2: Run tests to verify RED**

```bash
node quantClient/free-review-utils.test.js
```

Expected: module not found.

- [ ] **Step 3: Implement frontend utilities**

Define grouped metric metadata containing `key`, `label`, `format`, and whether range filtering/sorting is enabled. Keep API snake-case names unchanged in JavaScript payloads.

- [ ] **Step 4: Add Vue state and request methods**

Add:

```text
freeReviewMeta, freeReviewBuild, freeReviewSectors,
freeReviewRows, freeReviewTotal, freeReviewFilters,
freeReviewPage, freeReviewPageSize,
freeReviewSort, freeReviewVisibleColumns,
freeReviewLoading, freeReviewBuildTimer
```

Methods:

```text
loadFreeReviewMeta()
loadFreeReviewSectors()
queryFreeReview(resetPage=false)
startFreeReviewBuild(force=false)
pollFreeReviewBuild()
selectFreeReviewSector(industry)
resetFreeReviewFilters()
saveFreeReviewFilterPreset()
applyFreeReviewFilterPreset()
exportFreeReviewCsv()
```

Poll build status every two seconds only while status is pending/running, and clear the timer on success/failure.
Implement export with a direct `fetch()` call and `response.blob()` because the existing `request()` helper always parses JSON. Use the Spring `Content-Disposition` filename when available.

- [ ] **Step 5: Run utility and syntax tests**

```bash
node quantClient/free-review-utils.test.js
node --check quantClient/free-review-utils.js
node --check quantClient/main.js
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add quantClient/free-review-utils.js \
  quantClient/free-review-utils.test.js quantClient/main.js
git commit -m "feat: add free review view model"
```

---

### Task 9: Replace legacy pool pages with the free-review UI

**Files:**
- Modify: `quantClient/index.html`
- Modify: `quantClient/styles.css`
- Create: `quantClient/free-review-layout.test.js`
- Modify: `quantClient/cache-view-model.test.js`

**Interfaces:**
- Consumes: Vue state/methods and metric groups from Task 8.
- Produces: the visible free-review page and removal of legacy pool navigation/statistics.

- [ ] **Step 1: Write failing layout contract tests**

Read `index.html` as text and assert:

```text
contains: 自由复盘选股
contains: free-review-utils.js
contains: 生成复盘数据
contains: 重新生成
contains: 导出 CSV
contains: 板块总览
contains: 财务覆盖率
does not contain navigation buttons: 超跌反转 / 趋势突破 / 主升浪启动
does not contain activeTab === 'dip' / 'strong' / 'first_limit' sections
```

Do not reject those words inside backtest/evaluation explanatory tables; target only navigation and page-section patterns.

- [ ] **Step 2: Run layout test to verify RED**

```bash
node quantClient/free-review-layout.test.js
```

Expected: assertions fail.

- [ ] **Step 3: Replace navigation and summary cards**

Add:

```html
<button
  :class="{ active: activeTab === 'free_review' }"
  @click="activeTab = 'free_review'; loadFreeReviewMeta()"
>自由复盘选股</button>
```

Remove the three legacy navigation buttons and their count cards. Keep sector-potential, realtime, backtest, evaluation, history, and cache navigation.
Remove the global legacy `stage-guide` from the main dashboard so the retired pool concepts are not shown above the new screener; strategy names may remain inside backtest/evaluation compatibility tables.

- [ ] **Step 4: Build free-review page**

Implement:

1. metadata/build progress header;
2. all-sector summary table;
3. collapsible filter groups;
4. preset controls;
5. fixed identity/score columns plus grouped optional columns;
6. sortable headers;
7. 50/100/200 page size and pagination;
8. CSV export;
9. stock-row click using the existing technical-detail workflow.

Use semantic buttons and labels and keep horizontal scrolling inside the stock table.

- [ ] **Step 5: Add responsive styles**

Add `.free-review-*` classes for:

- build status/progress;
- sector grid/table;
- filter group grid;
- sticky first four stock columns;
- score badges and risk flags;
- column chooser;
- pagination;
- mobile stacking below 900px.

- [ ] **Step 6: Cache-bust assets**

Add:

```html
<script src="./free-review-utils.js?v=20260730-v1"></script>
<script src="./main.js?v=20260730-free-review-v1"></script>
```

Update the stylesheet query version as well.

- [ ] **Step 7: Run all frontend tests**

```bash
set -e
for test_file in quantClient/*.test.js; do
  node "$test_file"
done
node --check quantClient/main.js
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add quantClient/index.html quantClient/styles.css \
  quantClient/free-review-layout.test.js \
  quantClient/cache-view-model.test.js
git commit -m "feat: replace legacy pools with free review"
```

---

### Task 10: Update deployment and run complete regression

**Files:**
- Modify: `DEPLOY_SERVER.md`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: server initialization, build, warm-up, verification, and troubleshooting instructions for the free-review module.

- [ ] **Step 1: Update deployment file list and initialization**

Add the new Python, Java DTO, and frontend utility files to the release description. Add an idempotent initialization command:

```bash
python3 -c "
import settings
settings.load_env_files()
from financial_cache import init_financial_cache
from free_review_repository import init_free_review_schema
init_financial_cache()
init_free_review_schema()
print('free review schema ready')
"
```

- [ ] **Step 2: Add server verification**

Document:

```bash
curl --fail -X POST \
  'http://127.0.0.1:8081/api/quant/free-review/build?force=true'

curl --fail \
  'http://127.0.0.1:8081/api/quant/free-review/build-status'

curl --fail \
  'http://127.0.0.1:8081/api/quant/free-review/meta'
```

Include MySQL row-count checks for `financial_indicator_cache`, `review_stock_snapshot`, and `review_snapshot_build`, plus the 5000-point VIP permission failure message.

- [ ] **Step 3: Run full verification**

```bash
python3 -m unittest discover -s tests -p 'test_*.py'

set -e
for test_file in quantClient/*.test.js; do
  node "$test_file"
done
node --check quantClient/main.js

mvn -q -f quantServer/quantServer/pom.xml test

git diff --check
```

Expected: Python, frontend, and Java tests all pass and diff check is clean.

- [ ] **Step 4: Review compatibility**

Run focused legacy suites:

```bash
python3 -m unittest \
  tests.test_intraday_monitor_service \
  tests.test_morning_follow_service \
  tests.test_overnight_monitor_service \
  tests.test_realtime_info_service
```

Expected: all pass, proving the retained legacy pools still support dependent services.

- [ ] **Step 5: Commit**

```bash
git add DEPLOY_SERVER.md
git commit -m "docs: deploy free review screener"
```
