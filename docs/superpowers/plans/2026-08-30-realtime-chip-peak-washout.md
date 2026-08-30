# Realtime Chip Peak Washout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add cached chip-peak analytics, bottom-washout scoring, build-position guidance, and chip-prioritized ordering to the three realtime information stage tables while leaving overnight premium behavior unchanged.

**Architecture:** Create a focused `chip_peak_service.py` that owns Tushare chip loading, validation, caching, peak extraction, scoring, and row enrichment. `realtime_info_service.py` will enrich only intraday candidates before stage grouping and add the chip rule version to cache keys; the shared Vue table will render the resulting fields through tested formatting helpers. Overnight construction and rows remain outside the enrichment path.

**Tech Stack:** Python 3.12, pandas, Tushare Pro through the existing `data_service._query_tushare`, `unittest`, Vue 3 browser template, CommonJS-compatible JavaScript utilities, Node.js assertion tests.

**Spec:** `docs/superpowers/specs/2026-08-30-realtime-chip-peak-washout-design.md`

## Global Constraints

- Apply chip analytics only to `observation_stocks`, `trigger_stocks`, and `launch_stocks` in the realtime information page.
- Do not fetch, inject, display, score, sort, or cache chip data inside the overnight premium section.
- Chip signals influence stage ordering but never remove an existing candidate.
- Missing or failed chip data must degrade to `筹码数据暂缺` without failing realtime information construction.
- Use percent-number units throughout (`12.5` means 12.5%).
- The build-position flag requires score >= 80, bottom position <= 35%, peak distance from -8% through +15%, and either 70% or 90% concentration <= 15%.
- Preserve the current ordering of `intraday.stocks`; only the three stage-specific lists become chip-prioritized.
- Every production behavior change begins with a failing test.

---

### Task 1: Pure Chip Metrics and Washout Scoring

**Files:**
- Create: `chip_peak_service.py`
- Create: `tests/test_chip_peak_service.py`

**Interfaces:**
- Produces: `empty_chip_peak_fields(warning: str | None = None) -> dict[str, Any]`
- Produces: `calculate_concentration(low: Any, high: Any) -> float | None`
- Produces: `extract_chip_peaks(chips: pd.DataFrame) -> dict[str, float | None]`
- Produces: `build_chip_peak_fields(row: dict[str, Any], chips: pd.DataFrame, perf: pd.DataFrame, history: pd.DataFrame) -> dict[str, Any]`
- The output field names and thresholds must exactly match the approved spec.

- [ ] **Step 1: Write failing tests for concentration and peak extraction**

Create `tests/test_chip_peak_service.py` with tests that name the production behavior which will make them pass:

```python
import unittest

import pandas as pd

from chip_peak_service import (
    build_chip_peak_fields,
    calculate_concentration,
    extract_chip_peaks,
)


class ChipPeakServiceTests(unittest.TestCase):
    def test_concentration_accepts_ten_and_fifteen_percent_boundaries(self):
        self.assertAlmostEqual(calculate_concentration(90, 110), 10.0)
        self.assertAlmostEqual(calculate_concentration(85, 115), 15.0)

    def test_concentration_rejects_invalid_cost_ranges(self):
        self.assertIsNone(calculate_concentration(None, 110))
        self.assertIsNone(calculate_concentration(110, 90))
        self.assertIsNone(calculate_concentration(0, 0))

    def test_peak_extraction_skips_adjacent_bins_for_secondary_peak(self):
        chips = pd.DataFrame([
            {"price": 10.00, "percent": 12.0},
            {"price": 10.10, "percent": 11.5},
            {"price": 9.50, "percent": 9.0},
        ])
        result = extract_chip_peaks(chips)
        self.assertEqual(result["chip_peak_price"], 10.0)
        self.assertEqual(result["chip_peak_percent"], 12.0)
        self.assertEqual(result["chip_secondary_peak_price"], 9.5)
        self.assertEqual(result["chip_secondary_peak_percent"], 9.0)
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_chip_peak_service -v`

Expected: FAIL because `chip_peak_service` does not exist.

- [ ] **Step 3: Implement concentration, peak extraction, and stable empty fields**

Create `chip_peak_service.py` with the stable field dictionary and minimal numeric helpers. Implement concentration as:

```python
def calculate_concentration(low: Any, high: Any) -> float | None:
    low_value = _finite_float(low)
    high_value = _finite_float(high)
    if low_value is None or high_value is None or high_value < low_value:
        return None
    denominator = high_value + low_value
    if denominator <= 0:
        return None
    return (high_value - low_value) / denominator * 100
```

Sort valid `price, percent` rows by percent descending. The secondary peak must satisfy `abs(price / primary_price - 1) >= 0.02`. Return `None` for peak fields when there is no valid row.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `.venv/bin/python -m unittest tests.test_chip_peak_service -v`

Expected: all three tests PASS.

- [ ] **Step 5: Write failing tests for the score, labels, bottom position, and build-position gate**

Append focused cases using a helper that supplies one perf row and at least 20 historical bars:

```python
def history_frame(low=8.0, high=12.0):
    return pd.DataFrame([
        {"ts_code": "600001.SH", "trade_date": f"202607{index + 1:02d}", "low": low, "high": high}
        for index in range(20)
    ])

def perf_frame(cost_15=9.0, cost_85=11.0, cost_5=8.5, cost_95=11.5, winner=40.0):
    return pd.DataFrame([{
        "ts_code": "600001.SH", "trade_date": "20260730",
        "cost_15pct": cost_15, "cost_85pct": cost_85,
        "cost_5pct": cost_5, "cost_95pct": cost_95,
        "weight_avg": 10.1, "winner_rate": winner,
    }])

def test_dense_bottom_peak_with_washout_structure_is_buildable(self):
    chips = pd.DataFrame([
        {"price": 8.7, "percent": 14.0},
        {"price": 9.2, "percent": 8.0},
    ])
    row = {
        "ts_code": "600001.SH", "current_price": 9.0,
        "bottom_consolidation": True,
        "bottom_volume_contraction": 0.7,
        "bottom_ma_convergence_pct": 4.0,
    }
    result = build_chip_peak_fields(row, chips, perf_frame(), history_frame())
    self.assertEqual(result["chip_peak_bottom_position_pct"], 17.5)
    self.assertAlmostEqual(result["chip_price_distance_pct"], 3.448275862, places=6)
    self.assertGreaterEqual(result["chip_washout_score"], 80)
    self.assertTrue(result["chip_build_position"])
    self.assertEqual(result["chip_washout_label"], "底部洗盘 · 可建仓")

def test_score_label_boundaries_are_stable(self):
    self.assertEqual(_washout_label(80), "底部洗盘 · 可建仓")
    self.assertEqual(_washout_label(65), "底部筹码密集 · 等待确认")
    self.assertEqual(_washout_label(45), "筹码整理")
    self.assertEqual(_washout_label(44.999), "筹码结构偏弱")

def test_missing_peak_or_both_concentrations_returns_unavailable(self):
    result = build_chip_peak_fields(
        {"ts_code": "600001.SH", "current_price": 9.0},
        pd.DataFrame(), pd.DataFrame(), history_frame(),
    )
    self.assertFalse(result["chip_data_complete"])
    self.assertFalse(result["chip_build_position"])
    self.assertEqual(result["chip_washout_label"], "筹码数据暂缺")
```

Import `_washout_label` in the test module for exact boundary verification.

- [ ] **Step 6: Run the new score tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_chip_peak_service -v`

Expected: FAIL because scoring and label behavior are not implemented.

- [ ] **Step 7: Implement the exact five-part score and result reason**

Implement private helpers for concentration points, bottom-position points, peak-distance points, structure points, and winner-rate points. Sum to a maximum of 100 and apply the exact bands from the spec. Use only history rows matching `row["ts_code"]`, sort by `trade_date`, and keep the last 120; require at least 20 valid high/low rows.

Set `chip_data_complete` only when the main peak and at least one concentration are available. Set `chip_build_position` with all four approved gates, not score alone. Build `chip_washout_reason` from the computed score components, for example:

```python
reason = (
    f"主峰 {peak_price:.2f}，70%密集度 {_display(concentration_70)}，"
    f"90%密集度 {_display(concentration_90)}，底部位置 {_display(bottom_position)}，"
    f"距主峰 {_display(distance)}，洗盘评分 {score:.0f}"
)
```

Do not expose `NaN`; use `None` for unavailable JSON-facing values.

- [ ] **Step 8: Run pure service tests and verify GREEN**

Run: `.venv/bin/python -m unittest tests.test_chip_peak_service -v`

Expected: all tests PASS with no warnings.

- [ ] **Step 9: Commit Task 1**

```bash
git add chip_peak_service.py tests/test_chip_peak_service.py
git commit -m "feat: calculate chip peak washout signals"
```

---

### Task 2: Cached Tushare Loading and Realtime Stage Integration

**Files:**
- Modify: `chip_peak_service.py`
- Modify: `tests/test_chip_peak_service.py`
- Modify: `realtime_info_service.py`
- Modify: `tests/test_realtime_info_service.py`

**Interfaces:**
- Consumes: existing `data_service._query_tushare(api_name: str, **kwargs)`.
- Produces: `load_chip_data(ts_code: str, trade_date: str) -> tuple[pd.DataFrame, pd.DataFrame]`.
- Produces: `attach_chip_peak_fields(rows: list[dict[str, Any]], history: pd.DataFrame, trade_date: str, loader: Callable[[str, str], tuple[pd.DataFrame, pd.DataFrame]] = load_chip_data) -> tuple[list[dict[str, Any]], list[str]]`.
- `attach_chip_peak_fields` returns a new row list and deduplicated warnings without mutating its input.

- [ ] **Step 1: Write failing loader/cache/deduplication tests**

Add tests that inject a recording loader and use duplicate stock codes:

```python
def test_attach_loads_each_unique_stock_once_and_keeps_failed_rows(self):
    calls = []
    def loader(ts_code, trade_date):
        calls.append((ts_code, trade_date))
        if ts_code == "600002.SH":
            raise RuntimeError("no permission")
        return valid_chips_frame(), perf_frame()

    rows = [
        {"ts_code": "600001.SH", "current_price": 9.0},
        {"ts_code": "600001.SH", "current_price": 9.1},
        {"ts_code": "600002.SH", "current_price": 8.0},
    ]
    enriched, warnings = attach_chip_peak_fields(
        rows, combined_history(), "20260730", loader=loader,
    )
    self.assertEqual(calls.count(("600001.SH", "20260730")), 1)
    self.assertEqual(calls.count(("600002.SH", "20260730")), 1)
    self.assertEqual(len(enriched), 3)
    self.assertEqual(enriched[2]["chip_washout_label"], "筹码数据暂缺")
    self.assertIn("600002.SH", warnings[0])
```

Add a cache test by patching `chip_peak_service._query_tushare`, calling `load_chip_data` twice for the same key, and asserting exactly one `cyq_chips` and one `cyq_perf` call. Clear the service cache in `setUp` through `clear_chip_peak_cache()`.

- [ ] **Step 2: Run the loader tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_chip_peak_service -v`

Expected: FAIL because loader, cache, and bulk attachment are missing.

- [ ] **Step 3: Implement loading, successful-result cache, and short failure backoff**

In `chip_peak_service.py`, import `_query_tushare` and add a lock-protected cache keyed by `(ts_code, trade_date)`. Cache successful DataFrames for the process lifetime. Track failures with monotonic timestamps and retry after 30 seconds; never store a failure as successful data.

`load_chip_data` must call:

```python
chips = _query_tushare("cyq_chips", ts_code=ts_code, trade_date=trade_date)
perf = _query_tushare("cyq_perf", ts_code=ts_code, trade_date=trade_date)
```

Return copies so callers cannot mutate cached frames. `attach_chip_peak_fields` deduplicates codes before loading, catches failures per code, and retains every input row. Load unique codes with a `ThreadPoolExecutor(max_workers=min(4, len(codes)))`; keep result association by code rather than completion order, and return rows in their original order. The four-worker ceiling limits initial-load latency without creating unbounded Tushare concurrency.

- [ ] **Step 4: Run loader tests and verify GREEN**

Run: `.venv/bin/python -m unittest tests.test_chip_peak_service -v`

Expected: all tests PASS.

- [ ] **Step 5: Write failing realtime integration and ordering tests**

In `tests/test_realtime_info_service.py`, import `_attach_realtime_chip_fields` and add a focused test proving it enriches the intraday rows only. Extend the existing `_group_realtime_stage_rows` tests with rows where the original score favors one stock but the chip build-position flag favors another:

```python
def test_stage_groups_prioritize_buildable_chip_signal_before_original_score(self):
    rows = [
        {
            "ts_code": "600001.SH", "resonance_stage": "observation",
            "bottom_setup_score": 99, "chip_build_position": False,
            "chip_washout_score": 60,
        },
        {
            "ts_code": "600002.SH", "resonance_stage": "observation",
            "bottom_setup_score": 70, "chip_build_position": True,
            "chip_washout_score": 82,
        },
    ]
    result = _group_realtime_stage_rows(rows, 20)
    self.assertEqual(result["observation_stocks"][0]["ts_code"], "600002.SH")
```

Add a cache-key assertion that `_database_realtime_result_key(20)` includes `chip-peak-washout-v1`. Add a database fallback test proving the immediately previous bottom-consolidation cache key can still be read and its rows survive without chip fields. Add an uncached-build test with patched chip attachment and patched `build_realtime_tail_premium_monitor` which asserts the overnight input/output rows contain no `chip_` keys.

- [ ] **Step 6: Run the integration tests and verify RED**

Run:

```bash
.venv/bin/python -m unittest \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_stage_groups_prioritize_buildable_chip_signal_before_original_score \
  -v
```

Expected: FAIL because stage grouping still starts with the legacy score.

- [ ] **Step 7: Integrate chip enrichment before stage grouping**

In `realtime_info_service.py`:

- Import `attach_chip_peak_fields`.
- Add `_REALTIME_CHIP_PEAK_RULE_VERSION = "chip-peak-washout-v1"`.
- Add a thin `_attach_realtime_chip_fields(rows, history, trade_date)` wrapper so integration tests can patch one stable boundary.
- Call the wrapper after `all_rows` is built and before `_group_realtime_stage_rows`.
- Append returned warnings to intraday `fallback_warnings`.
- Do not pass enriched rows, loader, or chip fields into `build_realtime_tail_premium_monitor`.
- Prefix each stage key with `(bool(chip_build_position), float(chip_washout_score or 0))`, leaving every existing key after it unchanged.
- Keep the existing `stocks` ordering by retaining a pre-enrichment order or enriching in place without re-sorting `all_rows`.
- Preserve the current bottom-consolidation database key as `_pre_chip_database_realtime_result_key(limit)` and make `_legacy_database_realtime_result_key(limit)` represent only the still-older key. Add the chip version to `_realtime_result_key`, `_database_realtime_result_key`, and the successful-result cache tuple. When chip-version data is absent, database loading may read the pre-chip key as a fallback, mark `legacy_rule_cache=True`, and leave rows intact for the frontend's missing-data display.
- Call `clear_chip_peak_cache()` from `clear_realtime_derived_caches()` so tests and explicit cache clears are deterministic.

- [ ] **Step 8: Run realtime and API regression tests and verify GREEN**

Run:

```bash
.venv/bin/python -m unittest \
  tests.test_chip_peak_service \
  tests.test_realtime_info_service \
  tests.test_realtime_info_api \
  -v
```

Expected: all tests PASS; existing overnight mock call assertions remain unchanged.

- [ ] **Step 9: Commit Task 2**

```bash
git add chip_peak_service.py tests/test_chip_peak_service.py realtime_info_service.py tests/test_realtime_info_service.py
git commit -m "feat: enrich realtime stages with chip signals"
```

---

### Task 3: Three-Stage Chip Peak Presentation

**Files:**
- Modify: `quantClient/realtime-info-utils.js`
- Modify: `quantClient/realtime-info-utils.test.js`
- Modify: `quantClient/main.js`
- Modify: `quantClient/index.html`
- Modify: `quantClient/styles.css`
- Modify: `quantClient/realtime-tail-premium-layout.test.js`

**Interfaces:**
- Produces: `chipPeakDisplay(row: object) -> {label: string, score: string, peak: string, concentration: string, state: string, title: string}`.
- Consumes: the stable `chip_*` response fields from Task 2.

- [ ] **Step 1: Write failing JavaScript formatting tests**

Extend `quantClient/realtime-info-utils.test.js`:

```javascript
assert.deepEqual(
  chipPeakDisplay({
    chip_washout_label: '底部洗盘 · 可建仓',
    chip_washout_score: 84,
    chip_peak_price: 10.2,
    chip_peak_percent: 12.3,
    chip_price_distance_pct: 4.5,
    chip_concentration_70_pct: 9.8,
    chip_concentration_90_pct: 14.2,
    chip_build_position: true,
    chip_washout_reason: '底部筹码集中',
  }),
  {
    label: '底部洗盘 · 可建仓', score: '84分',
    peak: '主峰 10.20 / 12.30% · 距峰 +4.50%',
    concentration: '70% 9.80% · 90% 14.20%',
    state: 'strong', title: '底部筹码集中',
  },
);

assert.deepEqual(
  chipPeakDisplay({ chip_washout_label: '筹码数据暂缺' }),
  {
    label: '筹码数据暂缺', score: '--', peak: '主峰 --',
    concentration: '70% -- · 90% --', state: 'muted',
    title: '暂无有效筹码峰数据',
  },
);
```

Add cases for waiting-confirmation (`watch`), weak structure (`risk`), and non-finite inputs so the output never includes `NaN`.

- [ ] **Step 2: Run the utility test and verify RED**

Run: `node quantClient/realtime-info-utils.test.js`

Expected: FAIL because `chipPeakDisplay` is not exported.

- [ ] **Step 3: Implement the tested display helper**

Expose `chipPeakDisplay` from the UMD wrapper and returned API. Use a local finite-number formatter that emits `--`; include an explicit plus sign for positive peak distance. Derive states as follows:

```javascript
if (source.chip_build_position === true) state = 'strong';
else if (label.includes('等待确认') || label === '筹码整理') state = 'watch';
else if (label === '筹码结构偏弱') state = 'risk';
else state = 'muted';
```

- [ ] **Step 4: Run the utility test and verify GREEN**

Run: `node quantClient/realtime-info-utils.test.js`

Expected: PASS.

- [ ] **Step 5: Write failing layout assertions for strict three-stage scope**

Update `quantClient/realtime-tail-premium-layout.test.js` to split the HTML at `<h3>盘末隔夜溢价 TOP20</h3>`. Assert the pre-overnight stage portion contains `<th>筹码峰</th>`, `chipPeakDisplay(row)`, and `chip-washout`; assert the overnight portion does not contain `chipPeakDisplay`, `chip_peak_`, `chip_washout_`, or the `筹码峰` header.

- [ ] **Step 6: Run the layout test and verify RED**

Run: `node quantClient/realtime-tail-premium-layout.test.js`

Expected: FAIL because the stage table has no chip column.

- [ ] **Step 7: Add the shared stage column and styles**

In `quantClient/main.js`, add a method returning `chipPeakDisplay(row)`.

In the stage table in `quantClient/index.html` only:

- Insert `<th>筹码峰</th>` after “抗跌力”.
- Render label/score, peak line, and concentration line from the helper.
- Bind the result state to the existing badge classes and `chip-washout` wrapper.
- Bind `title` to the helper title.
- Change the empty-row colspan from 13 to 14.
- Do not edit the overnight table header, body, detail grid, or empty-row colspan.

Add compact wrapping styles in `quantClient/styles.css` for `.chip-washout`, `.chip-washout-main`, and `.chip-washout-detail`, reusing existing strong/watch/risk/muted colors rather than introducing a new palette.

Bump query-string versions for `styles.css`, `realtime-info-utils.js`, and `main.js` in `index.html` to `20260830-chip-peak-v1` so browsers do not retain stale assets.

- [ ] **Step 8: Run all frontend tests and verify GREEN**

Run:

```bash
for test_file in quantClient/*.test.js; do node "$test_file"; done
```

Expected: every JavaScript test prints its success message and exits zero.

- [ ] **Step 9: Commit Task 3**

```bash
git add quantClient/realtime-info-utils.js quantClient/realtime-info-utils.test.js quantClient/main.js quantClient/index.html quantClient/styles.css quantClient/realtime-tail-premium-layout.test.js
git commit -m "feat: show chip peaks in realtime stage tables"
```

---

### Task 4: End-to-End Verification and Operational Documentation

**Files:**
- Modify: `README_BACKEND.md`
- Test: `tests/test_chip_peak_service.py`
- Test: `tests/test_realtime_info_service.py`
- Test: `tests/test_realtime_info_api.py`
- Test: `quantClient/realtime-info-utils.test.js`
- Test: `quantClient/realtime-tail-premium-layout.test.js`

**Interfaces:**
- Consumes: completed chip service, realtime integration, and frontend presentation.
- Produces: documented runtime behavior and verification evidence; no new business behavior.

- [ ] **Step 1: Add a failing documentation-presence assertion**

Add to the existing layout regression test or a focused Node assertion:

```javascript
const backendReadme = fs.readFileSync(path.join(__dirname, '..', 'README_BACKEND.md'), 'utf8');
assert.ok(backendReadme.includes('cyq_chips'));
assert.ok(backendReadme.includes('cyq_perf'));
assert.ok(backendReadme.includes('筹码数据暂缺'));
```

- [ ] **Step 2: Run the assertion and verify RED**

Run: `node quantClient/realtime-tail-premium-layout.test.js`

Expected: FAIL because the runtime notes are not yet documented.

- [ ] **Step 3: Document data requirements and graceful degradation**

Add a short “实时筹码峰” subsection to `README_BACKEND.md` describing:

- The configured Tushare token must have `cyq_chips` and `cyq_perf` access.
- The three realtime stage tables cache valid results per stock and trade date.
- Missing permissions or upstream failures display `筹码数据暂缺` and do not remove candidates.
- The overnight premium module is not part of this feature.

- [ ] **Step 4: Run the documentation/layout test and verify GREEN**

Run: `node quantClient/realtime-tail-premium-layout.test.js`

Expected: PASS.

- [ ] **Step 5: Run the complete automated verification suite**

Run:

```bash
.venv/bin/python -m unittest discover -s tests -v
for test_file in quantClient/*.test.js; do node "$test_file"; done
git diff --check
```

Expected: all Python and JavaScript tests PASS and `git diff --check` emits no output.

- [ ] **Step 6: Perform one read-only live-data smoke check**

With the configured project environment, run a script that calls `load_chip_data("600519.SH", latest_completed_trade_date)` and `build_chip_peak_fields(...)` using its recent market history. Print only the symbol, trade date, main peak, both concentrations, score, label, and build-position flag; do not print credentials or raw environment values.

Expected: the response contains finite peak and concentration values, a score from 0 through 100, and one approved label. If upstream access is temporarily unavailable, record the warning and rely on the automated degradation tests rather than changing thresholds.

- [ ] **Step 7: Review the diff specifically for overnight isolation**

Run:

```bash
git diff -- realtime_tail_premium_service.py tests/test_realtime_tail_premium_service.py
git diff -U3 -- quantClient/index.html | sed -n '/盘末隔夜溢价/,$p'
```

Expected: no backend overnight service changes and no chip markup inside the overnight HTML section.

- [ ] **Step 8: Commit Task 4**

```bash
git add README_BACKEND.md quantClient/realtime-tail-premium-layout.test.js
git commit -m "docs: describe realtime chip data behavior"
```
