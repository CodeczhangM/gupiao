# Realtime Market-Relative Resonance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Modify “实时信息” intraday realtime resonance so it prefers stocks that outperform a rising mainboard market and remain resilient when the mainboard market falls.

**Architecture:** Keep the feature inside `realtime_info_service.py` because realtime resonance is already implemented there. Add focused helpers that compute a mainboard equal-weight market benchmark, attach per-stock relative-strength fields, apply a market-state-aware filter, and include a rule-version string in the intraday cache key. Preserve the existing sector-potential, 60-minute signal, tail-minute refresh, and main-force flows.

**Tech Stack:** Python 3, pandas, unittest, existing realtime market snapshot and minute-loader utilities.

## Global Constraints

- “大盘” uses realtime market snapshot mainboard A-share equal-weight average `pct_chg`.
- Exclude ChiNext, STAR Market, Beijing exchange, and ST names from the benchmark.
- Do not add a new realtime index data source.
- Replace the fixed intraday prefilter `pct_chg >= 0.2` with market-state-aware relative-strength rules when the benchmark is available.
- If benchmark calculation fails, fall back to the old fixed `pct_chg >= 0.2` behavior.
- Keep existing turnover, volume-ratio, sector-potential, 60-minute, tail-minute, and main-force flows.
- New fields are realtime output fields only and must not write back to market cache.
- Include a rule version string in the intraday result cache key.

---

## File Structure

- Modify: `realtime_info_service.py`
  - Add constants for the market-relative resonance rule version and thresholds.
  - Add helper functions for benchmark calculation, label/reason generation, scoring, and filtering.
  - Enrich `realtime_market` before sector ranking and minute loading.
  - Update intraday candidate filtering and final sorting.
  - Include the rule version in the intraday in-memory cache key.
- Modify: `tests/test_realtime_info_service.py`
  - Import new helper functions.
  - Add unit tests for up, flat, down, and fallback market states.
  - Add an integration-style realtime section test that verifies output fields and sorting.

---

### Task 1: Benchmark And Per-Stock Relative Fields

**Files:**
- Modify: `realtime_info_service.py`
- Test: `tests/test_realtime_info_service.py`

**Interfaces:**
- Consumes: existing `_is_mainboard_a_stock(codes: pd.Series) -> pd.Series`.
- Produces: `_build_market_relative_benchmark(market: pd.DataFrame) -> dict[str, Any]`
- Produces: `_attach_market_relative_fields(market: pd.DataFrame, benchmark: dict[str, Any] | None = None) -> tuple[pd.DataFrame, dict[str, Any]]`

- [ ] **Step 1: Write failing tests**

Add imports in `tests/test_realtime_info_service.py`:

```python
from realtime_info_service import (
    MinuteLoadResult,
    build_realtime_info,
    _REALTIME_INTRADAY_RESULT_CACHE,
    _attach_market_relative_fields,
    _build_market_relative_benchmark,
    _enrich_rows_with_market,
    _fill_missing_realtime_volume_ratio,
    _market_price_map,
    _apply_minute_snapshots_to_market,
    _load_realtime_market_inputs,
    _load_realtime_intraday_signal_bars,
    _minute_price_snapshot,
    _minute_result_with_1459_fallback,
    _snapshot_supports_realtime_filters,
    _trading_session_progress,
)
```

Add tests near the existing realtime volume-ratio helper tests:

```python
    def test_market_relative_benchmark_uses_mainboard_non_st_equal_weight(self):
        market = pd.DataFrame([
            {"ts_code": "600001.SH", "name": "主板一", "pct_chg": 1.0},
            {"ts_code": "000001.SZ", "name": "主板二", "pct_chg": 3.0},
            {"ts_code": "300001.SZ", "name": "创业板", "pct_chg": 20.0},
            {"ts_code": "688001.SH", "name": "科创板", "pct_chg": 15.0},
            {"ts_code": "920001.BJ", "name": "北交所", "pct_chg": 10.0},
            {"ts_code": "600002.SH", "name": "ST风险", "pct_chg": -9.0},
        ])

        benchmark = _build_market_relative_benchmark(market)

        self.assertAlmostEqual(benchmark["market_pct_chg"], 2.0)
        self.assertEqual(benchmark["market_state"], "up")
        self.assertEqual(benchmark["market_state_label"], "大盘上涨")
        self.assertEqual(benchmark["sample_count"], 2)

    def test_attach_market_relative_fields_adds_strength_label_reason_and_score(self):
        market = pd.DataFrame([
            {
                "ts_code": "600001.SH",
                "name": "强势股",
                "pct_chg": 3.2,
                "turnover_rate": 5.0,
                "volume_ratio": 2.0,
            },
            {
                "ts_code": "600002.SH",
                "name": "普通股",
                "pct_chg": 1.2,
                "turnover_rate": 1.0,
                "volume_ratio": 1.0,
            },
        ])
        benchmark = {
            "market_pct_chg": 1.0,
            "market_state": "up",
            "market_state_label": "大盘上涨",
            "sample_count": 2000,
        }

        result, returned = _attach_market_relative_fields(market, benchmark)

        self.assertEqual(returned, benchmark)
        strong = result[result["ts_code"] == "600001.SH"].iloc[0]
        self.assertAlmostEqual(strong["market_pct_chg"], 1.0)
        self.assertAlmostEqual(strong["relative_strength"], 2.2)
        self.assertEqual(strong["market_resonance_label"], "强于大盘")
        self.assertIn("大盘 1.00%", strong["market_resonance_reason"])
        self.assertIn("个股 3.20%", strong["market_resonance_reason"])
        self.assertGreater(strong["realtime_relative_strength_score"], 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
env HOME=/tmp python3 -m unittest -v \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_market_relative_benchmark_uses_mainboard_non_st_equal_weight \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_attach_market_relative_fields_adds_strength_label_reason_and_score
```

Expected: import failure for `_build_market_relative_benchmark` and `_attach_market_relative_fields`.

- [ ] **Step 3: Implement minimal helpers**

Add constants near the existing realtime constants in `realtime_info_service.py`:

```python
_REALTIME_MARKET_RELATIVE_RULE_VERSION = "market-relative-v1"
_MARKET_RELATIVE_UP_THRESHOLD = 0.3
_MARKET_RELATIVE_DOWN_THRESHOLD = -0.3
```

Add helpers after `_overnight_pct_allowed()`:

```python
def _build_market_relative_benchmark(market: pd.DataFrame) -> dict[str, Any]:
    if market is None or market.empty or "ts_code" not in market.columns:
        return {
            "market_pct_chg": None,
            "market_state": "fallback",
            "market_state_label": "大盘不可用",
            "sample_count": 0,
        }
    data = market.copy()
    data["ts_code"] = data["ts_code"].astype(str)
    allowed = _is_mainboard_a_stock(data["ts_code"]) & data["ts_code"].apply(
        _realtime_output_allowed
    )
    if "name" in data.columns:
        allowed = allowed & ~data["name"].astype(str).str.upper().str.contains("ST")
    data = data[allowed].copy()
    if "pct_chg" not in data.columns:
        return {
            "market_pct_chg": None,
            "market_state": "fallback",
            "market_state_label": "大盘不可用",
            "sample_count": 0,
        }
    data["pct_chg"] = pd.to_numeric(data["pct_chg"], errors="coerce")
    pct = data["pct_chg"].dropna()
    if pct.empty:
        return {
            "market_pct_chg": None,
            "market_state": "fallback",
            "market_state_label": "大盘不可用",
            "sample_count": 0,
        }
    market_pct = round(float(pct.mean()), 6)
    if market_pct >= _MARKET_RELATIVE_UP_THRESHOLD:
        state, label = "up", "大盘上涨"
    elif market_pct <= _MARKET_RELATIVE_DOWN_THRESHOLD:
        state, label = "down", "大盘下跌"
    else:
        state, label = "flat", "大盘震荡"
    return {
        "market_pct_chg": market_pct,
        "market_state": state,
        "market_state_label": label,
        "sample_count": int(len(pct)),
    }


def _market_relative_label(state: str) -> str:
    return {
        "up": "强于大盘",
        "flat": "震荡走强",
        "down": "逆势抗跌",
    }.get(str(state), "原规则")


def _market_relative_reason(stock_pct: Any, benchmark: dict[str, Any]) -> str:
    market_pct = benchmark.get("market_pct_chg")
    try:
        stock_value = float(stock_pct)
        market_value = float(market_pct)
    except (TypeError, ValueError):
        return "大盘基准不可用，沿用原实时涨幅规则"
    relative = stock_value - market_value
    return (
        f"大盘 {market_value:.2f}%，个股 {stock_value:.2f}%，"
        f"相对强 {relative:.2f}pct"
    )


def _market_relative_score(row: pd.Series) -> float:
    relative = pd.to_numeric(pd.Series([row.get("relative_strength")]), errors="coerce").iloc[0]
    stock_pct = pd.to_numeric(pd.Series([row.get("pct_chg")]), errors="coerce").iloc[0]
    volume_ratio = pd.to_numeric(pd.Series([row.get("volume_ratio")]), errors="coerce").iloc[0]
    turnover = pd.to_numeric(pd.Series([row.get("turnover_rate")]), errors="coerce").iloc[0]
    relative = 0.0 if pd.isna(relative) else float(relative)
    stock_pct = 0.0 if pd.isna(stock_pct) else float(stock_pct)
    volume_ratio = 0.0 if pd.isna(volume_ratio) else float(volume_ratio)
    turnover = 0.0 if pd.isna(turnover) else float(turnover)
    return round(
        relative * 40
        + max(stock_pct, 0) * 10
        + min(max(volume_ratio, 0), 4) * 8
        + (10 if 2 <= turnover <= 8 else 0),
        2,
    )


def _attach_market_relative_fields(
    market: pd.DataFrame,
    benchmark: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if market is None or market.empty:
        return market, benchmark or _build_market_relative_benchmark(market)
    benchmark = benchmark or _build_market_relative_benchmark(market)
    result = market.copy()
    market_pct = benchmark.get("market_pct_chg")
    result["market_pct_chg"] = market_pct
    result["market_resonance_state"] = benchmark.get("market_state")
    result["market_resonance_state_label"] = benchmark.get("market_state_label")
    result["market_resonance_label"] = _market_relative_label(
        str(benchmark.get("market_state"))
    )
    result["pct_chg"] = (
        pd.to_numeric(result["pct_chg"], errors="coerce")
        if "pct_chg" in result.columns
        else pd.NA
    )
    if market_pct is None:
        result["relative_strength"] = pd.NA
    else:
        result["relative_strength"] = result["pct_chg"] - float(market_pct)
    result["market_resonance_reason"] = result["pct_chg"].apply(
        lambda value: _market_relative_reason(value, benchmark)
    )
    result["realtime_relative_strength_score"] = result.apply(
        _market_relative_score,
        axis=1,
    )
    return result, benchmark
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
env HOME=/tmp python3 -m unittest -v \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_market_relative_benchmark_uses_mainboard_non_st_equal_weight \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_attach_market_relative_fields_adds_strength_label_reason_and_score
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add realtime_info_service.py tests/test_realtime_info_service.py
git commit -m "feat: add realtime market-relative benchmark"
```

---

### Task 2: Market-State-Aware Intraday Candidate Filter

**Files:**
- Modify: `realtime_info_service.py`
- Test: `tests/test_realtime_info_service.py`

**Interfaces:**
- Consumes: `_attach_market_relative_fields(market, benchmark=None) -> tuple[pd.DataFrame, dict[str, Any]]`
- Produces: `_market_relative_candidate_mask(candidates: pd.DataFrame, benchmark: dict[str, Any]) -> pd.Series`
- Modifies: `_load_realtime_intraday_signal_bars(market, sector_potential, trade_date, now, minute_loader=None) -> dict[str, dict[str, pd.DataFrame]]`

- [ ] **Step 1: Write failing tests**

Add `_market_relative_candidate_mask` to the realtime import list.

Add tests near `test_signal_minutes_accept_relaxed_positive_mainboard_candidate`:

```python
    def test_market_relative_filter_requires_more_than_market_when_market_up(self):
        candidates = pd.DataFrame([
            {"ts_code": "600001.SH", "pct_chg": 2.2},
            {"ts_code": "600002.SH", "pct_chg": 1.7},
        ])
        candidates, benchmark = _attach_market_relative_fields(
            candidates,
            {
                "market_pct_chg": 1.0,
                "market_state": "up",
                "market_state_label": "大盘上涨",
                "sample_count": 2000,
            },
        )

        mask = _market_relative_candidate_mask(candidates, benchmark)

        self.assertEqual(mask.tolist(), [True, False])

    def test_market_relative_filter_accepts_small_drop_when_market_down(self):
        candidates = pd.DataFrame([
            {"ts_code": "600001.SH", "pct_chg": -0.2},
            {"ts_code": "600002.SH", "pct_chg": -1.1},
            {"ts_code": "600003.SH", "pct_chg": 0.1},
        ])
        candidates, benchmark = _attach_market_relative_fields(
            candidates,
            {
                "market_pct_chg": -2.0,
                "market_state": "down",
                "market_state_label": "大盘下跌",
                "sample_count": 2000,
            },
        )

        mask = _market_relative_candidate_mask(candidates, benchmark)

        self.assertEqual(mask.tolist(), [True, False, True])

    def test_market_relative_filter_falls_back_to_old_positive_rule_without_benchmark(self):
        candidates = pd.DataFrame([
            {"ts_code": "600001.SH", "pct_chg": 0.2},
            {"ts_code": "600002.SH", "pct_chg": 0.19},
            {"ts_code": "600003.SH", "pct_chg": -0.2},
        ])
        candidates, benchmark = _attach_market_relative_fields(
            candidates,
            {
                "market_pct_chg": None,
                "market_state": "fallback",
                "market_state_label": "大盘不可用",
                "sample_count": 0,
            },
        )

        mask = _market_relative_candidate_mask(candidates, benchmark)

        self.assertEqual(mask.tolist(), [True, False, False])
```

Add an integration test:

```python
    def test_signal_minutes_use_market_relative_filter_for_down_market(self):
        requested_codes = []
        market = pd.DataFrame([
            {
                "ts_code": "600001.SH",
                "name": "抗跌股",
                "industry": "机器人",
                "turnover_rate": 3.0,
                "volume_ratio": 1.4,
                "amount": 800_000,
                "pct_chg": -0.2,
            },
            {
                "ts_code": "600002.SH",
                "name": "弱势股",
                "industry": "机器人",
                "turnover_rate": 3.0,
                "volume_ratio": 1.4,
                "amount": 790_000,
                "pct_chg": -1.3,
            },
            {
                "ts_code": "600003.SH",
                "name": "市场样本",
                "industry": "其他",
                "turnover_rate": 3.0,
                "volume_ratio": 1.4,
                "amount": 780_000,
                "pct_chg": -3.5,
            },
        ])
        sectors = pd.DataFrame([{"industry_name": "机器人"}])

        def minute_loader(ts_code, start, end, freq, trade_date):
            requested_codes.append(ts_code)
            return MinuteLoadResult(
                build_60min_bars(ts_code, water_macd_kdj_cross_closes()),
                "fixture",
                [],
            )

        result = _load_realtime_intraday_signal_bars(
            market,
            sectors,
            "20260729",
            datetime(2026, 7, 29, 14, 50),
            minute_loader=minute_loader,
        )

        self.assertEqual(requested_codes, ["600001.SH"])
        self.assertEqual(list(result), ["600001.SH"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
env HOME=/tmp python3 -m unittest -v \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_market_relative_filter_requires_more_than_market_when_market_up \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_market_relative_filter_accepts_small_drop_when_market_down \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_market_relative_filter_falls_back_to_old_positive_rule_without_benchmark \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_signal_minutes_use_market_relative_filter_for_down_market
```

Expected: import failure for `_market_relative_candidate_mask`.

- [ ] **Step 3: Implement candidate mask and use it in minute loading**

Add helper after `_attach_market_relative_fields()`:

```python
def _market_relative_candidate_mask(
    candidates: pd.DataFrame,
    benchmark: dict[str, Any],
) -> pd.Series:
    if candidates is None or candidates.empty:
        return pd.Series([], dtype=bool)
    pct = (
        pd.to_numeric(candidates["pct_chg"], errors="coerce")
        if "pct_chg" in candidates.columns
        else pd.Series(pd.NA, index=candidates.index)
    )
    market_pct = benchmark.get("market_pct_chg") if benchmark else None
    state = str((benchmark or {}).get("market_state") or "fallback")
    if market_pct is None or state == "fallback":
        return pct.ge(0.2).fillna(False)
    relative = (
        pd.to_numeric(candidates["relative_strength"], errors="coerce")
        if "relative_strength" in candidates.columns
        else pct - float(market_pct)
    )
    if state == "up":
        return (pct >= float(market_pct) + 1.0).fillna(False) & pct.ge(1.5).fillna(False)
    if state == "down":
        return relative.ge(1.5).fillna(False) & pct.ge(-0.5).fillna(False)
    return pct.ge(1.0).fillna(False) & relative.ge(1.0).fillna(False)
```

In `_load_realtime_intraday_signal_bars()`, compute the benchmark from the full incoming market snapshot before filtering to the sector candidates:

```python
    benchmark = _build_market_relative_benchmark(market)
```

After numeric conversion and before the existing candidate filter, attach fields to the sector candidates with that full-market benchmark:

```python
    candidates, benchmark = _attach_market_relative_fields(candidates, benchmark)
```

Replace:

```python
        & (candidates["pct_chg"] >= 0.2)
```

with:

```python
        & _market_relative_candidate_mask(candidates, benchmark)
```

Keep the existing turnover and volume-ratio checks unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
env HOME=/tmp python3 -m unittest -v \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_market_relative_filter_requires_more_than_market_when_market_up \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_market_relative_filter_accepts_small_drop_when_market_down \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_market_relative_filter_falls_back_to_old_positive_rule_without_benchmark \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_signal_minutes_use_market_relative_filter_for_down_market
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add realtime_info_service.py tests/test_realtime_info_service.py
git commit -m "feat: filter realtime candidates by market-relative strength"
```

---

### Task 3: Propagate Fields To Final Output, Sorting, And Cache Version

**Files:**
- Modify: `realtime_info_service.py`
- Test: `tests/test_realtime_info_service.py`

**Interfaces:**
- Consumes: `_attach_market_relative_fields(market, benchmark=None) -> tuple[pd.DataFrame, dict[str, Any]]`
- Consumes: `_REALTIME_MARKET_RELATIVE_RULE_VERSION: str`
- Modifies: `_build_realtime_intraday_section(...) -> dict[str, Any]`

- [ ] **Step 1: Write failing tests**

Add tests near `test_intraday_confluence_uses_today_market_when_sector_history_is_empty`:

```python
    @patch("realtime_info_service._load_tail_minute_bars_for_pick")
    def test_intraday_confluence_outputs_market_relative_fields(self, tail_loader):
        import realtime_info_service

        market = pd.DataFrame([
            {
                "trade_date": "20260731",
                "ts_code": "600301.SH",
                "name": "强势共振",
                "industry": "机器人",
                "close": 12.6,
                "high": 12.8,
                "low": 12.1,
                "pct_chg": 3.2,
                "turnover_rate": 4.8,
                "volume_ratio": 1.5,
                "amount": 180_000_000,
            },
            {
                "trade_date": "20260731",
                "ts_code": "600302.SH",
                "name": "市场样本",
                "industry": "其他",
                "close": 10.1,
                "high": 10.2,
                "low": 9.9,
                "pct_chg": -1.2,
                "turnover_rate": 3.0,
                "volume_ratio": 1.1,
                "amount": 120_000_000,
            },
        ])
        tail_loader.return_value = MinuteLoadResult(
            pd.DataFrame(), "not_available", []
        )

        def minute_loader(ts_code, start, end, freq, trade_date):
            return MinuteLoadResult(
                build_60min_bars(ts_code, water_macd_kdj_cross_closes()),
                "fixture",
                [],
            )

        result = realtime_info_service._build_realtime_intraday_section(
            market,
            pd.DataFrame(),
            "20260731",
            datetime(2026, 7, 31, 14, 20),
            limit=10,
            minute_loader=minute_loader,
            force_refresh=True,
        )

        row = result["stocks"][0]
        self.assertEqual(row["ts_code"], "600301.SH")
        self.assertIn("market_pct_chg", row)
        self.assertIn("relative_strength", row)
        self.assertIn("market_resonance_label", row)
        self.assertIn("market_resonance_reason", row)
        self.assertIn("realtime_relative_strength_score", row)
        self.assertIn(row["market_resonance_label"], {"强于大盘", "震荡走强", "逆势抗跌"})

    def test_intraday_cache_key_includes_market_relative_rule_version(self):
        import realtime_info_service

        market = pd.DataFrame()
        history = pd.DataFrame()

        with (
            patch("realtime_info_service.macd_parameter_key", return_value="macd-test"),
            patch("realtime_info_service._REALTIME_INTRADAY_RESULT_CACHE", {}) as cache,
        ):
            realtime_info_service._build_realtime_intraday_section(
                market,
                history,
                "20260731",
                datetime(2026, 7, 31, 14, 20),
                limit=10,
                force_refresh=True,
            )

        [cache_key] = list(cache.keys())
        self.assertIn("market-relative-v1", cache_key)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
env HOME=/tmp python3 -m unittest -v \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_intraday_confluence_outputs_market_relative_fields \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_intraday_cache_key_includes_market_relative_rule_version
```

Expected: output field assertion failure or cache-key assertion failure.

- [ ] **Step 3: Enrich realtime market before downstream processing**

In `_build_realtime_intraday_section()`, add `_REALTIME_MARKET_RELATIVE_RULE_VERSION` to `cache_key`:

```python
    cache_key = (
        str(trade_date),
        str(base_trade_date or trade_date),
        int(limit),
        _realtime_end_datetime(trade_date, now=now),
        macd_parameter_key(),
        _REALTIME_MARKET_RELATIVE_RULE_VERSION,
    )
```

After `_fill_missing_realtime_volume_ratio(...)`, enrich the whole realtime market:

```python
    realtime_market, market_relative_benchmark = _attach_market_relative_fields(
        realtime_market
    )
```

Update both calls to `_load_realtime_intraday_signal_bars()` to pass the enriched `realtime_market`. Do not pass `market_relative_benchmark` unless the function signature is explicitly changed; the enriched fields are enough for output propagation, and `_load_realtime_intraday_signal_bars()` can recompute the same benchmark from the same enriched frame.

- [ ] **Step 4: Update final sorting**

In preliminary sorting, change the sort key to include `realtime_relative_strength_score` before `intraday_signal_score`:

```python
    preliminary_rows = sorted(
        preliminary_rows,
        key=lambda item: (
            item.get("preliminary_status") == "主力抢筹",
            item["signal"].get("next_day_bias") == "高开偏强",
            float(item["signal"].get("realtime_relative_strength_score") or 0),
            float(item["signal"].get("intraday_signal_score") or 0),
            float(item["signal"].get("volume_ratio") or 0),
        ),
        reverse=True,
    )[:_REALTIME_TAIL_CANDIDATE_LIMIT]
```

In final row sorting, make the same change:

```python
    rows = sorted(
        rows,
        key=lambda item: (
            item.get("main_force_status") == "主力抢筹",
            item.get("next_day_bias") == "高开偏强",
            float(item.get("realtime_relative_strength_score") or 0),
            float(item.get("intraday_signal_score") or 0),
            float(item.get("volume_ratio") or 0),
        ),
        reverse=True,
    )[: max(1, min(int(limit), 100))]
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
env HOME=/tmp python3 -m unittest -v \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_intraday_confluence_outputs_market_relative_fields \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_intraday_cache_key_includes_market_relative_rule_version
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add realtime_info_service.py tests/test_realtime_info_service.py
git commit -m "feat: expose realtime market-relative resonance fields"
```

---

### Task 4: Regression Sweep

**Files:**
- Modify: `realtime_info_service.py`
- Test: `tests/test_realtime_info_service.py`

**Interfaces:**
- Consumes: all helpers and behavior from Tasks 1-3.
- Produces: verified realtime info behavior with no known regression in the focused test suite.

- [ ] **Step 1: Run the focused realtime info service suite**

Run:

```bash
env HOME=/tmp python3 -m unittest -v tests.test_realtime_info_service
```

Expected: PASS.

- [ ] **Step 2: Fix compatibility failures from changed default filtering**

If `test_signal_minutes_accept_relaxed_positive_mainboard_candidate` fails because a one-row market benchmark classifies `pct_chg=0.2` as flat and now requires `pct_chg >= 1.0`, update that test fixture to make benchmark unavailable so it covers the documented fallback path:

```python
        market = pd.DataFrame([{
            "ts_code": "600202.SH",
            "industry": "机器人",
            "turnover_rate": 1.0,
            "volume_ratio": 1.0,
            "amount": 300_000,
            "pct_chg": 0.2,
        }])
        with patch(
            "realtime_info_service._build_market_relative_benchmark",
            return_value={
                "market_pct_chg": None,
                "market_state": "fallback",
                "market_state_label": "大盘不可用",
                "sample_count": 0,
            },
        ):
            result = _load_realtime_intraday_signal_bars(
                market,
                sectors,
                "20260729",
                datetime(2026, 7, 29, 14, 50),
                minute_loader=minute_loader,
            )
```

Run:

```bash
env HOME=/tmp python3 -m unittest -v \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_signal_minutes_accept_relaxed_positive_mainboard_candidate
```

Expected: PASS.

- [ ] **Step 3: Run related API suite**

Run:

```bash
env HOME=/tmp python3 -m unittest -v tests.test_realtime_info_api
```

Expected: PASS.

- [ ] **Step 4: Check final diff**

Run:

```bash
git diff -- realtime_info_service.py tests/test_realtime_info_service.py
```

Expected: diff only contains market-relative resonance helpers, tests, cache-key versioning, filtering, and sorting changes.

- [ ] **Step 5: Commit final compatibility fixes if any**

If Step 2 required edits, commit them:

```bash
git add realtime_info_service.py tests/test_realtime_info_service.py
git commit -m "test: align realtime confluence fixtures with market-relative filter"
```

If Step 2 required no edits, do not create an empty commit.
