# Morning Follow Market Snapshot Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent the next-morning-follow endpoint from returning 502 when the requested day’s Tushare market snapshot is not ready.

**Architecture:** Reuse the shared Eastmoney market snapshot loader after the existing Tushare sync attempt, validate that the fallback snapshot can support the existing filters, and finally fall back to the nearest prior complete local snapshot. Carry requested and actual dates separately so downstream minute windows and confirmation dates always match the actual candidate pool.

**Tech Stack:** Python 3, pandas, FastAPI, Vue 2, Python `unittest`, Node `assert`.

## Global Constraints

- Do not change existing next-morning-follow filter thresholds, scoring, sorting, or plan copy.
- Prefer same-day local/Tushare data, then same-day Eastmoney data, then the nearest prior complete local snapshot.
- A prior-day result must be labeled `备用缓存` and `非当日`.
- Only return 502 when all three market input paths are unavailable.
- Downstream daily and minute calculations use the actual `candidate_trade_date`.

---

### Task 1: Same-day Eastmoney snapshot fallback

**Files:**
- Modify: `morning_follow_service.py`
- Test: `tests/test_morning_follow_service.py`

**Interfaces:**
- Consumes: `realtime_market_source.load_eastmoney_market_snapshot(trade_date) -> tuple[pd.DataFrame, str | None]`.
- Produces: `_follow_snapshot_is_usable(market: pd.DataFrame, trade_date: str) -> bool`.

- [ ] **Step 1: Write the failing same-day fallback test**

Add a test where the first local snapshot and the post-sync snapshot are empty, while Eastmoney returns a complete qualifying row:

```python
@patch("morning_follow_service.load_eastmoney_market_snapshot")
@patch("morning_follow_service.load_recent_daily", return_value=pd.DataFrame())
@patch("morning_follow_service.load_market_snapshot")
@patch("morning_follow_service.sync_cached_market_data")
@patch("morning_follow_service.get_trade_dates")
def test_follow_inputs_use_eastmoney_when_today_snapshot_stays_empty(
    self, trade_dates, sync_market, load_snapshot, _history, eastmoney
):
    trade_dates.return_value = ["20260731", "20260730", "20260729"]
    load_snapshot.side_effect = [pd.DataFrame(), pd.DataFrame()]
    eastmoney.return_value = (pd.DataFrame([{
        "ts_code": "600101.SH",
        "trade_date": "20260730",
        "name": "当天候选",
        "industry": "机器人",
        "close": 10,
        "pct_chg": 4,
        "turnover_rate": 5,
        "volume_ratio": 2,
        "amount": 300_000,
    }]), None)

    market, _history, metadata = _load_follow_inputs(
        datetime(2026, 7, 30, 14, 45)
    )

    self.assertEqual(market.iloc[0]["ts_code"], "600101.SH")
    self.assertEqual(metadata["candidate_trade_date"], "20260730")
    self.assertEqual(metadata["data_source"], "eastmoney_snapshot_fallback")
    self.assertEqual(metadata["data_status"], "live")
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
python3 -m unittest tests.test_morning_follow_service.MorningFollowServiceTests.test_follow_inputs_use_eastmoney_when_today_snapshot_stays_empty -v
```

Expected: FAIL because `_load_follow_inputs` raises `20260730 市场快照未就绪`.

- [ ] **Step 3: Implement same-day fallback**

Import `load_eastmoney_market_snapshot`. Add `_follow_snapshot_is_usable` that verifies the date, required fields, numeric values, and at least one row inside the existing daily prefilter ranges. In `_load_follow_inputs`, attempt Eastmoney only after local reload remains unusable. Set:

```python
metadata.update({
    "requested_candidate_trade_date": requested_date,
    "candidate_trade_date": requested_date,
    "data_trade_date": requested_date,
    "latest_trade_date": trade_dates[0],
    "data_current": True,
    "data_status": "live",
    "data_status_label": "实时数据",
    "data_source": "eastmoney_snapshot_fallback",
})
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
python3 -m unittest tests.test_morning_follow_service -v
```

Expected: all morning-follow service tests PASS.

- [ ] **Step 5: Commit**

```bash
git add morning_follow_service.py tests/test_morning_follow_service.py
git commit -m "fix: add current snapshot fallback to morning follow"
```

### Task 2: Prior complete-day fallback and date consistency

**Files:**
- Modify: `morning_follow_service.py`
- Test: `tests/test_morning_follow_service.py`
- Test: `tests/test_morning_follow_api.py`

**Interfaces:**
- Produces: `_previous_follow_trade_date(trade_dates: list[str], requested_date: str) -> str | None`.
- Extends metadata with `fallback_warnings`, `data_current`, `data_status`, and actual/requested dates.

- [ ] **Step 1: Write the failing prior-day fallback test**

Return empty local snapshots for `20260730`, an unusable Eastmoney result, and a complete local snapshot for `20260729`. Assert:

```python
self.assertEqual(metadata["requested_candidate_trade_date"], "20260730")
self.assertEqual(metadata["candidate_trade_date"], "20260729")
self.assertEqual(metadata["confirmation_trade_date"], "20260730")
self.assertEqual(metadata["data_status"], "stale")
self.assertEqual(metadata["data_status_label"], "备用缓存")
self.assertFalse(metadata["data_current"])
self.assertEqual(metadata["data_source"], "previous_snapshot")
```

Also assert that `load_recent_daily` receives `"20260729"` rather than `"20260730"`.

- [ ] **Step 2: Verify prior-day test is RED**

Run the new test; expect the current `LookupError`.

- [ ] **Step 3: Implement actual-date fallback**

Select the greatest calendar date less than the requested date. Load and validate that snapshot; if usable, replace the actual candidate date and recompute confirmation date as the next calendar date after the actual candidate date. Store the Tushare and Eastmoney errors in a deduplicated `fallback_warnings` list.

If no source succeeds, raise:

```python
raise LookupError(
    f"{requested_date} 市场快照未就绪；"
    + "；".join(fallback_warnings)
)
```

- [ ] **Step 4: Add API regression test**

Patch `_load_follow_inputs` through `build_morning_follow_monitor` or patch the service at the API boundary and prove a stale structured payload remains HTTP 200. Retain the existing 502 test for true all-source failure.

- [ ] **Step 5: Run backend tests**

Run:

```bash
python3 -m unittest \
  tests.test_morning_follow_service \
  tests.test_morning_follow_api \
  tests.test_realtime_info_service \
  tests.test_realtime_market_source -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add morning_follow_service.py \
  tests/test_morning_follow_service.py tests/test_morning_follow_api.py
git commit -m "fix: fall back to prior morning follow snapshot"
```

### Task 3: Display fallback status and verify the reported failure

**Files:**
- Modify: `quantClient/morning-follow-utils.js`
- Modify: `quantClient/morning-follow-utils.test.js`
- Modify: `quantClient/main.js`
- Modify: `quantClient/index.html`
- Modify: `quantClient/styles.css`

**Interfaces:**
- Produces: `morningFollowDataStatus(payload) -> { text: string, state: string, detail: string }`.

- [ ] **Step 1: Write failing frontend status tests**

Add literal assertions for:

```javascript
morningFollowDataStatus({
  data_status: 'stale',
  data_status_label: '备用缓存',
  requested_candidate_trade_date: '20260730',
  candidate_trade_date: '20260729',
})
```

Expected:

```javascript
{
  text: '备用缓存',
  state: 'warning',
  detail: '计划候选日 20260730 · 实际数据日 20260729',
}
```

Also cover `live`.

- [ ] **Step 2: Verify frontend test is RED**

Run:

```bash
node quantClient/morning-follow-utils.test.js
```

Expected: FAIL because `morningFollowDataStatus` is not exported.

- [ ] **Step 3: Implement and render status**

Export the formatter, expose it from the browser wrapper, add a Vue computed property, and render the status beside the next-morning-follow refresh controls. Reuse the existing warning/danger color treatment.

- [ ] **Step 4: Run full verification**

Run:

```bash
python3 -m unittest discover -s tests
node quantClient/realtime-info-utils.test.js
node quantClient/morning-follow-utils.test.js
node quantClient/morning-follow-layout.test.js
cd quantServer/quantServer && mvn test
```

Expected: all Python, Node, and Maven tests PASS.

- [ ] **Step 5: Reproduce the original scenario**

Invoke:

```python
build_morning_follow_monitor(
    limit=10,
    now=datetime(2026, 7, 30, 14, 45),
)
```

Verify it returns a structured payload instead of raising `20260730 市场快照未就绪`; record selected data source, requested date, actual date, status, candidate count, and warnings.

- [ ] **Step 6: Commit**

```bash
git add quantClient/morning-follow-utils.js \
  quantClient/morning-follow-utils.test.js quantClient/main.js \
  quantClient/index.html quantClient/styles.css
git commit -m "feat: label morning follow fallback data"
```
