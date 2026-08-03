# Morning Follow Minute Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the outer “次日早盘跟进” use validated Eastmoney current-day 1-minute bars when Tushare returns no usable confirmation-day minutes.

**Architecture:** Keep the existing Tushare call as the primary path and add an isolated Eastmoney parser, short cache, and fallback loader inside `morning_follow_service.py`. The builder passes minute-source diagnostics into the existing confirmation function; all candidate filters and confirmation thresholds stay unchanged.

**Tech Stack:** Python 3.10, pandas, requests, `unittest`, Tushare `stk_mins`, Eastmoney `trends2`

## Global Constraints

- Modify only `morning_follow_service.py` and `tests/test_morning_follow_service.py`.
- Do not modify `realtime_info_service.py`, its HTML block, `overnight_monitor_service.py`, `/overnight-monitor`, or internal “实时信息/隔夜选股”.
- Tushare remains primary; Eastmoney is called only when Tushare has no usable confirmation-day rows.
- Accept only rows for `confirmation_trade_date`, between 09:30 and 10:00, and no later than `now`.
- Do not change candidate filters, strict/relaxed tiers, confirmation thresholds, or T+1 rules.
- Cache successful Eastmoney data for 20 seconds and empty/error results for 5 seconds.
- A failure for one stock must not abort remaining candidates.
- Existing tracked and untracked files contain user work; do not stage or commit implementation files.

---

## Execution Preflight

- [ ] Record isolation checksums before Task 1:

```bash
sha256sum realtime_info_service.py
sed -n "/activeTab === 'realtime_info'/,/activeTab === 'reports'/p" \
  quantClient/index.html | sha256sum
```

- [ ] Run the existing focused baseline:

```bash
env HOME=/tmp python3 -m unittest tests.test_morning_follow_service -v
```

Expected: all existing morning-follow tests pass.

---

### Task 1: Parse and validate Eastmoney morning trends

**Files:**
- Modify: `morning_follow_service.py:1-30,630-655`
- Test: `tests/test_morning_follow_service.py:1-260`

**Interfaces:**
- Produces: `_eastmoney_secid(ts_code: str) -> str | None`
- Produces: `_parse_eastmoney_trends(payload: Any, ts_code: str, confirmation_trade_date: str, now: datetime) -> pd.DataFrame`
- The frame columns are `ts_code, trade_time, open, close, high, low, vol, amount`.

- [ ] **Step 1: Add parser imports and failing tests**

Extend the test imports with `_eastmoney_secid` and
`_parse_eastmoney_trends`. Add:

```python
def test_eastmoney_secid_supports_sh_and_sz_only(self):
    self.assertEqual(_eastmoney_secid("600298.SH"), "1.600298")
    self.assertEqual(_eastmoney_secid("300910.SZ"), "0.300910")
    self.assertIsNone(_eastmoney_secid("830001.BJ"))
    self.assertIsNone(_eastmoney_secid("bad-code"))


def test_parse_eastmoney_trends_filters_date_window_and_future_rows(self):
    payload = {
        "data": {
            "trends": [
                "2026-07-29 09:31,9.90,9.91,9.92,9.89,100,99000.00,9.90",
                "2026-07-30 09:30,10.10,10.10,10.10,10.10,100,101000.00,10.10",
                "2026-07-30 09:31,10.10,10.12,10.13,10.09,200,202000.00,10.11",
                "2026-07-30 09:37,10.20,10.21,10.22,10.19,150,153000.00,10.16",
            ],
        },
    }

    result = _parse_eastmoney_trends(
        payload,
        "600101.SH",
        "20260730",
        datetime(2026, 7, 30, 9, 36),
    )

    self.assertEqual(
        result["trade_time"].dt.strftime("%H:%M").tolist(),
        ["09:30", "09:31"],
    )
    self.assertEqual(result["ts_code"].tolist(), ["600101.SH", "600101.SH"])
    self.assertEqual(result.iloc[1]["close"], 10.12)
    self.assertEqual(result.iloc[1]["vol"], 200)


def test_parse_eastmoney_trends_rejects_invalid_payload_rows(self):
    payload = {
        "data": {
            "trends": [
                "bad-row",
                "2026-07-30 09:31,broken,10.12,10.13,10.09,200,202000.00,10.11",
            ],
        },
    }

    result = _parse_eastmoney_trends(
        payload,
        "600101.SH",
        "20260730",
        datetime(2026, 7, 30, 9, 36),
    )

    self.assertTrue(result.empty)
```

- [ ] **Step 2: Run parser tests and verify RED**

Run:

```bash
env HOME=/tmp python3 -m unittest \
  tests.test_morning_follow_service.MorningFollowServiceTests.test_eastmoney_secid_supports_sh_and_sz_only \
  tests.test_morning_follow_service.MorningFollowServiceTests.test_parse_eastmoney_trends_filters_date_window_and_future_rows \
  tests.test_morning_follow_service.MorningFollowServiceTests.test_parse_eastmoney_trends_rejects_invalid_payload_rows \
  -v
```

Expected: import errors because the two helpers do not exist.

- [ ] **Step 3: Implement the minimal parser**

Add `import requests`. Define a shared empty-frame helper and:

```python
_MORNING_MINUTE_COLUMNS = [
    "ts_code", "trade_time", "open", "close", "high", "low", "vol", "amount",
]


def _eastmoney_secid(ts_code: str) -> str | None:
    text = str(ts_code or "").upper()
    if text.endswith(".SH") and text[:6].isdigit():
        return f"1.{text[:6]}"
    if text.endswith(".SZ") and text[:6].isdigit():
        return f"0.{text[:6]}"
    return None


def _empty_morning_bars() -> pd.DataFrame:
    return pd.DataFrame(columns=_MORNING_MINUTE_COLUMNS)


def _parse_eastmoney_trends(
    payload: Any,
    ts_code: str,
    confirmation_trade_date: str,
    now: datetime,
) -> pd.DataFrame:
    data = payload.get("data") if isinstance(payload, dict) else None
    trends = data.get("trends") if isinstance(data, dict) else None
    if not isinstance(trends, list):
        return _empty_morning_bars()
    records = []
    for trend in trends:
        parts = str(trend).split(",")
        if len(parts) < 8:
            continue
        records.append({
            "ts_code": ts_code,
            "trade_time": parts[0],
            "open": parts[1],
            "close": parts[2],
            "high": parts[3],
            "low": parts[4],
            "vol": parts[5],
            "amount": parts[6],
        })
    frame = pd.DataFrame(records, columns=_MORNING_MINUTE_COLUMNS)
    return _normalize_confirmation_bars(
        frame,
        confirmation_trade_date,
        now,
    ).reindex(columns=_MORNING_MINUTE_COLUMNS)
```

`_normalize_confirmation_bars` already coerces and validates
`trade_time/open/close/vol`; extend its numeric coercion to `high`, `low`, and
`amount` only when those columns are present.

- [ ] **Step 4: Run parser tests and full morning service tests**

Run:

```bash
env HOME=/tmp python3 -m unittest tests.test_morning_follow_service -v
```

Expected: all tests pass.

---

### Task 2: Add cached Eastmoney fallback decision

**Files:**
- Modify: `morning_follow_service.py:20-30,630-655`
- Test: `tests/test_morning_follow_service.py:1-610`

**Interfaces:**
- Consumes: `_eastmoney_secid(...)`, `_parse_eastmoney_trends(...)`
- Produces: `_eastmoney_morning_bars(ts_code: str, confirmation_trade_date: str, now: datetime) -> tuple[pd.DataFrame, str | None]`
- Changes: `_morning_bars_for_candidate(...) -> tuple[pd.DataFrame, str, str | None]`
- Tuple fields are `(bars, source, failure_reason)`.

- [ ] **Step 1: Add failing source-selection tests**

Import `_EASTMONEY_MORNING_CACHE`, `_eastmoney_morning_bars`, and
`_morning_bars_for_candidate`. Clear `_EASTMONEY_MORNING_CACHE` in `setUp`.
Add:

```python
@patch("morning_follow_service._eastmoney_morning_bars")
@patch("morning_follow_service._cached_minute_bars")
def test_morning_loader_keeps_usable_tushare_as_primary(
    self,
    cached_minutes,
    eastmoney_minutes,
):
    cached_minutes.return_value = morning_bars(
        [10.1, 10.12, 10.14, 10.16, 10.18, 10.2]
    )

    bars, source, reason = _morning_bars_for_candidate(
        "600101.SH",
        "20260730",
        datetime(2026, 7, 30, 9, 36),
    )

    self.assertEqual(source, "tushare")
    self.assertIsNone(reason)
    self.assertEqual(len(bars), 6)
    eastmoney_minutes.assert_not_called()


@patch("morning_follow_service._eastmoney_morning_bars")
@patch("morning_follow_service._cached_minute_bars")
def test_morning_loader_falls_back_when_tushare_is_empty(
    self,
    cached_minutes,
    eastmoney_minutes,
):
    cached_minutes.return_value = pd.DataFrame()
    eastmoney_minutes.return_value = (
        morning_bars([10.1, 10.12, 10.14, 10.16, 10.18, 10.2]),
        None,
    )

    bars, source, reason = _morning_bars_for_candidate(
        "600101.SH",
        "20260730",
        datetime(2026, 7, 30, 9, 36),
    )

    self.assertEqual(source, "eastmoney_fallback")
    self.assertIsNone(reason)
    self.assertEqual(len(bars), 6)


@patch("morning_follow_service._eastmoney_morning_bars")
@patch("morning_follow_service._cached_minute_bars")
def test_morning_loader_falls_back_when_tushare_only_has_old_date(
    self,
    cached_minutes,
    eastmoney_minutes,
):
    old = morning_bars([10.1] * 6)
    old["trade_time"] = old["trade_time"].str.replace(
        "2026-07-30",
        "2026-07-29",
    )
    cached_minutes.return_value = old
    eastmoney_minutes.return_value = (
        morning_bars([10.1, 10.12, 10.14, 10.16, 10.18, 10.2]),
        None,
    )

    bars, source, reason = _morning_bars_for_candidate(
        "600101.SH",
        "20260730",
        datetime(2026, 7, 30, 9, 36),
    )

    self.assertEqual(source, "eastmoney_fallback")
    self.assertEqual(len(bars), 6)


@patch("morning_follow_service._eastmoney_morning_bars")
@patch("morning_follow_service._cached_minute_bars")
def test_morning_loader_reports_both_sources_unavailable(
    self,
    cached_minutes,
    eastmoney_minutes,
):
    cached_minutes.return_value = pd.DataFrame()
    eastmoney_minutes.return_value = (
        pd.DataFrame(),
        "东方财富备用源请求超时",
    )

    bars, source, reason = _morning_bars_for_candidate(
        "600101.SH",
        "20260730",
        datetime(2026, 7, 30, 9, 36),
    )

    self.assertTrue(bars.empty)
    self.assertEqual(source, "unavailable")
    self.assertEqual(
        reason,
        "Tushare当日分钟为空；东方财富备用源请求超时",
    )
```

- [ ] **Step 2: Add failing HTTP parsing and cache test**

```python
@patch("morning_follow_service.requests.get")
def test_eastmoney_morning_bars_fetches_and_reuses_short_cache(self, get):
    response = get.return_value
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "data": {
            "trends": [
                "2026-07-30 09:30,10.10,10.10,10.10,10.10,100,101000.00,10.10",
                "2026-07-30 09:31,10.10,10.12,10.13,10.09,200,202000.00,10.11",
            ],
        },
    }

    first, first_error = _eastmoney_morning_bars(
        "600101.SH",
        "20260730",
        datetime(2026, 7, 30, 9, 36),
    )
    second, second_error = _eastmoney_morning_bars(
        "600101.SH",
        "20260730",
        datetime(2026, 7, 30, 9, 36, 10),
    )

    self.assertEqual(len(first), 2)
    self.assertEqual(len(second), 2)
    self.assertIsNone(first_error)
    self.assertIsNone(second_error)
    get.assert_called_once()
```

- [ ] **Step 3: Run fallback tests and verify RED**

Run:

```bash
env HOME=/tmp python3 -m unittest \
  tests.test_morning_follow_service.MorningFollowServiceTests.test_morning_loader_keeps_usable_tushare_as_primary \
  tests.test_morning_follow_service.MorningFollowServiceTests.test_morning_loader_falls_back_when_tushare_is_empty \
  tests.test_morning_follow_service.MorningFollowServiceTests.test_morning_loader_falls_back_when_tushare_only_has_old_date \
  tests.test_morning_follow_service.MorningFollowServiceTests.test_morning_loader_reports_both_sources_unavailable \
  tests.test_morning_follow_service.MorningFollowServiceTests.test_eastmoney_morning_bars_fetches_and_reuses_short_cache \
  -v
```

Expected: import/signature failures because the fallback loader and cache do not
exist and `_morning_bars_for_candidate` still returns only a frame.

- [ ] **Step 4: Implement HTTP loader and cache**

Add:

```python
_EASTMONEY_TRENDS_URL = (
    "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
)
_EASTMONEY_MORNING_CACHE: dict[
    tuple[str, str],
    tuple[float, pd.DataFrame, str | None],
] = {}
_EASTMONEY_SUCCESS_TTL_SECONDS = 20
_EASTMONEY_FAILURE_TTL_SECONDS = 5
```

Implement `_eastmoney_morning_bars` using:

```python
response = requests.get(
    _EASTMONEY_TRENDS_URL,
    params={
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "ndays": 1,
        "iscr": 0,
    },
    timeout=(2, 4),
)
response.raise_for_status()
bars = _parse_eastmoney_trends(
    response.json(),
    ts_code,
    confirmation_trade_date,
    now,
)
```

Cache `(time.monotonic(), bars.copy(), error)` under
`(ts_code, confirmation_trade_date)`. Use 20 seconds when bars are non-empty,
otherwise 5 seconds. Convert exceptions into
`f"东方财富备用源请求失败: {exc}"`; do not raise.

- [ ] **Step 5: Implement primary/fallback decision**

Change `_morning_bars_for_candidate` to:

```python
def _morning_bars_for_candidate(
    ts_code: str,
    confirmation_trade_date: str | None,
    now: datetime,
) -> tuple[pd.DataFrame, str, str | None]:
```

Keep the existing date/window guards. Return an empty frame with source
`"unavailable"` outside the confirmation window. Normalize Tushare rows with
`_normalize_confirmation_bars`; return `"tushare"` when usable. Only then call
`_eastmoney_morning_bars`. Return `"eastmoney_fallback"` when fallback rows are
usable, otherwise return:

```python
(
    _empty_morning_bars(),
    "unavailable",
    f"Tushare当日分钟为空；{fallback_error or '东方财富备用源未返回有效数据'}",
)
```

- [ ] **Step 6: Run fallback tests and full morning service tests**

Run:

```bash
env HOME=/tmp python3 -m unittest tests.test_morning_follow_service -v
```

Expected: all tests pass.

---

### Task 3: Integrate source diagnostics without changing confirmation rules

**Files:**
- Modify: `morning_follow_service.py:145-290,690-800`
- Test: `tests/test_morning_follow_service.py:500-610`

**Interfaces:**
- Consumes: `_morning_bars_for_candidate(...) -> tuple[pd.DataFrame, str, str | None]`
- Extends: `_morning_confirmation(..., minute_failure_reason: str | None = None) -> dict[str, Any]`
- Adds output field: `morning_minute_source`.

- [ ] **Step 1: Update orchestration test to fail on the new contract**

Extend the test imports with `_MORNING_FOLLOW_RESULT_CACHE`. Change the existing
mocked return to:

```python
morning_bars_for_candidate.return_value = (
    morning_bars([10.1, 10.12, 10.14, 10.16, 10.18, 10.2]),
    "eastmoney_fallback",
    None,
)
```

Add:

```python
self.assertEqual(
    result["stocks"][0]["morning_minute_source"],
    "eastmoney_fallback",
)
```

In the same test, clear the result cache, change the loader return, and build
again:

```python
_MORNING_FOLLOW_RESULT_CACHE.clear()
morning_bars_for_candidate.return_value = (
    pd.DataFrame(),
    "unavailable",
    "Tushare当日分钟为空；东方财富备用源请求超时",
)
unavailable = build_morning_follow_monitor(
    limit=10,
    now=datetime(2026, 7, 30, 9, 36),
)
stock = unavailable["stocks"][0]
```

Assert:

```python
self.assertEqual(stock["follow_status"], "数据未就绪")
self.assertEqual(stock["morning_minute_source"], "unavailable")
self.assertEqual(
    stock["follow_reason"],
    "Tushare当日分钟为空；东方财富备用源请求超时",
)
```

- [ ] **Step 2: Run the two orchestration tests and verify RED**

Run:

```bash
env HOME=/tmp python3 -m unittest \
  tests.test_morning_follow_service.MorningFollowServiceTests.test_monitor_builds_previous_day_pool_then_confirms_current_morning \
  -v
```

Expected: tuple handling/output-field assertions fail because the builder still
passes the whole tuple as a frame and does not expose source diagnostics.

- [ ] **Step 3: Pass diagnostics through the builder**

In the setup loop:

```python
morning_bars, minute_source, minute_failure_reason = (
    _morning_bars_for_candidate(
        setup["ts_code"],
        metadata.get("confirmation_trade_date"),
        current,
    )
)
setup["morning_minute_source"] = minute_source
setup.update(_morning_confirmation(
    setup,
    morning_bars,
    current,
    metadata.get("confirmation_trade_date"),
    minute_failure_reason=minute_failure_reason,
))
```

On unexpected loader exceptions, set:

```python
minute_source = "unavailable"
minute_failure_reason = f"早盘确认数据加载失败: {exc}"
morning_bars = _empty_morning_bars()
```

Add the optional parameter to `_morning_confirmation`. In its `usable.empty`
branch use `minute_failure_reason or "缺少确认日分钟数据"`. Do not change any other
status or threshold branch.

- [ ] **Step 4: Run full morning service and API tests**

Run:

```bash
env HOME=/tmp python3 -m unittest \
  tests.test_morning_follow_service \
  tests.test_morning_follow_api \
  -v
```

Expected: all tests pass.

---

### Task 4: Real-data verification and isolation regression

**Files:**
- Verify only; no new production files.

**Interfaces:**
- Verifies output field `morning_minute_source`.
- Verifies existing `follow_status` rules against real confirmation-day bars.

- [ ] **Step 1: Run current-day fallback diagnostic**

Run during the confirmation day:

```bash
env HOME=/tmp python3 -u -c "from datetime import datetime; from morning_follow_service import _morning_bars_for_candidate; bars, source, reason = _morning_bars_for_candidate('600298.SH', '20260730', datetime.now()); print('source', source); print('reason', reason); print('rows', len(bars)); print(bars.tail(5).to_string(index=False))"
```

Expected when Tushare is delayed and Eastmoney is available:

```text
source eastmoney_fallback
reason None
rows > 0
```

- [ ] **Step 2: Run the real monitor**

```bash
env HOME=/tmp python3 -u -c "from datetime import datetime; from morning_follow_service import build_morning_follow_monitor; result = build_morning_follow_monitor(limit=30, max_fetch=30, now=datetime.now()); print('count', result['count']); print('warnings', result['warnings']); [print(row['ts_code'], row['follow_status'], row.get('morning_minute_source'), row.get('follow_reason')) for row in result['stocks']]"
```

Expected:

- candidates with Eastmoney minutes no longer all show `数据未就绪`;
- their `morning_minute_source` is `eastmoney_fallback`;
- each status is calculated by the existing confirmation rules.

- [ ] **Step 3: Run related regression suite**

```bash
env HOME=/tmp python3 -m unittest \
  tests.test_morning_follow_service \
  tests.test_morning_follow_api \
  tests.test_realtime_info_service \
  tests.test_realtime_info_api \
  tests.test_intraday_monitor_service \
  tests.test_overnight_monitor_service \
  -v
node quantClient/morning-follow-utils.test.js
node quantClient/realtime-info-utils.test.js
node --check quantClient/main.js
python3 -m py_compile \
  morning_follow_service.py realtime_info_service.py app.py \
  intraday_monitor_service.py overnight_monitor_service.py
(cd quantServer/quantServer && mvn test)
```

Expected: all related tests and syntax checks pass.

- [ ] **Step 4: Verify isolation and diff hygiene**

```bash
sha256sum realtime_info_service.py
sed -n "/activeTab === 'realtime_info'/,/activeTab === 'reports'/p" \
  quantClient/index.html | sha256sum
git diff --check -- \
  morning_follow_service.py tests/test_morning_follow_service.py
git status --short
```

Expected:

- both isolation checksums match the Execution Preflight values;
- only `morning_follow_service.py` and
  `tests/test_morning_follow_service.py` contain this fix;
- implementation files remain unstaged and uncommitted.
