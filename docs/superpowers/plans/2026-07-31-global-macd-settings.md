# Global MACD Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every stock-selection MACD hardcode with one MySQL-backed global configuration whose default is `5/34/5`, editable from the UI through a “save and recalculate” action.

**Architecture:** A focused `indicator_settings.py` module owns schema initialization, validation, versioned configuration reads/updates, and the common pandas MACD calculation. Every daily and 60-minute strategy consumes that function, while derived-result caches and free-review snapshot versions include the configuration parameter key so old `12/26/9` results cannot be returned after an update. Python exposes read/update APIs, Spring proxies them, and the Vue page edits the settings and displays the active calculation basis.

**Tech Stack:** Python 3.10, pandas, PyMySQL, FastAPI/Pydantic, MySQL, Java 17, Spring Boot RestClient, Vue 3 static frontend, Python unittest, Node.js assertion tests, Maven.

## Global Constraints

- Default MACD periods are fast `5`, slow `34`, and signal `5`.
- All three periods are integers from `2` through `120`, and fast must be smaller than slow.
- MySQL is the authoritative global configuration source.
- Every MACD used by selection, scoring, realtime information, intraday monitoring, overnight selection, morning follow-up, or sector confirmation must use the common calculation.
- Saving settings invalidates only derived results; daily and minute source-market caches remain intact.
- Old derived rows may remain in MySQL for diagnostics but cannot match the current parameter key.
- A free-review rebuild is started after a successful setting update.
- Tests must prove old cache keys and old free-review snapshots are not returned under the new configuration.

---

### Task 1: Add versioned MACD settings and common calculation

**Files:**
- Create: `indicator_settings.py`
- Create: `tests/test_indicator_settings.py`

**Interfaces:**
- Produces:
  - `MacdSettings(fast_period, slow_period, signal_period, config_version, updated_at)`
  - `init_indicator_settings() -> None`
  - `validate_macd_periods(fast, slow, signal) -> tuple[int, int, int]`
  - `load_macd_settings() -> dict`
  - `update_macd_settings(fast, slow, signal) -> dict`
  - `macd_parameter_key(settings=None) -> str`
  - `calculate_macd(close, settings=None, min_periods=True) -> tuple[Series, Series, Series]`

- [ ] **Step 1: Write failing schema/default tests**

Test that initialization creates `indicator_settings` with primary key `config_key`, a version column, and an idempotent default row:

```text
config_key='macd'
fast_period=5
slow_period=34
signal_period=5
config_version=1
```

- [ ] **Step 2: Run the schema tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_indicator_settings
```

Expected: import fails because `indicator_settings.py` does not exist.

- [ ] **Step 3: Implement schema initialization and reads**

Use a process-local schema lock and the existing `database.get_connection()` transaction pattern. Cache configuration reads for at most five seconds, with an explicit cache reset after updates.

- [ ] **Step 4: Write failing validation and transaction tests**

Cover:

- valid `5/34/5`;
- non-integers;
- values below 2 or above 120;
- fast equal to or greater than slow;
- update increments `config_version`;
- simulated update failure rolls back and does not replace the in-memory setting.

- [ ] **Step 5: Implement validated transactional updates**

Lock the `macd` row with `SELECT ... FOR UPDATE`, update all periods plus `config_version=config_version+1`, commit through `get_connection()`, clear the short read cache, and return the committed row.

- [ ] **Step 6: Write failing calculation tests**

Compare `calculate_macd()` against:

```python
ema_fast = close.ewm(span=5, adjust=False, min_periods=5).mean()
ema_slow = close.ewm(span=34, adjust=False, min_periods=34).mean()
dif = ema_fast - ema_slow
dea = dif.ewm(span=5, adjust=False, min_periods=5).mean()
histogram = (dif - dea) * 2
```

Also assert `macd_parameter_key()` includes all periods and the configuration version.

- [ ] **Step 7: Implement the common calculation and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_indicator_settings
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add indicator_settings.py tests/test_indicator_settings.py
git commit -m "feat: add global macd settings"
```

---

### Task 2: Migrate every stock-selection MACD calculation

**Files:**
- Modify: `strategy.py`
- Modify: `free_review_scoring.py`
- Modify: `overnight_monitor_service.py`
- Modify: `tests/test_advantage_stock_scoring.py`
- Modify: `tests/test_free_review_scoring.py`
- Modify: `tests/test_overnight_monitor_service.py`
- Create: `tests/test_macd_configuration_usage.py`

**Interfaces:**
- Consumes: `calculate_macd()` and `load_macd_settings()`.
- Produces: unchanged strategy result fields, calculated with the global settings.

- [ ] **Step 1: Write a failing hardcode contract test**

Parse production Python sources and fail when a stock-selection MACD calculation contains `span=12`, `span=26`, or a MACD signal `span=9`. Exclude KDJ `ewm(com=2)` and explanatory text.

- [ ] **Step 2: Run the contract and strategy tests to verify RED**

Run:

```bash
python3 -m unittest \
  tests.test_macd_configuration_usage \
  tests.test_advantage_stock_scoring \
  tests.test_free_review_scoring \
  tests.test_overnight_monitor_service
```

Expected: the contract test reports the hardcoded sites in `strategy.py`, `free_review_scoring.py`, and `overnight_monitor_service.py`.

- [ ] **Step 3: Replace daily and 60-minute calculations**

Replace all MACD calculations in:

- `_macd_kdj_60m_signal`;
- daily feature construction;
- breakout/confluence construction;
- free-review history metrics;
- sector 60-minute signal calculation.

Preserve each caller’s previous `min_periods` behavior by passing the appropriate common-function option.

- [ ] **Step 4: Add result provenance**

Add these fields to relevant public result metadata:

```text
macd_fast_period
macd_slow_period
macd_signal_period
macd_parameter_key
```

Do not rename existing DIF, DEA, histogram, cross, or bullish fields.

- [ ] **Step 5: Run focused strategy tests**

Run the Step 2 command.

Expected: all tests pass and no stock-selection MACD hardcodes remain.

- [ ] **Step 6: Commit**

```bash
git add strategy.py free_review_scoring.py overnight_monitor_service.py \
  tests/test_advantage_stock_scoring.py tests/test_free_review_scoring.py \
  tests/test_overnight_monitor_service.py tests/test_macd_configuration_usage.py
git commit -m "feat: apply global macd settings to strategies"
```

---

### Task 3: Version derived caches and free-review snapshots

**Files:**
- Modify: `realtime_cache.py`
- Modify: `realtime_info_service.py`
- Modify: `intraday_monitor_service.py`
- Modify: `overnight_monitor_service.py`
- Modify: `morning_follow_service.py`
- Modify: `free_review_scoring.py`
- Modify: `free_review_repository.py`
- Modify: `free_review_service.py`
- Modify: `tests/test_realtime_cache.py`
- Modify: `tests/test_realtime_info_service.py`
- Modify: `tests/test_intraday_monitor_service.py`
- Modify: `tests/test_morning_follow_service.py`
- Modify: `tests/test_free_review_repository.py`
- Modify: `tests/test_free_review_service.py`

**Interfaces:**
- Consumes: `macd_parameter_key()`.
- Produces:
  - parameter-aware in-memory result keys;
  - parameter-aware MySQL `cache_key` values;
  - current free-review score version such as `free-review-v1-macd-5-34-5-v2`.

- [ ] **Step 1: Write failing cache isolation tests**

Test that:

- identical request parameters under two MACD versions produce different keys;
- the realtime MySQL loader does not request the old key;
- overnight and morning-follow in-memory keys differ by MACD key;
- free-review repository queries only the current derived score version.

- [ ] **Step 2: Run focused tests to verify RED**

Run:

```bash
python3 -m unittest \
  tests.test_realtime_cache \
  tests.test_realtime_info_service \
  tests.test_intraday_monitor_service \
  tests.test_morning_follow_service \
  tests.test_free_review_repository \
  tests.test_free_review_service
```

Expected: key/version assertions fail.

- [ ] **Step 3: Add the parameter key to derived cache keys**

Include `macd_parameter_key()` wherever filtered result data is cached. Do not change raw minute-bar cache keys because their values do not depend on MACD periods.

- [ ] **Step 4: Derive the free-review version**

Keep the base version constant `free-review-v1`, and add:

```python
def current_score_version():
    return f"{BASE_SCORE_VERSION}-{macd_parameter_key()}"
```

Use the derived value for builds, status, persistence, queries, sectors, metadata, and CSV.

- [ ] **Step 5: Expose stale-version information**

Metadata may report the latest old snapshot for diagnostics, but `ready` is true only when a snapshot exists for the current parameter key. Include active MACD settings in metadata.

- [ ] **Step 6: Run focused tests to verify GREEN**

Run the Step 2 command.

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add realtime_cache.py realtime_info_service.py \
  intraday_monitor_service.py overnight_monitor_service.py \
  morning_follow_service.py free_review_scoring.py \
  free_review_repository.py free_review_service.py tests
git commit -m "feat: version results by macd settings"
```

---

### Task 4: Add settings APIs and save-and-recalculate orchestration

**Files:**
- Create: `indicator_settings_models.py`
- Modify: `indicator_settings.py`
- Modify: `free_review_service.py`
- Modify: `app.py`
- Create: `tests/test_indicator_settings_api.py`
- Modify: `tests/test_indicator_settings.py`
- Modify: `tests/test_free_review_service.py`

**Interfaces:**
- Produces:
  - `GET /api/indicator-settings/macd`
  - `PUT /api/indicator-settings/macd`
  - `MacdSettingsUpdate`
  - `save_macd_settings_and_recalculate(request) -> dict`

- [ ] **Step 1: Write failing API tests**

Assert:

- GET returns the current periods, version, key, and update time;
- PUT validates and forwards `5/34/5`;
- validation failures map to HTTP 422;
- database failures map to HTTP 502;
- successful PUT returns the new setting plus free-review queued/running status.

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
python3 -m unittest \
  tests.test_indicator_settings \
  tests.test_indicator_settings_api \
  tests.test_free_review_service
```

Expected: models, routes, and orchestration are missing.

- [ ] **Step 3: Implement request model and routes**

Use Pydantic integer bounds and a model validator for `fast_period < slow_period`. Return setting fields in snake case.

- [ ] **Step 4: Implement update orchestration**

After a committed settings update:

1. clear only process-local derived-result dictionaries through explicit service functions;
2. start `start_free_review_build(force=True)`;
3. return both the committed setting and build state.

MySQL result rows are not deleted; versioned keys make them unreachable under the current setting.

- [ ] **Step 5: Run API and service tests**

Run the Step 2 command.

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add indicator_settings.py indicator_settings_models.py \
  free_review_service.py app.py tests/test_indicator_settings.py \
  tests/test_indicator_settings_api.py tests/test_free_review_service.py
git commit -m "feat: expose global macd settings api"
```

---

### Task 5: Proxy settings through Spring

**Files:**
- Create: `quantServer/quantServer/src/main/java/com/codec/quantserver/dto/MacdSettingsRequest.java`
- Modify: `quantServer/quantServer/src/main/java/com/codec/quantserver/service/QuantPythonClient.java`
- Modify: `quantServer/quantServer/src/main/java/com/codec/quantserver/controller/QuantController.java`
- Modify: `quantServer/quantServer/src/test/java/com/codec/quantserver/controller/QuantControllerTest.java`

**Interfaces:**
- Produces:
  - `GET /api/quant/indicator-settings/macd`
  - `PUT /api/quant/indicator-settings/macd`

- [ ] **Step 1: Write failing Spring controller tests**

Test GET forwarding and PUT body mapping for snake-case JSON:

```json
{
  "fast_period": 5,
  "slow_period": 34,
  "signal_period": 5
}
```

- [ ] **Step 2: Run the controller test to verify RED**

Run:

```bash
mvn -q -f quantServer/quantServer/pom.xml \
  -Dtest=QuantControllerTest test
```

Expected: DTO, client methods, and routes are missing.

- [ ] **Step 3: Implement DTO, RestClient calls, and controller routes**

Annotate every DTO property with its snake-case JSON name. Forward Python errors through the existing exception handler.

- [ ] **Step 4: Run focused and full Maven tests**

```bash
mvn -q -f quantServer/quantServer/pom.xml \
  -Dtest=QuantControllerTest test
mvn -q -f quantServer/quantServer/pom.xml test
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add quantServer/quantServer/src/main/java \
  quantServer/quantServer/src/test/java/com/codec/quantserver/controller/QuantControllerTest.java
git commit -m "feat: proxy global macd settings"
```

---

### Task 6: Add frontend global settings and result provenance

**Files:**
- Modify: `quantClient/free-review-utils.js`
- Modify: `quantClient/free-review-utils.test.js`
- Modify: `quantClient/main.js`
- Modify: `quantClient/index.html`
- Modify: `quantClient/styles.css`
- Modify: `quantClient/free-review-layout.test.js`

**Interfaces:**
- Produces:
  - `validateMacdSettings(settings) -> {valid, message}`
  - Vue state `macdSettings`, `macdSettingsForm`, `macdSettingsSaving`
  - methods `loadMacdSettings()` and `saveMacdSettingsAndRecalculate()`

- [ ] **Step 1: Write failing frontend utility tests**

Cover:

- default `5/34/5`;
- valid boundaries;
- non-integer rejection;
- fast not smaller than slow;
- current parameter label `MACD 5/34/5`.

- [ ] **Step 2: Write failing layout tests**

Require:

- three labeled period inputs;
- current version and update time;
- “保存并重新计算” button;
- MACD provenance text in free-review, realtime, intraday, overnight, and morning-follow sections.

- [ ] **Step 3: Run tests to verify RED**

```bash
node quantClient/free-review-utils.test.js
node quantClient/free-review-layout.test.js
```

Expected: settings utilities and controls are missing.

- [ ] **Step 4: Implement settings state and API calls**

Load settings during initial refresh. Validate before PUT. On success:

- replace displayed settings with the committed response;
- store returned free-review build state;
- start the existing two-second build polling;
- clear displayed derived realtime rows so old-period results are not shown.

- [ ] **Step 5: Add controls and provenance labels**

Place the editor in the free-review page. Display `MACD fast/slow/signal` beside each realtime data timestamp without adding separate editors to every page.

- [ ] **Step 6: Run all frontend tests**

```bash
set -e
for test_file in quantClient/*.test.js; do
  node "$test_file"
done
node --check quantClient/free-review-utils.js
node --check quantClient/main.js
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add quantClient/free-review-utils.js \
  quantClient/free-review-utils.test.js quantClient/main.js \
  quantClient/index.html quantClient/styles.css \
  quantClient/free-review-layout.test.js
git commit -m "feat: configure global macd periods"
```

---

### Task 7: Update deployment and run complete regression

**Files:**
- Modify: `DEPLOY_SERVER.md`

**Interfaces:**
- Produces: idempotent settings initialization, server verification, rollback, and cache-version troubleshooting instructions.

- [ ] **Step 1: Update deployment initialization**

Add `indicator_settings.py` and `indicator_settings_models.py` to release checks and Python compilation. Initialize the settings schema before services start:

```bash
python3 -c "
import settings
settings.load_env_files()
from indicator_settings import init_indicator_settings
init_indicator_settings()
print('indicator settings ready')
"
```

- [ ] **Step 2: Add server API verification**

Document:

```bash
curl --fail \
  'http://127.0.0.1:8081/api/quant/indicator-settings/macd'

curl --fail -X PUT \
  'http://127.0.0.1:8081/api/quant/indicator-settings/macd' \
  -H 'Content-Type: application/json' \
  -d '{"fast_period":5,"slow_period":34,"signal_period":5}'
```

Explain that raw market cache row counts do not fall after updates, while new derived cache keys and free-review versions include the MACD parameter key.

- [ ] **Step 3: Run complete verification**

```bash
python3 -m unittest discover -s tests -p 'test_*.py'

set -e
for test_file in quantClient/*.test.js; do
  node "$test_file"
done
node --check quantClient/main.js

mvn -q -f quantServer/quantServer/pom.xml test
```

Expected: all pass.

- [ ] **Step 4: Run compatibility suites**

```bash
python3 -m unittest \
  tests.test_advantage_stock_scoring \
  tests.test_intraday_monitor_service \
  tests.test_morning_follow_service \
  tests.test_overnight_monitor_service \
  tests.test_realtime_info_service \
  tests.test_free_review_scoring \
  tests.test_free_review_service
```

Expected: all pass under MACD `5/34/5`.

- [ ] **Step 5: Check target diffs and commit**

```bash
git diff --check -- \
  indicator_settings.py indicator_settings_models.py strategy.py \
  free_review_scoring.py free_review_repository.py free_review_service.py \
  realtime_cache.py realtime_info_service.py intraday_monitor_service.py \
  overnight_monitor_service.py morning_follow_service.py app.py \
  quantClient quantServer/quantServer/src DEPLOY_SERVER.md tests

git add DEPLOY_SERVER.md
git commit -m "docs: deploy global macd settings"
```

