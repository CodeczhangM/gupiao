# Realtime Tail Premium Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade only the realtime-information overnight block into an explainable 14:50–15:00 TOP20 premium selector and make both automatic and forced realtime refresh obey bounded freshness rules.

**Architecture:** Add a pure `tail_premium_scoring.py` factor/score engine and a realtime-only orchestration service that prepares daily, sector, limit-up, 60-minute, and tail-minute inputs. `realtime_info_service.py` calls the new orchestration path while the standalone morning-follow and legacy overnight endpoints remain unchanged. Carry `force_refresh` through every realtime cache layer and validate database results by market phase and actual data cutoff.

**Tech Stack:** Python 3, pandas, FastAPI, existing Tushare/Eastmoney/Sina fallback chain, MySQL cache, Vue 2 browser client, Python `unittest`, Node `assert`, Maven tests.

## Global Constraints

- Do not modify `morning_follow_service.py`, `/api/morning-follow-monitor`, or the outer “次日早盘跟进” table.
- Do not change the standalone legacy `/api/overnight-monitor` behavior unless a shared low-level helper receives a backward-compatible optional argument.
- Opening-auction return means current trade day's `open / pre_close - 1`; it is not the 14:57 closing auction.
- MACD always uses global settings, default 5/34/5, and includes MACD provenance in results.
- All amount comparisons normalize to yuan before applying the 50 million yuan liquidity floor.
- Never label prior-day snapshot data as current-day live data.
- Empty/failed refreshes never overwrite the last successful database result.
- Follow red-green-refactor for every behavior change and commit only task-scoped source/test files.
- Preserve unrelated generated files and existing untracked documents in the dirty worktree.

---

### Task 1: Pure daily-factor and eligibility engine

**Files:**
- Create: `tail_premium_scoring.py`
- Create: `tests/test_tail_premium_scoring.py`

**Interfaces:**
- Produces: `normalize_amount_yuan(...)`, `build_daily_factor_frame(market, history, trade_date, macd_settings=None)`, and `eligible_tail_universe(factors)`.
- Consumes: current market rows, at least 60 historical daily rows per symbol where available, and `indicator_settings.calculate_macd(...)`.

- [ ] **Step 1: Write failing amount-normalization tests**

Cover current snapshots whose `amount` is already yuan and cached Tushare daily rows whose amount is in thousand yuan. Require an explicit unit/source hint instead of magnitude-only guessing whenever metadata is available.

- [ ] **Step 2: Write failing eligibility tests**

Build fixtures for:

- normal liquid stock;
- `ST`, `*ST`, and `退市` names;
- zero-volume/suspended stock;
- five-day average amount below 50 million yuan;
- `close < MA60` with declining MA60;
- `close < MA60` with rising MA60;
- fewer than 60 history rows.

Assert only ST/suspended/illiquid and confirmed long-term downtrend rows are excluded. Insufficient MA60 history remains with `history_quality="insufficient"` and cannot receive full trend points.

- [ ] **Step 3: Verify RED**

Run:

```bash
python3 -m unittest tests.test_tail_premium_scoring.TailPremiumScoringTests -v
```

Expected: FAIL because `tail_premium_scoring` does not exist.

- [ ] **Step 4: Implement daily factors**

Compute per symbol in batch/grouped form:

- MA5, MA10, MA20, MA60 and prior MA60;
- `return20`, 60-day high and `high_position_60`;
- five-day average amount in yuan;
- current/prior volume and price-change relationships;
- MACD DIF, DEA, histogram, golden-cross and above-zero flags using global settings;
- upper-shadow ratio with zero-range protection.

Merge the latest factor row back into the current snapshot without replacing current live price/volume fields with historical values.

- [ ] **Step 5: Implement eligibility reasons**

Return both eligible rows and machine-readable exclusion/data-quality fields:

```python
{
    "eligible_tail_premium": True,
    "exclusion_reasons": [],
    "data_quality_warnings": [],
}
```

Use vectorized filters where practical and stable `ts_code` ordering.

- [ ] **Step 6: Verify GREEN**

Run:

```bash
python3 -m unittest tests.test_tail_premium_scoring -v
```

Expected: all factor and eligibility tests PASS.

- [ ] **Step 7: Commit**

```bash
git add tail_premium_scoring.py tests/test_tail_premium_scoring.py
git commit -m "feat: calculate realtime tail premium factors"
```

---

### Task 2: Weighted score and risk model

**Files:**
- Modify: `tail_premium_scoring.py`
- Modify: `tests/test_tail_premium_scoring.py`

**Interfaces:**
- Produces: `score_tail_premium_row(row) -> dict` and `rank_tail_premium_candidates(frame, limit=20)`.
- Output keys: `premium_score`, six `*_score` fields, `risk_score`, `risk_level`, `buy_reasons`, `risk_items`, and `next_day_plan`.

- [ ] **Step 1: Write failing tail-score boundary tests**

Test exact values immediately below, on, and above:

- tail returns 0%, 0.5%, and 1%;
- opening-auction returns -0.1%, 0.1%, and 0.3%;
- close positions 50%, 75%, and 90%;
- tail-volume ratios 1.2, 2.0, and 3.0 with both rising and falling tails.

Assert the raw subscore is normalized and capped to the 35-point tail module.

- [ ] **Step 2: Write failing module tests**

Independently verify:

- 20-day/recent-five-day/consecutive limit-up scoring and quality flags;
- sector score from change, rank, breadth, strong count, and limit count;
- MA alignment, above-MA20, MACD golden cross, and above-zero scoring;
- volume-ratio bands and price-volume confirmation;
- return20 and 60-day-high position bands.

- [ ] **Step 3: Write failing risk and aggregate tests**

Cover high-position giant bearish volume, volume-price stagnation, long upper shadow, turnover/position risk, over-hot tail and volume. Assert:

```text
premium_score =
  tail + limit + sector + trend + volume + position - risk
```

with final 0–100 clipping. Verify risk items are deduplicated and reason order is deterministic.

- [ ] **Step 4: Verify RED**

Run:

```bash
python3 -m unittest tests.test_tail_premium_scoring -v
```

Expected: new score tests FAIL because scoring functions are absent.

- [ ] **Step 5: Implement score tables as named constants**

Keep thresholds and maximum module weights in one immutable configuration mapping. Return numeric component scores rather than embedding only display strings.

Use the approved next-day default plan:

- target profit: +3%;
- stop loss: -3%;
- high open above 3%: realize at least half;
- failure to hold intraday average: reduce/exit.

- [ ] **Step 6: Implement deterministic ranking**

Sort by:

1. `premium_score` descending;
2. `tail_score` descending;
3. `sector_score` descending;
4. normalized amount descending;
5. `ts_code` ascending.

Return at most 20 rows by default.

- [ ] **Step 7: Verify GREEN**

Run:

```bash
python3 -m unittest tests.test_tail_premium_scoring -v
```

Expected: all scoring tests PASS.

- [ ] **Step 8: Commit**

```bash
git add tail_premium_scoring.py tests/test_tail_premium_scoring.py
git commit -m "feat: score tail premium candidates"
```

---

### Task 3: Realtime-only premium monitor orchestration

**Files:**
- Create: `realtime_tail_premium_service.py`
- Create: `tests/test_realtime_tail_premium_service.py`
- Modify: `realtime_info_service.py`
- Modify: `tests/test_realtime_info_service.py`

**Interfaces:**
- Produces: `build_realtime_tail_premium_monitor(...) -> dict`.
- Consumes: shared market/history frames, request-scoped `minute_loader`, sector-potential rows, current time, trade date, and the scoring engine.
- Replaces only the `build_overnight_monitor(...)` call inside realtime information.

- [ ] **Step 1: Write failing time-state tests**

Assert:

- before 14:50: `selection_state="waiting_tail_window"` and no row is labeled as a final tail buy;
- 14:50–15:00: `selection_state="live_tail_window"`;
- after 15:00: `selection_state="closed_final"`;
- `data_as_of` equals the latest usable minute bar time.

- [ ] **Step 2: Write failing integration fixture**

Build a small market/history/minute fixture with:

- one high-scoring eligible stock;
- one ST stock;
- one illiquid stock;
- one confirmed MA60 downtrend;
- one eligible but risky stock.

Assert the response contains the eligible stocks only, has all component scores and details, and uses the approved opening-auction calculation.

- [ ] **Step 3: Write failing realtime wiring test**

Patch `realtime_info_service.build_realtime_tail_premium_monitor` and assert `_build_realtime_info_uncached` passes shared market/history/minute loader and places the response under `overnight`. Assert the legacy `build_overnight_monitor` is not called by realtime information.

- [ ] **Step 4: Verify RED**

Run:

```bash
python3 -m unittest \
  tests.test_realtime_tail_premium_service \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_realtime_info_uses_tail_premium_monitor -v
```

Expected: FAIL because the realtime-only service and wiring do not exist.

- [ ] **Step 5: Implement bounded candidate preparation**

1. Build all-market daily factors and eligibility.
2. Merge existing sector potential fields.
3. Pre-rank eligible rows by inexpensive daily/snapshot factors.
4. Limit expensive minute loading to a bounded pool large enough to produce 20 final rows.
5. Reuse the request-scoped minute loader for 60-minute and 14:25-to-current 1-minute windows.
6. Derive tail return, close position, tail volume ratio, and 60-minute signal fields.
7. Score, sort, and return TOP20.

Keep per-symbol minute failures isolated. A failed symbol gets a warning and cannot receive unavailable minute-factor points; one failure must not abort the whole response.

- [ ] **Step 6: Merge limit-up and sector fields**

Prefer explicit existing limit-up data where available. Otherwise derive conservative daily limit-up counts from historical percentage/price-limit information and label the derivation. Never invent `limit_time` or open-count values.

Reuse `rank_sector_potential(...)` output for sector metrics and map by industry.

- [ ] **Step 7: Implement realtime-info integration**

Change the realtime build to call only the new service for its `overnight` section. Keep `intraday` construction and outer morning-follow service behavior unchanged.

- [ ] **Step 8: Verify GREEN**

Run:

```bash
python3 -m unittest \
  tests.test_tail_premium_scoring \
  tests.test_realtime_tail_premium_service \
  tests.test_realtime_info_service -v
```

Expected: all focused tests PASS.

- [ ] **Step 9: Commit**

```bash
git add realtime_tail_premium_service.py tail_premium_scoring.py \
  realtime_info_service.py tests/test_realtime_tail_premium_service.py \
  tests/test_realtime_info_service.py
git commit -m "feat: add realtime tail premium monitor"
```

---

### Task 4: Database-result freshness

**Files:**
- Modify: `realtime_info_service.py`
- Modify: `tests/test_realtime_info_service.py`

**Interfaces:**
- Changes: `_load_database_realtime_result(limit, now)` validates trade date, `updated_at`, and `data_as_of`.
- Produces: `_realtime_result_max_age_seconds(now)` and explicit cache rejection/freshness metadata.

- [ ] **Step 1: Write failing database-age tests**

Freeze current time during an open trading session and assert:

- a result updated 10 seconds ago is returned;
- a result updated 31 seconds ago is rejected and causes a fresh build;
- a result whose trade date is yesterday is rejected as a live fast path;
- a post-close result with final `data_as_of` for today remains reusable;
- during lunch, a result through 11:30 remains valid under the lunch cutoff rule.

- [ ] **Step 2: Verify RED**

Run:

```bash
python3 -m unittest \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_database_result_expires_during_trading \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_database_result_rejects_prior_trade_date \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_final_post_close_result_remains_reusable -v
```

Expected: FAIL because database results currently have no age/date validation.

- [ ] **Step 3: Implement phase-aware validation**

Use `updated_at` for cache computation age and `data_as_of` for market-data age:

- active trading: maximum result age 25 seconds and expected data cutoff tolerance;
- lunch: accept the last 11:30 cutoff without requiring natural-time freshness;
- pre-open: accept the latest completed trade-day result only as a labeled non-current cache;
- post-close: accept today's complete result;
- historical/non-current payload: never return with `data_status="live"`.

If timestamps cannot be parsed, reject the fast path safely.

- [ ] **Step 4: Preserve stale fallback separately**

Fast-path rejection must not delete the cache. If all live providers fail later, the same record remains eligible as a stale fallback with the original timestamps and computed age.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
python3 -m unittest tests.test_realtime_info_service -v
```

Expected: all realtime-info cache tests PASS.

- [ ] **Step 6: Commit**

```bash
git add realtime_info_service.py tests/test_realtime_info_service.py
git commit -m "fix: expire realtime database results during trading"
```

---

### Task 5: Force-refresh propagation through all cache layers

**Files:**
- Modify: `realtime_info_service.py`
- Modify: `overnight_monitor_service.py`
- Modify: `realtime_market_source.py`
- Modify: `tests/test_realtime_info_service.py`
- Modify: `tests/test_overnight_monitor_service.py`
- Modify: `tests/test_realtime_market_source.py`

**Interfaces:**
- Carries: `force_refresh` from `build_realtime_info` through `_build_realtime_info_uncached`, `_build_realtime_intraday_section`, the request-scoped loader, `_persistent_minute_result`, and current-day primary/fallback fetches.
- Backward compatibility: every new parameter defaults to `False`.

- [ ] **Step 1: Write failing propagation test**

Patch each layer with a recording fake and call:

```python
build_realtime_info(
    now=datetime(2026, 7, 31, 14, 52),
    limit=20,
    force_refresh=True,
)
```

Assert:

- final memory/database results are bypassed;
- `_REALTIME_INTRADAY_RESULT_CACHE` is bypassed;
- current-day one-minute database cache is bypassed;
- provider fetch is attempted;
- refreshed bars are upserted;
- historical sealed bars may still be reused.

- [ ] **Step 2: Write failing non-force regression test**

Assert a normal request still reuses a genuinely fresh database result and a fresh minute window to meet the latency goal.

- [ ] **Step 3: Verify RED**

Run:

```bash
python3 -m unittest \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_force_refresh_reaches_current_day_minute_provider \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_normal_refresh_reuses_fresh_minute_cache -v
```

Expected: force test FAIL because the flag currently stops at the top-level result cache.

- [ ] **Step 4: Implement force-aware loaders**

- Add `force_refresh=False` to low-level helpers.
- Skip only mutable current-day cache entries when forced.
- Keep sealed historical data reusable.
- Clear/request-bypass provider response caches for the explicit force request without disabling the provider failure circuit breaker.
- Ensure the per-request deduplication cache still prevents duplicate calls inside the same forced build.

- [ ] **Step 5: Bypass derived result caches**

Do not read the intraday/overnight derived caches during a forced build. Store the successfully rebuilt result afterward so subsequent normal requests are fast.

- [ ] **Step 6: Verify GREEN**

Run:

```bash
python3 -m unittest \
  tests.test_realtime_market_source \
  tests.test_overnight_monitor_service \
  tests.test_realtime_info_service -v
```

Expected: all source, loader, and service tests PASS.

- [ ] **Step 7: Commit**

```bash
git add realtime_info_service.py overnight_monitor_service.py \
  realtime_market_source.py tests/test_realtime_info_service.py \
  tests/test_overnight_monitor_service.py tests/test_realtime_market_source.py
git commit -m "fix: force realtime refresh through minute caches"
```

---

### Task 6: Realtime TOP20 API contract

**Files:**
- Modify: `tests/test_realtime_info_api.py`
- Modify: `tests/test_realtime_info_service.py`
- Modify only if required: `app.py`

**Interfaces:**
- Existing endpoint: `GET /api/realtime-info?limit=20&force_refresh=<bool>`.
- `overnight.stocks` includes the detailed premium fields and never exceeds `limit`.

- [ ] **Step 1: Write failing API contract test**

Assert:

- `limit=20` is forwarded;
- `force_refresh=true` is forwarded;
- response exposes `data_as_of`, `cache_source`, `selection_state`, scoring version, MACD provenance, component scores, reasons, risks, and plan;
- no row contains non-finite JSON numbers.

- [ ] **Step 2: Verify RED**

Run:

```bash
python3 -m unittest tests.test_realtime_info_api -v
```

Expected: detailed premium contract test FAIL.

- [ ] **Step 3: Complete response metadata**

Add a dedicated score version such as `tail-premium-v1` plus the MACD parameter key. Keep existing top-level realtime metadata backward compatible.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
python3 -m unittest \
  tests.test_realtime_info_api \
  tests.test_realtime_info_service \
  tests.test_realtime_tail_premium_service -v
```

Expected: all API/service tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py realtime_info_service.py realtime_tail_premium_service.py \
  tests/test_realtime_info_api.py tests/test_realtime_info_service.py \
  tests/test_realtime_tail_premium_service.py
git commit -m "feat: expose tail premium realtime results"
```

---

### Task 7: Realtime-information UI and details

**Files:**
- Modify: `quantClient/index.html`
- Modify: `quantClient/main.js`
- Modify: `quantClient/styles.css`
- Modify: `quantClient/realtime-info-utils.js`
- Modify: `quantClient/realtime-info-utils.test.js`
- Create: `quantClient/realtime-tail-premium-layout.test.js`

**Interfaces:**
- Changes realtime client request to `limit=20`.
- Adds compact score summary and expandable details only inside realtime information.

- [ ] **Step 1: Write failing utility tests**

Add pure utility tests for:

- selection-state text before/during/after the tail window;
- premium/risk badge state;
- component-score summary;
- stale data cutoff and cache-age text;
- array/string normalization for reasons and risks.

- [ ] **Step 2: Write failing layout test**

Parse `index.html` as text and assert:

- realtime “盘末隔夜溢价 TOP20” title exists;
- core columns exist;
- expandable details contain six module scores, indicators, reasons, risks, and next-day plan;
- the outer “次日早盘跟进” markup and endpoint reference remain present and unchanged in behavior.

- [ ] **Step 3: Verify RED**

Run:

```bash
node quantClient/realtime-info-utils.test.js
node quantClient/realtime-tail-premium-layout.test.js
```

Expected: new utility/layout assertions FAIL.

- [ ] **Step 4: Implement client request and row expansion**

- Request `/realtime-info?limit=20`.
- Keep the existing 30-second timer.
- Rename only the realtime overnight heading.
- Show compact columns: stock, sector, price/change, premium score, tail score, volume/turnover, tail signal, risk, and action.
- Add an expand button and a second detail row for all remaining fields.
- Use CSS wrapping/minimum widths so reasons and metrics are readable without truncating every column.

- [ ] **Step 5: Make data time explicit**

Display:

- actual screening trade date;
- actual `data_as_of`;
- live/fresh/database/stale state;
- stale age when applicable;
- “14:50前预观察” versus “盘末动态候选” versus “收盘最终结果”.

- [ ] **Step 6: Verify GREEN**

Run:

```bash
node --check quantClient/main.js
node --check quantClient/realtime-info-utils.js
node quantClient/realtime-info-utils.test.js
node quantClient/realtime-tail-premium-layout.test.js
node quantClient/morning-follow-utils.test.js
node quantClient/morning-follow-layout.test.js
```

Expected: all frontend checks PASS.

- [ ] **Step 7: Commit**

```bash
git add quantClient/index.html quantClient/main.js quantClient/styles.css \
  quantClient/realtime-info-utils.js quantClient/realtime-info-utils.test.js \
  quantClient/realtime-tail-premium-layout.test.js
git commit -m "feat: show realtime tail premium top20"
```

---

### Task 8: Regression, performance, and deployment verification

**Files:**
- Modify if behavior changed: `DEPLOY_SERVER.md`
- Test only: all relevant suites

- [ ] **Step 1: Run focused Python tests**

```bash
python3 -m unittest \
  tests.test_tail_premium_scoring \
  tests.test_realtime_tail_premium_service \
  tests.test_realtime_info_service \
  tests.test_realtime_info_api \
  tests.test_realtime_market_source \
  tests.test_overnight_monitor_service \
  tests.test_morning_follow_service \
  tests.test_morning_follow_api -v
```

Expected: all focused tests PASS.

- [ ] **Step 2: Run full Python suite**

```bash
python3 -m unittest discover -s tests -v
```

Expected: all Python tests PASS.

- [ ] **Step 3: Run frontend suite**

```bash
node quantClient/realtime-info-utils.test.js
node quantClient/realtime-tail-premium-layout.test.js
node quantClient/morning-follow-utils.test.js
node quantClient/morning-follow-layout.test.js
node quantClient/free-review-utils.test.js
node quantClient/free-review-layout.test.js
```

Expected: all available frontend tests PASS. If a listed legacy file does not exist, record that fact and run every discovered `quantClient/*.test.js` file instead.

- [ ] **Step 4: Run Spring regression**

```bash
cd quantServer/quantServer
mvn test
```

Expected: Spring proxy tests PASS; no route changes are required.

- [ ] **Step 5: Verify boundaries and cache semantics**

```bash
rg -n "morning-follow-monitor|limit=20|force_refresh|premium_score|selection_state" \
  app.py realtime_info_service.py realtime_tail_premium_service.py \
  quantClient/main.js quantClient/index.html
git diff --check
git status --short
```

Confirm:

- external morning-follow code was not modified;
- realtime client requests 20;
- normal live refresh expires old result caches;
- force refresh reaches current-day minute providers;
- old generated/untracked files remain untouched.

- [ ] **Step 6: Optional live smoke test**

When provider credentials and services are available:

```bash
curl --fail \
  'http://127.0.0.1:8000/api/realtime-info?limit=20&force_refresh=true'
curl --fail \
  'http://127.0.0.1:8081/api/quant/realtime-info?limit=20&force_refresh=true'
```

Validate that `data_as_of` advances, rows are at most 20, and stale data is labeled rather than presented as live. If services or credentials are unavailable, report the smoke test as not run rather than claiming it passed.

- [ ] **Step 7: Update deployment notes only if needed**

If no configuration/schema/command changed, leave `DEPLOY_SERVER.md` untouched. If a new runtime requirement is introduced, add only the exact upgrade and verification commands.

- [ ] **Step 8: Final task commit if documentation changed**

```bash
git add DEPLOY_SERVER.md
git commit -m "docs: verify realtime tail premium deployment"
```

Skip this commit when the deployment document is unchanged.

