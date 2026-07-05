# Breakout Confluence Top Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade “趋势突破 Top” so backend ranking and frontend display emphasize Bollinger expansion, weekly up/sideways trend, KDJ golden-cross setup, and MACD zero-axis/cross readiness.

**Architecture:** Add a focused breakout-confluence helper in `strategy.py`, merge its per-stock fields into the existing breakout pool, and keep existing strategy boundaries intact. Increase scan history to 180 days only when the strong base pool is non-empty, then expose the new fields through existing JSON cleaning and render them in the existing Vue table.

**Tech Stack:** Python, pandas, unittest, Vue 3 CDN app, plain CSS/HTML.

---

## File Structure

- Modify `strategy.py`: add `_build_breakout_confluence_stats`, scoring helpers, breakout reason labels, output columns, and sorting/filtering changes inside `pick_breakout_stocks`.
- Modify `quant_service.py`: change trend scan history window from 100 to 180 days.
- Modify `tests/test_advantage_stock_scoring.py`: add deterministic fixtures and unit tests for confluence acceptance/rejection and returned fields.
- Modify `quantClient/main.js`: add display helpers and breakout-only confluence columns.
- Modify `quantClient/styles.css`: add compact nowrap styling for the new confluence score cell.

## Task 1: Add Breakout Confluence Unit Tests

**Files:**
- Modify: `tests/test_advantage_stock_scoring.py`

- [ ] **Step 1: Add a confluence history fixture near `build_daily_breakout_history`**

Add this helper after `build_daily_breakout_history`:

```python
def build_breakout_confluence_history(
    ts_code="600001.SH",
    weekly_down=False,
    flat_boll=False,
    macd_deep_below_zero=False,
):
    rows = []
    close = 9.0
    dates = pd.date_range("2026-01-01", periods=180, freq="B").strftime("%Y%m%d")
    for index, trade_date in enumerate(dates):
        if weekly_down:
            close = 20.0 - index * 0.045
        elif macd_deep_below_zero:
            close = 13.0 - index * 0.018
        elif index < 130:
            close = 9.0 + index * 0.012
        elif index < 165:
            close = 10.6 + (index - 130) * 0.01
        else:
            close = 10.95 + (index - 165) * 0.08

        if flat_boll and index >= 150:
            close = 11.0 + ((index % 2) * 0.02)

        is_latest = index == 179
        high = close + (0.18 if index >= 165 else 0.08)
        low = close - (0.12 if index >= 165 else 0.08)
        vol = 230.0 if is_latest else 95.0 if index >= 165 else 80.0
        pct_chg = 8.0 if is_latest else 1.2 if index >= 165 else 0.2
        rows.append({
            "ts_code": ts_code,
            "trade_date": trade_date,
            "pct_chg": pct_chg,
            "close": round(close, 4),
            "high": round(high, 4),
            "low": round(low, 4),
            "vol": vol,
        })

    history = pd.DataFrame(rows)
    history.loc[history.index[-21:-1], "high"] = history["close"].iloc[-21:-1].max() - 0.05
    history.loc[history.index[-1], ["close", "high", "low", "pct_chg", "vol"]] = [13.2, 13.35, 12.2, 8.0, 230.0]
    if flat_boll:
        history.loc[history.index[-20:], ["close", "high", "low"]] = [11.0, 11.05, 10.95]
        history.loc[history.index[-1], ["close", "high", "low", "pct_chg", "vol"]] = [11.02, 11.05, 10.95, 3.0, 230.0]
    if weekly_down:
        history.loc[history.index[-1], ["close", "high", "low", "pct_chg", "vol"]] = [12.0, 12.2, 11.5, 6.0, 230.0]
    if macd_deep_below_zero:
        history.loc[history.index[-1], ["close", "high", "low", "pct_chg", "vol"]] = [9.7, 9.85, 9.4, 4.0, 230.0]
    return history
```

- [ ] **Step 2: Add acceptance and field tests**

Add this test near existing breakout tests:

```python
def test_breakout_pool_accepts_boll_weekly_kdj_macd_confluence(self):
    market = build_daily_breakout_market()
    market.loc[market["ts_code"] == "600001.SH", ["close", "high", "low", "pct_chg", "vol"]] = [13.2, 13.35, 12.2, 8.0, 230.0]
    history = build_breakout_confluence_history()

    result = pick_breakout_stocks(market, history)

    self.assertFalse(result.empty)
    row = result.iloc[0]
    self.assertTrue(bool(row["boll_width_expand"]))
    self.assertTrue(bool(row["weekly_trend_ok"]))
    self.assertIn(row["weekly_trend_state"], {"上升", "横盘"})
    self.assertTrue(bool(row["macd_zero_axis_ready"]))
    self.assertGreaterEqual(row["breakout_confluence_score"], 10)
    self.assertGreaterEqual(row["breakout_confluence_count"], 3)
    self.assertIn("布林开口", row["breakout_reason"])
```

- [ ] **Step 3: Add rejection tests for hard gates**

Add these tests below the acceptance test:

```python
def test_breakout_pool_rejects_when_weekly_trend_is_down(self):
    market = build_daily_breakout_market()
    market.loc[market["ts_code"] == "600001.SH", ["close", "high", "low", "pct_chg", "vol"]] = [12.0, 12.2, 11.5, 6.0, 230.0]
    history = build_breakout_confluence_history(weekly_down=True)

    result = pick_breakout_stocks(market, history)

    self.assertTrue(result.empty)


def test_breakout_pool_rejects_when_bollinger_width_is_not_expanding(self):
    market = build_daily_breakout_market()
    market.loc[market["ts_code"] == "600001.SH", ["close", "high", "low", "pct_chg", "vol"]] = [11.02, 11.05, 10.95, 3.0, 230.0]
    history = build_breakout_confluence_history(flat_boll=True)

    result = pick_breakout_stocks(market, history)

    self.assertTrue(result.empty)


def test_breakout_pool_rejects_when_macd_is_far_below_zero_axis(self):
    market = build_daily_breakout_market()
    market.loc[market["ts_code"] == "600001.SH", ["close", "high", "low", "pct_chg", "vol"]] = [9.7, 9.85, 9.4, 4.0, 230.0]
    history = build_breakout_confluence_history(macd_deep_below_zero=True)

    result = pick_breakout_stocks(market, history)

    self.assertTrue(result.empty)
```

- [ ] **Step 4: Run the new tests and verify they fail**

Run:

```bash
python3 -m unittest tests.test_advantage_stock_scoring.TestAdvantageStockScoring.test_breakout_pool_accepts_boll_weekly_kdj_macd_confluence -v
```

Expected: fail with missing fields such as `boll_width_expand` or an empty result.

- [ ] **Step 5: Commit failing tests**

```bash
git add tests/test_advantage_stock_scoring.py
git commit -m "test: cover breakout confluence gates"
```

## Task 2: Compute Breakout Confluence Stats

**Files:**
- Modify: `strategy.py`
- Test: `tests/test_advantage_stock_scoring.py`

- [ ] **Step 1: Add `_build_breakout_confluence_stats` after `_build_strong_history_stats`**

Implement the helper with this structure:

```python
def _build_breakout_confluence_stats(hist_df: pd.DataFrame) -> pd.DataFrame:
    hist = hist_df.copy()
    numeric_cols = ["pct_chg", "vol", "close", "high", "low"]
    for col in numeric_cols:
        if col in hist.columns:
            hist[col] = pd.to_numeric(hist[col], errors="coerce")
    if "high" not in hist.columns:
        hist["high"] = hist["close"]
    if "low" not in hist.columns:
        hist["low"] = hist["close"]
    hist = hist.dropna(subset=["ts_code", "trade_date", "close", "high", "low"])
    hist = hist.sort_values(["ts_code", "trade_date"])

    rows = []
    for ts_code, group in hist.groupby("ts_code"):
        group = group.tail(180).copy()
        if len(group) < 61:
            continue
        close = group["close"]
        high = group["high"].fillna(close)
        low = group["low"].fillna(close)

        boll_middle = close.rolling(20, min_periods=20).mean()
        boll_std = close.rolling(20, min_periods=20).std(ddof=0)
        boll_upper = boll_middle + 2 * boll_std
        boll_lower = boll_middle - 2 * boll_std
        boll_width = (boll_upper - boll_lower) / boll_middle
        latest_boll_width = boll_width.iloc[-1] if len(boll_width) else pd.NA
        prior_boll_width = boll_width.iloc[-6] if len(boll_width) >= 6 else pd.NA
        recent_boll_width = boll_width.tail(3)
        boll_width_expand = bool(
            pd.notna(latest_boll_width) and
            pd.notna(prior_boll_width) and
            latest_boll_width > prior_boll_width and
            recent_boll_width.notna().sum() == 3 and
            recent_boll_width.iloc[-1] > recent_boll_width.iloc[0]
        )
        last_close = close.iloc[-1]
        latest_upper = boll_upper.iloc[-1] if len(boll_upper) else pd.NA
        latest_middle = boll_middle.iloc[-1] if len(boll_middle) else pd.NA
        close_near_boll_upper = bool(
            pd.notna(latest_upper) and
            pd.notna(latest_middle) and
            last_close > latest_middle and
            last_close >= latest_upper * 0.97
        )

        weekly = group.copy()
        weekly["week_index"] = pd.to_datetime(weekly["trade_date"], format="%Y%m%d", errors="coerce").dt.to_period("W")
        weekly_close = weekly.dropna(subset=["week_index"]).groupby("week_index")["close"].last()
        weekly_ma5 = weekly_close.rolling(5, min_periods=5).mean()
        weekly_ma10 = weekly_close.rolling(10, min_periods=10).mean()
        if len(weekly_close) < 12 or weekly_ma10.dropna().empty:
            weekly_trend_state = "数据不足"
            weekly_trend_ok = False
        else:
            latest_weekly_ma5 = weekly_ma5.iloc[-1]
            latest_weekly_ma10 = weekly_ma10.iloc[-1]
            weekly_ma10_base = weekly_ma10.iloc[-4] if len(weekly_ma10) >= 4 else pd.NA
            weekly_ma10_slope = (
                latest_weekly_ma10 / weekly_ma10_base - 1
                if pd.notna(latest_weekly_ma10) and pd.notna(weekly_ma10_base) and weekly_ma10_base
                else pd.NA
            )
            weekly_close_above_ma10 = bool(pd.notna(latest_weekly_ma10) and weekly_close.iloc[-1] >= latest_weekly_ma10 * 0.98)
            if pd.notna(latest_weekly_ma5) and pd.notna(latest_weekly_ma10) and latest_weekly_ma5 >= latest_weekly_ma10 and pd.notna(weekly_ma10_slope) and weekly_ma10_slope >= 0:
                weekly_trend_state = "上升"
            elif weekly_close_above_ma10 and pd.notna(weekly_ma10_slope) and weekly_ma10_slope >= -0.03:
                weekly_trend_state = "横盘"
            else:
                weekly_trend_state = "下降"
            weekly_trend_ok = weekly_trend_state in {"上升", "横盘"}

        low_min = low.rolling(9, min_periods=9).min()
        high_max = high.rolling(9, min_periods=9).max()
        rsv = (close - low_min) / (high_max - low_min) * 100
        rsv = rsv.replace([float("inf"), -float("inf")], pd.NA).fillna(50)
        kdj_k = rsv.ewm(com=2, adjust=False, min_periods=1).mean()
        kdj_d = kdj_k.ewm(com=2, adjust=False, min_periods=1).mean()
        kdj_golden_series = (kdj_k > kdj_d) & (kdj_k.shift(1) <= kdj_d.shift(1))
        kdj_golden_cross = bool(kdj_golden_series.iloc[-1])
        kdj_recent_golden_cross = bool(kdj_golden_series.tail(3).fillna(False).any() or (kdj_k.iloc[-1] > kdj_d.iloc[-1] and kdj_k.iloc[-1] >= kdj_k.iloc[-2] and kdj_d.iloc[-1] >= kdj_d.iloc[-2]))
        kdj_breakout_signal = bool(kdj_golden_cross or kdj_recent_golden_cross)

        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9, adjust=False).mean()
        macd_bar = (dif - dea) * 2
        macd_golden_series = (dif > dea) & (dif.shift(1) <= dea.shift(1))
        macd_gap = dif - dea
        normalized_zero_tolerance = max(last_close * 0.005, 0.03)
        macd_zero_axis_ready = bool(
            (dif.iloc[-1] >= 0 and dea.iloc[-1] >= 0) or
            (dif.iloc[-1] >= -normalized_zero_tolerance and dif.iloc[-1] > dif.iloc[-2])
        )
        macd_gap_contracting = bool(len(macd_gap) >= 3 and macd_gap.iloc[-1] < 0 and macd_gap.iloc[-1] > macd_gap.iloc[-2] > macd_gap.iloc[-3])
        macd_golden_cross = bool(macd_golden_series.iloc[-1])
        macd_recent_golden_cross = bool(macd_golden_series.tail(3).fillna(False).any())
        macd_cross_ready = bool(macd_golden_cross or macd_recent_golden_cross or macd_gap_contracting)

        confluence_fields = [
            boll_width_expand,
            close_near_boll_upper,
            weekly_trend_state == "上升",
            weekly_trend_state == "横盘",
            kdj_breakout_signal,
            macd_cross_ready,
        ]
        rows.append({
            "ts_code": ts_code,
            "boll_width": latest_boll_width,
            "boll_width_expand": boll_width_expand,
            "close_near_boll_upper": close_near_boll_upper,
            "boll_breakout_ready": bool(boll_width_expand and close_near_boll_upper),
            "weekly_trend_state": weekly_trend_state,
            "weekly_trend_ok": weekly_trend_ok,
            "kdj_recent_golden_cross": kdj_recent_golden_cross,
            "kdj_breakout_signal": kdj_breakout_signal,
            "macd_zero_axis_ready": macd_zero_axis_ready,
            "macd_cross_ready": macd_cross_ready,
            "breakout_confluence_count": int(sum(bool(value) for value in confluence_fields)),
            "breakout_confluence_score": (
                int(boll_width_expand) * 4 +
                int(close_near_boll_upper) * 3 +
                int(weekly_trend_state == "上升") * 4 +
                int(weekly_trend_state == "横盘") * 2 +
                int(kdj_breakout_signal) * 3 +
                int(macd_cross_ready) * 4
            ),
        })

    return pd.DataFrame(rows)
```

- [ ] **Step 2: Merge confluence stats in `pick_breakout_stocks`**

After `stats = _build_strong_history_stats(hist_df)`, add:

```python
    confluence_stats = _build_breakout_confluence_stats(hist_df)
```

After the existing `candidates = pd.merge(base, stats, on="ts_code", how="inner")`, add:

```python
    if not confluence_stats.empty:
        candidates = pd.merge(candidates, confluence_stats, on="ts_code", how="left")
```

Then fill defaults before scoring:

```python
    for column in (
        "boll_width_expand", "close_near_boll_upper", "boll_breakout_ready",
        "weekly_trend_ok", "kdj_recent_golden_cross", "kdj_breakout_signal",
        "macd_zero_axis_ready", "macd_cross_ready",
    ):
        if column not in candidates.columns:
            candidates[column] = False
        candidates[column] = candidates[column].fillna(False)
    if "weekly_trend_state" not in candidates.columns:
        candidates["weekly_trend_state"] = "数据不足"
    candidates["weekly_trend_state"] = candidates["weekly_trend_state"].fillna("数据不足")
    if "breakout_confluence_score" not in candidates.columns:
        candidates["breakout_confluence_score"] = 0
    if "breakout_confluence_count" not in candidates.columns:
        candidates["breakout_confluence_count"] = 0
    candidates["breakout_confluence_score"] = pd.to_numeric(candidates["breakout_confluence_score"], errors="coerce").fillna(0)
    candidates["breakout_confluence_count"] = pd.to_numeric(candidates["breakout_confluence_count"], errors="coerce").fillna(0)
```

- [ ] **Step 3: Run confluence stats test and verify progress**

Run:

```bash
python3 -m unittest tests.test_advantage_stock_scoring.TestAdvantageStockScoring.test_breakout_pool_accepts_boll_weekly_kdj_macd_confluence -v
```

Expected: still may fail on hard gates or score integration, but fields should exist.

- [ ] **Step 4: Commit stats implementation**

```bash
git add strategy.py tests/test_advantage_stock_scoring.py
git commit -m "feat: compute breakout confluence stats"
```

## Task 3: Apply Confluence Gates, Score, Reasons, and Output Columns

**Files:**
- Modify: `strategy.py`
- Test: `tests/test_advantage_stock_scoring.py`

- [ ] **Step 1: Add confluence hard gates to breakout result filter**

Replace the existing `result = candidates[...]` filter in `pick_breakout_stocks` with:

```python
    result = candidates[
        candidates["trend_upward"] &
        candidates["weekly_trend_ok"] &
        candidates["boll_width_expand"] &
        candidates["macd_zero_axis_ready"] &
        ~candidates["risk_reject"] &
        (candidates["overnight_premium_score"] >= candidates["breakout_entry_threshold"])
    ].copy()
```

- [ ] **Step 2: Add confluence score into final breakout score**

Replace:

```python
    result["breakout_score"] = result["overnight_premium_score"]
    result["score"] = result["overnight_premium_score"]
```

with:

```python
    result["breakout_score"] = (
        result["overnight_premium_score"] + result["breakout_confluence_score"]
    ).clip(lower=0, upper=100)
    result["score"] = result["breakout_score"]
```

- [ ] **Step 3: Extend `_build_breakout_reason` labels**

Add these labels before the existing `daily_tail_strength` label:

```python
        ("boll_width_expand", "布林开口"),
        ("close_near_boll_upper", "贴近上轨"),
        ("kdj_breakout_signal", "KDJ金叉"),
        ("macd_cross_ready", "MACD将金叉"),
```

Then append weekly labels before building `reasons`:

```python
    weekly_state = row.get("weekly_trend_state")
    weekly_reason = "周线上升" if weekly_state == "上升" else "周线横盘" if weekly_state == "横盘" else None
```

And change the `reasons` line to:

```python
    reasons = ([weekly_reason] if weekly_reason else []) + [label for field, label in labels if row.get(field)]
```

- [ ] **Step 4: Update breakout sorting**

Replace the final `return result.sort_values(...)` in `pick_breakout_stocks` with:

```python
    return result.sort_values(
        [
            "breakout_confluence_score",
            "breakout_confluence_count",
            "breakout_score",
            "sector_rank",
            "relative_strength_rank",
            "amount_yuan",
            "volume_expand_rate",
        ],
        ascending=[False, False, False, True, True, False, False],
    )
```

- [ ] **Step 5: Add new fields to `format_for_ai`**

In `wanted_cols`, add the following fields near existing breakout fields:

```python
        "boll_width", "boll_width_expand", "close_near_boll_upper", "boll_breakout_ready",
        "weekly_trend_state", "weekly_trend_ok", "kdj_recent_golden_cross",
        "kdj_breakout_signal", "macd_zero_axis_ready", "macd_cross_ready",
        "breakout_confluence_count", "breakout_confluence_score",
```

- [ ] **Step 6: Run breakout confluence tests**

Run:

```bash
python3 -m unittest tests.test_advantage_stock_scoring.TestAdvantageStockScoring.test_breakout_pool_accepts_boll_weekly_kdj_macd_confluence tests.test_advantage_stock_scoring.TestAdvantageStockScoring.test_breakout_pool_rejects_when_weekly_trend_is_down tests.test_advantage_stock_scoring.TestAdvantageStockScoring.test_breakout_pool_rejects_when_bollinger_width_is_not_expanding tests.test_advantage_stock_scoring.TestAdvantageStockScoring.test_breakout_pool_rejects_when_macd_is_far_below_zero_axis -v
```

Expected: all four tests pass.

- [ ] **Step 7: Commit gate and scoring changes**

```bash
git add strategy.py tests/test_advantage_stock_scoring.py
git commit -m "feat: rank breakout stocks by confluence"
```

## Task 4: Update Scan History Window

**Files:**
- Modify: `quant_service.py`
- Test: `tests/test_advantage_stock_scoring.py`

- [ ] **Step 1: Add a test for the 180-day history request**

In `tests/test_advantage_stock_scoring.py`, update the existing `run_quant_scan` mock test or add:

```python
@patch.object(quant_service, "get_sector_data", return_value=(pd.DataFrame(), pd.DataFrame()))
@patch.object(quant_service, "get_moneyflow_summary", return_value={
    "trade_date": "20260705",
    "source": "test",
    "total_net_amount": 0,
    "inflow_count": 0,
    "outflow_count": 0,
    "top_inflow": [],
    "top_outflow": [],
})
@patch.object(quant_service, "get_recent_daily_data", return_value=pd.DataFrame())
@patch.object(quant_service, "get_market_data", return_value=(build_daily_breakout_market(), "20260705"))
def test_run_quant_scan_requests_180_days_for_breakout_confluence(self, _market, recent_daily, _moneyflow, _sector):
    quant_service.run_quant_scan(include_ai=False, limit=20)

    self.assertEqual(recent_daily.call_args.kwargs["n"], 180)
```

- [ ] **Step 2: Run the new test and verify it fails**

Run:

```bash
python3 -m unittest tests.test_advantage_stock_scoring.TestAdvantageStockScoring.test_run_quant_scan_requests_180_days_for_breakout_confluence -v
```

Expected: fail because `n` is currently 100.

- [ ] **Step 3: Change `quant_service.run_quant_scan` history window**

Replace:

```python
        hist_days = 100 if not strong_base.empty else 40
```

with:

```python
        hist_days = 180 if not strong_base.empty else 40
```

- [ ] **Step 4: Run the scan history test**

Run:

```bash
python3 -m unittest tests.test_advantage_stock_scoring.TestAdvantageStockScoring.test_run_quant_scan_requests_180_days_for_breakout_confluence -v
```

Expected: pass.

- [ ] **Step 5: Commit history-window change**

```bash
git add quant_service.py tests/test_advantage_stock_scoring.py
git commit -m "feat: load longer history for breakout confluence"
```

## Task 5: Render Breakout Confluence Fields in the Frontend

**Files:**
- Modify: `quantClient/main.js`
- Modify: `quantClient/styles.css`

- [ ] **Step 1: Add display helpers to `StockTable.methods`**

Change the `methods` object to include:

```javascript
methods: {
  formatNumber,
  signedClass,
  displayMetric,
  displayPercent,
  confluenceText(row) {
    const count = row.breakout_confluence_count ?? '--';
    const score = row.breakout_confluence_score ?? '--';
    return `${count}项 / ${score}分`;
  },
  bollText(row) {
    const labels = [];
    if (row.boll_width_expand) labels.push('开口');
    if (row.close_near_boll_upper) labels.push('贴上轨');
    return labels.length ? labels.join('、') : '--';
  },
  kdjText(row) {
    return row.kdj_breakout_signal ? '金叉' : '--';
  },
  macdText(row) {
    const labels = [];
    if (row.macd_zero_axis_ready) labels.push('零轴');
    if (row.macd_cross_ready) labels.push('金叉');
    return labels.length ? labels.join('、') : '--';
  },
},
```

- [ ] **Step 2: Add breakout-only table headers**

Inside the `template v-else` header block, insert after `状态`:

```html
              <th v-if="mode === 'breakout'">共振</th>
              <th v-if="mode === 'breakout'">布林</th>
              <th v-if="mode === 'breakout'">周线</th>
              <th v-if="mode === 'breakout'">KDJ</th>
              <th v-if="mode === 'breakout'">MACD</th>
```

- [ ] **Step 3: Add breakout-only table cells**

Inside the `template v-else` body block, insert after the `状态` cell:

```html
              <td v-if="mode === 'breakout'"><strong>{{ confluenceText(row) }}</strong></td>
              <td v-if="mode === 'breakout'">{{ bollText(row) }}</td>
              <td v-if="mode === 'breakout'">{{ row.weekly_trend_state || '--' }}</td>
              <td v-if="mode === 'breakout'">{{ kdjText(row) }}</td>
              <td v-if="mode === 'breakout'">{{ macdText(row) }}</td>
```

- [ ] **Step 4: Fix empty colspan for breakout mode**

Change the empty row colspan expression from:

```html
            <td :colspan="mode === 'reversal' ? 17 : (mode === 'breakout' ? 17 : 14)" class="empty">暂无数据</td>
```

to:

```html
            <td :colspan="mode === 'reversal' ? 17 : (mode === 'breakout' ? 22 : 14)" class="empty">暂无数据</td>
```

- [ ] **Step 5: Add compact nowrap style**

Add to `quantClient/styles.css`:

```css
.table-wrap td strong {
  white-space: nowrap;
}
```

- [ ] **Step 6: Commit frontend rendering**

```bash
git add quantClient/main.js quantClient/styles.css
git commit -m "feat: show breakout confluence columns"
```

## Task 6: Full Verification

**Files:**
- Verify only.

- [ ] **Step 1: Run targeted Python tests**

Run:

```bash
python3 -m unittest tests.test_advantage_stock_scoring -v
```

Expected: all tests pass.

- [ ] **Step 2: Run stock detail regression tests**

Run:

```bash
python3 -m unittest tests.test_stock_detail_service -v
```

Expected: all tests pass.

- [ ] **Step 3: Inspect changed files**

Run:

```bash
git diff -- strategy.py quant_service.py tests/test_advantage_stock_scoring.py quantClient/main.js quantClient/styles.css
```

Expected: diff only contains breakout confluence stats, history-window change, tests, and UI display changes.

- [ ] **Step 4: Commit final verification note if any fixes were needed**

If verification required fixes, commit them:

```bash
git add strategy.py quant_service.py tests/test_advantage_stock_scoring.py quantClient/main.js quantClient/styles.css
git commit -m "fix: stabilize breakout confluence verification"
```

If no fixes were needed, do not create an empty commit.

## Self-Review

- Spec coverage: backend indicators, hard gates, scoring, sort order, history window, frontend columns, old-field fallback, and tests are covered by Tasks 1-6.
- Placeholder scan: no placeholder steps remain; each task has concrete files, snippets, commands, and expected outcomes.
- Type consistency: field names match the spec and are reused consistently: `boll_width_expand`, `close_near_boll_upper`, `weekly_trend_state`, `weekly_trend_ok`, `kdj_breakout_signal`, `macd_zero_axis_ready`, `macd_cross_ready`, `breakout_confluence_count`, and `breakout_confluence_score`.
