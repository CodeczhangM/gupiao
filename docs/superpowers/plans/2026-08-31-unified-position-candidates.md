# Unified Position Candidates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the three realtime bottom-stage tables with one conservative, explainable 0–10 stock position-candidate ranking driven primarily by sector heat, price/volume, MACD, and chip structure.

**Architecture:** Add a pure scoring module that consumes the already-enriched realtime rows and returns score breakdowns, risk deductions, conservative position levels, and a stable ranking. Integrate it after minute and chip enrichment in `realtime_info_service.py`, expose the unified list while keeping legacy stage arrays for API compatibility, and render only one frontend table.

**Tech Stack:** Python 3.12, pandas, `unittest`, Vue 3 global build, plain JavaScript/HTML/CSS, Node assertion-based layout tests.

**Spec:** `docs/superpowers/specs/2026-08-31-unified-position-candidates-design.md`

## Global Constraints

- Only merge the realtime intraday tables; do not change the independent tail-premium or morning-follow modules.
- Positive weights are exactly: sector 30, price/volume 20, MACD 20, chip peak 15, relative/tail 10, bottom structure 5.
- Risk deductions are capped at 30 points and final score is clipped to 0–100.
- Conservative levels are: immediate at 80+, confirmation at 65–79, observation at 50–64; scores below 50 are hidden.
- Immediate entry additionally requires sector score at least 22, price/volume score at least 14, complete chip data with chip score at least 8, no dual-timeframe MACD weakness, available post-14:30 confirmation when that window exists, and no high-risk item.
- Return at most 10 candidates and allow fewer than 5; never backfill weak rows.
- Continue using the global MACD settings and include their parameter key in the score version/cache key.
- Keep `observation_stocks`, `trigger_stocks`, and `launch_stocks` temporarily for API compatibility, but the frontend must not render them as separate tables.

---

### Task 1: Pure unified scoring engine

**Files:**
- Create: `position_candidate_scoring.py`
- Create: `tests/test_position_candidate_scoring.py`

**Interfaces:**
- Consumes: enriched stock dictionaries containing existing sector, market, volume, MACD, chip, tail, and bottom fields.
- Produces: `score_position_candidate(row: dict[str, Any], *, market_phase: str = "") -> dict[str, Any]`.
- Produces: `rank_position_candidates(rows: list[dict[str, Any]], limit: int = 10, *, market_phase: str = "") -> list[dict[str, Any]]`.
- Produces: `position_score_version(macd_settings: dict[str, Any] | None = None) -> str`.

- [ ] **Step 1: Write failing score-breakdown tests**

Create fixtures with literal expected outcomes. The first fixture is a hot-sector, healthy-volume, dual-MACD, buildable-chip row; the second has a stronger chip score but a cold sector and weak volume. Assert the first ranks higher, proving sector > volume > chip priority through behavior rather than constants.

```python
def test_hot_sector_and_healthy_volume_beat_stronger_chip_only_row(self):
    strong_context = score_position_candidate({
        **base_row("600001.SH"),
        "sector_rank": 2,
        "sector_avg_pct_chg": 2.2,
        "sector_up_ratio": 0.76,
        "sector_limit_up_count": 3,
        "sector_macd_status": "水上多头",
        "volume_ratio": 1.8,
        "turnover_rate": 6.0,
        "price_volume_confirmed": True,
        "main_force_status": "主力抢筹",
        "chip_washout_score": 68,
        "chip_data_complete": True,
        "chip_build_position": True,
    })
    chip_only = score_position_candidate({
        **base_row("600002.SH"),
        "sector_rank": 35,
        "sector_avg_pct_chg": -0.2,
        "sector_up_ratio": 0.35,
        "sector_limit_up_count": 0,
        "sector_macd_status": "水下空头",
        "volume_ratio": 0.7,
        "turnover_rate": 2.0,
        "chip_washout_score": 92,
        "chip_data_complete": True,
        "chip_build_position": True,
    })
    self.assertGreater(strong_context["position_score"], chip_only["position_score"])
    self.assertGreater(strong_context["sector_hot_score"], chip_only["sector_hot_score"])
    self.assertGreater(strong_context["price_volume_score"], chip_only["price_volume_score"])
```

Also assert every returned row has these exact fields:

```python
{
    "position_score", "position_level", "position_level_reason",
    "sector_hot_score", "price_volume_score", "macd_score",
    "chip_peak_score", "relative_tail_score", "bottom_structure_score",
    "position_risk_penalty", "position_risk_items",
    "position_positive_reasons", "position_missing_confirmations",
}
```

- [ ] **Step 2: Run the score tests and verify RED**

Run:

```bash
HOME=/tmp/piao-test-home .venv/bin/python -m unittest \
  tests.test_position_candidate_scoring.PositionCandidateScoringTests.test_hot_sector_and_healthy_volume_beat_stronger_chip_only_row -v
```

Expected: FAIL because `position_candidate_scoring` does not exist.

- [ ] **Step 3: Implement factor scorers and score version**

Create these small pure helpers in `position_candidate_scoring.py`: `_sector_hot_score` returns the bounded sector score plus reasons; `_price_volume_score` returns the bounded price/volume score plus reasons; `_macd_score` additionally returns whether both timeframes are weak; `_chip_peak_score` returns the bounded chip score plus reasons; `_relative_tail_score` additionally returns missing confirmations; `_bottom_structure_score` returns its bounded score plus reasons; and `_risk_penalty` returns the capped deduction, risk items, and high-risk veto flag.

```python
WEIGHTS = {
    "sector": 30.0,
    "price_volume": 20.0,
    "macd": 20.0,
    "chip": 15.0,
    "relative_tail": 10.0,
    "bottom": 5.0,
}

COMPONENT_FIELDS = {
    "sector": "sector_hot_score",
    "price_volume": "price_volume_score",
    "macd": "macd_score",
    "chip": "chip_peak_score",
    "relative_tail": "relative_tail_score",
    "bottom": "bottom_structure_score",
}
```

Use bounded, monotonic bands rather than multiplying unbounded raw values. Reuse existing normalized fields where possible:

- sector: `sector_avg_pct_chg`, `sector_up_ratio`, `sector_limit_up_count`, `sector_rank`, `sector_macd_status`, `sector_potential_score`;
- price/volume: `pct_chg`, `volume_ratio`, `turnover_rate`, `price_volume_confirmed`, `price_volume_stagnation`, `main_force_status`, `tail_return_after_1430`, `tail_volume_ratio`;
- MACD: `macd_golden_cross`, `macd_above_zero`, `intraday_signal_tier`, `intraday_signal_reason`, `sector_macd_status`;
- chip: `chip_washout_score`, `chip_data_complete`, `chip_build_position`, `chip_concentration_70_pct`, `chip_price_distance_pct`, `chip_winner_rate`;
- relative/tail: `realtime_relative_strength_score`, `relative_strength`, `market_resonance_state`, `tail_strength_score`, `tail_close_position`;
- bottom: `resonance_stage`, `bottom_setup_score`, `bottom_breakout_strength`, `bottom_volume_expansion`.

Derive component scores independently and cap each at its weight. Do not use `chip_washout_score / 100 * 15` alone: combine it with `chip_build_position` and data completeness so incomplete data cannot look confirmed.

Implement the version as:

```python
BASE_POSITION_SCORE_VERSION = "position-candidate-v1"

def position_score_version(settings=None):
    return f"{BASE_POSITION_SCORE_VERSION}-{macd_parameter_key(settings)}"
```

- [ ] **Step 4: Write failing conservative-level tests**

Add literal tests for:

1. score 80+ with all hard confirmations → `立即建仓`;
2. score 80+ but incomplete chip data → `等待确认后建仓`;
3. score 80+ but sector score below 22 → `等待确认后建仓`;
4. score 80+ with dual MACD weakness or a high-risk veto → hidden;
5. score 50–64 → `观察建仓`;
6. score below 50 → hidden;
7. pre-14:30 missing tail data does not deduct points but caps the level at confirmation;
8. after 14:30 missing tail confirmation prevents immediate entry.

Exercise the public scoring/ranking functions. Do not patch the private factor helpers.

- [ ] **Step 5: Run the conservative-level tests and verify RED**

Run:

```bash
HOME=/tmp/piao-test-home .venv/bin/python -m unittest tests.test_position_candidate_scoring -v
```

Expected: FAIL on missing level, hard-gate, and ranking behavior.

- [ ] **Step 6: Implement risk, levels, filtering, and stable ranking**

Implement:

```python
def score_position_candidate(row, *, market_phase=""):
    # calculate six components
    # subtract capped risk penalty
    # derive missing confirmations and conservative level
    # return original row plus all score/explanation fields

def rank_position_candidates(rows, limit=10, *, market_phase=""):
    scored = [score_position_candidate(row, market_phase=market_phase) for row in rows]
    visible = [row for row in scored if row["position_level"] != "不展示"]
    tier = {"立即建仓": 3, "等待确认后建仓": 2, "观察建仓": 1}
    return sorted(
        visible,
        key=lambda row: (
            tier[row["position_level"]],
            row["position_score"],
            row["sector_hot_score"],
            row["price_volume_score"],
            row["macd_score"],
            str(row.get("ts_code") or ""),
        ),
        reverse=True,
    )[:max(1, min(int(limit), 10))]
```

Apply the existing mainboard/non-ST/trading checks as hard visibility gates. Treat absent chip or sector data as missing confirmation, not an exception. Mark `position_high_risk_veto` in the returned row to make veto behavior inspectable.

- [ ] **Step 7: Run all pure scoring tests**

Run:

```bash
HOME=/tmp/piao-test-home .venv/bin/python -m unittest tests.test_position_candidate_scoring -v
```

Expected: all tests PASS.

- [ ] **Step 8: Commit the scoring engine**

```bash
git add position_candidate_scoring.py tests/test_position_candidate_scoring.py
git commit -m "feat: score unified position candidates"
```

---

### Task 2: Integrate unified ranking into realtime intraday output

**Files:**
- Modify: `realtime_info_service.py:29-65`
- Modify: `realtime_info_service.py:1649-1713`
- Modify: `realtime_info_service.py:2006-2073`
- Modify: `tests/test_realtime_info_service.py:3177-3255`

**Interfaces:**
- Consumes: `rank_position_candidates(rows, limit=10, market_phase=phase)` and `position_score_version()` from Task 1.
- Produces: `intraday.position_candidates`, `intraday.position_candidate_count`, `intraday.position_score_version`, and `intraday.position_filter_debug`.
- Preserves: `intraday.observation_stocks`, `intraday.trigger_stocks`, `intraday.launch_stocks` for compatibility.

- [ ] **Step 1: Write failing integration tests for a single deduplicated pool**

Add a focused test that passes rows from all three stages, including the same `ts_code` twice. Patch only the external chip loader if necessary; keep the real unified scoring function active. Assert:

```python
self.assertEqual(
    [row["ts_code"] for row in result["position_candidates"]],
    ["hot-launch", "hot-trigger", "stable-observation"],
)
self.assertEqual(result["stocks"], result["position_candidates"])
self.assertEqual(result["position_candidate_count"], 3)
self.assertLessEqual(len(result["position_candidates"]), 10)
self.assertIn("position-candidate-v1-macd-5-34-5", result["position_score_version"])
```

Add a separate test with only four qualifying rows and assert exactly four are returned, proving no backfill to five.

- [ ] **Step 2: Run integration tests and verify RED**

Run:

```bash
HOME=/tmp/piao-test-home .venv/bin/python -m unittest \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_realtime_intraday_returns_one_unified_position_pool \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_unified_position_pool_does_not_backfill_to_five -v
```

Expected: FAIL because `position_candidates` and the unified score metadata are absent.

- [ ] **Step 3: Add the unified pool after chip enrichment**

Import Task 1 functions near the other scoring imports. After `_attach_realtime_chip_fields` returns the enriched rows and chip warnings, deduplicate those enriched rows by `ts_code` before scoring:

```python
deduplicated = list({str(row.get("ts_code") or ""): row for row in all_rows}.values())
position_candidates = rank_position_candidates(
    deduplicated,
    limit=min(int(limit), 10),
    market_phase=phase,
)
```

Keep `_group_realtime_stage_rows(all_rows, limit)` and its three legacy fields unchanged for compatibility. Set `stocks` to the unified list, not the pre-score list.

- [ ] **Step 4: Add filter diagnostics with exact final reasons**

Implement `_build_position_filter_debug(source_rows, candidates)` returning:

```python
{
    "source_count": len(source_rows),
    "visible_count": len(candidates),
    "filtered_count": len(source_rows) - len(candidates),
    "top_reasons": [{"reason": "综合分低于50", "count": 3}],
    "samples": [{"ts_code": "600001.SH", "name": "示例股份", "reason": "综合分低于50"}],
}
```

Use `position_level_reason`, `position_missing_confirmations`, hard-gate reason, or `综合分低于50` as concrete reasons. Do not label score/hard-gate removals as “TOP排序截断”. Add an integration assertion that these reasons are visible.

- [ ] **Step 5: Include scoring version in realtime cache identity**

Extend `_realtime_intraday_cache_key` and the persistent `realtime_info` cache key with `position_score_version()`. This prevents old three-table results or old weights from surviving a deploy under the same cache key. Update cache-key tests to assert `position-candidate-v1` is present.

- [ ] **Step 6: Run realtime integration and cache tests**

Run:

```bash
HOME=/tmp/piao-test-home .venv/bin/python -c '
import indicator_settings
indicator_settings.load_macd_settings=lambda:{"fast_period":5,"slow_period":34,"signal_period":5,"version":1}
import unittest
suite=unittest.defaultTestLoader.loadTestsFromNames([
  "tests.test_position_candidate_scoring",
  "tests.test_realtime_info_service.RealtimeInfoServiceTests.test_realtime_intraday_returns_one_unified_position_pool",
  "tests.test_realtime_info_service.RealtimeInfoServiceTests.test_unified_position_pool_does_not_backfill_to_five",
  "tests.test_realtime_info_service.RealtimeInfoServiceTests.test_intraday_cache_key_includes_position_score_version",
])
result=unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
'
```

Expected: all selected tests PASS without network or MySQL access.

- [ ] **Step 7: Commit realtime integration**

```bash
git add realtime_info_service.py tests/test_realtime_info_service.py
git commit -m "feat: expose unified position candidates"
```

---

### Task 3: Render one conservative position table

**Files:**
- Modify: `quantClient/main.js:715-749`
- Modify: `quantClient/index.html:924-1030`
- Modify: `quantClient/styles.css`
- Modify: `quantClient/realtime-tail-premium-layout.test.js`
- Create: `quantClient/position-candidate-layout.test.js`

**Interfaces:**
- Consumes: `realtimeInfo.intraday.position_candidates` from Task 2, with fallback to `intraday.stocks` during mixed-version deployment.
- Produces: computed `realtimePositionCandidates` and one “近期观察与建仓” table.

- [ ] **Step 1: Write a failing frontend layout contract**

Create `quantClient/position-candidate-layout.test.js` that loads `index.html` and `main.js`, then asserts:

```javascript
assert.ok(main.includes('realtimePositionCandidates'));
assert.ok(main.includes('position_candidates'));
assert.ok(html.includes('近期观察与建仓'));
assert.ok(html.includes('position_level'));
assert.ok(html.includes('position_score'));
assert.ok(html.includes('sector_hot_score'));
assert.ok(html.includes('price_volume_score'));
assert.ok(html.includes('macd_score'));
assert.ok(html.includes('chip_peak_score'));
assert.ok(html.includes('position_risk_items'));
assert.ok(!html.includes('v-for="stageTable in realtimeStageTables"'));
```

Update `realtime-tail-premium-layout.test.js` so it no longer requires the three table titles/computed arrays, while it still verifies that bottom-stage detail fields and the independent tail-premium module remain available.

- [ ] **Step 2: Run Node tests and verify RED**

Run:

```bash
node quantClient/position-candidate-layout.test.js
node quantClient/realtime-tail-premium-layout.test.js
```

Expected: the new contract FAILS because the unified computed property/table do not exist; the updated regression test may also fail until the old table loop is removed.

- [ ] **Step 3: Replace the three computed lists with one compatible list**

In `main.js`, replace frontend use of `realtimeObservationRows`, `realtimeTriggerRows`, `realtimeLaunchRows`, and `realtimeStageTables` with:

```javascript
realtimePositionCandidates() {
  const intraday = this.realtimeInfo && this.realtimeInfo.intraday;
  if (intraday && Array.isArray(intraday.position_candidates)) {
    return intraday.position_candidates;
  }
  return intraday && Array.isArray(intraday.stocks) ? intraday.stocks : [];
},
```

Keep existing display helpers (`chipPeakDisplay`, `monitorSignalText`, market-relative and tail helpers) and add only small helpers for level badge and joined score reasons.

- [ ] **Step 4: Replace the three panels with one table**

Render one panel headed “近期观察与建仓”. Required columns:

1. 股票/板块;
2. 建仓等级/综合分;
3. 热点板块/板块分;
4. 当前价/涨跌幅;
5. 量价与主力/量价分;
6. 日线与 60 分钟 MACD/MACD 分;
7. 筹码峰/筹码分;
8. 相对强度/尾盘承接;
9. 风险与入选原因.

Use an expandable `<details>` block in the final column for `position_positive_reasons`, `position_missing_confirmations`, `position_risk_items`, and existing `bottom_consolidation_reason`. The empty row must say “暂无达到保守型观察建仓标准的股票”.

- [ ] **Step 5: Add scoped layout styles**

Add `.position-candidate-table` styles only. Permit reason wrapping and compact score stacks; do not change the independent tail-premium table styles. Keep horizontal scrolling available on narrow screens.

- [ ] **Step 6: Run frontend layout regressions**

Run:

```bash
node quantClient/position-candidate-layout.test.js
node quantClient/realtime-tail-premium-layout.test.js
node quantClient/chip-peak-layout.test.js
node quantClient/realtime-info-utils.test.js
```

Expected: all four scripts print their success messages and exit 0.

- [ ] **Step 7: Commit the unified UI**

```bash
git add quantClient/main.js quantClient/index.html quantClient/styles.css \
  quantClient/position-candidate-layout.test.js \
  quantClient/realtime-tail-premium-layout.test.js
git commit -m "feat: show one position candidate table"
```

---

### Task 4: Validate API compatibility, missing-data degradation, and limits

**Files:**
- Modify: `tests/test_realtime_info_service.py`
- Modify: `tests/test_realtime_info_api.py`
- Modify: `quantClient/position-candidate-layout.test.js`

**Interfaces:**
- Consumes: final Task 2 response contract.
- Verifies: endpoint forwards `limit`, response retains legacy arrays, unified list is capped at 10, and missing data downgrades instead of aborting.

- [ ] **Step 1: Add failing service-level degradation tests**

Add tests that build candidates with:

- missing chip data;
- missing sector data;
- missing 60-minute data;
- unavailable tail minutes before 14:30;
- unavailable tail minutes after 14:30;
- one malformed candidate mixed with valid candidates.

Assert the batch remains successful; missing chip/sector/after-14:30 confirmation cannot produce `立即建仓`; pre-14:30 tail absence is a missing confirmation rather than a risk deduction; one malformed row is skipped with a warning.

- [ ] **Step 2: Run degradation tests and verify RED**

Run:

```bash
HOME=/tmp/piao-test-home .venv/bin/python -m unittest \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_unified_candidates_degrade_missing_confirmations \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_unified_candidates_skip_one_malformed_row -v
```

Expected: FAIL until all degradation paths and warnings are wired through the realtime section.

- [ ] **Step 3: Implement batch-safe scoring integration**

Wrap only the per-row scoring boundary, not the entire builder:

```python
scored, warnings = [], []
for row in deduplicated:
    try:
        scored.append(score_position_candidate(row, market_phase=phase))
    except Exception as exc:
        warnings.append(f"{row.get('ts_code') or '--'} 统一建仓评分失败: {str(exc)[:120]}")
position_candidates = rank_scored_position_candidates(scored, limit=min(limit, 10))
```

If Task 1 exposes only `rank_position_candidates`, extend it with `rank_scored_position_candidates(scored, limit=10)` and add a pure test for it before changing integration. Merge warnings into `fallback_warnings` without duplicates.

- [ ] **Step 4: Add API response compatibility assertions**

In `tests/test_realtime_info_api.py`, patch the service result with a complete unified payload and assert `/api/realtime-info` returns:

```python
intraday = response.get_json()["intraday"]
self.assertEqual(intraday["stocks"], intraday["position_candidates"])
self.assertLessEqual(len(intraday["position_candidates"]), 10)
self.assertIn("observation_stocks", intraday)
self.assertIn("trigger_stocks", intraday)
self.assertIn("launch_stocks", intraday)
```

No Java endpoint change is required because the existing realtime endpoint forwards the response generically.

- [ ] **Step 5: Run API and service compatibility tests**

Run:

```bash
HOME=/tmp/piao-test-home .venv/bin/python -c '
import indicator_settings
indicator_settings.load_macd_settings=lambda:{"fast_period":5,"slow_period":34,"signal_period":5,"version":1}
import unittest
suite=unittest.defaultTestLoader.loadTestsFromNames([
  "tests.test_position_candidate_scoring",
  "tests.test_realtime_info_api",
  "tests.test_realtime_info_service",
])
result=unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(not result.wasSuccessful())
'
```

Expected: all tests PASS. If an existing test expects three independently truncated frontend groups, update only its expected consumer behavior; retain backend legacy arrays.

- [ ] **Step 6: Commit compatibility coverage**

```bash
git add position_candidate_scoring.py realtime_info_service.py \
  tests/test_position_candidate_scoring.py tests/test_realtime_info_service.py \
  tests/test_realtime_info_api.py quantClient/position-candidate-layout.test.js
git commit -m "test: verify conservative position candidate flow"
```

---

### Task 5: Full verification and deployment handoff

**Files:**
- Verify: `position_candidate_scoring.py`
- Verify: `realtime_info_service.py`
- Verify: `tests/test_position_candidate_scoring.py`
- Verify: `tests/test_realtime_info_service.py`
- Verify: `tests/test_realtime_info_api.py`
- Verify: `quantClient/main.js`
- Verify: `quantClient/index.html`
- Verify: `quantClient/styles.css`
- Verify: `quantClient/position-candidate-layout.test.js`
- Verify: `quantClient/realtime-tail-premium-layout.test.js`

**Interfaces:**
- Consumes: completed backend and frontend implementation.
- Produces: verified deployable unified candidate flow and a concise restart/cache-refresh handoff.

- [ ] **Step 1: Run focused Python tests**

```bash
HOME=/tmp/piao-test-home .venv/bin/python -c '
import indicator_settings
indicator_settings.load_macd_settings=lambda:{"fast_period":5,"slow_period":34,"signal_period":5,"version":1}
import unittest
suite=unittest.defaultTestLoader.loadTestsFromNames([
  "tests.test_position_candidate_scoring",
  "tests.test_realtime_info_service",
  "tests.test_realtime_info_api",
  "tests.test_tail_premium_scoring",
  "tests.test_realtime_tail_premium_service",
])
result=unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(not result.wasSuccessful())
'
```

Expected: zero failures and zero errors.

- [ ] **Step 2: Run frontend tests when Node is available**

```bash
node quantClient/position-candidate-layout.test.js
node quantClient/realtime-tail-premium-layout.test.js
node quantClient/chip-peak-layout.test.js
node quantClient/realtime-info-utils.test.js
```

Expected: every script exits 0. If Node is unavailable, record that limitation explicitly; do not claim frontend tests passed.

- [ ] **Step 3: Run syntax and diff verification**

```bash
.venv/bin/python -m compileall -q \
  position_candidate_scoring.py realtime_info_service.py \
  tests/test_position_candidate_scoring.py tests/test_realtime_info_service.py \
  tests/test_realtime_info_api.py
git diff --check
```

Expected: exit 0 with no output from `git diff --check`.

- [ ] **Step 4: Inspect one controlled response**

Build a fixture response or call the locally running endpoint with `debug=true`. Verify:

- only one “近期观察与建仓” frontend table is consumed;
- `position_candidates` and `stocks` match;
- each visible row has a level, total score, six component scores, risk deductions, and explanations;
- rows are ordered immediate → confirmation → observation;
- list size is 0–10;
- the independent tail-premium payload is unchanged.

- [ ] **Step 5: Commit final verification-only adjustments if any**

If verification required no changes, do not create an empty commit. If it exposed a documentation or test correction:

```bash
git add position_candidate_scoring.py realtime_info_service.py \
  tests/test_position_candidate_scoring.py tests/test_realtime_info_service.py \
  tests/test_realtime_info_api.py quantClient/main.js quantClient/index.html \
  quantClient/styles.css quantClient/position-candidate-layout.test.js \
  quantClient/realtime-tail-premium-layout.test.js
git commit -m "chore: finalize unified position candidates"
```

- [ ] **Step 6: Deployment handoff**

Report the exact test counts and any environment limitations. Instruct deployment to restart the Python service, then force-refresh realtime information once so the new score-version cache key populates. Java restart is unnecessary unless its process caches the complete HTTP response outside the existing client behavior.
