# Cycle Watchlist Entry Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a persistent manually managed A-share watchlist that checks every trading hour for explainable low-buy and confirmed-entry opportunities and displays upgrade alerts in the browser.

**Architecture:** Isolate persistence, pure scoring, and orchestration in three Python modules exposed through FastAPI and the existing Spring proxy. Spring triggers six scheduled checks; Python validates trading dates, enforces idempotency, evaluates each stock independently, and stores transitions. Vue displays three independently sorted groups and history without changing existing monitor modules.

**Tech Stack:** Python 3.12, pandas, FastAPI, PyMySQL/MySQL, Java 17 Spring Boot RestClient and scheduling, Vue 3, Python `unittest`, JUnit/MockMvc, Node assertions.

**Spec:** `docs/superpowers/specs/2026-08-28-cycle-watchlist-entry-alerts-design.md`

## Global Constraints

- Support `.SH` and `.SZ` only; never infer `.BJ`.
- Accept six digits or a matching suffix and normalize suffixes to uppercase.
- Use one scoring rule for every stock and target a 2–5 trading-day hold.
- Statuses are exactly `watch`, `low_buy`, `confirmed`, and `data_delayed`.
- Alert only on an effective status upgrade; repeated equal states do not alert.
- Schedule `09:35`, `10:35`, `11:25`, `13:30`, `14:30`, and `14:55` in `Asia/Shanghai`.
- Web reminders only; no external messaging, trading, or new scheduler dependency.
- Preserve realtime confluence, overnight, and morning-follow behavior.

## File Map

- `cycle_watch_scoring.py`: code normalization and pure factor scoring.
- `cycle_watch_repository.py`: schema, CRUD, evaluations, history, and alert read state.
- `cycle_watch_service.py`: market loading, batch isolation, transitions, and grouped output.
- `cycle_watch_models.py`, `app.py`: request validation and Python routes.
- Spring DTOs, `QuantPythonClient`, `QuantController`, `CycleWatchScheduler`: proxy and schedule.
- `quantClient/cycle-watch-utils.js`, `main.js`, `index.html`, `styles.css`: browser behavior and UI.

---

### Task 1: Normalize codes and score entries

**Files:**
- Create: `cycle_watch_scoring.py`
- Create: `tests/test_cycle_watch_scoring.py`

**Interfaces:**
- Produces: `normalize_cycle_watch_code(raw: str) -> str`.
- Produces: `evaluate_cycle_entry(ts_code: str, daily: pd.DataFrame, bars_60m: pd.DataFrame, realtime: dict, planned_low_price: float | None = None, planned_high_price: float | None = None) -> dict`.
- The result contains `status`, `status_label`, `opportunity_score`, prices, three condition arrays, `invalidation_reason`, and `factors`.

- [ ] **Step 1: Write failing normalization tests**

```python
def test_normalizes_supported_codes(self):
    self.assertEqual(normalize_cycle_watch_code("600000"), "600000.SH")
    self.assertEqual(normalize_cycle_watch_code("688981.sh"), "688981.SH")
    self.assertEqual(normalize_cycle_watch_code("000001"), "000001.SZ")
    self.assertEqual(normalize_cycle_watch_code("300750.sz"), "300750.SZ")

def test_rejects_unsupported_and_mismatched_codes(self):
    for raw in ("920001", "600000.SZ", "123", "ABCDEF"):
        with self.assertRaises(ValueError):
            normalize_cycle_watch_code(raw)
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m unittest tests.test_cycle_watch_scoring -v`

Expected: import failure because the module is absent.

- [ ] **Step 3: Implement normalization**

```python
SH_PREFIXES = ("600", "601", "603", "605", "688", "689")
SZ_PREFIXES = ("000", "001", "002", "003", "300", "301")

def normalize_cycle_watch_code(raw: str) -> str:
    value = str(raw or "").strip().upper()
    match = re.fullmatch(r"(\d{6})(?:\.(SH|SZ))?", value)
    if not match:
        raise ValueError("股票代码必须为六位数字，可选带 .SH 或 .SZ 后缀")
    digits, supplied = match.groups()
    expected = "SH" if digits.startswith(SH_PREFIXES) else "SZ" if digits.startswith(SZ_PREFIXES) else None
    if expected is None:
        raise ValueError("暂只支持沪深 A 股代码")
    if supplied and supplied != expected:
        raise ValueError(f"股票代码 {digits} 的市场后缀应为 .{expected}")
    return f"{digits}.{expected}"
```

- [ ] **Step 4: Run GREEN**

Run: `.venv/bin/python -m unittest tests.test_cycle_watch_scoring -v`

Expected: normalization tests pass.

- [ ] **Step 5: Add failing literal-fixture scoring tests**

Test these observable cases with complete hand-built OHLCV frames: shrinking 3–8 day pullback near MA20 returns `low_buy` and score at least 65; the same daily setup plus two 60-minute/realtime confirmations returns `confirmed`; a volume-ratio-at-least-1.5 close more than 2% below MA20 returns `watch` with risk `放量跌破MA20`; insufficient daily rows returns `data_delayed`.

- [ ] **Step 6: Run RED, implement scoring, then run GREEN**

Run before and after: `.venv/bin/python -m unittest tests.test_cycle_watch_scoring -v`

Implement daily points: structure 15, 3–10% pullback 15, within 2% of support 15, recent-three/prior-five volume at most 0.8 for 10, no new three-day low 10, 20-day position at most 70% for 5. Add 6 points for each of five intraday confirmations. `confirmed` requires daily score at least 65 and two confirmations; `low_buy` requires daily score at least 65 and no hard risk. Save `rule_version="cycle-entry-v1"` and all raw factors.

Expected after: all scoring tests pass.

- [ ] **Step 7: Commit**

```bash
git add cycle_watch_scoring.py tests/test_cycle_watch_scoring.py
git commit -m "feat: add cycle watch entry scoring"
```

---

### Task 2: Persist watchlist and evaluations

**Files:**
- Create: `cycle_watch_repository.py`
- Create: `tests/test_cycle_watch_repository.py`

**Interfaces:**
- Uses: `database.get_connection()`.
- Produces: `init_cycle_watch_schema`, `upsert_watch_stock`, `update_watch_stock`, `delete_watch_stock`, `list_watch_stocks`, `save_cycle_evaluation`, `list_cycle_history`, `latest_effective_evaluation`, `mark_cycle_alerts_read`.

- [ ] **Step 1: Write failing schema and CRUD tests**

Reuse the fake-connection style from `tests/test_realtime_cache.py`. Assert both tables initialize; duplicate upsert restores `enabled=1`; price/note values round-trip; delete removes the current row while evaluation history remains queryable.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m unittest tests.test_cycle_watch_repository -v`

Expected: module import fails.

- [ ] **Step 3: Implement schema and CRUD**

Create `cycle_watchlist` with primary key `ts_code` and the approved fields. Create `cycle_watch_evaluations` with JSON text fields plus:

```sql
UNIQUE KEY uq_cycle_eval_slot (ts_code, trade_date, schedule_slot),
INDEX idx_cycle_eval_history (ts_code, checked_at),
INDEX idx_cycle_eval_alerts (trade_date, is_new_alert, alert_read)
```

Serialize arrays with `ensure_ascii=False`; decode missing arrays as `[]` and factors as `{}`.

- [ ] **Step 4: Run CRUD tests GREEN**

Run: `.venv/bin/python -m unittest tests.test_cycle_watch_repository -v`

Expected: schema and CRUD tests pass.

- [ ] **Step 5: Add failing idempotency and alert-read tests**

```python
def test_same_scheduled_slot_reuses_evaluation(self):
    first = save_cycle_evaluation(evaluation_fixture(), "1035")
    second = save_cycle_evaluation(evaluation_fixture(score=72), "1035")
    self.assertEqual(first["id"], second["id"])

def test_mark_read_only_changes_requested_trade_date(self):
    self.assertEqual(mark_cycle_alerts_read("20260828"), 2)
```

- [ ] **Step 6: Run RED, implement transaction-safe writes, then GREEN**

Use `INSERT ... ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)` and update `latest_evaluation_id` plus `last_checked_at` in the same connection transaction. Run `.venv/bin/python -m unittest tests.test_cycle_watch_repository -v`; expect all pass.

- [ ] **Step 7: Commit**

```bash
git add cycle_watch_repository.py tests/test_cycle_watch_repository.py
git commit -m "feat: persist cycle watchlist evaluations"
```

---

### Task 3: Orchestrate checks and alert transitions

**Files:**
- Create: `cycle_watch_service.py`
- Create: `tests/test_cycle_watch_service.py`

**Interfaces:**
- Consumes: Tasks 1–2, cached daily data, realtime snapshot, existing 60-minute fallback loaders, and `get_trade_dates`.
- Produces: `add_cycle_watch`, `edit_cycle_watch`, `remove_cycle_watch`, `get_cycle_watchlist`, `check_cycle_watchlist`, `get_cycle_watch_history`, `read_cycle_watch_alerts`.

- [ ] **Step 1: Write failing CRUD validation tests**

Assert `600000` reaches the repository as `600000.SH`; note length over 500 and a low price greater than high price raise `ValueError`; missing name is permitted and retried later.

- [ ] **Step 2: Run RED, implement minimal CRUD orchestration, then GREEN**

Run before/after: `.venv/bin/python -m unittest tests.test_cycle_watch_service -v`.

Name lookup checks latest cached snapshot then recent daily rows. Validate positive prices and normalize every path code.

- [ ] **Step 3: Add failing batch and transition tests**

Test: equal status does not alert; `watch -> low_buy` and `low_buy -> confirmed` alert; delayed data never alerts and retains previous valid status reference; one stock failure does not abort peers; non-trading day skips minute loading; repeated schedule slot returns stored evaluation.

- [ ] **Step 4: Run RED and implement batch behavior**

Use priority `data_delayed=0`, `watch=1`, `low_buy=2`, `confirmed=3`, ignoring delayed rows when finding the previous effective state. Use a process lock plus repository uniqueness. Return exact groups `confirmed_stocks`, `low_buy_stocks`, `watch_stocks`, `delayed_stocks`, plus `stocks`, counts, time fields, slot, skipped flag, and unread count.

- [ ] **Step 5: Implement independent sorts and run GREEN**

Confirmed: score, confirmation count, relative strength descending. Low-buy: support distance ascending, volume contraction quality and score descending. Watch: score descending. Run `.venv/bin/python -m unittest tests.test_cycle_watch_service -v`; expect all pass.

- [ ] **Step 6: Commit**

```bash
git add cycle_watch_service.py tests/test_cycle_watch_service.py
git commit -m "feat: evaluate cycle watchlist alerts"
```

---

### Task 4: Expose FastAPI endpoints

**Files:**
- Create: `cycle_watch_models.py`
- Modify: `app.py`
- Create: `tests/test_cycle_watch_api.py`

**Interfaces:**
- Consumes: Task 3 service API.
- Produces: all seven approved `/api/cycle-watchlist` routes.

- [ ] **Step 1: Write failing endpoint tests**

Cover list, create, patch, delete, check all/one, history limit 1–200, read alerts, `ValueError -> 422`, `LookupError -> 404`, unexpected error `-> 502`.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m unittest tests.test_cycle_watch_api -v`

Expected: models/routes absent.

- [ ] **Step 3: Implement Pydantic models**

```python
class CycleWatchCreateRequest(BaseModel):
    ts_code: str = Field(min_length=6, max_length=9)
    note: str | None = Field(default=None, max_length=500)
    planned_low_price: float | None = Field(default=None, gt=0)
    planned_high_price: float | None = Field(default=None, gt=0)

class CycleWatchUpdateRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)
    planned_low_price: float | None = Field(default=None, gt=0)
    planned_high_price: float | None = Field(default=None, gt=0)
    enabled: bool | None = None
```

Also add a check request with optional code and slot pattern `^(0935|1035|1125|1330|1430|1455|manual)$`.

- [ ] **Step 4: Implement routes and run GREEN**

Register `/check` and `/alerts/read` before parameter paths. Delete returns 204. Run API tests plus existing realtime/monitor API tests; expect all pass.

- [ ] **Step 5: Commit**

```bash
git add cycle_watch_models.py app.py tests/test_cycle_watch_api.py
git commit -m "feat: expose cycle watchlist api"
```

---

### Task 5: Add Spring proxy and scheduler

**Files:**
- Create: `quantServer/quantServer/src/main/java/com/codec/quantserver/dto/CycleWatchCreateRequest.java`
- Create: `quantServer/quantServer/src/main/java/com/codec/quantserver/dto/CycleWatchUpdateRequest.java`
- Create: `quantServer/quantServer/src/main/java/com/codec/quantserver/service/CycleWatchScheduler.java`
- Modify: `quantServer/quantServer/src/main/java/com/codec/quantserver/service/QuantPythonClient.java`
- Modify: `quantServer/quantServer/src/main/java/com/codec/quantserver/controller/QuantController.java`
- Modify: `quantServer/quantServer/src/test/java/com/codec/quantserver/controller/QuantControllerTest.java`
- Create: `quantServer/quantServer/src/test/java/com/codec/quantserver/service/CycleWatchSchedulerTest.java`

**Interfaces:**
- Produces matching `/api/quant/cycle-watchlist` routes and scheduled Python calls.

- [ ] **Step 1: Write failing MockMvc proxy tests and run RED**

Test all seven routes, forwarded bodies, path variables, and history limit clamp. Run `cd quantServer/quantServer && ./mvnw -q -Dtest=QuantControllerTest test`; expect missing methods.

- [ ] **Step 2: Implement DTOs, client, and controller**

Add client methods `cycleWatchlist`, `createCycleWatch`, `updateCycleWatch`, `deleteCycleWatch`, `checkCycleWatch`, `cycleWatchHistory`, and `readCycleWatchAlerts`. Clamp history to 1–200 and build encoded path variables with `uriBuilder.build(tsCode)`.

- [ ] **Step 3: Run controller tests GREEN**

Run the same Maven command; expect pass.

- [ ] **Step 4: Write failing scheduler tests and run RED**

Call six scheduled methods directly and assert slots `0935`, `1035`, `1125`, `1330`, `1430`, `1455`. Assert a client exception is logged/swallowed. Run `./mvnw -q -Dtest=CycleWatchSchedulerTest test`; expect class absent.

- [ ] **Step 5: Implement and test scheduling**

Use six `@Scheduled` annotations such as `@Scheduled(cron="0 35 9 * * MON-FRI", zone="Asia/Shanghai")`, delegating to `runSlot`. Enable scheduling once in existing application configuration. Run full `./mvnw -q test`; expect pass.

- [ ] **Step 6: Commit**

```bash
git add quantServer/quantServer/src/main quantServer/quantServer/src/test
git commit -m "feat: schedule cycle watchlist checks"
```

---

### Task 6: Add browser utilities and Vue state

**Files:**
- Create: `quantClient/cycle-watch-utils.js`
- Create: `quantClient/cycle-watch-utils.test.js`
- Modify: `quantClient/index.html`
- Modify: `quantClient/main.js`

**Interfaces:**
- Produces: `normalizeCycleWatchInput`, `cycleWatchGroups`, `cycleWatchAlertCount` plus Vue load/mutation/history methods.

- [ ] **Step 1: Write failing utility tests**

```javascript
assert.strictEqual(normalizeCycleWatchInput(' 600000 '), '600000.SH');
assert.strictEqual(normalizeCycleWatchInput('300750.sz'), '300750.SZ');
assert.throws(() => normalizeCycleWatchInput('920001'), /沪深/);
assert.deepStrictEqual(cycleWatchGroups(rows).confirmed.map(r => r.ts_code), ['c']);
assert.strictEqual(cycleWatchAlertCount([{is_new_alert: true, alert_read: false}]), 1);
```

- [ ] **Step 2: Run RED, implement pure utilities, run GREEN**

Run before/after: `node quantClient/cycle-watch-utils.test.js`. Group server statuses only; never recompute technical signals in JavaScript.

- [ ] **Step 3: Add failing layout/state assertions**

Assert the cycle tab, form, three labels, delayed label, history, pause/resume, delete, and utility script include exist. Run the Node layout tests; expect missing markup/state failure.

- [ ] **Step 4: Implement Vue state and methods**

Add `cycleWatch`, form/loading/error/history state and methods `loadCycleWatchlist`, `addCycleWatch`, `updateCycleWatch`, `deleteCycleWatch`, `checkCycleWatch`, `loadCycleWatchHistory`, `markCycleWatchAlertsRead`. Restore loading flags in `finally`; require `window.confirm` only for delete.

- [ ] **Step 5: Run GREEN and commit**

Run both JS tests; expect pass.

```bash
git add quantClient/cycle-watch-utils.js quantClient/cycle-watch-utils.test.js quantClient/main.js quantClient/index.html
git commit -m "feat: add cycle watchlist client state"
```

---

### Task 7: Render the cycle-watch dashboard

**Files:**
- Modify: `quantClient/index.html`
- Modify: `quantClient/styles.css`
- Modify: `quantClient/cycle-watch-utils.test.js`

**Interfaces:**
- Consumes: Task 6 state and methods.
- Produces: complete management, opportunity, delayed, and history views.

- [ ] **Step 1: Extend failing UI assertions and run RED**

Assert bindings for code, optional note/prices, all mutations, manual check, read alerts, and history. Assert each table uses its own computed group and empty state.

- [ ] **Step 2: Implement page markup**

Add a sidebar button with unread count; a management form; separate `确认介入`, `低吸提示`, and `继续观察` tables; gray delayed-data rows; expandable history. Show price, percent, score, support, matched/missing/risk lists, check time, and data time.

- [ ] **Step 3: Add scoped styles**

Use `.cycle-watch-*` selectors only. Reuse panel/table/badge colors; keep global realtime table widths unchanged. Ensure the form works at 390px and tables scroll horizontally.

- [ ] **Step 4: Run JS tests and manual layout check GREEN**

Run `node quantClient/cycle-watch-utils.test.js` and existing layout tests. Serve the client and inspect desktop plus 390px width; verify delete confirmation.

- [ ] **Step 5: Commit**

```bash
git add quantClient/index.html quantClient/styles.css quantClient/cycle-watch-utils.test.js
git commit -m "feat: render cycle watchlist alerts"
```

---

### Task 8: Deploy and verify end to end

**Files:**
- Modify: `DEPLOY_SERVER.md`
- Modify: checked-in deployment copy/check scripts referenced there.

**Interfaces:**
- Produces: deployable feature and complete regression evidence.

- [ ] **Step 1: Add files and database verification to deployment docs**

Include all four new Python modules and `cycle-watch-utils.js`. Document `SHOW TABLES LIKE 'cycle_watchlist'`, `SHOW TABLES LIKE 'cycle_watch_evaluations'`, and recent-row SELECT queries.

- [ ] **Step 2: Run focused Python tests**

```bash
.venv/bin/python -m unittest tests.test_cycle_watch_scoring tests.test_cycle_watch_repository tests.test_cycle_watch_service tests.test_cycle_watch_api
```

Expected: zero failures.

- [ ] **Step 3: Run surrounding regressions**

```bash
.venv/bin/python -m unittest tests.test_realtime_info_service tests.test_realtime_info_api tests.test_intraday_monitor_service tests.test_intraday_monitor_api tests.test_overnight_monitor_service tests.test_morning_follow_service
```

Expected: zero failures and unchanged existing monitor behavior.

- [ ] **Step 4: Run Java, browser, syntax, and diff checks**

```bash
cd quantServer/quantServer && ./mvnw -q test
cd ../..
node quantClient/cycle-watch-utils.test.js
node quantClient/realtime-tail-premium-layout.test.js
.venv/bin/python -m py_compile cycle_watch_models.py cycle_watch_repository.py cycle_watch_scoring.py cycle_watch_service.py app.py
git diff --check
```

Expected: every command exits 0.

- [ ] **Step 5: Exercise one disposable API row safely**

Use `600000` only if it is absent from the user watchlist; otherwise obtain a permitted code. Add, confirm normalized suffix, run manual check, inspect all groups and history, then delete only that newly created row. Never delete a pre-existing row.

- [ ] **Step 6: Commit deployment documentation**

```bash
git add DEPLOY_SERVER.md
git commit -m "docs: deploy cycle watchlist alerts"
```

- [ ] **Step 7: Request final code review**

Use `superpowers:requesting-code-review`, verify findings, rerun affected focused tests, then rerun Steps 2–4 before reporting completion.
