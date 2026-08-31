# Limit Gene Pullback Confirmation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current same-day-resonance candidate list with a conservative 0–10 stock model that requires a prior 1–10 trading-day limit-up gene, scores 20-day resonance and support pullbacks, and reserves “立即建仓” for a confirmed breakout after a held pullback.

**Architecture:** Add a pure historical feature module that converts daily bars into limit-gene, decayed-event, merged-support, pullback, and confirmation fields. `realtime_info_service.py` will build a broad main-board history-qualified pool first, cheaply pre-rank it, and only then load expensive minute/tail/chip confirmation for the bounded shortlist; the pure scoring module owns weights, vetoes, levels, explanations, and ranking. The existing realtime API shape remains additive, while the Vue page renders the richer candidate and debug fields; the independent overnight-premium path is untouched.

**Tech Stack:** Python 3.12, pandas, unittest/pytest, FastAPI service layer, Vue 3 browser client, Node built-in test runner.

**Spec:** `docs/superpowers/specs/2026-08-31-limit-gene-pullback-confirmation-design.md`

## Global Constraints

- Only change the “近期观察与建仓” pool, scoring, explanations, and filter debug; do not change overnight premium, morning follow-up, free review, or other pools.
- Continue to admit only Shanghai/Shenzhen main-board, non-ST, actively traded stocks with valid prices.
- Historical limit-up eligibility is exactly prior trading days 1–10, excludes today, includes day 10, and excludes day 11.
- A visible stock must also have at least one verifiable resonance event in prior trading days 1–20.
- Event decay is 100% for days 1–3, 80% for 4–7, 60% for 8–12, 40% for 13–20, and zero afterward; duplicate event types do not stack.
- Merge key levels within 1.5%, retain at most three support zones, and expose the strongest nearby/below zone as the primary support.
- Positive weights are support/pullback 30, 20-day resonance 20, hot sector 18, price-volume/main force 12, chip peak 8, current MACD 7, relative strength/tail 5; risk penalty is capped at 30.
- “立即建仓” requires score >= 80, support score >= 22, held pullback, breakout >= 0.5%, price-volume confirmation, price within 5% above support, non-weak sector, no simultaneous daily/60m MACD weakness, and no veto/missing required confirmation.
- “等待突破建仓” starts at 65, “观察建仓” spans 50–64, and weak/vetoed rows are not backfilled.
- Today limit-up, today limit-down, and source-marked sealed boards are debug-only, never candidates.
- Missing history hides the row; missing chip/minute/tail data never fabricates confirmation and downgrades “立即建仓”.
- Use score version `position-candidate-v2-limit-gene-pullback` plus the global MACD parameter key in all realtime cache keys.
- Strong refresh must reuse per-stock/per-trade-date historical features and restrict minute/tail/chip fetches to the bounded pre-shortlist.

---

## File Structure

- Create `position_candidate_history.py`: pure daily-history normalization, historical limit-up detection, event decay/deduplication, key-level merging, pullback classification, and breakout-confirmation feature extraction.
- Create `tests/test_position_candidate_history.py`: boundary and scenario tests for every historical feature contract.
- Modify `position_candidate_scoring.py`: v2 weights, hard gates, risk vetoes, level assignment, explanation fields, and stable ranking.
- Modify `realtime_info_service.py`: broad history-first candidate pool, bounded expensive enrichment, failure isolation, cache versioning, and exact debug funnel.
- Modify `tests/test_position_candidate_scoring.py`: v2 scoring and degradation tests.
- Modify `tests/test_realtime_info_service.py`: service pool, performance boundary, cache, isolation, and debug integration tests.
- Modify `quantClient/index.html`: consolidated candidate columns and expanded zero-result diagnostics.
- Modify `quantClient/main.js`: level styling and debug/candidate view-model helpers.
- Modify `quantClient/styles.css`: compact support/confirmation and debug presentation.
- Modify `tests/test_position_candidate_layout.py`: UI contract and independent overnight-module regression assertions.

### Task 1: Historical limit gene and decayed resonance events

**Files:**
- Create: `position_candidate_history.py`
- Create: `tests/test_position_candidate_history.py`

**Interfaces:**
- Consumes: daily bars with `ts_code`, `trade_date`, `open`, `high`, `low`, `close`, `pct_chg`, `vol`, `amount`, optional `limit_flag`, and optional precomputed MACD/signal fields.
- Produces: `extract_limit_gene(bars: pd.DataFrame, as_of_trade_date: str) -> dict[str, Any]`, `extract_resonance_events(bars: pd.DataFrame, as_of_trade_date: str) -> dict[str, Any]`, and `event_decay(trading_days_ago: int) -> float`.

- [ ] **Step 1: Write failing boundary tests**

```python
def test_limit_gene_accepts_days_one_and_ten_but_not_today_or_day_eleven():
    assert extract_limit_gene(_bars(limit_days={1}), "20260831")["limit_gene_eligible"]
    assert extract_limit_gene(_bars(limit_days={10}), "20260831")["limit_gene_eligible"]
    assert not extract_limit_gene(_bars(limit_days={0}), "20260831")["limit_gene_eligible"]
    assert not extract_limit_gene(_bars(limit_days={11}), "20260831")["limit_gene_eligible"]

def test_resonance_decay_boundaries_and_same_type_deduplication():
    assert [event_decay(day) for day in (1, 3, 4, 7, 8, 12, 13, 20, 21)] == [1, 1, .8, .8, .6, .6, .4, .4, 0]
    result = extract_resonance_events(_bars(events=[("daily_macd", 2, 10), ("daily_macd", 6, 20)]), "20260831")
    assert len([e for e in result["resonance_events"] if e["type"] == "daily_macd"]) == 1
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_position_candidate_history.py -v`

Expected: FAIL because `position_candidate_history` does not exist.

- [ ] **Step 3: Implement deterministic history normalization and extractors**

```python
EVENT_WEIGHTS = {"daily_macd": 5, "hourly_macd_kdj": 5, "volume_breakout": 4, "bottom_first_up": 3, "volume_contraction": 3}

def event_decay(trading_days_ago: int) -> float:
    for end, factor in ((3, 1.0), (7, .8), (12, .6), (20, .4)):
        if 1 <= trading_days_ago <= end:
            return factor
    return 0.0
```

Normalize `trade_date` with `pd.to_datetime(..., errors="coerce")`, sort ascending, exclude the as-of row from historical windows, treat `pct_chg >= 9.5` or a reliable `limit_flag` as a limit-up, and return the latest qualifying candle’s date, days ago, close, body low `min(open, close)`, start price, and 10-day count. Detect only verifiable event flags/indicator transitions, select the highest decayed contribution per type, cap total contribution at 20, and return event type/date/days/raw strength/decay/contribution.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_position_candidate_history.py -v`

Expected: PASS, including malformed dates, insufficient history, and no fabricated events.

- [ ] **Step 5: Commit**

```bash
git add position_candidate_history.py tests/test_position_candidate_history.py
git commit -m "feat: extract limit gene and historical resonance"
```

### Task 2: Key levels, pullback state, and breakout confirmation

**Files:**
- Modify: `position_candidate_history.py`
- Modify: `tests/test_position_candidate_history.py`

**Interfaces:**
- Consumes: normalized bars, Task 1 limit-gene result, optional `chip_peak_price`, current quote, and optional minute confirmation fields.
- Produces: `merge_key_levels(levels: list[dict[str, Any]], tolerance_pct: float = 1.5, limit: int = 3) -> list[dict[str, Any]]` and `extract_pullback_confirmation(bars: pd.DataFrame, gene: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]`.

- [ ] **Step 1: Write failing structure tests**

```python
def test_nearby_level_sources_merge_and_strongest_nearby_support_wins():
    zones = merge_key_levels([_level(10.00, "MA10"), _level(10.12, "筹码峰"), _level(9.20, "启动价")])
    assert zones[0]["lower"] == 10.00
    assert zones[0]["upper"] == 10.12
    assert zones[0]["sources"] == ["MA10", "筹码峰"]

def test_pullback_reclaim_and_confirmed_breakout_are_distinct():
    reclaimed = extract_pullback_confirmation(_pullback_bars(intraday_breach=True), _gene(), _current(price=10.3))
    confirmed = extract_pullback_confirmation(_pullback_bars(), _gene(), _current(price=10.56, volume_ratio=1.4))
    assert reclaimed["pullback_state"] == "盘中跌破但收回"
    assert not reclaimed["breakout_confirmed"]
    assert confirmed["breakout_pct"] >= .5 and confirmed["price_volume_confirmation"]
```

- [ ] **Step 2: Run the focused scenarios and verify RED**

Run: `.venv/bin/python -m pytest tests/test_position_candidate_history.py -k 'level or pullback or breakout' -v`

Expected: FAIL because the level and confirmation functions are absent.

- [ ] **Step 3: Implement level extraction and state machine**

Build candidate levels from limit candle body low/start, post-limit platform lower/upper edges, MA10, MA20, chip peak, and prior breakout. Merge relative price distance `abs(a-b)/min(a,b) * 100 <= 1.5`, combine sorted unique sources and strengths, keep three strongest zones, and select the strongest zone at/below or within 1.5% of current price. Classify `关键位上方企稳`, `回踩关键位未破`, `盘中跌破但收回`, or `有效跌破关键位`; set `support_volume_break_veto` when a close below the zone occurs with volume at least 1.5 times the pullback baseline. Choose confirmation level by local post-pullback high, platform upper, prior breakout, then MA/chip pressure; require current price at least 0.5% above it and volume ratio 1.2–3.0 or amount above the pullback average.

- [ ] **Step 4: Run all history tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_position_candidate_history.py -v`

Expected: PASS for multi-source merge, maximum-three zones, held support, intraday reclaim, valid break, volume break veto, and confirmation precedence.

- [ ] **Step 5: Commit**

```bash
git add position_candidate_history.py tests/test_position_candidate_history.py
git commit -m "feat: detect pullback and breakout confirmation"
```

### Task 3: Conservative v2 scoring and ranking

**Files:**
- Modify: `position_candidate_scoring.py`
- Modify: `tests/test_position_candidate_scoring.py`

**Interfaces:**
- Consumes: rows containing Task 1/2 fields plus sector, price-volume, chip, current MACD, relative-strength, tail, quote provenance, and risk fields.
- Produces: existing `score_position_candidate(row: dict[str, Any], market_phase: str = "") -> dict[str, Any]`, `rank_position_candidates(...)`, and `position_score_version(...)`, now with v2 fields `support_pullback_score`, `historical_resonance_score`, `confirmation_state`, and `position_filter_reason`.

- [ ] **Step 1: Replace v1 expectations with failing v2 policy tests**

```python
def test_immediate_entry_requires_held_pullback_and_confirmed_breakout():
    waiting = score_position_candidate({**_v2_row(), "breakout_confirmed": False})
    immediate = score_position_candidate({**_v2_row(), "breakout_confirmed": True, "breakout_pct": .7, "price_volume_confirmation": True})
    assert waiting["position_level"] == "等待突破建仓"
    assert immediate["position_level"] == "立即建仓"

def test_limit_gene_and_recent_resonance_are_hard_visibility_gates():
    assert score_position_candidate({**_v2_row(), "limit_gene_eligible": False})["position_level"] == "不展示"
    assert score_position_candidate({**_v2_row(), "resonance_events": []})["position_level"] == "不展示"
```

Also assert the exact weight caps, today up/down/sealed reasons with actual percent/price/source/time, support dominance over a sector/chip-only row, simultaneous MACD weakness veto, missing chip/minute/tail downgrade, risk cap 30, score bands, stable 10-row cap, and no weak backfill.

- [ ] **Step 2: Run scoring tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_position_candidate_scoring.py -v`

Expected: FAIL on v1 weights/version/levels.

- [ ] **Step 3: Implement v2 bounded score components and gates**

```python
BASE_POSITION_SCORE_VERSION = "position-candidate-v2-limit-gene-pullback"
WEIGHTS = {"support": 30.0, "resonance": 20.0, "sector": 18.0, "price_volume": 12.0, "chip": 8.0, "macd": 7.0, "relative_tail": 5.0}
```

Retain the public functions, replace same-day bottom scoring with explicit support and historical-event scoring, and return all component scores plus positive reasons, missing confirmations, risk items, veto flag, and a single exact filter reason. Apply hard gates before score bands; make “立即建仓” satisfy every spec predicate, use the exact label “等待突破建仓”, and rank visible rows by level priority, total score, support score, resonance score, then code for deterministic ties.

- [ ] **Step 4: Run scoring tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_position_candidate_scoring.py -v`

Expected: PASS with score totals bounded to 0–100 and no v1 label/version references.

- [ ] **Step 5: Commit**

```bash
git add position_candidate_scoring.py tests/test_position_candidate_scoring.py
git commit -m "feat: score conservative limit pullback candidates"
```

### Task 4: History-first realtime pool and bounded enrichment

**Files:**
- Modify: `realtime_info_service.py:1728-2225`
- Modify: `tests/test_realtime_info_service.py`

**Interfaces:**
- Consumes: broad `signal_market`, grouped daily `history`, sector context, existing minute loader, chip service, and Task 1/2/3 functions.
- Produces: `_build_history_position_pool(market: pd.DataFrame, history: pd.DataFrame, trade_date: str) -> tuple[list[dict[str, Any]], dict[str, Any]]`, `_select_position_enrichment_shortlist(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]`, and the existing realtime result’s `position_candidates`.

- [ ] **Step 1: Write failing service-flow tests**

```python
def test_candidate_can_enter_from_twenty_day_history_without_today_intraday_signal():
    result = _build_history_position_pool(_market(["600001.SH"]), _eligible_history(), "20260831")
    assert result[0][0]["ts_code"] == "600001.SH"

def test_expensive_enrichment_only_receives_bounded_prequalified_rows():
    with patch("realtime_info_service._load_tail_minutes_for_candidates") as tail, patch("realtime_info_service.attach_chip_peak_fields") as chip:
        _build_realtime_intraday_section(..., limit=10)
        assert len(tail.call_args.args[0]) <= POSITION_ENRICHMENT_LIMIT
        assert len(chip.call_args.args[0]) <= POSITION_ENRICHMENT_LIMIT
```

Cover main-board-only behavior, absent current resonance, historical-feature cache reuse on forced refresh, and isolation of one stock throwing during feature extraction.

- [ ] **Step 2: Run focused service tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_realtime_info_service.py -k 'history_position_pool or enrichment_shortlist or historical_feature_cache or candidate_without_today' -v`

Expected: FAIL because candidate construction still iterates only `intraday_signal_stocks`.

- [ ] **Step 3: Implement the history-first, cheap-first pipeline**

Add a per-`(ts_code, trade_date, position_score_version())` historical-feature cache. Iterate the main-board market universe, attach sector context by industry, extract history features with per-symbol `try/except`, remove rows lacking the two hard historical gates, and cheap-pre-rank by support/event/sector/current daily data. Use a named bounded constant (initially 40, never lower than requested output and never above 60) before minute/tail/chip calls. Merge enriched fields back by `ts_code`, score once, and keep the existing stage arrays only as backward-compatible projections of scored rows; do not call or alter the overnight-premium builder.

- [ ] **Step 4: Run service and scoring regression tests**

Run: `.venv/bin/python -m pytest tests/test_realtime_info_service.py tests/test_position_candidate_scoring.py -v`

Expected: PASS; spies prove expensive loaders never see the full market, and a symbol failure becomes a warning without aborting the batch.

- [ ] **Step 5: Commit**

```bash
git add realtime_info_service.py tests/test_realtime_info_service.py
git commit -m "feat: build realtime candidates from historical signals"
```

### Task 5: Exact filter funnel, cache versioning, and degradation

**Files:**
- Modify: `realtime_info_service.py:1728-1783,2147-2235,2405-2575`
- Modify: `tests/test_realtime_info_service.py`

**Interfaces:**
- Consumes: every actual rejection emitted by Tasks 3/4.
- Produces: `_build_position_filter_debug(source_rows, scored_rows, visible_rows, failures) -> dict[str, Any]` and realtime cache keys containing `position_score_version()`.

- [ ] **Step 1: Write failing funnel and cache tests**

```python
def test_filter_debug_counts_each_actual_branch_and_exposes_evidence():
    debug = _build_position_filter_debug(_debug_rows(), _scored_rows(), _visible_rows(), [])
    assert debug["funnel"] == {"source_main_board": 8, "no_limit_gene": 1, "today_limit_up": 1, "today_limit_down": 1, "source_sealed": 1, "no_recent_resonance": 1, "support_broken": 1, "score_below_50": 1, "top_truncated": 0, "final": 1}
    assert {"current_price", "pct_chg", "quote_time", "quote_source", "latest_limit_up_date", "latest_resonance_date", "primary_support", "confirmation_price", "component_scores", "risk_penalty", "reason"} <= debug["samples"][0].keys()

def test_realtime_cache_key_changes_with_v2_score_version():
    assert position_score_version() in _database_realtime_result_key(10)
```

Also assert missing history hides, missing chip/minute/tail downgrades, tail-before-14:30 is not penalized, zero candidates sets `auto_expand`, and force refresh reuses immutable history features while refreshing quote/minute-dependent fields.

- [ ] **Step 2: Run debug/cache tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_realtime_info_service.py -k 'filter_debug or cache_key or degradation or force_refresh' -v`

Expected: FAIL on missing v2 funnel fields/version key.

- [ ] **Step 3: Implement reason-led accounting and v2 cache separation**

Record rejection at the branch where it happens, never infer it later from the final score. Return ordered funnel counters, `top_reasons`, up to 20 evidence-rich samples, isolated calculation failures, and `auto_expand = final == 0`. Add the score version and MACD key to memory/database keys and reject legacy cached position candidates instead of silently adapting them; retain historical feature cache across force refresh, but clear/recompute quote, minute, tail, and current-state fields.

- [ ] **Step 4: Run backend regression suite**

Run: `.venv/bin/python -m pytest tests/test_position_candidate_history.py tests/test_position_candidate_scoring.py tests/test_realtime_info_service.py -v`

Expected: PASS with exact funnel equality and independent single-stock exception handling.

- [ ] **Step 5: Commit**

```bash
git add realtime_info_service.py tests/test_realtime_info_service.py
git commit -m "feat: explain v2 position filtering"
```

### Task 6: Consolidated candidate and debug UI

**Files:**
- Modify: `quantClient/index.html:927-1033`
- Modify: `quantClient/main.js:720-760,970-990`
- Modify: `quantClient/styles.css:1229-1260`
- Modify: `tests/test_position_candidate_layout.py`

**Interfaces:**
- Consumes: additive v2 candidate fields and `position_filter_debug` from Task 5.
- Produces: one compact “近期观察与建仓” table ordered from immediate to observation, and automatically expanded zero-result diagnostics.

- [ ] **Step 1: Write failing static UI contract tests**

```python
def test_candidate_table_exposes_gene_support_confirmation_and_component_scores(self):
    html = self.index_html
    for label in ("近期涨停", "主关键位", "突破确认", "历史共振", "板块/量价/筹码"):
        self.assertIn(label, html)
    self.assertIn("等待突破建仓", self.main_js)
    self.assertIn("positionFilterDebugPayload.auto_expand", self.main_js)

def test_overnight_panel_and_endpoint_remain_independent(self):
    self.assertIn("/realtime-info/tail-premium", self.main_js)
    self.assertNotIn("tailPremiumRows", self.candidate_table_html)
```

- [ ] **Step 2: Run layout tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_position_candidate_layout.py -v`

Expected: FAIL on new labels/helper behavior.

- [ ] **Step 3: Implement the compact v2 presentation**

Render code/name/sector, level and total score, latest prior limit-up date/days/count, primary support zone and pullback state, confirmation price/breakout percent/volume confirmation, latest 20-day event with decay, and compact component scores in the approved priority order. Update level class mapping to `立即建仓`, `等待突破建仓`, and `观察建仓`. In debug, show exact funnel counts and evidence fields; open it when the user toggle is on or `auto_expand` is true. Keep the table at 0–10 rows and do not duplicate the three removed legacy candidate panels.

- [ ] **Step 4: Run UI tests and browser syntax checks**

Run: `.venv/bin/python -m pytest tests/test_position_candidate_layout.py -v`

Run: `node --check quantClient/main.js`

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add quantClient/index.html quantClient/main.js quantClient/styles.css tests/test_position_candidate_layout.py
git commit -m "feat: display pullback confirmation candidates"
```

### Task 7: End-to-end regression and performance acceptance

**Files:**
- Modify: `tests/test_realtime_info_service.py` only if an uncovered acceptance regression requires an explicit fixture.

**Interfaces:**
- Consumes: completed backend and frontend contracts.
- Produces: verification evidence that the v2 path works, remains bounded, and does not alter overnight-premium behavior.

- [ ] **Step 1: Add any missing end-to-end acceptance test before changing code**

```python
def test_v2_pipeline_returns_only_confirmed_or_watchable_top_ten_and_preserves_overnight_builder():
    result = build_realtime_info(...)
    rows = result["intraday"]["position_candidates"]
    assert 0 <= len(rows) <= 10
    assert all(row["limit_gene_eligible"] and row["resonance_events"] for row in rows)
    assert all(row["position_level"] in {"立即建仓", "等待突破建仓", "观察建仓"} for row in rows)
    assert build_realtime_tail_premium_info(...) == expected_overnight_fixture
```

- [ ] **Step 2: Run the full targeted suite and fix only demonstrated failures**

Run: `.venv/bin/python -m pytest tests/test_position_candidate_history.py tests/test_position_candidate_scoring.py tests/test_realtime_info_service.py tests/test_position_candidate_layout.py -v`

Expected: PASS.

- [ ] **Step 3: Verify refresh work is bounded**

Run: `.venv/bin/python -m pytest tests/test_realtime_info_service.py -k 'enrichment_shortlist or historical_feature_cache or force_refresh' -v`

Expected: PASS; loaders receive no more than `POSITION_ENRICHMENT_LIMIT`, and the second force-refresh call hits historical cache while reloading current confirmation.

- [ ] **Step 4: Run broader regressions and syntax checks**

Run: `.venv/bin/python -m pytest tests -q`

Run: `node --test quantClient/*.test.js`

Run: `node --check quantClient/main.js`

Expected: all commands exit 0. If a pre-existing unrelated failure occurs, capture its exact test and traceback without weakening candidate assertions.

- [ ] **Step 5: Inspect the working tree and commit final test-only adjustments**

Run: `git status --short`

```bash
git add tests/test_realtime_info_service.py
git commit -m "test: cover limit pullback candidate pipeline"
```

Skip the commit when Step 1 required no new test file change; do not stage unrelated user files or generated artifacts.
