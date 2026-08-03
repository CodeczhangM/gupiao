# Morning Follow Relaxed Tier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep every daily-qualified candidate that passes the stock 60-minute model and non-downward sector model, annotate failed tail conditions, and upgrade relaxed candidates to “谨慎跟进” when next-morning confirmation succeeds.

**Architecture:** Extend only `morning_follow_service.py` with a strict/relaxed setup tier. Tail thresholds become scored annotations while daily, stock 60-minute, sector 60-minute, and minute-data availability remain hard gates. Extend the existing outer Vue panel and its isolated utility; do not modify realtime information’s overnight table or the legacy overnight service.

**Tech Stack:** Python 3.10, pandas, `unittest`, Vue 3 global build, Node.js assertions

## Global Constraints

- Do not modify `realtime_info_service.py`, `build_overnight_monitor`, `/overnight-monitor`, or the realtime-information “隔夜选股” block.
- Keep the existing daily hard filters, stock 60-minute model, sector-not-down rule, and minute-data availability as hard gates.
- Tail return `0.15%–1.20%`, tail volume `1.20–3.00`, and tail close position `>= 0.75` become scored annotations, not hard rejection rules.
- A relaxed candidate may become “谨慎跟进” only when every existing 9:35 morning confirmation rule passes.
- “谨慎跟进” copy must include light-position, no-chasing, incomplete-tail-confirmation, and T+1 language.
- Existing tracked files contain user changes; do not stage or commit implementation files.

---

## Execution Preflight

- [ ] Before Task 1, record the two isolation checksums:

```bash
sha256sum realtime_info_service.py
sed -n "/activeTab === 'realtime_info'/,/activeTab === 'reports'/p" \
  quantClient/index.html | sha256sum
```

Keep both values in the execution notes for Task 3.

---

### Task 1: Relaxed setup tier and cautious morning upgrade

**Files:**
- Modify: `morning_follow_service.py:149-446`
- Modify: `tests/test_morning_follow_service.py:129-375`

**Interfaces:**
- Produces: `_tail_condition_evaluation(tail_return: float, tail_volume: float, tail_position: float) -> tuple[int, list[str]]`.
- Extends: `_setup_row(...)` with `tail_condition_pass_count`, `tail_conditions_all_pass`, `tail_condition_notes`, `setup_tier`, `setup_tier_label`, and `setup_tier_reason`.
- Extends: `_morning_confirmation(...)` with relaxed “宽松观察” and “谨慎跟进” states.

- [ ] **Step 1: Write failing strict/relaxed setup tests**

In `test_setup_row_requires_tail_rules_and_never_uses_opening_auction`, keep the
existing controlled signal and add:

```python
self.assertEqual(result["setup_tier"], "strict")
self.assertEqual(result["setup_tier_label"], "严格候选")
self.assertEqual(result["tail_condition_pass_count"], 3)
self.assertTrue(result["tail_conditions_all_pass"])
self.assertEqual(result["tail_condition_notes"], [])
self.assertEqual(result["follow_status"], "明日观察")
```

Replace the three tail-value subtests in
`test_setup_row_rejects_failed_tail_or_missing_sector_signal` with:

```python
for field, invalid_value, expected_note in (
    ("tail_return_after_1430", 0.11, "尾盘涨幅0.11%，低于0.15%"),
    ("tail_return_after_1430", 1.30, "尾盘涨幅1.30%，高于1.20%"),
    ("tail_volume_ratio", 0.85, "尾盘量能0.85倍，低于1.20倍"),
    ("tail_volume_ratio", 3.10, "尾盘量能3.10倍，高于3.00倍"),
    ("tail_close_position", 0.57, "尾盘收盘位置57%，低于75%"),
):
    with self.subTest(field=field):
        signal_builder.return_value = {
            **valid_signal,
            field: invalid_value,
        }
        result = _setup_row(stock, bars, sector, leader_codes=set())
        self.assertIsNotNone(result)
        self.assertEqual(result["setup_tier"], "relaxed")
        self.assertEqual(result["setup_tier_label"], "宽松观察")
        self.assertEqual(result["tail_condition_pass_count"], 2)
        self.assertIn(expected_note, result["tail_condition_notes"])
        self.assertEqual(result["follow_status"], "宽松观察")
```

Keep hard-gate assertions for an empty sector signal, an excluded sector, and a
tail frame without `low`. Add:

```python
signal_builder.return_value = None
self.assertIsNone(_setup_row(stock, bars, sector, leader_codes=set()))
```

Add a score-only relaxation assertion:

```python
signal_builder.return_value = {
    **valid_signal,
    "macd_above_zero_60m": False,
    "macd_recent_golden_cross_60m": True,
    "kdj_bullish_60m": False,
}
weak_sector = {
    **sector,
    "sector_macd_above_zero": False,
    "sector_macd_trending_up": False,
}
result = _setup_row(stock, bars, weak_sector, leader_codes=set())
self.assertIsNotNone(result)
self.assertEqual(result["tail_condition_notes"], [])
self.assertEqual(result["setup_tier"], "relaxed")
self.assertIn("低于严格候选70分", result["setup_tier_reason"])
```

- [ ] **Step 2: Write failing morning status tests**

Add:

```python
def test_relaxed_setup_waits_as_relaxed_observation_before_confirmation_day(self):
    result = _morning_confirmation(
        {
            "close": 10.0,
            "previous_tail_support": 9.95,
            "setup_tier": "relaxed",
        },
        pd.DataFrame(),
        datetime(2026, 7, 29, 15, 10),
        "20260730",
    )

    self.assertEqual(result["follow_status"], "宽松观察")
    self.assertIn("轻仓", result["morning_entry_plan"])
    self.assertIn("不可追高", result["morning_entry_plan"])


def test_relaxed_setup_upgrades_to_cautious_follow_after_935(self):
    result = _morning_confirmation(
        {
            "close": 10.0,
            "previous_tail_support": 9.95,
            "setup_tier": "relaxed",
        },
        morning_bars([10.1, 10.12, 10.14, 10.16, 10.18, 10.2]),
        datetime(2026, 7, 30, 9, 36),
        "20260730",
    )

    self.assertEqual(result["follow_status"], "谨慎跟进")
    self.assertIn("尾盘条件未完全确认", result["morning_entry_plan"])
    self.assertIn("下一交易日", result["t1_exit_plan"])
```

The existing strict test must continue to expect “可以跟进”, and existing
abandon tests must continue to expect “放弃”.

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```bash
env HOME=/tmp python3 -m unittest \
  tests.test_morning_follow_service.MorningFollowServiceTests.test_setup_row_requires_tail_rules_and_never_uses_opening_auction \
  tests.test_morning_follow_service.MorningFollowServiceTests.test_setup_row_rejects_failed_tail_or_missing_sector_signal \
  tests.test_morning_follow_service.MorningFollowServiceTests.test_relaxed_setup_waits_as_relaxed_observation_before_confirmation_day \
  tests.test_morning_follow_service.MorningFollowServiceTests.test_relaxed_setup_upgrades_to_cautious_follow_after_935 \
  -v
```

Expected: setup assertions fail because invalid tail values still return `None`;
morning assertions fail because the existing function returns “明日观察” and
“可以跟进”.

- [ ] **Step 4: Implement tail annotations and setup tiers**

Add before `_setup_row`:

```python
def _tail_condition_evaluation(
    tail_return: float,
    tail_volume: float,
    tail_position: float,
) -> tuple[int, list[str]]:
    notes: list[str] = []
    passed = 0

    if 0.15 <= tail_return <= 1.20:
        passed += 1
    elif tail_return < 0.15:
        notes.append(f"尾盘涨幅{tail_return:.2f}%，低于0.15%")
    else:
        notes.append(f"尾盘涨幅{tail_return:.2f}%，高于1.20%")

    if 1.20 <= tail_volume <= 3.00:
        passed += 1
    elif tail_volume < 1.20:
        notes.append(f"尾盘量能{tail_volume:.2f}倍，低于1.20倍")
    else:
        notes.append(f"尾盘量能{tail_volume:.2f}倍，高于3.00倍")

    if tail_position >= 0.75:
        passed += 1
    else:
        notes.append(f"尾盘收盘位置{tail_position * 100:.0f}%，低于75%")

    return passed, notes
```

In `_setup_row`, retain the `None` rejection for missing/non-finite tail values,
but remove the three range-based early returns. Replace fixed
`tail_score = 30` with:

```python
tail_condition_pass_count, tail_condition_notes = _tail_condition_evaluation(
    tail_return,
    tail_volume,
    tail_position,
)
tail_score = tail_condition_pass_count * 10
```

After calculating `follow_setup_score`, replace the score rejection with:

```python
tail_conditions_all_pass = tail_condition_pass_count == 3
strict = tail_conditions_all_pass and follow_setup_score >= 70
setup_tier = "strict" if strict else "relaxed"
setup_tier_label = "严格候选" if strict else "宽松观察"
if strict:
    setup_tier_reason = "尾盘三项及观察分均达标"
elif tail_condition_notes:
    setup_tier_reason = "；".join(tail_condition_notes)
else:
    setup_tier_reason = f"观察分{follow_setup_score}，低于严格候选70分"
```

Add these fields to the returned row:

```python
"tail_condition_pass_count": tail_condition_pass_count,
"tail_conditions_all_pass": tail_conditions_all_pass,
"tail_condition_notes": tail_condition_notes,
"setup_tier": setup_tier,
"setup_tier_label": setup_tier_label,
"setup_tier_reason": setup_tier_reason,
"follow_status": "明日观察" if strict else "宽松观察",
```

Build `follow_reason` without setup-day opening-auction fields:

```python
reason_parts = [
    setup_tier_label,
    f"尾盘涨幅{tail_return:.2f}%",
    f"尾盘量能{tail_volume:.2f}倍",
    str(sector_signal.get("sector_macd_status") or "板块60分趋势确认"),
    setup_tier_reason,
]
if leader_score:
    reason_parts.append("板块龙头加分")
```

- [ ] **Step 5: Implement cautious morning conversion**

At the start of `_morning_confirmation`, derive:

```python
relaxed = setup.get("setup_tier") == "relaxed"
observation_status = "宽松观察" if relaxed else "明日观察"
entry_plan = (
    "仅在9:35–10:00早盘条件全部成立时轻仓考虑；"
    "不可追高，尾盘条件未完全确认"
    if relaxed
    else "仅在9:35–10:00条件全部成立时考虑跟进"
)
```

Use `observation_status` and `entry_plan` in `base`. In the existing
all-confirmed return branch, use:

```python
"follow_status": "谨慎跟进" if relaxed else "可以跟进",
"follow_reason": (
    "早盘承接确认，但前日尾盘条件未全部达标"
    if relaxed
    else "开盘幅度适中且承接确认"
),
```

Do not change any abandon or waiting thresholds.

- [ ] **Step 6: Run all service tests and verify GREEN**

Run:

```bash
env HOME=/tmp python3 -m unittest tests.test_morning_follow_service -v
```

Expected: all tests pass.

---

### Task 2: Sorting and outer-panel remarks

**Files:**
- Modify: `morning_follow_service.py:683-707`
- Modify: `tests/test_morning_follow_service.py`
- Modify: `quantClient/morning-follow-utils.js`
- Modify: `quantClient/morning-follow-utils.test.js`
- Modify: `quantClient/main.js:719-728`
- Modify: `quantClient/index.html:368-379`

**Interfaces:**
- Produces: `_follow_sort_key(row: dict[str, Any]) -> tuple[int, float, int, float, float]`.
- Produces: JavaScript `morningFollowRemarkText(row) -> string`.
- Extends: JavaScript `morningFollowBadgeState(status)` for “谨慎跟进” and “宽松观察”.

- [ ] **Step 1: Write a failing Python sorting test**

Import `_follow_sort_key` from `morning_follow_service` and add:

```python
def test_follow_sort_orders_cautious_after_strict_and_before_observation(self):
    rows = [
        {
            "ts_code": "600103.SH",
            "follow_status": "宽松观察",
            "follow_setup_score": 90,
            "tail_condition_pass_count": 2,
            "tail_close_position": 0.9,
            "amount": 500_000,
        },
        {
            "ts_code": "600102.SH",
            "follow_status": "谨慎跟进",
            "follow_setup_score": 70,
            "tail_condition_pass_count": 1,
            "tail_close_position": 0.6,
            "amount": 300_000,
        },
        {
            "ts_code": "600101.SH",
            "follow_status": "可以跟进",
            "follow_setup_score": 80,
            "tail_condition_pass_count": 3,
            "tail_close_position": 0.8,
            "amount": 400_000,
        },
    ]

    ordered = sorted(rows, key=_follow_sort_key)

    self.assertEqual(
        [row["ts_code"] for row in ordered],
        ["600101.SH", "600102.SH", "600103.SH"],
    )
```

- [ ] **Step 2: Write failing frontend utility tests**

Extend `quantClient/morning-follow-utils.test.js`:

```javascript
const {
  morningFollowBadgeState,
  morningFollowRemarkText,
} = require('./morning-follow-utils.js');

assert.equal(morningFollowBadgeState('谨慎跟进'), 'watch');
assert.equal(morningFollowBadgeState('宽松观察'), 'watch');
assert.equal(
  morningFollowRemarkText({
    setup_tier_label: '宽松观察',
    tail_condition_notes: [
      '尾盘涨幅0.11%，低于0.15%',
      '尾盘量能0.85倍，低于1.20倍',
    ],
  }),
  '宽松观察 · 尾盘涨幅0.11%，低于0.15%；尾盘量能0.85倍，低于1.20倍',
);
assert.equal(
  morningFollowRemarkText({
    setup_tier_label: '严格候选',
    tail_condition_notes: [],
    setup_tier_reason: '尾盘三项及观察分均达标',
  }),
  '严格候选 · 尾盘三项及观察分均达标',
);
```

- [ ] **Step 3: Run both focused tests and verify RED**

Run:

```bash
env HOME=/tmp python3 -m unittest \
  tests.test_morning_follow_service.MorningFollowServiceTests.test_follow_sort_orders_cautious_after_strict_and_before_observation \
  -v
node quantClient/morning-follow-utils.test.js
```

Expected: Python import failure for `_follow_sort_key` and Node failure because
`morningFollowRemarkText` is absent.

- [ ] **Step 4: Implement the Python sort key**

Add:

```python
def _follow_sort_key(row: dict[str, Any]) -> tuple[int, float, int, float, float]:
    status_order = {
        "可以跟进": 0,
        "谨慎跟进": 1,
        "等待确认": 2,
        "等待9:35确认": 3,
        "明日观察": 4,
        "宽松观察": 5,
        "数据未就绪": 6,
        "放弃": 7,
    }
    return (
        status_order.get(str(row.get("follow_status")), 9),
        -float(row.get("follow_setup_score") or 0),
        -int(row.get("tail_condition_pass_count") or 0),
        -float(row.get("tail_close_position") or 0),
        -float(row.get("amount") or 0),
    )
```

Replace the local `status_order` and sort lambda in
`build_morning_follow_monitor` with:

```python
setups.sort(key=_follow_sort_key)
```

- [ ] **Step 5: Implement frontend badge and remark helpers**

In `quantClient/morning-follow-utils.js`, add:

```javascript
function morningFollowRemarkText(row) {
  if (!row) return '--';
  const parts = [];
  if (row.setup_tier_label) parts.push(row.setup_tier_label);
  const notes = Array.isArray(row.tail_condition_notes)
    ? row.tail_condition_notes.filter(Boolean)
    : [];
  if (notes.length) parts.push(notes.join('；'));
  else if (row.setup_tier_reason) parts.push(row.setup_tier_reason);
  return parts.length ? parts.join(' · ') : '--';
}
```

Make `morningFollowBadgeState` explicitly return `watch` for “谨慎跟进” and
“宽松观察”. Export and expose both helpers:

```javascript
root.morningFollowRemarkText = api.morningFollowRemarkText;
return { morningFollowBadgeState, morningFollowRemarkText };
```

In `quantClient/main.js`, add:

```javascript
morningFollowRemark(row) {
  return morningFollowRemarkText(row);
},
```

Keep “谨慎跟进” neutral at row level: do not add it to `monitor-strong`.
Keep “放弃/数据未就绪” risk behavior unchanged.

- [ ] **Step 6: Render tier and notes only in the outer table**

In the final cell of the section guarded by
`activeTab === 'overnight_monitor'`, prepend:

```html
<span class="monitor-badge" :class="morningFollowBadgeClass(row.follow_status)">
  {{ row.setup_tier_label || '观察候选' }}
</span>
<span>{{ morningFollowRemark(row) }}</span>
<br>
```

Then keep the existing `follow_reason`, `morning_entry_plan`, and
`t1_exit_plan` text. Change the column header to:

```html
<th>备注 / 跟进条件 / T+1计划</th>
```

Do not edit the section guarded by `activeTab === 'realtime_info'`.

- [ ] **Step 7: Run service and frontend tests**

Run:

```bash
env HOME=/tmp python3 -m unittest tests.test_morning_follow_service -v
node --check quantClient/main.js
for test_file in quantClient/*.test.js; do node "$test_file"; done
```

Expected: all commands exit 0.

---

### Task 3: Real-data funnel and isolation regression

**Files:**
- Verify: `morning_follow_service.py`
- Verify: `quantClient/index.html`
- Verify only: `realtime_info_service.py`

**Interfaces:**
- Consumes: `build_morning_follow_monitor(limit=10, max_fetch=30, now=...)`.
- Verifies: relaxed setup fields and unchanged realtime/legacy overnight paths.

- [ ] **Step 1: Retrieve the preflight isolation checksums**

Confirm the execution notes contain the `realtime_info_service.py` checksum and
the extracted `realtime_info` HTML-block checksum recorded before Task 1. If
either is missing, stop and do not claim byte-level isolation.

- [ ] **Step 2: Run related Python regression**

Run:

```bash
env HOME=/tmp python3 -m unittest \
  tests.test_morning_follow_service \
  tests.test_morning_follow_api \
  tests.test_overnight_monitor_service \
  tests.test_overnight_monitor_api \
  tests.test_realtime_info_service \
  tests.test_realtime_info_api \
  -v
```

Expected: all tests pass. Do not use `unittest discover` as the sole completion
gate because the pre-existing `tests.test_stock_detail_api` module independently
hangs in this workspace.

- [ ] **Step 3: Run Java and frontend regression**

Run:

```bash
cd quantServer/quantServer && mvn -q test
cd /mnt/d/piao
node --check quantClient/main.js
for test_file in quantClient/*.test.js; do node "$test_file"; done
```

Expected: all commands exit 0.

- [ ] **Step 4: Run the 2026-07-29 real-data funnel**

Run:

```bash
env HOME=/tmp python3 -u -c "
from datetime import datetime
from morning_follow_service import build_morning_follow_monitor

result = build_morning_follow_monitor(
    limit=30,
    max_fetch=30,
    now=datetime(2026, 7, 29, 15, 10),
)
stocks = result.get('stocks') or []
print('daily_rows', result.get('daily_rows'))
print('daily_hard_filter_count', result.get('daily_hard_filter_count'))
print('setup_qualified_count', result.get('setup_qualified_count'))
print('strict', sum(row.get('setup_tier') == 'strict' for row in stocks))
print('relaxed', sum(row.get('setup_tier') == 'relaxed' for row in stocks))
print([
    (
        row.get('ts_code'),
        row.get('setup_tier_label'),
        row.get('tail_condition_pass_count'),
        row.get('tail_condition_notes'),
        row.get('follow_status'),
    )
    for row in stocks
])
"
```

Expected for the current cached snapshot: the previously diagnosed 11 stocks
that pass the stock 60-minute model and non-downward sector model are returned as
strict or relaxed rows. If market data has changed, verify instead that every
returned relaxed row passed the hard gates and has an explanatory note; do not
widen the daily, stock-model, or sector-model gates.

- [ ] **Step 5: Prove realtime and legacy overnight isolation**

Run:

```bash
sha256sum realtime_info_service.py
sed -n "/activeTab === 'realtime_info'/,/activeTab === 'reports'/p" \
  quantClient/index.html | sha256sum
rg -n "build_overnight_monitor|/overnight-monitor" \
  app.py overnight_monitor_service.py \
  quantServer/quantServer/src/main/java/com/codec/quantserver/controller/QuantController.java \
  quantServer/quantServer/src/main/java/com/codec/quantserver/service/QuantPythonClient.java
```

Expected: both checksums match Step 1, and the legacy endpoint/service references
remain present and unchanged by this task.

- [ ] **Step 6: Final source validation**

Run:

```bash
python3 -m py_compile morning_follow_service.py tests/test_morning_follow_service.py
git diff --check -- \
  quantClient/main.js \
  quantClient/index.html
if rg -n '[[:blank:]]+$' \
  morning_follow_service.py \
  tests/test_morning_follow_service.py \
  quantClient/morning-follow-utils.js \
  quantClient/morning-follow-utils.test.js; then
  exit 1
fi
git status --short
```

Expected: no syntax or source whitespace errors. Implementation files remain
unstaged and uncommitted.
