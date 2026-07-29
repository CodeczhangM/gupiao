# Sector Potential Expand And Leader Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make sector-potential rows expandable and restrict recommended stocks to large-cap, low-position limit-up, recent pullback-confirmed consolidation candidates.

**Architecture:** Reuse the existing `rank_sector_potential()` response and enrich `leader_stocks` with history-backed shape signals. Keep sector ranking unchanged, filter only the stocks selected for each sector, and add local Vue expand state in the static dashboard.

**Tech Stack:** Python 3, pandas, unittest, Vue 3 global build, plain JavaScript, static HTML/CSS.

## Global Constraints

- `total_mv` is treated as Tushare-style ten-thousand-yuan units when below raw yuan scale; 200 billion yuan is `20_000_000_000` after normalization.
- Recommended sector leader stocks must pass all hard filters: large-cap, bullish MAs, low-position historical limit-up, pullback confirmed within the latest 3 trade dates, and consolidation/box-wash structure.
- Sector `potential_score` ranking stays unchanged.
- Do not revert unrelated dirty worktree changes.

---

### Task 1: Back-End Leader Filters

**Files:**
- Modify: `tests/test_advantage_stock_scoring.py`
- Modify: `strategy.py`

**Interfaces:**
- Consumes: `rank_sector_potential(market_df, history_df, breakout_pool=None, first_limit_pool=None, limit=20, leaders_per_sector=5) -> pd.DataFrame`
- Produces: `leader_stocks` items with `total_mv_yuan`, `leader_reason`, and hard-filtered candidates.

- [ ] **Step 1: Write failing Python tests**

Add tests to `SectorPotentialRankingTests` that build one qualifying large-cap pullback stock and several rejecting variants.

- [ ] **Step 2: Run test to verify it fails**

Run: `env HOME=/tmp python3 -m unittest tests.test_advantage_stock_scoring.SectorPotentialRankingTests -v`

- [ ] **Step 3: Implement minimal history-backed filters**

Add helper logic in `strategy.py` for MA trend, low-position limit-up, 3-date pullback confirmation, and consolidation/box checks. Pass sector history into `_select_sector_leaders()`.

- [ ] **Step 4: Run Python tests**

Run: `env HOME=/tmp python3 -m unittest tests.test_advantage_stock_scoring.SectorPotentialRankingTests -v`

### Task 2: Front-End Expandable Sector Rows

**Files:**
- Modify: `quantClient/sector-potential-utils.js`
- Modify: `quantClient/sector-potential-utils.test.js`
- Modify: `quantClient/main.js`
- Modify: `quantClient/styles.css`

**Interfaces:**
- Consumes: `leader_stocks` from Task 1.
- Produces: expandable sector-potential rows and richer leader text.

- [ ] **Step 1: Write failing JavaScript display test**

Assert `sectorLeaderText()` includes total market cap and leader reason when present.

- [ ] **Step 2: Run test to verify it fails**

Run: `node quantClient/sector-potential-utils.test.js`

- [ ] **Step 3: Implement expandable UI**

Add `expandedRows` state and toggle helpers to `SectorPotentialTable`; render a details row below each sector.

- [ ] **Step 4: Run JavaScript test**

Run: `node quantClient/sector-potential-utils.test.js`

### Task 3: Final Verification

**Files:**
- Verify: `strategy.py`
- Verify: `tests/test_advantage_stock_scoring.py`
- Verify: `quantClient/main.js`
- Verify: `quantClient/sector-potential-utils.js`

- [ ] **Step 1: Run focused backend tests**

Run: `env HOME=/tmp python3 -m unittest tests.test_advantage_stock_scoring.SectorPotentialRankingTests -v`

- [ ] **Step 2: Run focused frontend test**

Run: `node quantClient/sector-potential-utils.test.js`

- [ ] **Step 3: Inspect diff**

Run: `git diff -- strategy.py tests/test_advantage_stock_scoring.py quantClient/main.js quantClient/sector-potential-utils.js quantClient/sector-potential-utils.test.js quantClient/styles.css docs/superpowers/specs/2026-07-24-sector-potential-expand-and-leader-filter-design.md docs/superpowers/plans/2026-07-24-sector-potential-expand-and-leader-filter.md`
