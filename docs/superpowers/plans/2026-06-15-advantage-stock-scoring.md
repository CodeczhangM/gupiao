# Advantage Stock Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the existing strong-stock ranking with the approved binary 100-point advantage-stock score and expose its score breakdown consistently through CLI, API, backtest, AI formatting, and the web table.

**Architecture:** Keep `pick_stocks(df, hist_df)` as the public strategy interface. Build per-stock indicators from 60 daily rows, merge them into the cleaned market snapshot, calculate seven boolean signals and weighted score columns, then sort with explicit tie-breakers. Reuse the same function from every entry point so scanning and backtesting stay consistent.

**Tech Stack:** Python 3, pandas, standard-library `unittest`, Vue 3 template.

---

### Task 1: Add Scoring Behavior Tests

**Files:**
- Create: `tests/test_advantage_stock_scoring.py`
- Test: `strategy.py`

- [ ] **Step 1: Write a synthetic 60-day history fixture**

Create helpers that generate deterministic daily bars and a matching market snapshot. The fixture must control current close, current volume, prior five-day volume, prior 20-day high, industry, and the final two MACD values through close-series shape.

- [ ] **Step 2: Write failing tests for score components**

Cover bottom-area scoring, volume-price-rise scoring, MA20/MA30 scoring, MACD-turning-up scoring, platform-breakout scoring, hot-theme scoring, and the exact weighted total. Assert the public result contains all seven boolean fields and all seven `_score` fields.

- [ ] **Step 3: Write failing boundary and exclusion tests**

Assert that `1.20` bottom ratio and `1.5` volume ratio qualify, equality with MA20/MA30 and the prior platform high does not qualify, prior baselines exclude the current row, histories shorter than 60 rows are omitted, and zero prior volume yields a zero volume ratio.

- [ ] **Step 4: Run tests and verify RED**

Run: `python -m unittest tests.test_advantage_stock_scoring -v`

Expected: failures because the approved fields and score behavior do not exist yet.

### Task 2: Implement the 100-Point Strategy

**Files:**
- Modify: `strategy.py`
- Test: `tests/test_advantage_stock_scoring.py`

- [ ] **Step 1: Make base candidate filtering match the design**

Keep Shanghai/Shenzhen A shares, exclude codes starting with `3`, exclude names containing `ST`, and require `close > 3`. Remove turnover as a hard requirement while preserving the existing function signature for callers.

- [ ] **Step 2: Build approved 60-day indicators**

For each stock with at least 60 valid rows, calculate `ma20`, `ma30`, `recent_low_60`, prior-five-day mean volume, `volume_expand_rate`, `previous_high_20`, current MACD, previous MACD, and `macd_turning_up`. Prior-five volume and prior-20 high must use rows before the latest row.

- [ ] **Step 3: Rank hot industries**

Calculate the existing sector strength formula for all eligible industries and return the top five without applying the old rebound thresholds. Merge sector metrics for display and set `hot_theme` from membership in that top-five set.

- [ ] **Step 4: Calculate binary score fields**

Create:

```python
in_bottom_area_score = in_bottom_area * 20
volume_price_rise_score = volume_price_rise * 20
close_above_ma20_score = close_above_ma20 * 10
close_above_ma30_score = close_above_ma30 * 10
macd_turning_up_score = macd_turning_up * 10
volume_platform_breakout_score = volume_platform_breakout * 20
hot_theme_score = hot_theme * 10
```

Set `score` to their sum, construct `strong_reason` from matched Chinese item names, and sort by `score`, platform breakout, volume-price rise, hot theme, `pct_chg`, and `volume_expand_rate`, all descending.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `python -m unittest tests.test_advantage_stock_scoring -v`

Expected: all scoring tests pass.

### Task 3: Make Entry Points and Output Consistent

**Files:**
- Modify: `main.py`
- Modify: `strategy.py`
- Modify: `quantClient/main.js`
- Modify: `quantClient/index.html`
- Test: `tests/test_advantage_stock_scoring.py`

- [ ] **Step 1: Add a failing CLI wiring test**

Use `unittest.mock` to run `main.main()` with stubbed data services and assert the same 60-day `hist_df` object is passed to both `pick_stocks` and `pick_dip_stocks`.

- [ ] **Step 2: Run the wiring test and verify RED**

Run: `python -m unittest tests.test_advantage_stock_scoring.MainWiringTests -v`

Expected: failure because `main.py` currently calls `pick_stocks(df)` before loading history.

- [ ] **Step 3: Reorder CLI history loading**

Fetch 60 trading days immediately after the market snapshot, pass it into `pick_stocks(df, hist_df)`, and reuse it for dip scoring. Keep the existing fallback to an empty DataFrame on data-fetch failure.

- [ ] **Step 4: Expand AI-visible fields**

Update `format_for_ai` to include `ma30`, `recent_low_60`, `previous_high_20`, all seven boolean signals, all seven component scores, and total `score`.

- [ ] **Step 5: Replace strong-stock web columns**

For `mode === 'strong'`, display total score, score reason, and seven component scores. Preserve existing dip-stock fields for `mode !== 'strong'`. Rename visible strong-stock labels to “优势股” and update the frontend cache-buster date.

- [ ] **Step 6: Run wiring and scoring tests**

Run: `python -m unittest tests.test_advantage_stock_scoring -v`

Expected: all tests pass.

### Task 4: Regression Verification

**Files:**
- Verify: `strategy.py`
- Verify: `main.py`
- Verify: `quant_service.py`
- Verify: `backtest_service.py`
- Verify: `quantClient/main.js`

- [ ] **Step 1: Compile Python modules**

Run: `python -m compileall -q strategy.py main.py quant_service.py backtest_service.py tests`

Expected: exit code 0 with no syntax errors.

- [ ] **Step 2: Run all local unit tests**

Run: `python -m unittest discover -s tests -v`

Expected: all tests pass.

- [ ] **Step 3: Check frontend JavaScript syntax**

Run: `node --check quantClient/main.js`

Expected: exit code 0.

- [ ] **Step 4: Review the final diff**

Run: `git diff --check`

Expected: no whitespace errors. Confirm unrelated user changes remain intact and only the planned areas were modified.

