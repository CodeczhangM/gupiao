# 实时查询数据库混合缓存 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将实时分钟行情和最近一次成功筛选结果持久化到 MySQL，让“快速查看”通常在 1 秒内返回，同时保留可主动触发的“强制刷新”。

**Architecture:** 新建独立 `realtime_cache.py` 负责表结构、分钟行情 CRUD、结果 JSON CRUD、新鲜度判断和 5 个交易日清理。实时共振与实时信息服务先复用进程内缓存，再读数据库结果；强制刷新绕过最终结果缓存，但分钟加载器仍可复用满足截止时间的新鲜数据库数据。外部刷新成功后才原子更新结果缓存，失败或异常空结果只读取旧缓存，不覆盖成功记录。

**Tech Stack:** Python 3.10、pandas、FastAPI、PyMySQL/MySQL、Vue 3、Node.js assert tests、Spring Boot/Java 17、Maven。

## Global Constraints

- 不修改现有筛选条件、排序和 Tushare/东方财富/新浪回退顺序。
- 快速查看默认 `force_refresh=false`；强制刷新使用 `force_refresh=true`。
- 分钟行情最多允许 90 秒盘中延迟；历史日和收盘后的当日数据视为固定数据。
- 数据库缓存只保留最近 5 个交易日。
- 数据库不可用时必须回退当前内存缓存和外部数据源流程。
- 异常结果、数据不可用结果和意外空结果不得覆盖最后一次成功快照。

---

### Task 1: MySQL 实时缓存仓储

**Files:**
- Create: `realtime_cache.py`
- Create: `tests/test_realtime_cache.py`

**Interfaces:**
- Consumes: `database.get_connection()`、pandas DataFrame、现有 JSON-safe 字典。
- Produces:
  - `init_realtime_cache() -> None`
  - `load_minute_cache(ts_code: str, start: str, end: str, freq: str) -> pd.DataFrame`
  - `save_minute_cache(frame: pd.DataFrame, freq: str, source_name: str, cache_trade_date: str) -> None`
  - `minute_cache_is_fresh(frame: pd.DataFrame, requested_start: str, requested_end: str, now: datetime, freq: str) -> bool`
  - `load_result_cache(cache_scope: str, cache_key: str) -> dict[str, Any] | None`
  - `save_result_cache(cache_scope: str, cache_key: str, payload: dict[str, Any]) -> None`
  - `prune_realtime_cache(keep_trade_dates: list[str]) -> None`

- [ ] **Step 1: Write failing schema and result round-trip tests**

```python
class RealtimeCacheTests(unittest.TestCase):
    def test_init_creates_minute_and_result_tables(self):
        cursor, connection = fake_connection()
        with patch("realtime_cache.get_connection", return_value=connection):
            realtime_cache.init_realtime_cache()
        sql = "\n".join(call.args[0] for call in cursor.execute.call_args_list)
        self.assertIn("CREATE TABLE IF NOT EXISTS realtime_minute_cache", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS realtime_result_cache", sql)

    def test_result_cache_decodes_json_and_timestamp(self):
        cursor, connection = fake_connection()
        cursor.fetchone.return_value = {
            "payload_json": '{"stocks":[{"ts_code":"600001.SH"}]}',
            "updated_at": datetime(2026, 7, 30, 14, 40),
        }
        with patch("realtime_cache.get_connection", return_value=connection):
            result = realtime_cache.load_result_cache("realtime_info", "limit=10")
        self.assertEqual(result["payload"]["stocks"][0]["ts_code"], "600001.SH")
        self.assertEqual(result["updated_at"], "2026-07-30 14:40:00")
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python -m unittest tests.test_realtime_cache -v
```

Expected: import failure because `realtime_cache.py` does not exist.

- [ ] **Step 3: Implement schema and result CRUD**

Create both InnoDB tables. Use `(ts_code, trade_time, freq)` as the minute primary key and store `cache_trade_date` on every upsert so a multi-day 60-minute indicator window remains reusable for the target date. Use `(cache_scope, cache_key, trade_date)` as the result primary key and read the newest row by `updated_at DESC`. Serialize payloads with `json.dumps(..., ensure_ascii=False, allow_nan=False)` and use `INSERT ... ON DUPLICATE KEY UPDATE` so a same-day result replacement is atomic inside `get_connection()`.

```python
def load_result_cache(cache_scope, cache_key):
    init_realtime_cache()
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT payload_json, updated_at FROM realtime_result_cache
                   WHERE cache_scope=%s AND cache_key=%s""",
                (cache_scope, cache_key),
            )
            row = cursor.fetchone()
    if not row:
        return None
    return {
        "payload": json.loads(row["payload_json"]),
        "updated_at": row["updated_at"].isoformat(sep=" "),
    }
```

- [ ] **Step 4: Write failing minute upsert, freshness, and pruning tests**

```python
def test_minute_cache_upserts_normalized_rows(self):
    frame = pd.DataFrame([{
        "ts_code": "600001.SH",
        "trade_time": "2026-07-30 14:39:00",
        "open": 10, "high": 10.2, "low": 9.9, "close": 10.1,
        "vol": 1000, "amount": 10100,
    }])
    cursor, connection = fake_connection()
    with patch("realtime_cache.get_connection", return_value=connection):
        realtime_cache.save_minute_cache(
            frame, "1min", "eastmoney_fallback", "20260730"
        )
    self.assertEqual(cursor.executemany.call_count, 1)

def test_current_minute_cache_requires_requested_end_within_90_seconds(self):
    frame = pd.DataFrame([{"trade_time": "2026-07-30 14:39:00"}])
    self.assertTrue(realtime_cache.minute_cache_is_fresh(
                frame, "2026-07-30 09:30:00", "2026-07-30 14:40:00",
                datetime(2026, 7, 30, 14, 40), "1min"
    ))
    self.assertFalse(realtime_cache.minute_cache_is_fresh(
                frame, "2026-07-30 09:30:00", "2026-07-30 14:42:00",
                datetime(2026, 7, 30, 14, 42), "1min"
    ))

def test_prune_keeps_exactly_supplied_five_trade_dates(self):
    cursor, connection = fake_connection()
    keep = ["20260730", "20260729", "20260728", "20260727", "20260724"]
    with patch("realtime_cache.get_connection", return_value=connection):
        realtime_cache.prune_realtime_cache(keep)
    executed = "\n".join(call.args[0] for call in cursor.execute.call_args_list)
    self.assertIn("DELETE FROM realtime_minute_cache", executed)
    self.assertIn("DELETE FROM realtime_result_cache", executed)
```

- [ ] **Step 5: Implement minute CRUD, freshness, and bounded cleanup**

Normalize `trade_time` with `pd.to_datetime`, convert NaN to `None`, use `executemany` for upsert, and select ordered rows between start/end. For historical dates return fresh when the requested interval is covered. For the current date enforce the 90-second boundary; during lunch normalize the expected end to 11:30 and after 15:00 normalize it to 15:00. Cleanup must compare `cache_trade_date` and result `trade_date` against the exact five supplied dates; do not delete a multi-day indicator window according to each bar's own `trade_time`.

- [ ] **Step 6: Run repository tests**

Run:

```bash
python -m unittest tests.test_realtime_cache -v
```

Expected: all repository tests pass.

- [ ] **Step 7: Commit**

```bash
git add realtime_cache.py tests/test_realtime_cache.py
git commit -m "feat: add realtime mysql cache repository"
```

---

### Task 2: Persist and reuse minute行情

**Files:**
- Modify: `realtime_info_service.py`
- Modify: `intraday_monitor_service.py`
- Modify: `tests/test_realtime_info_service.py`
- Modify: `tests/test_intraday_monitor_service.py`

**Interfaces:**
- Consumes: Task 1 `load_minute_cache`、`save_minute_cache`、`minute_cache_is_fresh`。
- Produces:
  - `realtime_info_service._persistent_minute_result(...) -> MinuteLoadResult`
  - `intraday_monitor_service._persistent_minute_bars(...) -> pd.DataFrame`

- [ ] **Step 1: Write failing realtime-info minute reuse test**

```python
def test_persistent_minute_result_skips_external_loader_when_database_is_fresh(self):
    cached = pd.DataFrame([{
        "ts_code": "600001.SH",
        "trade_time": "2026-07-30 14:39:00",
        "close": 10.1,
    }])
    with (
        patch("realtime_info_service.load_minute_cache", return_value=cached),
        patch("realtime_info_service.minute_cache_is_fresh", return_value=True),
        patch("realtime_info_service._minute_result_with_1459_fallback") as external,
    ):
        result = realtime_info_service._persistent_minute_result(
            "600001.SH", "2026-07-30 09:30:00",
            "2026-07-30 14:40:00", "1min", "20260730",
            datetime(2026, 7, 30, 14, 40),
        )
    external.assert_not_called()
    self.assertEqual(result.source, "database")
    self.assertEqual(result.bars.iloc[0]["close"], 10.1)
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
python -m unittest tests.test_realtime_info_service.RealtimeInfoServiceTests.test_persistent_minute_result_skips_external_loader_when_database_is_fresh -v
```

Expected: attribute error for `_persistent_minute_result`.

- [ ] **Step 3: Implement realtime-info persistent loader**

Read the database first. If fresh, return `MinuteLoadResult(cached, "database", [])`. Otherwise call the existing `_minute_result_with_1459_fallback`; save only non-empty usable bars with their actual source and use this request's `trade_date` as `cache_trade_date`. Catch database read/write errors, append a warning, and continue through the existing external loader.

Wire `_request_minute_loader` in `_build_realtime_info_uncached` to `_persistent_minute_result` without changing existing per-request deduplication or provider order.

- [ ] **Step 4: Write failing realtime-confluence minute reuse test**

```python
def test_intraday_persistent_minutes_use_database_before_tushare(self):
    cached = pd.DataFrame([{
        "ts_code": "600001.SH",
        "trade_time": "2026-07-30 14:39:00",
        "close": 10.1,
    }])
    with (
        patch("intraday_monitor_service.load_minute_cache", return_value=cached),
        patch("intraday_monitor_service.minute_cache_is_fresh", return_value=True),
        patch("intraday_monitor_service._cached_minute_bars") as tushare,
    ):
        result = intraday_monitor_service._persistent_minute_bars(
            "600001.SH", "2026-07-30 09:30:00",
            "2026-07-30 14:40:00", "1min",
            datetime(2026, 7, 30, 14, 40),
        )
    tushare.assert_not_called()
    self.assertEqual(result.iloc[0]["close"], 10.1)
```

- [ ] **Step 5: Implement realtime-confluence persistent loader**

Use the same freshness contract. On a miss call `_cached_minute_bars`, save non-empty frames with source `tushare`, and keep database failures non-fatal. Replace both 60-minute and tail 1-minute calls in `_monitor_row`.

- [ ] **Step 6: Run both service suites**

Run:

```bash
python -m unittest tests.test_realtime_info_service tests.test_intraday_monitor_service -v
```

Expected: all tests pass and existing multi-source fallback assertions remain unchanged.

- [ ] **Step 7: Commit**

```bash
git add realtime_info_service.py intraday_monitor_service.py tests/test_realtime_info_service.py tests/test_intraday_monitor_service.py
git commit -m "perf: persist realtime minute bars"
```

---

### Task 3: Database-backed final result fast path

**Files:**
- Modify: `realtime_info_service.py`
- Modify: `intraday_monitor_service.py`
- Modify: `app.py`
- Modify: `tests/test_realtime_info_service.py`
- Modify: `tests/test_intraday_monitor_service.py`
- Modify: `tests/test_intraday_monitor_api.py`

**Interfaces:**
- Consumes: Task 1 `load_result_cache`、`save_result_cache`、`prune_realtime_cache`。
- Produces:
  - `build_intraday_monitor(fetch_realtime=True, now=None, force_refresh=False)`
  - Existing `build_realtime_info(..., force_refresh=False)` with database fallback.
  - `/api/intraday-monitor?force_refresh=true`

- [ ] **Step 1: Write failing realtime-info database hit test**

```python
def test_database_result_cache_precedes_full_realtime_build(self):
    cached_payload = {
        "trade_date": "20260730",
        "data_as_of": "2026-07-30 14:39:00",
        "intraday": {"stocks": [{"ts_code": "600001.SH"}]},
        "overnight": {"stocks": []},
    }
    with (
        patch("realtime_info_service.load_result_cache", return_value={
            "payload": cached_payload,
            "updated_at": "2026-07-30 14:40:00",
        }),
        patch("realtime_info_service._build_realtime_info_uncached") as build,
    ):
        result = build_realtime_info(limit=10, force_refresh=False)
    build.assert_not_called()
    self.assertEqual(result["cache_source"], "database")
    self.assertTrue(result["result_cache_hit"])
```

- [ ] **Step 2: Verify RED, then implement realtime-info result cache**

Run the single test and confirm `_build_realtime_info_uncached` is currently called. Add the database lookup after the process-memory lookup and before the full build. On a successful fresh build, save scope `realtime_info`, key `limit=<safe_limit>`. If refresh fails or produces no valid stocks, return the database record as stale without saving the failed result.

- [ ] **Step 3: Write failing intraday database hit and force bypass tests**

```python
def test_intraday_database_result_returns_without_loading_report(self):
    cached = {
        "payload": {"trade_date": "20260730", "stocks": [{"ts_code": "600001.SH"}]},
        "updated_at": "2026-07-30 14:40:00",
    }
    with (
        patch("intraday_monitor_service.load_result_cache", return_value=cached),
        patch("intraday_monitor_service.get_latest_report") as report,
    ):
        result = build_intraday_monitor(force_refresh=False)
    report.assert_not_called()
    self.assertEqual(result["cache_source"], "database")

def test_intraday_force_refresh_bypasses_result_cache(self):
    with (
        patch("intraday_monitor_service.load_result_cache") as cache,
        patch("intraday_monitor_service.get_latest_report", return_value=latest_report_fixture()),
        patch("intraday_monitor_service.get_trade_dates", return_value=["20260730"]),
    ):
        build_intraday_monitor(fetch_realtime=False, force_refresh=True)
    cache.assert_not_called()
```

- [ ] **Step 4: Implement intraday result cache**

Use scope `intraday_monitor`, key `default`. Database hit returns a JSON-safe copy with `cache_source="database"`, `cache_updated_at`, and `result_cache_hit=True`. A successful force build stores `cache_source="fresh"` and only saves when the result contains stocks. Database errors must not prevent the existing direct build.

- [ ] **Step 5: Write and implement FastAPI parameter forwarding test**

```python
@patch("app.build_intraday_monitor", return_value={"stocks": []})
def test_endpoint_forwards_force_refresh(self, service):
    app.intraday_monitor(force_refresh=True)
    service.assert_called_once_with(force_refresh=True)
```

Change the route to accept `force_refresh: bool = False` and pass it to the service.

- [ ] **Step 6: Add five-trading-day cleanup after successful writes**

After `save_result_cache`, load `get_trade_dates(n=5)` and call `prune_realtime_cache`. Catch cleanup errors and append a cache warning without failing the response.

- [ ] **Step 7: Run Python result-cache and API suites**

Run:

```bash
python -m unittest tests.test_realtime_cache tests.test_realtime_info_service tests.test_intraday_monitor_service tests.test_realtime_info_api tests.test_intraday_monitor_api -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add realtime_info_service.py intraday_monitor_service.py app.py tests/test_realtime_info_service.py tests/test_intraday_monitor_service.py tests/test_intraday_monitor_api.py
git commit -m "perf: serve realtime results from mysql"
```

---

### Task 4: Java forwarding and dual-mode frontend

**Files:**
- Modify: `quantServer/quantServer/src/main/java/com/codec/quantserver/service/QuantPythonClient.java`
- Modify: `quantServer/quantServer/src/main/java/com/codec/quantserver/controller/QuantController.java`
- Modify: `quantServer/quantServer/src/test/java/com/codec/quantserver/controller/QuantControllerTest.java`
- Modify: `quantClient/realtime-info-utils.js`
- Modify: `quantClient/realtime-info-utils.test.js`
- Modify: `quantClient/main.js`
- Modify: `quantClient/index.html`

**Interfaces:**
- Consumes: Python `force_refresh` parameters and response fields `cache_source`、`cache_updated_at`、`result_cache_hit`。
- Produces:
  - Java `intradayMonitor(boolean forceRefresh)`
  - Frontend `realtimeCacheState(payload) -> {text, state, detail}`
  - Two buttons per module: “快速查看” and “强制刷新”。

- [ ] **Step 1: Write failing Java forwarding test**

```java
@Test
void intradayMonitorForwardsForceRefreshToPythonClient() throws Exception {
    QuantPythonClient client = mock(QuantPythonClient.class);
    when(client.intradayMonitor(true)).thenReturn(Map.of("cache_source", "fresh"));
    MockMvc mockMvc = MockMvcBuilders.standaloneSetup(new QuantController(client)).build();

    mockMvc.perform(get("/api/quant/intraday-monitor")
                    .param("force_refresh", "true"))
            .andExpect(status().isOk());

    verify(client).intradayMonitor(true);
}
```

- [ ] **Step 2: Run Java test and verify RED**

Run:

```bash
mvn -q -Dtest=QuantControllerTest test
```

Working directory: `quantServer/quantServer`.

Expected: compilation failure because `intradayMonitor(boolean)` does not exist.

- [ ] **Step 3: Implement Java forwarding**

Controller reads `@RequestParam(name="force_refresh", defaultValue="false")`. Client builds `/api/intraday-monitor?force_refresh=<value>`. Keep realtime-info forwarding unchanged.

- [ ] **Step 4: Write failing frontend cache-state tests**

```javascript
assert.deepEqual(
  realtimeCacheState({
    cache_source: 'database',
    cache_updated_at: '2026-07-30 14:40:00',
  }),
  {
    text: '数据库缓存',
    state: 'warning',
    detail: '缓存生成于 2026-07-30 14:40:00',
  },
);
assert.equal(realtimeCacheState({ cache_source: 'fresh' }).text, '实时更新');
```

- [ ] **Step 5: Run frontend test and verify RED**

Run:

```bash
node quantClient/realtime-info-utils.test.js
```

Expected: `realtimeCacheState is not a function`.

- [ ] **Step 6: Implement frontend status and buttons**

Expose `realtimeCacheState`. Add computed states for `intradayMonitor` and `realtimeInfo`. Change loaders to:

```javascript
async loadIntradayMonitor(showError = true, forceRefresh = false) {
  const forceQuery = forceRefresh ? '?force_refresh=true' : '';
  this.intradayMonitor = await this.request(`/intraday-monitor${forceQuery}`) || {};
}
```

Render two buttons in each header:

- 快速查看 calls the loader with `forceRefresh=false`.
- 强制刷新 calls the loader with `forceRefresh=true`.

Show “数据库缓存 / 实时更新 / 内存缓存” and `cache_updated_at`. Keep auto-refresh on quick mode so 30-second polling never triggers an expensive forced refresh.

- [ ] **Step 7: Update frontend cache-busting versions and run all frontend tests**

Run:

```bash
for test_file in quantClient/*.test.js; do node "$test_file" || exit 1; done
```

Expected: all frontend tests pass.

- [ ] **Step 8: Run Java suite**

Run:

```bash
mvn test
```

Working directory: `quantServer/quantServer`.

Expected: all Java tests pass.

- [ ] **Step 9: Commit**

```bash
git add quantServer/quantServer/src/main/java quantServer/quantServer/src/test/java quantClient/realtime-info-utils.js quantClient/realtime-info-utils.test.js quantClient/main.js quantClient/index.html
git commit -m "feat: add quick and forced realtime refresh"
```

---

### Task 5: Full regression and performance acceptance

**Files:**
- Modify only if a verification failure identifies a defect in files from Tasks 1–4.

**Interfaces:**
- Consumes: completed database repository, services, APIs, Java proxy, and frontend.
- Produces: verified fast-path timings and durable cache behavior.

- [ ] **Step 1: Run full Python suite**

```bash
python -m unittest discover -s tests
```

Expected: zero failures.

- [ ] **Step 2: Run full frontend suite**

```bash
for test_file in quantClient/*.test.js; do node "$test_file" || exit 1; done
```

Expected: zero failures.

- [ ] **Step 3: Run full Java suite**

```bash
mvn test
```

Working directory: `quantServer/quantServer`.

Expected: `BUILD SUCCESS`.

- [ ] **Step 4: Initialize and verify MySQL cache tables**

Run a read-only schema check after initialization:

```bash
python -c "from realtime_cache import init_realtime_cache; init_realtime_cache(); print('realtime cache schema ready')"
```

Then verify both tables exist through `information_schema.tables`.

- [ ] **Step 5: Measure forced refresh then database fast path**

Run one forced refresh for each service, restart the Python process, then run quick view:

```bash
python -c "from time import perf_counter; from realtime_info_service import build_realtime_info; s=perf_counter(); r=build_realtime_info(limit=3, force_refresh=True); print(round(perf_counter()-s,3), r.get('cache_source'), r.get('data_as_of'))"
python -c "from time import perf_counter; from realtime_info_service import build_realtime_info; s=perf_counter(); r=build_realtime_info(limit=3); print(round(perf_counter()-s,3), r.get('cache_source'), r.get('data_as_of'))"
```

Repeat for `build_intraday_monitor`. The second command must report `cache_source=database`, preserve `data_as_of`, and normally complete within 1 second on the local MySQL instance.

- [ ] **Step 6: Verify failure preservation**

Temporarily mock the external loader in an automated test so forced refresh fails, then assert the previously saved database payload remains unchanged and is returned with stale/cache metadata.

- [ ] **Step 7: Inspect diff and commit any verification-only fixes**

```bash
git diff --check
git status --short
```

If Tasks 1–4 required no fixes, do not create an empty commit.
