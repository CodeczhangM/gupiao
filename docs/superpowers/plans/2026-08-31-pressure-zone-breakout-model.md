# Pressure Zone Breakout Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the mechanical single-price breakout logic with configurable pressure zones, explainable breakout validation, false-breakout risk, risk/reward, split scores, and A+/A/B+/B/C/X entry states.

**Architecture:** Keep the existing candidate endpoint and `extract_pullback_confirmation()` compatibility entry point. Add focused settings, pressure-zone, and breakout-evaluation modules; calculate market-wide structure from database daily bars, then enrich only the top ten candidates with bounded concurrent minute requests before final scoring.

**Tech Stack:** Python 3.12, pandas, FastAPI, PyMySQL, unittest, Vue 3 static client, Spring Boot proxy.

**Spec:** `docs/superpowers/specs/2026-08-31-pressure-zone-breakout-model-design.md`

## Global Constraints

- Keep `/api/realtime-info/position-candidates` and its Java proxy route compatible.
- Keep legacy response fields during migration: `primary_support`, `confirmation_price`, `position_score`, and `position_level`.
- Use database daily bars for the market-wide pass; request minute data for at most 10 candidates.
- Use 5 minute-request workers, 6-second request timeout, 15-second network-stage budget, and 45-second total soft budget by default.
- Missing chip or minute data lowers `data_confidence`; it must not be treated as a zero-quality indicator.
- Do not add AI prediction or non-deterministic scoring.
- Implement every production behavior through a failing test first.

---

### Task 1: Configurable Position Strategy Settings

**Files:**
- Create: `position_strategy_settings.py`
- Create: `position_strategy_settings_models.py`
- Modify: `app.py`
- Modify: `position_candidate_scoring.py`
- Test: `tests/test_position_strategy_settings.py`
- Test: `tests/test_position_strategy_settings_api.py`

**Interfaces:**
- Produces: `load_position_strategy_settings(force: bool = False) -> dict[str, Any]`
- Produces: `update_position_strategy_settings(payload: dict[str, Any]) -> dict[str, Any]`
- Produces: `position_strategy_parameter_key(settings: dict | None = None) -> str`
- Produces: GET/PUT `/api/indicator-settings/position-strategy`

- [ ] **Step 1: Write failing validation and persistence tests**

Test that defaults contain the exact `pressure`, `breakout`, `distance`, `risk_reward`, and `network` groups from the spec; invalid ordering such as `critical_pct >= waiting_pct` raises `ValueError`; update increments `version`; returned dictionaries are defensive copies.

- [ ] **Step 2: Run the settings tests and verify RED**

Run: `.venv/bin/python -m unittest -v tests.test_position_strategy_settings`

Expected: import failure because `position_strategy_settings` does not exist.

- [ ] **Step 3: Implement settings storage and validation**

Create one MySQL row with `setting_key='position_strategy'`, a JSON settings document, version, and timestamp. Deep-merge submitted values over defaults, validate numeric ranges and ordered thresholds, cache for five seconds, and return:

```python
{
    "pressure": {...},
    "breakout": {...},
    "distance": {...},
    "risk_reward": {...},
    "network": {...},
    "version": 2,
    "updated_at": "2026-08-31 15:00:00",
}
```

- [ ] **Step 4: Write failing API tests**

Mock only the settings service boundary. Verify GET returns the document, PUT forwards the nested payload, validation errors return HTTP 400, and successful update clears realtime derived caches.

- [ ] **Step 5: Implement the FastAPI models and routes**

Add nested Pydantic models with optional fields for partial updates. Add GET/PUT routes beside MACD settings. Include `position_strategy_parameter_key()` in `position_score_version()` so configuration changes invalidate scoring caches.

- [ ] **Step 6: Run focused tests**

Run: `.venv/bin/python -m unittest -v tests.test_position_strategy_settings tests.test_position_strategy_settings_api tests.test_indicator_settings`

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add position_strategy_settings.py position_strategy_settings_models.py app.py position_candidate_scoring.py tests/test_position_strategy_settings.py tests/test_position_strategy_settings_api.py
git commit -m "feat: add configurable position strategy settings"
```

### Task 2: Pressure Candidate Extraction and ATR Clustering

**Files:**
- Create: `pressure_zone_service.py`
- Modify: `position_candidate_history.py`
- Test: `tests/test_pressure_zone_service.py`

**Interfaces:**
- Produces: `calculate_atr(bars: pd.DataFrame, period: int = 14) -> float | None`
- Produces: `extract_pressure_candidates(bars, gene, chip_context, settings) -> list[dict]`
- Produces: `cluster_pressure_candidates(candidates, atr, settings) -> list[dict]`
- Produces each zone with `lower`, `upper`, `touch_count`, `strength_score`, `sources`, `touches`, and `evidence`

- [ ] **Step 1: Write failing ATR and pivot tests**

Use literal OHLC fixtures. Verify true-range gap handling, two-sided pivot detection, rejection within five days, and rejection threshold `max(2%, 0.8 * ATR%)`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.venv/bin/python -m unittest -v tests.test_pressure_zone_service.PressureCandidateTests`

Expected: import failure for the new module.

- [ ] **Step 3: Implement normalization, ATR, pivots, and rejection evidence**

Normalize dates and finite OHLCV values once. A pivot records price, date, source, volume ratio, subsequent drawdown, and recency. Do not accept a normal local high without required rejection.

- [ ] **Step 4: Write failing source-priority tests**

Verify extraction identifies: a platform with two or three touches; a 1.5x-volume rejection high; a post-limit next-day or platform high; a chip upper edge when supplied; and a 20-day maximum only when no structural candidates exist.

- [ ] **Step 5: Implement pressure source extraction**

Return independent evidence entries rather than immediately collapsing them. Label fallback evidence exactly `N日最高价兜底` and give it lower base strength than structural sources.

- [ ] **Step 6: Write failing ATR-cluster tests**

Verify candidates merge when distance is within:

```python
min(reference_price * 0.02, max(atr * 0.35, reference_price * 0.01))
```

Verify prices outside the threshold remain separate and a cluster never spans more than two percent.

- [ ] **Step 7: Implement clustering and pressure strength**

Score touch count 25, rejection 20, volume 15, post-limit structure 15, recency 10, chip confluence 10, and multi-source evidence 5. Preserve raw evidence and cap at 100.

- [ ] **Step 8: Integrate without changing the public history entry point yet**

Import the new functions into `position_candidate_history.py`; leave existing support-zone behavior intact until Task 4 switches the compatibility entry point.

- [ ] **Step 9: Run focused and existing history tests**

Run: `.venv/bin/python -m unittest -v tests.test_pressure_zone_service tests.test_position_candidate_history`

Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add pressure_zone_service.py position_candidate_history.py tests/test_pressure_zone_service.py
git commit -m "feat: detect atr clustered pressure zones"
```

### Task 3: Select the Actionable Pressure Zone and Build the Trade Plan

**Files:**
- Modify: `pressure_zone_service.py`
- Test: `tests/test_pressure_zone_service.py`

**Interfaces:**
- Produces: `select_actionable_pressure_zone(zones, current_price, settings) -> tuple[dict | None, str]`
- Produces: `build_breakout_trade_plan(support_zone, selected_zone, higher_zones, current_price, atr, settings) -> dict`

- [ ] **Step 1: Write failing distance-aware selection tests**

Given current price 10.00, zone 10.20/strength 82 and zone 12.50/strength 90, require selection of 10.20. Verify a recently crossed zone remains selectable for breakthrough evaluation and zones above five percent remain only target candidates.

- [ ] **Step 2: Run selection tests and verify RED**

Run: `.venv/bin/python -m unittest -v tests.test_pressure_zone_service.PressureSelectionTests`

Expected: missing function failure.

- [ ] **Step 3: Implement distance suitability and explanation**

Rank zones using explicit distance bands followed by strength, touches, recency, and price. Return a Chinese explanation containing distance, strength, selected source, and why a stronger remote zone lost.

- [ ] **Step 4: Write failing trigger, confirm, stop, target, and distance tests**

Verify:

```python
trigger = max(high * 1.001, high + 0.05 * atr)
confirm = max(high * 1.005, high + 0.30 * atr)
invalid = min(support_low * 0.985, support_low - 0.30 * atr)
```

Require `pressure_low <= pressure_high <= breakout_trigger <= breakout_confirm`; compute all three distance fields. Prefer the next higher zone for `target_price`, otherwise use structural height `pressure_high + (pressure_mid - support_price)`.

- [ ] **Step 5: Implement the trade-plan builder**

Return null plan values and an explicit missing reason when pressure, support, ATR, or target structure is unavailable. Never synthesize a target from desired risk/reward.

- [ ] **Step 6: Run pressure service tests**

Run: `.venv/bin/python -m unittest -v tests.test_pressure_zone_service`

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add pressure_zone_service.py tests/test_pressure_zone_service.py
git commit -m "feat: build distance aware breakout trade plans"
```

### Task 4: Breakout Quality, False-Breakout Risk, and Risk/Reward

**Files:**
- Create: `breakout_trade_evaluation.py`
- Modify: `position_candidate_history.py`
- Test: `tests/test_breakout_trade_evaluation.py`
- Test: `tests/test_position_candidate_history.py`

**Interfaces:**
- Produces: `evaluate_breakout(daily_bar, plan, context, settings) -> dict`
- Produces: `calculate_risk_reward(plan, breakout_state, current_price, settings) -> dict`
- Compatibility: `extract_pullback_confirmation(...)` returns old and new fields

- [ ] **Step 1: Write failing breakout-state boundary tests**

Test NOT_TRIGGERED, TOUCHING, TRIGGERED, CONFIRMED, FAILED, and OVEREXTENDED using literal prices around pressure, trigger, and confirm. A close above confirm without volume and close-position confirmation must not become CONFIRMED.

- [ ] **Step 2: Run state tests and verify RED**

Run: `.venv/bin/python -m unittest -v tests.test_breakout_trade_evaluation.BreakoutStateTests`

- [ ] **Step 3: Implement state and quality scoring**

Score price 25, volume 20, close position 15, upper shadow 10, hold duration 10, tail 10, VWAP 5, and sector 5. Normalize over available weight, expose `breakout_quality_available_weight`, and return null score with `未触发` before pressure interaction.

- [ ] **Step 4: Write failing false-breakout tests**

Verify a close back inside pressure, close-position below 0.5, missing volume, upper shadow over 0.4, three prior failed attacks, tail reversal, below-VWAP close, and next-day return each add separate evidence. Verify LOW/MEDIUM/HIGH boundaries at 25 and 50.

- [ ] **Step 5: Implement independent false-breakout risk**

Return `false_breakout_risk_score`, `false_breakout_risk`, and `false_breakout_evidence`. Keep risk independent from quality and mark a severe close-back failure.

- [ ] **Step 6: Write failing risk/reward tests**

Verify entry selection by state, positive risk and reward requirements, ratios 1.49/1.5/2/3, null output for missing plan prices, and evidence containing entry, stop, target, risk, and reward.

- [ ] **Step 7: Implement risk/reward calculation**

Return ratio, 0-100 score, label, and calculation evidence. Do not backsolve target from a desired ratio.

- [ ] **Step 8: Replace mechanical confirmation through the compatibility entry point**

In `extract_pullback_confirmation()`, keep current support extraction, call pressure extraction, clustering, selection, plan, daily evaluation, and risk/reward. Map:

```python
primary_support = support_price
confirmation_price = breakout_confirm
breakout_confirmed = breakout_state == "CONFIRMED"
breakout_pct = -distance_to_confirm_pct
```

- [ ] **Step 9: Run evaluation and history suites**

Run: `.venv/bin/python -m unittest -v tests.test_breakout_trade_evaluation tests.test_position_candidate_history tests.test_pressure_zone_service`

Expected: all pass after updating legacy expectations to the new structural fixtures.

- [ ] **Step 10: Commit**

```bash
git add breakout_trade_evaluation.py position_candidate_history.py tests/test_breakout_trade_evaluation.py tests/test_position_candidate_history.py
git commit -m "feat: evaluate breakout quality and trade risk"
```

### Task 5: Split Scores and A+/A/B+/B/C/X Levels

**Files:**
- Modify: `position_candidate_scoring.py`
- Test: `tests/test_position_candidate_scoring.py`

**Interfaces:**
- Produces: `stock_quality_score`, `entry_timing_score`, `breakout_quality_score`, `risk_reward_score`, `data_confidence`, `final_score`
- Produces: `build_position_level` and `build_position_status`
- Compatibility: `position_score=final_score`, `position_level=build_position_status`

- [ ] **Step 1: Write failing split-score tests**

Verify stock quality weights are sector25, daily price-volume20, support20, limit/resonance15, chip10, MACD5, relative strength5. Verify missing chip is excluded from available-weight normalization and lowers confidence instead of becoming a zero-quality observation.

- [ ] **Step 2: Run scoring tests and verify RED**

Run: `.venv/bin/python -m unittest -v tests.test_position_candidate_scoring.PositionCandidateScoringTests.test_score_exposes_new_breakout_breakdown`

- [ ] **Step 3: Implement score components and compact MACD state**

Return `macd_strength` as 强/中/弱. Keep detailed MACD evidence for debugging but cap its quality contribution at five points.

- [ ] **Step 4: Write failing level boundary tests**

Cover A+ confirmation requirements, A at exactly1.5%, B+ through3%, C through5%, X above5%, B overextended, RR below1.5 veto, HIGH false-breakout veto, and missing-data C downgrade. Verify already-triggered rows never display 等待突破.

- [ ] **Step 5: Implement hard-rule level classification**

Apply visibility and risk gates before scores. Rank visible levels in order A+, A, B+, B, C, then final score, entry timing, stock quality, and code. Preserve exact filter reasons for X rows.

- [ ] **Step 6: Run scoring tests**

Run: `.venv/bin/python -m unittest -v tests.test_position_candidate_scoring`

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add position_candidate_scoring.py tests/test_position_candidate_scoring.py
git commit -m "feat: rank candidates by breakout entry quality"
```

### Task 6: Bounded Minute Enrichment

**Files:**
- Create: `position_candidate_minute_enrichment.py`
- Modify: `realtime_info_service.py`
- Test: `tests/test_position_candidate_minute_enrichment.py`
- Test: `tests/test_realtime_info_service.py`

**Interfaces:**
- Produces: `enrich_position_candidates_with_minutes(rows, trade_date, now, loader, settings) -> tuple[list[dict], list[str], dict]`
- Consumes at most `network.enrichment_limit` daily-prefiltered rows
- Produces performance fields for attempted, completed, timed-out, failed, and elapsed milliseconds

- [ ] **Step 1: Write failing minute-metric tests**

With literal minute bars, verify VWAP from amount/volume when available, typical-price weighted fallback, close above/below VWAP, post-trigger hold ratio, 14:30 return, and tail-stability evidence.

- [ ] **Step 2: Run minute tests and verify RED**

Run: `.venv/bin/python -m unittest -v tests.test_position_candidate_minute_enrichment.MinuteMetricTests`

- [ ] **Step 3: Implement pure minute metric calculation**

Normalize timestamps before comparisons. Return unavailable fields and warnings for empty or malformed frames; never compare strings to pandas timestamps.

- [ ] **Step 4: Write failing concurrency and budget tests**

Use a controlled loader to verify only ten rows are requested, five workers are used, completed results merge by code, failures preserve daily rows, and elapsed budget stops accepting late results. Inject clock/executor boundaries where necessary rather than sleeping in tests.

- [ ] **Step 5: Implement bounded enrichment**

Use configured workers and per-request timeout. Respect the 15-second stage deadline, cancel pending work, avoid waiting for abandoned futures during response assembly, and return daily rows with missing-data evidence on failure.

- [ ] **Step 6: Integrate the two-pass candidate pipeline**

Load 60 daily days. Build and score daily rows, remove X and distance-over-five rows, select the top ten, enrich them, then recompute breakout evaluation and final scoring. Do not request minutes for the rest of the market.

- [ ] **Step 7: Add performance and fallback assertions**

Verify response performance includes `minute_enrichment_ms`, counts, configured budget, total elapsed time, and zero network calls when no daily candidate survives.

- [ ] **Step 8: Run service tests**

Run: `.venv/bin/python -m unittest -v tests.test_position_candidate_minute_enrichment tests.test_realtime_info_service tests.test_realtime_info_api`

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add position_candidate_minute_enrichment.py realtime_info_service.py tests/test_position_candidate_minute_enrichment.py tests/test_realtime_info_service.py tests/test_realtime_info_api.py
git commit -m "perf: enrich breakout candidates within minute budget"
```

### Task 7: Chip Pressure Edges and Explainable Debug Output

**Files:**
- Modify: `chip_peak_service.py`
- Modify: `realtime_info_service.py`
- Test: `tests/test_chip_peak_service.py`
- Test: `tests/test_realtime_info_service.py`

**Interfaces:**
- Produces: `chip_pressure_low`, `chip_pressure_high`, `chip_pressure_data_available`, `chip_pressure_reason`
- Produces debug fields: zones, selection reason, breakout evidence, false-breakout evidence, RR evidence, score components, and missing data

- [ ] **Step 1: Write failing chip-edge tests**

Construct a chip distribution with a dense cluster above current price. Verify the cluster lower/upper bounds and source. Verify absent chips return `available=false`, null bounds, and a missing reason rather than zero-valued pressure.

- [ ] **Step 2: Run chip tests and verify RED**

Run: `.venv/bin/python -m unittest -v tests.test_chip_peak_service`

- [ ] **Step 3: Implement chip pressure extraction and cache fields**

Cluster adjacent chip prices using the same percentage/ATR context where possible, choose the strongest cluster above current price, and retain fields in cached chip payloads.

- [ ] **Step 4: Write failing debug-contract tests**

Verify filtered samples include selected and rejected pressure zones, selection reason, distances, quality evidence, false-breakout evidence, RR calculation, score components, confidence, and exact X reason.

- [ ] **Step 5: Extend debug payloads and warnings**

Keep normal console output quiet. Put per-stock evidence in response debug structures and operational failures in `fallback_warnings`.

- [ ] **Step 6: Run chip and service tests**

Run: `.venv/bin/python -m unittest -v tests.test_chip_peak_service tests.test_realtime_info_service`

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add chip_peak_service.py realtime_info_service.py tests/test_chip_peak_service.py tests/test_realtime_info_service.py
git commit -m "feat: explain chip pressure and breakout filtering"
```

### Task 8: Java Proxy for Position Strategy Settings

**Files:**
- Create: `quantServer/quantServer/src/main/java/com/codec/quantserver/dto/PositionStrategySettingsRequest.java`
- Modify: `quantServer/quantServer/src/main/java/com/codec/quantserver/service/QuantPythonClient.java`
- Modify: `quantServer/quantServer/src/main/java/com/codec/quantserver/controller/QuantController.java`
- Modify: `quantServer/quantServer/src/test/java/com/codec/quantserver/controller/QuantControllerTest.java`

**Interfaces:**
- Produces: GET/PUT `/api/quant/indicator-settings/position-strategy`
- Forwards nested JSON without flattening numeric configuration groups

- [ ] **Step 1: Write failing controller forwarding tests**

Create local `QuantPythonClient` and `MockMvc` variables in each test. Verify GET and PUT routes, nested request JSON, HTTP status, and forwarded client methods.

- [ ] **Step 2: Run Java test compilation and verify RED**

Run: `mvn -q test-compile`

Working directory: `quantServer/quantServer`

Expected: missing DTO/client/controller methods.

- [ ] **Step 3: Implement DTO, client, and controller routes**

Represent nested settings as `Map<String, Object>` fields or a single validated map payload so new configuration keys remain forward compatible. Preserve the existing response map behavior.

- [ ] **Step 4: Verify Java compilation and tests**

Run: `mvn -q test-compile`

Then run: `mvn -q -Dtest=QuantControllerTest test`

Expected: compilation passes; if WSL blocks Mockito agent attachment, report that environmental failure separately after confirming `test-compile` passes.

- [ ] **Step 5: Commit**

```bash
git add quantServer/quantServer/src/main/java quantServer/quantServer/src/test/java/com/codec/quantserver/controller/QuantControllerTest.java
git commit -m "feat: proxy position strategy settings"
```

### Task 9: Candidate Table and Expandable Evidence

**Files:**
- Modify: `quantClient/index.html`
- Modify: `quantClient/main.js`
- Modify: `quantClient/styles.css`
- Modify: `tests/test_position_candidate_layout.py`

**Interfaces:**
- Consumes all new candidate fields while tolerating legacy-only responses
- Displays A+/A/B+/B/C labels, trigger distance, pressure plan, quality, risk, RR, sector, tail, and compact score summary

- [ ] **Step 1: Write failing layout contract tests**

Require headers for 建仓等级, 距触发价, 支撑/压力区, 触发/确认, 突破质量, 假突破, 盈亏比, 板块, 尾盘, 评分摘要, and 依据. Require no standalone MACD score column and require expandable evidence bindings.

- [ ] **Step 2: Run layout tests and verify RED**

Run: `.venv/bin/python -m unittest -v tests.test_position_candidate_layout`

- [ ] **Step 3: Implement display helpers**

Add helpers for distance text, pressure range, breakout quality label, risk badge, RR label, compact score summary, MACD strong/medium/weak, and legacy fallbacks.

- [ ] **Step 4: Rebuild the candidate table**

Use wrapped detail cells and horizontal scrolling. Add one expandable detail row per stock containing pressure sources, zone selection, score components, breakout evidence, risk evidence, RR calculation, and missing data.

- [ ] **Step 5: Update level badges and cache-busting versions**

Map A+ to strong, A/B+ to watch, B/C to neutral, and X to hidden/debug. Update CSS and JS query versions so browsers load the new layout immediately.

- [ ] **Step 6: Run frontend contract tests**

Run: `.venv/bin/python -m unittest -v tests.test_position_candidate_layout tests.test_realtime_info_api`

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add quantClient/index.html quantClient/main.js quantClient/styles.css tests/test_position_candidate_layout.py
git commit -m "feat: show actionable breakout trade plans"
```

### Task 10: Full Regression, Performance Verification, and Documentation

**Files:**
- Modify if evidence requires: implementation and test files from Tasks 1-9
- Modify: `docs/superpowers/specs/2026-08-31-pressure-zone-breakout-model-design.md` only if verified behavior requires a documented correction

**Interfaces:**
- Verifies the complete endpoint and compatibility contract

- [ ] **Step 1: Run focused Python suites**

Run:

```bash
.venv/bin/python -m unittest -v \
  tests.test_position_strategy_settings \
  tests.test_position_strategy_settings_api \
  tests.test_pressure_zone_service \
  tests.test_breakout_trade_evaluation \
  tests.test_position_candidate_history \
  tests.test_position_candidate_scoring \
  tests.test_position_candidate_minute_enrichment \
  tests.test_chip_peak_service \
  tests.test_realtime_info_service \
  tests.test_realtime_info_api \
  tests.test_position_candidate_layout
```

Expected: all pass with no warnings caused by the new code.

- [ ] **Step 2: Run Java verification**

Run: `mvn -q test-compile` in `quantServer/quantServer`, then the controller test if Mockito attachment is available.

- [ ] **Step 3: Benchmark the daily-only and minute-enhanced paths**

Use dependency-injected loaders and production timing fields. Verify daily market-wide computation performs zero network calls, minute requests are at most ten, network elapsed is at most the configured 15-second budget, and total soft target is 45 seconds. Record actual elapsed values.

- [ ] **Step 4: Verify response invariants**

For every visible row assert pressure ordering, finite distances, allowed build level, RR gate, no X rows, no row beyond five-percent trigger distance, and complete explanation fields. Verify legacy fields remain present.

- [ ] **Step 5: Run diff and repository checks**

Run: `git diff --check` and inspect `git status --short`. Do not stage unrelated user changes.

- [ ] **Step 6: Commit any verification fixes separately**

```bash
git add tests/test_position_candidate_scoring.py tests/test_realtime_info_service.py
git commit -m "test: verify pressure zone breakout pipeline"
```

If verification requires fixes in different implementation files, replace the two listed test paths with the exact related files shown by `git diff --name-only`; never stage unrelated workspace changes.

- [ ] **Step 7: Report measured results**

Report test counts, Java compilation status, daily and enhanced elapsed time, request counts, fallback behavior, commits, and any environment-only limitation.
