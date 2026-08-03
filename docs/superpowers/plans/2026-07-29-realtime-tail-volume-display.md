# Realtime Tail Volume Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show an explicit tail-volume state and ratio in both realtime-information tables.

**Architecture:** Add one UMD-style pure frontend utility that maps API row data plus the 14:30 clock state to display text and an existing badge state. Load it before `main.js`, expose it through thin Vue methods, and render one new column in both the realtime confluence and overnight-selection tables.

**Tech Stack:** JavaScript, Vue 3 global build, Node.js `assert`

## Global Constraints

- Reuse `tail_volume_ratio` and `tail_after_1430_available`; do not add or recalculate backend fields.
- Use `1.5` as the only tail-volume expansion threshold.
- Display exactly two decimal places for valid ratios.
- Do not change realtime confluence or overnight-selection filtering, scoring, sorting, or row highlighting.
- Reuse existing `monitor-badge strong` and `monitor-badge muted` styles.

---

### Task 1: Pure tail-volume display utility

**Files:**
- Create: `quantClient/realtime-info-utils.js`
- Create: `quantClient/realtime-info-utils.test.js`

**Interfaces:**
- Consumes: `tailVolumeDisplay(row: object, isAfter1430: boolean)`.
- Produces: `{ text: string, state: "strong" | "muted" }`.

- [x] **Step 1: Write the failing utility test**

Create `quantClient/realtime-info-utils.test.js`:

```javascript
const assert = require('assert');
const { tailVolumeDisplay } = require('./realtime-info-utils.js');

assert.deepEqual(
  tailVolumeDisplay({ tail_after_1430_available: true, tail_volume_ratio: 1.5 }, true),
  { text: '放量 1.50倍', state: 'strong' },
);
assert.deepEqual(
  tailVolumeDisplay({ tail_after_1430_available: true, tail_volume_ratio: 1.49 }, true),
  { text: '正常 1.49倍', state: 'muted' },
);
assert.deepEqual(
  tailVolumeDisplay({ tail_after_1430_available: false }, false),
  { text: '未到', state: 'muted' },
);
assert.deepEqual(
  tailVolumeDisplay({ tail_after_1430_available: false }, true),
  { text: '无数据', state: 'muted' },
);
assert.deepEqual(
  tailVolumeDisplay({ tail_after_1430_available: true, tail_volume_ratio: null }, true),
  { text: '无数据', state: 'muted' },
);

console.log('realtime tail-volume display regression ok');
```

- [x] **Step 2: Run the test and verify the expected failure**

Run:

```bash
node quantClient/realtime-info-utils.test.js
```

Expected: FAIL with `Cannot find module './realtime-info-utils.js'`.

- [x] **Step 3: Implement the utility**

Create `quantClient/realtime-info-utils.js`:

```javascript
(function exposeRealtimeInfoUtils(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.tailVolumeDisplay = api.tailVolumeDisplay;
}(typeof globalThis !== 'undefined' ? globalThis : this, function buildRealtimeInfoUtils() {
  function tailVolumeDisplay(row, isAfter1430) {
    if (!row || row.tail_after_1430_available !== true) {
      return {
        text: isAfter1430 ? '无数据' : '未到',
        state: 'muted',
      };
    }
    const rawRatio = row.tail_volume_ratio;
    if (rawRatio === null || rawRatio === undefined || rawRatio === '') {
      return { text: '无数据', state: 'muted' };
    }
    const ratio = Number(rawRatio);
    if (!Number.isFinite(ratio)) {
      return { text: '无数据', state: 'muted' };
    }
    const expanded = ratio >= 1.5;
    return {
      text: `${expanded ? '放量' : '正常'} ${ratio.toFixed(2)}倍`,
      state: expanded ? 'strong' : 'muted',
    };
  }

  return { tailVolumeDisplay };
}));
```

- [x] **Step 4: Run the focused test and existing frontend utility tests**

Run:

```bash
node quantClient/realtime-info-utils.test.js
node quantClient/cache-utils.test.js
node quantClient/cache-view-model.test.js
node quantClient/sector-potential-utils.test.js
```

Expected: all four commands exit 0.

- [x] **Step 5: Preserve the utility and test without an intermediate commit**

`quantClient/index.html` and `quantClient/main.js` already contain uncommitted
user work for the surrounding realtime-information feature. Keep this task
unstaged so the final change can be reviewed without accidentally committing
that existing work.

### Task 2: Render tail-volume columns in both realtime tables

**Files:**
- Modify: `quantClient/index.html`
- Modify: `quantClient/main.js`

**Interfaces:**
- Consumes: global `tailVolumeDisplay(row, isAfter1430)` from Task 1.
- Produces: Vue methods `tailVolumeText(row) -> string` and `tailVolumeBadgeClass(row) -> string`.

- [x] **Step 1: Add thin Vue display methods**

Add beside `tailReturnText(row)` in `quantClient/main.js`:

```javascript
tailVolumeText(row) {
  return tailVolumeDisplay(row, this.isAfterClock('14:30')).text;
},
tailVolumeBadgeClass(row) {
  return tailVolumeDisplay(row, this.isAfterClock('14:30')).state;
},
```

- [x] **Step 2: Load the utility before the Vue application**

Add before `main.js` at the bottom of `quantClient/index.html`:

```html
<script src="./realtime-info-utils.js?v=20260729-tail-volume-v1"></script>
```

- [x] **Step 3: Add the realtime-confluence column**

In the “实时共振” table:

```html
<th>尾盘量能</th>
```

Place it immediately after “14:30后”. Add the matching cell:

```html
<td>
  <span class="monitor-badge" :class="tailVolumeBadgeClass(row)">
    {{ tailVolumeText(row) }}
  </span>
</td>
```

Change its empty row from `colspan="10"` to `colspan="11"`.

- [x] **Step 4: Add the overnight-selection column**

In the “隔夜选股” table, place the same header and cell immediately after “14:30后” and before “集合竞价”. Change its empty row from `colspan="11"` to `colspan="12"`.

- [x] **Step 5: Run syntax and utility verification**

Run:

```bash
node --check quantClient/realtime-info-utils.js
node --check quantClient/main.js
node quantClient/realtime-info-utils.test.js
node quantClient/cache-utils.test.js
node quantClient/cache-view-model.test.js
node quantClient/sector-potential-utils.test.js
```

Expected: all six commands exit 0.

- [x] **Step 6: Review the exact table structure**

Run:

```bash
rg -n "尾盘量能|tailVolumeText|tailVolumeBadgeClass|colspan=\"(11|12)\"|realtime-info-utils" \
  quantClient/index.html quantClient/main.js
```

Expected:

- two “尾盘量能” headers;
- two cells invoking both Vue methods;
- empty-state colspans 11 and 12;
- utility script loaded before `main.js`.

- [x] **Step 7: Review the working-tree boundary**

```bash
git status --short -- \
  quantClient/realtime-info-utils.js \
  quantClient/realtime-info-utils.test.js \
  quantClient/index.html \
  quantClient/main.js
```

Expected: only these four planned frontend files are part of this task; leave
them unstaged because the two tracked files contain pre-existing user changes.
