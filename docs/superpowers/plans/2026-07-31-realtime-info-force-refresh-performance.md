# Realtime Info Force Refresh Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce `/api/realtime-info?force_refresh=true` latency without returning stale whole-result cache.

**Architecture:** Keep the synchronous FastAPI contract. Optimize the slow path by making forced minute refresh incremental, adding Tushare primary-source circuit breaking, and parallelizing tail 1-minute reads with stable result ordering.

**Tech Stack:** Python 3, pandas, `ThreadPoolExecutor`, unittest/mock, existing FastAPI service modules.

## Global Constraints

- Do not change `/api/realtime-info` request or response shape.
- Do not return old whole-result cache for `force_refresh=true`.
- Keep minute fetch concurrency bounded at 4 workers.
- Preserve ranking and display order after concurrent work completes.

---

### Task 1: Incremental Forced Minute Refresh

**Files:**
- Modify: `tests/test_realtime_info_service.py`
- Modify: `realtime_info_service.py`

**Interfaces:**
- Consumes: `_persistent_minute_result(ts_code, start_datetime, end_datetime, freq, trade_date, now, force_refresh=False)`
- Produces: forced refresh reads existing minute cache, requests only the missing range, and returns combined bars.

- [ ] **Step 1: Write failing test**

Add a test that patches `load_minute_cache` to return rows through `14:49`, calls `_persistent_minute_result(..., end_datetime="14:50", force_refresh=True)`, and asserts `_minute_result_with_1459_fallback` receives `start_datetime="2026-07-30 14:50:00"` and the returned frame contains old plus refreshed rows.

- [ ] **Step 2: Run red test**

Run: `python -m unittest tests.test_realtime_info_service.RealtimeInfoServiceTests.test_force_refresh_incrementally_updates_database_minutes`

- [ ] **Step 3: Implement minimal code**

Change `_persistent_minute_result` so `force_refresh=True` still loads cached minutes, computes `fetch_start` with `minute_cache_next_fetch_start`, fetches only the missing range, saves new rows, and combines cached plus loaded rows.

- [ ] **Step 4: Run green test**

Run the same unittest target and confirm it passes.

### Task 2: Tushare Minute Circuit Breaker

**Files:**
- Modify: `tests/test_realtime_market_source.py`
- Modify: `realtime_market_source.py`

**Interfaces:**
- Consumes: `load_minutes_with_fallback(..., primary_loader)`
- Produces: repeated Tushare primary failures open a short circuit shared across symbols.

- [ ] **Step 1: Write failing test**

Add a test where the primary loader always raises, fallback loaders return empty frames, and three symbols are requested at the same clock time. Assert primary loader was called only twice and the third result warning says Tushare is circuit-open.

- [ ] **Step 2: Run red test**

Run: `python -m unittest tests.test_realtime_market_source.RealtimeMarketSourceTests.test_tushare_primary_failure_opens_circuit_for_other_symbols`

- [ ] **Step 3: Implement minimal code**

Use existing provider health helpers for a `tushare_minutes` provider around the primary loader path in `load_minutes_with_fallback`.

- [ ] **Step 4: Run green test**

Run the same unittest target and confirm it passes.

### Task 3: Concurrent Tail 1-Minute Reads

**Files:**
- Modify: `tests/test_realtime_info_service.py`
- Modify: `realtime_info_service.py`

**Interfaces:**
- Consumes: `_build_realtime_intraday_section(...)`
- Produces: tail 1-minute loading uses at most 4 workers and returns rows in the same sorted order as before.

- [ ] **Step 1: Write failing test**

Add a test that forces several preliminary rows, makes the 1-minute loader block briefly, records max concurrency, and asserts max concurrency is between 2 and 4 while result row order remains expected.

- [ ] **Step 2: Run red test**

Run: `python -m unittest tests.test_realtime_info_service.RealtimeInfoServiceTests.test_tail_minutes_load_with_bounded_parallelism_and_stable_order`

- [ ] **Step 3: Implement minimal code**

Replace the serial tail-minute loop with a bounded `ThreadPoolExecutor(max_workers=4)` and merge results back by `ts_code` or candidate index.

- [ ] **Step 4: Run green test**

Run the same unittest target and confirm it passes.

### Task 4: Verification

**Files:**
- Test: `tests/test_realtime_info_service.py`
- Test: `tests/test_realtime_market_source.py`

- [ ] **Step 1: Run focused tests**

Run: `python -m unittest tests.test_realtime_info_service tests.test_realtime_market_source`

- [ ] **Step 2: Smoke test endpoint**

Run: `curl -sS -o /tmp/realtime_force.json -w 'HTTP %{http_code} time_total=%{time_total}\n' --max-time 45 'http://127.0.0.1:8000/api/realtime-info?limit=20&force_refresh=true'`

- [ ] **Step 3: Review diff**

Run: `git diff -- realtime_info_service.py realtime_market_source.py tests/test_realtime_info_service.py tests/test_realtime_market_source.py`
