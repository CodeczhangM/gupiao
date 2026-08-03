# Morning Follow Table Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修复外层“次日早盘跟进”表格多列内容显示不全的问题，使 14 列在当前内容区内按比例完整展示，并允许单元格换行。

**Architecture:** 仅给外层早盘跟进表增加独立的 `morning-follow-table` 样式作用域，在现有 Vue 模板中保留全部字段和数据逻辑。通过固定百分比列宽、自动行高和文本换行覆盖通用表格的省略号规则；同时用静态 Node 回归测试确认样式只命中 `overnight_monitor`，不进入 `realtime_info`。

**Tech Stack:** Vue 模板（静态 HTML）、CSS、Node.js `assert` 静态回归测试。

**Global Constraints:**

- 不修改后端服务、筛选参数、接口字段或实时数据加载逻辑。
- 不修改“实时信息”页内部表格。
- 不覆盖或清理工作区中已有的用户改动。
- 实现文件不暂存、不提交；仅在当前工作区完成修改与验证。

---

### Task 1: Add a failing scoped layout regression test

**Files:**

- Create: `quantClient/morning-follow-layout.test.js`
- Read: `quantClient/index.html`
- Read: `quantClient/styles.css`

**Step 1: Record isolation baselines**

Run:

```bash
sha256sum realtime_info_service.py
sed -n "/activeTab === 'realtime_info'/,/activeTab === 'reports'/p" quantClient/index.html | sha256sum
```

Expected baselines:

```text
603ca540496460bad5d0f7c62c5c9806076e676a019c8a5b31491969b969cdec  realtime_info_service.py
e9980aedc4e57299912b4c165d24783634a25082ab445925b4bdb9e2191d1c26  -
```

If either value differs before implementation, stop and inspect the existing user changes instead of replacing them.

**Step 2: Write the failing static test**

Create `quantClient/morning-follow-layout.test.js`:

```js
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = __dirname;
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'styles.css'), 'utf8');

const outerStart = html.indexOf(`<section v-show="activeTab === 'overnight_monitor'"`);
const internalStart = html.indexOf(`<section v-show="activeTab === 'realtime_info'"`);
const reportsStart = html.indexOf(`<section v-show="activeTab === 'reports'"`, internalStart);

assert(outerStart >= 0, '缺少次日早盘跟进区块');
assert(internalStart > outerStart, '缺少实时信息区块或区块顺序异常');
assert(reportsStart > internalStart, '缺少历史区块或区块顺序异常');

const outerSection = html.slice(outerStart, internalStart);
const internalSection = html.slice(internalStart, reportsStart);

assert.match(
  outerSection,
  /class="table-wrap intraday-monitor-table morning-follow-table"/,
  '外层次日早盘跟进表应使用独立样式类'
);
assert.doesNotMatch(
  internalSection,
  /morning-follow-table/,
  '实时信息区块不能使用外层早盘跟进样式类'
);
assert.match(
  html,
  /styles\.css\?v=20260730-morning-follow-layout-v1/,
  '应更新样式缓存版本'
);

assert.match(
  css,
  /\.morning-follow-table table\s*\{[^}]*width:\s*100%;[^}]*min-width:\s*100%;[^}]*table-layout:\s*fixed;/s,
  '早盘跟进表应在内容区内使用固定布局'
);
assert.match(
  css,
  /\.morning-follow-table th,\s*\.morning-follow-table td\s*\{[^}]*height:\s*auto;[^}]*white-space:\s*normal;[^}]*overflow:\s*visible;[^}]*text-overflow:\s*clip;[^}]*overflow-wrap:\s*anywhere;/s,
  '早盘跟进单元格应允许换行且不使用省略号'
);
assert.match(
  css,
  /\.morning-follow-table \.monitor-reason-cell\s*\{[^}]*min-width:\s*0;[^}]*max-width:\s*none;/s,
  '备注列不应继承通用最小宽度'
);
assert.match(
  css,
  /\.morning-follow-table \.monitor-badge\s*\{[^}]*white-space:\s*normal;/s,
  '状态徽标应允许换行'
);

const widths = [8, 6, 5, 5, 4, 5, 5, 9, 4, 6, 5, 5, 8, 25];
widths.forEach((width, index) => {
  const column = index + 1;
  const rule = new RegExp(
    `\\.morning-follow-table th:nth-child\\(${column}\\),\\s*` +
      `\\.morning-follow-table td:nth-child\\(${column}\\)\\s*` +
      `\\{\\s*width:\\s*${width}%;\\s*\\}`,
    's'
  );
  assert.match(css, rule, `第 ${column} 列宽度应为 ${width}%`);
});

assert.strictEqual(
  widths.reduce((total, width) => total + width, 0),
  100,
  '列宽总和必须为 100%'
);

console.log('morning follow layout tests passed');
```

**Step 3: Run the test to verify RED**

Run:

```bash
node quantClient/morning-follow-layout.test.js
```

Expected: FAIL，提示外层表格缺少 `morning-follow-table`，或缺少对应样式规则。

### Task 2: Implement the scoped wrapping layout

**Files:**

- Modify: `quantClient/index.html`（外层 `overnight_monitor` 表格容器与 CSS 缓存版本）
- Modify: `quantClient/styles.css`（在现有 `.intraday-monitor-table` 规则之后添加独立作用域）
- Test: `quantClient/morning-follow-layout.test.js`

**Step 1: Scope the outer table and refresh the stylesheet cache key**

In `quantClient/index.html`, change only the outer `overnight_monitor` table container:

```html
<div class="table-wrap intraday-monitor-table morning-follow-table">
```

Update the stylesheet link:

```html
<link rel="stylesheet" href="./styles.css?v=20260730-morning-follow-layout-v1">
```

Do not add `morning-follow-table` anywhere inside the `realtime_info` section.

**Step 2: Add the wrapping and percentage-width CSS**

Append after the existing `.intraday-monitor-table` / `.monitor-reason-cell` base rules in `quantClient/styles.css`:

```css
.morning-follow-table table {
  width: 100%;
  min-width: 100%;
  table-layout: fixed;
}

.morning-follow-table th,
.morning-follow-table td {
  height: auto;
  padding: 8px 6px;
  white-space: normal;
  overflow: visible;
  text-overflow: clip;
  overflow-wrap: anywhere;
  line-height: 1.35;
}

.morning-follow-table .monitor-badge {
  white-space: normal;
  text-align: left;
}

.morning-follow-table .monitor-reason-cell {
  min-width: 0;
  max-width: none;
}

.morning-follow-table th:nth-child(1),
.morning-follow-table td:nth-child(1) { width: 8%; }

.morning-follow-table th:nth-child(2),
.morning-follow-table td:nth-child(2) { width: 6%; }

.morning-follow-table th:nth-child(3),
.morning-follow-table td:nth-child(3) { width: 5%; }

.morning-follow-table th:nth-child(4),
.morning-follow-table td:nth-child(4) { width: 5%; }

.morning-follow-table th:nth-child(5),
.morning-follow-table td:nth-child(5) { width: 4%; }

.morning-follow-table th:nth-child(6),
.morning-follow-table td:nth-child(6) { width: 5%; }

.morning-follow-table th:nth-child(7),
.morning-follow-table td:nth-child(7) { width: 5%; }

.morning-follow-table th:nth-child(8),
.morning-follow-table td:nth-child(8) { width: 9%; }

.morning-follow-table th:nth-child(9),
.morning-follow-table td:nth-child(9) { width: 4%; }

.morning-follow-table th:nth-child(10),
.morning-follow-table td:nth-child(10) { width: 6%; }

.morning-follow-table th:nth-child(11),
.morning-follow-table td:nth-child(11) { width: 5%; }

.morning-follow-table th:nth-child(12),
.morning-follow-table td:nth-child(12) { width: 5%; }

.morning-follow-table th:nth-child(13),
.morning-follow-table td:nth-child(13) { width: 8%; }

.morning-follow-table th:nth-child(14),
.morning-follow-table td:nth-child(14) { width: 25%; }
```

**Step 3: Run the focused test to verify GREEN**

Run:

```bash
node quantClient/morning-follow-layout.test.js
```

Expected:

```text
morning follow layout tests passed
```

### Task 3: Run regression and isolation verification

**Files:**

- Verify: `quantClient/index.html`
- Verify: `quantClient/styles.css`
- Verify: `quantClient/morning-follow-layout.test.js`
- Verify unchanged: `realtime_info_service.py`

**Step 1: Run neighboring frontend regression tests**

Run:

```bash
node quantClient/morning-follow-utils.test.js
node quantClient/realtime-info-utils.test.js
node --check quantClient/main.js
```

Expected: both test files pass and `node --check` exits with code 0.

**Step 2: Check patch formatting**

Run:

```bash
git diff --check -- quantClient/index.html quantClient/styles.css quantClient/morning-follow-layout.test.js
```

Expected: no output and exit code 0.

**Step 3: Verify realtime information isolation**

Run:

```bash
sha256sum realtime_info_service.py
sed -n "/activeTab === 'realtime_info'/,/activeTab === 'reports'/p" quantClient/index.html | sha256sum
```

Expected values remain:

```text
603ca540496460bad5d0f7c62c5c9806076e676a019c8a5b31491969b969cdec  realtime_info_service.py
e9980aedc4e57299912b4c165d24783634a25082ab445925b4bdb9e2191d1c26  -
```

**Step 4: Review the exact implementation diff**

Run:

```bash
git diff -- quantClient/index.html quantClient/styles.css
git status --short quantClient/index.html quantClient/styles.css quantClient/morning-follow-layout.test.js
```

Expected: only the approved outer-table class, CSS cache key, scoped wrapping rules, and new layout test are attributable to this task. Existing unrelated user changes remain intact.
