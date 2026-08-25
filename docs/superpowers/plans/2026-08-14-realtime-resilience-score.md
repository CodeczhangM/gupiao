# Realtime Resilience Score Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a daily-history `抗跌力` score to realtime confluence rows.

**Architecture:** Compute the score in `realtime_info_service.py` from the existing recent daily `history` dataframe, attach fields before intraday stock selection and preserve them through row construction. Render the field in the two realtime confluence tables through a small frontend formatter.

**Tech Stack:** Python, pandas, unittest, Vue template, Node assert tests.

## Global Constraints

- Use previous 20 trading days of daily bars, excluding the current realtime trade date when possible.
- Increase weights from old to recent rows.
- Do not fetch extra network data for this field.
- Keep output explainable with a score, label, and reason.

---

### Task 1: Backend Resilience Fields

**Files:**
- Modify: `tests/test_realtime_info_service.py`
- Modify: `realtime_info_service.py`

**Interfaces:**
- Produces: `_attach_historical_resilience_fields(market: pd.DataFrame, history: pd.DataFrame, trade_date: str) -> pd.DataFrame`
- Produces row fields: `historical_resilience_score`, `historical_resilience_label`, `historical_resilience_reason`, `historical_resilience_weighted_relative`, `historical_resilience_down_relative`, `historical_resilience_beat_ratio`, `historical_resilience_sample_count`

- [ ] **Step 1: Write failing backend tests**

Add tests that import `_attach_historical_resilience_fields`, pass daily history for one strong and one weak stock, and assert that the strong stock gets a higher score and a reason containing `近20日`.

- [ ] **Step 2: Run backend test to verify failure**

Run: `env HOME=/tmp python3 -m unittest -v tests.test_realtime_info_service.RealtimeInfoServiceTests.test_historical_resilience_score_uses_weighted_relative_daily_history tests.test_realtime_info_service.RealtimeInfoServiceTests.test_historical_resilience_score_marks_insufficient_history`

Expected: import or attribute failure before implementation.

- [ ] **Step 3: Implement backend helper and attach it**

Add helpers near the market-relative helpers. In `_build_realtime_intraday_section`, call `_attach_historical_resilience_fields(signal_market, history, trade_date)` after refreshing market-relative fields and before `_attach_intraday_signal_stocks`.

- [ ] **Step 4: Run backend tests**

Run: `env HOME=/tmp python3 -m unittest -v tests.test_realtime_info_service`

Expected: all tests pass.

### Task 2: Frontend Column

**Files:**
- Modify: `quantClient/realtime-info-utils.js`
- Modify: `quantClient/realtime-info-utils.test.js`
- Modify: `quantClient/main.js`
- Modify: `quantClient/index.html`

**Interfaces:**
- Produces: `historicalResilienceText(row) -> { text, title, state }`

- [ ] **Step 1: Write failing frontend formatter tests**

Assert that score `82.4` and label `强抗跌` render as `82分 · 强抗跌`, and missing score renders `--`.

- [ ] **Step 2: Run frontend test to verify failure**

Run: `node quantClient/realtime-info-utils.test.js`

Expected: failure because formatter is missing.

- [ ] **Step 3: Implement formatter and table column**

Expose `historicalResilienceText` globally, add Vue method wrapper, and add `抗跌力` column to both realtime confluence tables.

- [ ] **Step 4: Run frontend tests**

Run: `node quantClient/realtime-info-utils.test.js`

Expected: pass.
