# Cache Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Vue dashboard page for viewing market-cache health, running incremental/current-day synchronization, and surfacing scan cache warnings.

**Architecture:** Extend the existing build-free Vue application rather than adding a new framework or build step. Keep API state and actions in the root Vue instance, render a dedicated cache tab in `index.html`, and style it with the existing dashboard tokens.

**Tech Stack:** Vue 3 CDN, vanilla JavaScript, HTML, CSS, Node syntax checks.

## Global Constraints

- Use `/cache/status` and `/cache/sync` through the configured `/api/quant` base URL.
- Do not add dependencies or automatic polling.
- Missing response fields render as `--`.
- Synchronization errors preserve previously loaded cache status.
- Non-empty scan `cache_warnings` render separately from request failures.
- Preserve all unrelated dirty-worktree changes.

---

### Task 1: Cache page state and API actions

**Files:**
- Modify: `quantClient/main.js`
- Create: `quantClient/cache-view-model.test.js`

**Interfaces:**
- Consumes: existing `request(path, options)` method.
- Produces: `cacheStatus`, `cacheLoading`, `cacheSyncing`, `cacheRows`, `loadCacheStatus()`, and `syncCache(forceCurrent)`.

- [ ] **Step 1: Add a failing source-contract test**

Create a Node script that reads `main.js` and asserts the cache state keys, `/cache/status`, `/cache/sync`, `forceCurrent`, and cache warning handling exist.

- [ ] **Step 2: Run the test and verify failure**

Run: `node quantClient/cache-view-model.test.js`

Expected: assertion failure because cache state/actions are absent.

- [ ] **Step 3: Implement state, computed rows, and actions**

Add cache state to `data()`. Add computed normalization for the latest record per source. Implement status loading and POST synchronization; refresh status after success and retain old status after failure. Load status during `refreshAll()` without making report loading depend on it.

- [ ] **Step 4: Run test and syntax checks**

Run: `node quantClient/cache-view-model.test.js && node --check quantClient/main.js`

Expected: test prints `cache view-model contract ok`; syntax check exits 0.

### Task 2: Cache page markup and warning banner

**Files:**
- Modify: `quantClient/index.html`
- Modify: `quantClient/cache-view-model.test.js`

**Interfaces:**
- Consumes: state and methods from Task 1.
- Produces: navigation entry, summary cards, source table, two synchronization controls, and scan warning banner.

- [ ] **Step 1: Extend the contract test and verify failure**

Assert `index.html` contains the cache nav/tab, both action labels, the four status table columns, and `latest.cache_warnings`.

- [ ] **Step 2: Implement semantic markup**

Add the nav button, page-title mapping, warning section, cache summary metrics, source table, loading/empty states, and buttons bound to `syncCache(false)` and `syncCache(true)`.

- [ ] **Step 3: Run the contract test**

Run: `node quantClient/cache-view-model.test.js`

Expected: `cache view-model contract ok`.

### Task 3: Responsive styling and verification

**Files:**
- Modify: `quantClient/styles.css`
- Modify: `quantClient/index.html`

**Interfaces:**
- Produces: responsive cache status cards, source table, warning banner, and action layout matching the existing dashboard.

- [ ] **Step 1: Add cache-specific styles**

Use existing colors, border radii, panel backgrounds, and breakpoints. Add status badge colors for `complete`, `running`, `failed`, and empty states; keep the table horizontally scrollable on narrow screens.

- [ ] **Step 2: Bump static asset query versions**

Update CSS and JavaScript query versions in `index.html` so deployed browsers load the new assets.

- [ ] **Step 3: Run final checks**

Run: `node quantClient/cache-view-model.test.js`

Run: `node --check quantClient/main.js`

Run: `git diff --check -- quantClient/index.html quantClient/main.js quantClient/styles.css quantClient/cache-view-model.test.js`

Expected: contract test passes and both checks exit 0.
