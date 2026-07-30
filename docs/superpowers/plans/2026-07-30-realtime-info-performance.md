# Realtime Information Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make realtime information refresh quickly when providers are healthy and remain visibly usable from the last successful result when every live provider fails.

**Architecture:** Keep the existing synchronous API and screening rules. Add provider-level circuit state in `realtime_market_source.py`, request/result caching and bounded minute-data fan-out in `realtime_info_service.py`, then expose the cache state through the existing Python/Java proxy to the Vue page. All optimizations remain process-local and require no new dependency.

**Tech Stack:** Python 3, pandas, `concurrent.futures.ThreadPoolExecutor`, FastAPI, Java Spring proxy, Vue 2 browser client, Python `unittest`, Node `assert`, Maven tests.

## Global Constraints

- Do not change existing realtime-confluence or next-morning screening thresholds, scores, or ordering rules.
- Automatic refresh uses a 30-second result bucket; manual refresh bypasses only the result cache.
- External-provider failures are cached and circuit-broken for 60 seconds.
- Minute loads use at most 4 workers.
- Tail 1-minute confirmation is limited to the top 15 candidates selected by 60-minute information.
- Stale fallback must be labeled `备用缓存` and include its source update time and age.
- An empty live result must not overwrite the last successful non-empty result.

---

### Task 1: External provider circuit breaker

**Files:**
- Modify: `realtime_market_source.py`
- Test: `tests/test_realtime_market_source.py`

**Interfaces:**
- Consumes: existing `_fetch_eastmoney_minutes(...)`, `_fetch_sina_minutes(...)`, and `clear_realtime_source_caches()`.
- Produces: `_provider_available(provider: str) -> bool`, `_record_provider_result(provider: str, success: bool) -> None`; external fetchers return immediately while a provider is open.

- [ ] **Step 1: Write the failing circuit-breaker tests**

Add tests that patch `time.monotonic()` and `_run_curl()`:

```python
@patch("realtime_market_source._run_curl", side_effect=RuntimeError("down"))
@patch("realtime_market_source.time.monotonic", return_value=100.0)
def test_provider_failure_opens_circuit_for_other_symbols(clock, run_curl):
    for code in ("600298.SH", "300910.SZ", "600001.SH"):
        _fetch_eastmoney_minutes(
            code, "2026-07-30 14:25:00", "2026-07-30 14:30:00",
            "1min", "20260730",
        )
    self.assertEqual(run_curl.call_count, 2)

@patch("realtime_market_source._run_curl")
@patch("realtime_market_source.time.monotonic")
def test_provider_is_retried_after_circuit_timeout(clock, run_curl):
    clock.side_effect = [100.0] * 8 + [161.0] * 8
    run_curl.side_effect = [
        RuntimeError("down"), RuntimeError("down"),
        json.dumps({"data": {"trends": [
            "2026-07-30 14:29,10,10.2,10.3,9.9,200,2040,10"
        ]}}),
    ]
    # two failures open the circuit; a call after 60 seconds succeeds
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m unittest \
  tests.test_realtime_market_source.RealtimeMarketSourceTests.test_provider_failure_opens_circuit_for_other_symbols \
  tests.test_realtime_market_source.RealtimeMarketSourceTests.test_provider_is_retried_after_circuit_timeout -v
```

Expected: FAIL because the third symbol still calls `_run_curl` and no circuit state exists.

- [ ] **Step 3: Implement minimal provider state**

In `realtime_market_source.py`:

```python
FAILURE_TTL_SECONDS = 60
PROVIDER_FAILURE_THRESHOLD = 2
PROVIDER_CIRCUIT_SECONDS = 60
_PROVIDER_HEALTH: dict[str, tuple[int, float]] = {}

def _provider_available(provider: str) -> bool:
    failures, blocked_until = _PROVIDER_HEALTH.get(provider, (0, 0.0))
    return failures < PROVIDER_FAILURE_THRESHOLD or time.monotonic() >= blocked_until

def _record_provider_result(provider: str, success: bool) -> None:
    if success:
        _PROVIDER_HEALTH.pop(provider, None)
        return
    failures, _ = _PROVIDER_HEALTH.get(provider, (0, 0.0))
    failures += 1
    blocked_until = (
        time.monotonic() + PROVIDER_CIRCUIT_SECONDS
        if failures >= PROVIDER_FAILURE_THRESHOLD else 0.0
    )
    _PROVIDER_HEALTH[provider] = (failures, blocked_until)
```

Clear `_PROVIDER_HEALTH` in `clear_realtime_source_caches()`. Before each external fetch, return an empty frame with an explicit “数据源熔断中” error when unavailable; record success only for non-empty usable responses and failure for exceptions or empty responses.

- [ ] **Step 4: Run focused and source tests**

Run:

```bash
python3 -m unittest tests.test_realtime_market_source -v
```

Expected: all source tests PASS.

- [ ] **Step 5: Commit**

```bash
git add realtime_market_source.py tests/test_realtime_market_source.py
git commit -m "fix: circuit-break unavailable realtime providers"
```

### Task 2: Request-scoped minute reuse and bounded 60-minute fan-out

**Files:**
- Modify: `realtime_info_service.py`
- Modify: `overnight_monitor_service.py`
- Test: `tests/test_realtime_info_service.py`
- Test: `tests/test_overnight_monitor_service.py`

**Interfaces:**
- Consumes: `load_minutes_with_fallback(...)` and the existing `minute_loader` callback contract.
- Produces: `_request_minute_loader() -> Callable[..., MinuteLoadResult]` and `_load_signal_candidate(...)`; identical request keys return copies of one stored result.

- [ ] **Step 1: Write failing request-reuse test**

Add a test that builds a request loader around a counting primary loader and calls it twice with the same `(ts_code, start, end, freq, trade_date)`. Assert both results contain equal bars and the primary loader was invoked once.

- [ ] **Step 2: Verify request-reuse test is RED**

Run:

```bash
python3 -m unittest tests.test_realtime_info_service.RealtimeInfoServiceTests.test_request_minute_loader_reuses_identical_window -v
```

Expected: FAIL because `_request_minute_loader` does not exist.

- [ ] **Step 3: Implement the request loader**

Use a closure with a lock and a dictionary keyed by all five request fields:

```python
def _request_minute_loader(primary_loader):
    cache = {}
    lock = threading.Lock()

    def load(ts_code, start, end, freq, trade_date):
        key = (str(ts_code), str(start), str(end), str(freq), str(trade_date))
        with lock:
            cached = cache.get(key)
        if cached is not None:
            return MinuteLoadResult(cached.bars.copy(), cached.source, list(cached.warnings))
        loaded = load_minutes_with_fallback(
            ts_code, start, end, freq, trade_date, primary_loader=primary_loader
        )
        with lock:
            cache[key] = MinuteLoadResult(
                loaded.bars.copy(), loaded.source, list(loaded.warnings)
            )
        return loaded

    return load
```

Use this loader for both realtime sections. Extend `overnight_monitor_service._build_row` to accept already-loaded 60-minute bars so it does not retrieve the same window again.

- [ ] **Step 4: Write failing bounded-concurrency test**

Patch the per-symbol 60-minute load helper with a barrier-aware fake that records active calls. Build at least eight candidate symbols and assert `max_active > 1`, `max_active <= 4`, and output stock order remains the score order.

- [ ] **Step 5: Verify concurrency test is RED**

Run:

```bash
python3 -m unittest tests.test_realtime_info_service.RealtimeInfoServiceTests.test_signal_minutes_use_at_most_four_workers -v
```

Expected: FAIL because current loads are serial.

- [ ] **Step 6: Implement bounded fan-out**

Use `ThreadPoolExecutor(max_workers=4)` only around independent per-symbol 60-minute loads. Submit candidates in their existing order and consume futures in that same order so downstream ranking stays deterministic. Catch failures per future and produce the existing warning/empty-bar behavior.

Remove the unconditional `time.sleep(0.5)` from `_cached_minute_bars`; concurrency is bounded at the caller and the Tushare failure cache already prevents immediate repeated requests.

- [ ] **Step 7: Run focused service tests**

Run:

```bash
python3 -m unittest \
  tests.test_realtime_info_service \
  tests.test_overnight_monitor_service -v
```

Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add realtime_info_service.py overnight_monitor_service.py \
  tests/test_realtime_info_service.py tests/test_overnight_monitor_service.py
git commit -m "perf: share and parallelize realtime minute loads"
```

### Task 3: Rank before tail-minute confirmation

**Files:**
- Modify: `realtime_info_service.py`
- Test: `tests/test_realtime_info_service.py`

**Interfaces:**
- Consumes: rows generated from sector 60-minute signals.
- Produces: `_tail_candidate_rows(rows: list[dict[str, Any]], maximum: int = 15) -> list[dict[str, Any]]`.

- [ ] **Step 1: Write failing fan-out limit test**

Create 20 unique signal rows across sectors, patch `_load_tail_minute_bars_for_pick`, execute `_build_realtime_intraday_section`, and assert the tail loader receives no more than 15 distinct stock codes while the final result still honors `limit=10`.

- [ ] **Step 2: Verify the test is RED**

Run:

```bash
python3 -m unittest tests.test_realtime_info_service.RealtimeInfoServiceTests.test_tail_minutes_are_limited_before_fetch -v
```

Expected: FAIL because all 20 rows currently load 1-minute bars.

- [ ] **Step 3: Split row preparation from tail enrichment**

Build preliminary rows without 1-minute calls, sort them with the existing key, and keep the first 15. Enrich those rows with tail minutes, recalculate affected signal fields, then apply the same final sort and `limit`. Do not alter the existing status or score formulas.

- [ ] **Step 4: Run realtime section tests**

Run:

```bash
python3 -m unittest tests.test_realtime_info_service -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add realtime_info_service.py tests/test_realtime_info_service.py
git commit -m "perf: prune realtime tail-minute requests"
```

### Task 4: Top-level cache and visibly stale fallback

**Files:**
- Modify: `realtime_info_service.py`
- Modify: `app.py`
- Modify: `tests/test_realtime_info_service.py`
- Modify: `tests/test_realtime_info_api.py`

**Interfaces:**
- Changes: `build_realtime_info(now: datetime | None = None, limit: int = 10, force_refresh: bool = False) -> dict[str, Any]`.
- Changes: `/api/realtime-info?limit=10&force_refresh=false`.
- Produces response fields: `data_status`, `data_status_label`, `data_updated_at`, `stale_age_seconds`, `result_cache_hit`.

- [ ] **Step 1: Write failing top-level cache test**

Call `build_realtime_info` twice inside the same 30-second bucket with all expensive boundaries patched. Assert the second payload has `result_cache_hit is True` and the trade-date lookup, market sync, minute loader, and overnight builder each ran only for the first call.

- [ ] **Step 2: Verify cache test is RED**

Run:

```bash
python3 -m unittest tests.test_realtime_info_service.RealtimeInfoServiceTests.test_top_level_cache_precedes_market_sync -v
```

Expected: FAIL because market sync currently runs before the intraday cache.

- [ ] **Step 3: Implement top-level cache**

Add process-local state protected by a lock:

```python
_REALTIME_RESULT_CACHE: dict[tuple[int, str], tuple[float, dict[str, Any]]] = {}
_LAST_SUCCESSFUL_REALTIME_RESULT: dict[str, Any] | None = None

def _realtime_result_key(limit: int, now: datetime) -> tuple[int, str]:
    bucket = int(now.timestamp()) // 30
    return int(limit), str(bucket)
```

Check this cache at the first line of `build_realtime_info` unless `force_refresh=True`. Store only after both sections have been built and at least one section has non-empty stocks.

- [ ] **Step 4: Write failing stale-fallback tests**

First seed one successful result. Advance the clock, make trade date, sync, snapshot, and minute sources fail, and assert:

```python
self.assertEqual(result["data_status"], "stale")
self.assertEqual(result["data_status_label"], "备用缓存")
self.assertEqual(result["data_updated_at"], "2026-07-30 14:30:00")
self.assertGreater(result["stale_age_seconds"], 0)
```

Add a separate test proving that a process with no successful result returns `data_status == "unavailable"` and empty sections.

- [ ] **Step 5: Verify stale tests are RED**

Run the two new tests; expect missing status fields or an exception.

- [ ] **Step 6: Implement stale fallback**

Normalize fresh payloads to:

```python
{
    "data_status": "live",
    "data_status_label": "实时数据",
    "data_updated_at": current.isoformat(sep=" ", timespec="seconds"),
    "stale_age_seconds": 0,
}
```

On an empty/unavailable build, deep-copy `_LAST_SUCCESSFUL_REALTIME_RESULT`, change the status fields, retain its original `data_updated_at`, append a warning describing the live failure, and never overwrite the stored success. With no prior success, return structurally compatible empty `intraday` and `overnight` sections marked `unavailable`.

- [ ] **Step 7: Add API force-refresh contract test and implementation**

Update the API test to call:

```python
response = app.realtime_info(limit=10, force_refresh=True)
service.assert_called_once_with(limit=10, force_refresh=True)
```

Update the FastAPI handler to accept and forward `force_refresh`.

- [ ] **Step 8: Run focused backend tests**

Run:

```bash
python3 -m unittest \
  tests.test_realtime_info_service \
  tests.test_realtime_info_api -v
```

Expected: all tests PASS.

- [ ] **Step 9: Commit**

```bash
git add realtime_info_service.py app.py \
  tests/test_realtime_info_service.py tests/test_realtime_info_api.py
git commit -m "fix: cache realtime results and expose stale fallback"
```

### Task 5: Frontend cache state and manual refresh

**Files:**
- Modify: `quantClient/realtime-info-utils.js`
- Modify: `quantClient/realtime-info-utils.test.js`
- Modify: `quantClient/main.js`
- Modify: `quantClient/index.html`
- Modify: `quantClient/styles.css`

**Interfaces:**
- Consumes response fields from Task 4.
- Produces: `realtimeDataStatus(payload) -> { text: string, state: string, detail: string }`.
- Changes manual button request to `/realtime-info?limit=10&force_refresh=true`; timer requests keep `force_refresh=false`.

- [ ] **Step 1: Write failing status-format tests**

Add literal assertions:

```javascript
assert.deepEqual(
  realtimeDataStatus({
    data_status: 'stale',
    data_status_label: '备用缓存',
    data_updated_at: '2026-07-30 14:30:00',
    stale_age_seconds: 95,
  }),
  {
    text: '备用缓存',
    state: 'warning',
    detail: '数据时间 2026-07-30 14:30:00 · 已过期1分35秒',
  },
);
```

Also cover `live` and `unavailable`.

- [ ] **Step 2: Verify frontend test is RED**

Run:

```bash
node quantClient/realtime-info-utils.test.js
```

Expected: FAIL because `realtimeDataStatus` is not exported.

- [ ] **Step 3: Implement status formatting and UI**

Add `realtimeDataStatus` to the utility export. Render the returned text and detail beside the realtime refresh control. Add warning and unavailable styles that remain legible without changing the table layout.

Change `loadRealtimeInfo(showError = true, forceRefresh = false)` to append `&force_refresh=true` only for manual refresh. The 30-second timer calls it with `false`.

- [ ] **Step 4: Run frontend tests**

Run:

```bash
node quantClient/realtime-info-utils.test.js
node quantClient/morning-follow-utils.test.js
node quantClient/morning-follow-layout.test.js
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add quantClient/realtime-info-utils.js \
  quantClient/realtime-info-utils.test.js quantClient/main.js \
  quantClient/index.html quantClient/styles.css
git commit -m "feat: label stale realtime market data"
```

### Task 6: Proxy contract, instrumentation, and regression verification

**Files:**
- Modify: `quantServer/quantServer/src/main/java/com/codec/quantserver/controller/QuantController.java`
- Modify: `quantServer/quantServer/src/main/java/com/codec/quantserver/service/QuantPythonClient.java`
- Modify: `quantServer/quantServer/src/test/java/com/codec/quantserver/controller/QuantControllerTest.java`
- Modify: `realtime_info_service.py`

**Interfaces:**
- Consumes: `force_refresh` query parameter.
- Produces: per-request `performance` object with `market_sync_ms`, `intraday_60m_ms`, `tail_1m_ms`, `overnight_ms`, `minute_request_count`, `minute_cache_hit_count`, `provider_failure_count`, `used_stale_fallback`.

- [ ] **Step 1: Write failing Java proxy test**

Add a controller test that requests `/api/quant/realtime-info?limit=10&force_refresh=true`, verifies HTTP 200, and verifies the service receives both values.

- [ ] **Step 2: Verify Java test is RED**

Run:

```bash
cd quantServer/quantServer
mvn -Dtest=QuantControllerTest test
```

Expected: FAIL because the controller and Python client do not forward `force_refresh`.

- [ ] **Step 3: Forward force refresh**

Add a boolean request parameter with default `false` in `QuantController`, pass it into `QuantPythonClient`, and add `force_refresh` to the Python request URI.

- [ ] **Step 4: Add performance counters**

Measure stage boundaries with `time.perf_counter()`. Put counters only in the response/logging path; do not allow instrumentation failures to affect the result. Cached responses report their original build metrics and `result_cache_hit: true`.

- [ ] **Step 5: Run all regression tests**

Run:

```bash
python3 -m unittest discover -s tests -v
node quantClient/realtime-info-utils.test.js
node quantClient/morning-follow-utils.test.js
node quantClient/morning-follow-layout.test.js
cd quantServer/quantServer
mvn test
```

Expected: all Python, Node, and Maven tests PASS.

- [ ] **Step 6: Run a controlled smoke check**

Start the Python service or invoke `build_realtime_info(limit=10, force_refresh=True)` in the configured environment. Record total duration, `data_status`, selected sources, warning count, and the `performance` object. Invoke it again without force and verify cache hit and materially lower latency.

- [ ] **Step 7: Commit**

```bash
git add realtime_info_service.py \
  quantServer/quantServer/src/main/java/com/codec/quantserver/controller/QuantController.java \
  quantServer/quantServer/src/main/java/com/codec/quantserver/service/QuantPythonClient.java \
  quantServer/quantServer/src/test/java/com/codec/quantserver/controller/QuantControllerTest.java
git commit -m "perf: instrument realtime refresh pipeline"
```
