# Market Data Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cache all scan market inputs in MySQL, bootstrap 120 trading days, incrementally refresh missing/current dates, and make scans read the latest 100 complete cached days.

**Architecture:** Add a focused `market_cache.py` repository/synchronizer between the existing Tushare adapter and strategy-facing DataFrame functions. Preserve the public functions in `data_service.py`, add cache administration endpoints to FastAPI and Spring, and fall back to the latest complete cache window when remote refresh fails.

**Tech Stack:** Python 3.10, pandas, PyMySQL, FastAPI, unittest/mock, Java 17, Spring Boot RestClient, Maven.

## Global Constraints

- Cache `daily`, `daily_basic`, `stock_basic`, and `moneyflow_ind_dc`.
- Bootstrap exactly 120 recent trading days by default; require 100 complete days by default.
- Before 15:30 Asia/Shanghai, refresh the current trading day on every scan; after 15:30 reuse a complete current-day cache.
- Historical complete dates are not refreshed unless forced.
- Preserve existing strategy-facing DataFrame schemas and direct-fetch fallback when `MARKET_CACHE_ENABLED=false`.
- Do not overwrite or revert unrelated dirty-worktree changes.

---

### Task 1: MySQL cache repository

**Files:**
- Create: `market_cache.py`
- Create: `tests/test_market_cache.py`
- Modify: `database.py`

**Interfaces:**
- Consumes: `database.get_connection()`.
- Produces: `init_market_cache()`, `replace_daily_source(source_name, trade_date, frame)`, `load_market_snapshot(trade_date)`, `load_recent_daily(end_trade_date, n)`, `load_moneyflow(trade_date)`, `get_complete_dates(limit)`, and `get_cache_status()`.

- [ ] **Step 1: Write failing repository tests**

Add tests that mock `get_connection()` and verify table initialization creates all five tables, replacement deletes and batch-upserts inside one connection context, and read functions return DataFrames with expected columns.

- [ ] **Step 2: Verify repository tests fail**

Run: `env HOME=/tmp python3 -m unittest -v tests.test_market_cache`

Expected: import failure because `market_cache.py` does not exist.

- [ ] **Step 3: Implement schema and repository operations**

Create the normalized tables from the design with composite primary keys and implement parameterized `executemany` writes. Convert pandas null values to `None`; mark `market_cache_sync.status='complete'` only after data writes succeed in the same transaction context.

- [ ] **Step 4: Verify repository tests pass**

Run: `env HOME=/tmp python3 -m unittest -v tests.test_market_cache`

Expected: all repository tests pass.

### Task 2: Incremental synchronization policy

**Files:**
- Modify: `market_cache.py`
- Modify: `tests/test_market_cache.py`
- Modify: `.env.example`

**Interfaces:**
- Consumes: a fetch callback compatible with `_query_tushare(api_name: str, **kwargs) -> pandas.DataFrame` and a trading-date callback.
- Produces: `sync_market_cache(fetcher, trade_date_loader, force_current=False, now=None) -> dict` and `ensure_market_cache(fetcher, trade_date_loader, now=None) -> dict`.

- [ ] **Step 1: Write failing policy tests**

Cover empty-cache 120-day bootstrap, one missing date, pre-15:30 current-date refresh, post-15:30 reuse, non-trading day behavior, remote failure status, and process-lock duplicate prevention. Use injected `now` values in `Asia/Shanghai` for deterministic tests.

- [ ] **Step 2: Verify policy tests fail**

Run: `env HOME=/tmp python3 -m unittest -v tests.test_market_cache`

Expected: failures because synchronization functions are absent.

- [ ] **Step 3: Implement synchronization coordinator**

Read and validate `MARKET_CACHE_BOOTSTRAP_DAYS`, `MARKET_CACHE_REQUIRED_DAYS`, and `MARKET_CACHE_ENABLED`. Determine missing dates from sync records, fetch outside transactions, write each source/date atomically, refresh `stock_basic` when new dates are synchronized, and return `cache_updated`, `data_trade_date`, and `cache_warnings`.

- [ ] **Step 4: Verify policy tests pass**

Run: `env HOME=/tmp python3 -m unittest -v tests.test_market_cache`

Expected: all cache repository and synchronization tests pass.

### Task 3: Strategy-facing cached data reads

**Files:**
- Modify: `data_service.py`
- Modify: `quant_service.py`
- Modify: `tests/test_advantage_stock_scoring.py`
- Modify: `tests/test_stock_detail_service.py`

**Interfaces:**
- Consumes: cache synchronization and repository functions from Tasks 1–2.
- Produces: unchanged `get_market_data()`, `get_recent_daily_data(end_trade_date, n)`, `get_sector_data(trade_date)`, and `get_moneyflow_summary(trade_date, limit)` contracts; `run_quant_scan()` adds cache metadata.

- [ ] **Step 1: Write failing integration tests**

Assert enabled cache mode synchronizes once, reads current snapshot/history/moneyflow from MySQL, does not call direct range history fetching, and includes `data_trade_date`, `cache_updated`, and `cache_warnings` in reports. Assert disabled cache mode preserves existing calls.

- [ ] **Step 2: Verify integration tests fail**

Run: `env HOME=/tmp python3 -m unittest -v tests.test_advantage_stock_scoring tests.test_stock_detail_service`

Expected: cache-mode assertions fail.

- [ ] **Step 3: Route existing data functions through cache**

Keep `_query_tushare` as the remote adapter. Add a single scan preparation call, then load the market snapshot and recent 100-day history from cache. Preserve main-board filtering and output columns. Use the latest complete date with warnings after refresh failure when at least 100 days exist.

- [ ] **Step 4: Verify Python service integration tests pass**

Run: `env HOME=/tmp python3 -m unittest -v tests.test_advantage_stock_scoring tests.test_stock_detail_service`

Expected: all selected tests pass.

### Task 4: Cache administration API

**Files:**
- Modify: `app.py`
- Create: `tests/test_market_cache_api.py`
- Modify: `quantServer/quantServer/src/main/java/com/codec/quantserver/service/QuantPythonClient.java`
- Modify: `quantServer/quantServer/src/main/java/com/codec/quantserver/controller/QuantController.java`
- Modify: `quantServer/quantServer/src/test/java/com/codec/quantserver/controller/QuantControllerTest.java`

**Interfaces:**
- Produces: `POST /api/cache/sync`, `GET /api/cache/status`, `POST /api/quant/cache/sync`, and `GET /api/quant/cache/status`.

- [ ] **Step 1: Write failing FastAPI and Spring controller tests**

Test successful manual sync/status responses, `force_current` forwarding, and Spring controller delegation to `QuantPythonClient`.

- [ ] **Step 2: Verify API tests fail**

Run: `env HOME=/tmp python3 -m unittest -v tests.test_market_cache_api`

Run: `mvn -Dtest=QuantControllerTest test` from `quantServer/quantServer`.

Expected: missing-route failures.

- [ ] **Step 3: Implement API routes and forwarding**

Add FastAPI route handlers around cache service functions with logged 500 errors. Add RestClient methods and Spring mappings without changing existing request paths.

- [ ] **Step 4: Verify API tests pass**

Run both commands from Step 2.

Expected: all API tests pass.

### Task 5: Documentation and full verification

**Files:**
- Modify: `README_BACKEND.md`
- Modify: `DEPLOY_SERVER.md`

**Interfaces:**
- Documents initial sync, configuration, status inspection, manual synchronization, and restart behavior.

- [ ] **Step 1: Document configuration and operations**

Add the three environment variables, explain that first scan fills 120 days, show curl examples for status/manual sync, and state that subsequent scans fetch only missing/current dates.

- [ ] **Step 2: Run full Python verification**

Run: `env HOME=/tmp python3 -m unittest discover -s tests -p 'test_*.py'`

Expected: zero failures and zero errors.

- [ ] **Step 3: Run full Spring verification**

Run: `mvn test` from `quantServer/quantServer`.

Expected: `BUILD SUCCESS` with zero failures and errors.

- [ ] **Step 4: Check patch integrity**

Run: `git diff --check`

Expected: no output.
