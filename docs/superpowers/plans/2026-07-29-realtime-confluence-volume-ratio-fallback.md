# Realtime Confluence Volume Ratio Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent the realtime confluence candidate pool from becoming empty solely because the current `daily_basic` snapshot omits `volume_ratio`.

**Architecture:** Add two focused helpers in `realtime_info_service.py`: one calculates Shanghai A-share trading-session progress, and one fills only missing/non-positive volume ratios from current cumulative volume and each stock's latest five valid prior-day volumes. Feed that enriched market frame through sector ranking, candidate filtering, minute snapshot enrichment, and signal scoring without persisting derived values.

**Tech Stack:** Python 3.10, pandas, `unittest`, `unittest.mock`

## Global Constraints

- Keep the existing turnover-rate range of 2%–10% and volume-ratio threshold greater than 2.
- Preserve every positive `volume_ratio` supplied by the data source.
- Do not persist estimated volume ratios to the market cache.
- Leave volume ratio missing when current volume, historical average volume, or trading progress cannot produce a valid estimate.
- Keep the existing realtime result cache key and 58-second TTL.

---

### Task 1: Trading progress and volume-ratio fallback

**Files:**
- Modify: `realtime_info_service.py`
- Test: `tests/test_realtime_info_service.py`

**Interfaces:**
- Consumes: market/history frames with `ts_code`, `vol`, `trade_date`, and optional `volume_ratio`; a `%Y%m%d` trade date; a `datetime`.
- Produces: `_trading_session_progress(now: datetime) -> float` and `_fill_missing_realtime_volume_ratio(market: pd.DataFrame, history: pd.DataFrame, trade_date: str, now: datetime) -> pd.DataFrame`.

- [x] **Step 1: Write failing unit tests**

Add imports for the two helpers and tests covering estimation, preservation, the lunch break, and unavailable history:

```python
from realtime_info_service import (
    _fill_missing_realtime_volume_ratio,
    _trading_session_progress,
)

def test_trading_session_progress_stops_during_lunch_break(self):
    self.assertEqual(
        _trading_session_progress(datetime(2026, 7, 29, 12, 15)),
        0.5,
    )

def test_missing_realtime_volume_ratio_uses_prior_five_day_average(self):
    market = pd.DataFrame([{
        "ts_code": "600201.SH",
        "vol": 120_000,
        "volume_ratio": None,
    }])
    history = pd.DataFrame([
        {"ts_code": "600201.SH", "trade_date": date, "vol": volume}
        for date, volume in [
            ("20260722", 100_000),
            ("20260723", 100_000),
            ("20260724", 100_000),
            ("20260725", 100_000),
            ("20260728", 100_000),
        ]
    ])

    result = _fill_missing_realtime_volume_ratio(
        market,
        history,
        "20260729",
        datetime(2026, 7, 29, 14, 30),
    )

    self.assertAlmostEqual(result.iloc[0]["volume_ratio"], 48 / 35)

def test_existing_positive_realtime_volume_ratio_is_preserved(self):
    market = pd.DataFrame([{
        "ts_code": "600201.SH",
        "vol": 120_000,
        "volume_ratio": 2.8,
    }])
    history = pd.DataFrame([{
        "ts_code": "600201.SH",
        "trade_date": "20260728",
        "vol": 100_000,
    }])

    result = _fill_missing_realtime_volume_ratio(
        market,
        history,
        "20260729",
        datetime(2026, 7, 29, 15, 10),
    )

    self.assertEqual(result.iloc[0]["volume_ratio"], 2.8)

def test_missing_realtime_volume_ratio_stays_missing_without_history(self):
    market = pd.DataFrame([{
        "ts_code": "600201.SH",
        "vol": 120_000,
        "volume_ratio": None,
    }])

    result = _fill_missing_realtime_volume_ratio(
        market,
        pd.DataFrame(),
        "20260729",
        datetime(2026, 7, 29, 15, 10),
    )

    self.assertTrue(pd.isna(result.iloc[0]["volume_ratio"]))
```

- [x] **Step 2: Run tests and verify the expected failure**

Run:

```bash
env HOME=/tmp python3 -m unittest \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_trading_session_progress_stops_during_lunch_break \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_missing_realtime_volume_ratio_uses_prior_five_day_average \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_existing_positive_realtime_volume_ratio_is_preserved \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_missing_realtime_volume_ratio_stays_missing_without_history \
  -v
```

Expected: import failure because `_trading_session_progress` and `_fill_missing_realtime_volume_ratio` do not exist.

- [x] **Step 3: Implement the two helpers**

Add below `_first_present` in `realtime_info_service.py`:

```python
def _trading_session_progress(now: datetime) -> float:
    minutes = now.hour * 60 + now.minute + now.second / 60
    morning_start = 9 * 60 + 30
    morning_end = 11 * 60 + 30
    afternoon_start = 13 * 60
    afternoon_end = 15 * 60
    if minutes <= morning_start:
        return 0.0
    if minutes < morning_end:
        return (minutes - morning_start) / 240
    if minutes < afternoon_start:
        return 0.5
    if minutes < afternoon_end:
        return (120 + minutes - afternoon_start) / 240
    return 1.0


def _fill_missing_realtime_volume_ratio(
    market: pd.DataFrame,
    history: pd.DataFrame,
    trade_date: str,
    now: datetime,
) -> pd.DataFrame:
    if market is None or market.empty:
        return market
    result = market.copy()
    if "volume_ratio" not in result:
        result["volume_ratio"] = pd.NA
    result["volume_ratio"] = pd.to_numeric(result["volume_ratio"], errors="coerce")
    if (
        "ts_code" not in result
        or "vol" not in result
        or history is None
        or history.empty
        or not {"ts_code", "trade_date", "vol"}.issubset(history.columns)
    ):
        return result
    progress = _trading_session_progress(now)
    if progress <= 0:
        return result

    prior = history.copy()
    prior["trade_date"] = prior["trade_date"].astype(str)
    prior["vol"] = pd.to_numeric(prior["vol"], errors="coerce")
    prior = prior[
        (prior["trade_date"] < str(trade_date))
        & prior["vol"].notna()
        & (prior["vol"] > 0)
    ].sort_values(["ts_code", "trade_date"])
    if prior.empty:
        return result
    average_volume = (
        prior.groupby("ts_code", group_keys=False)
        .tail(5)
        .groupby("ts_code")["vol"]
        .mean()
    )
    current_volume = pd.to_numeric(result["vol"], errors="coerce")
    baseline = result["ts_code"].astype(str).map(average_volume) * progress
    estimate = current_volume / baseline
    missing = result["volume_ratio"].isna() | (result["volume_ratio"] <= 0)
    valid = current_volume.gt(0) & baseline.gt(0) & estimate.notna()
    result.loc[missing & valid, "volume_ratio"] = estimate[missing & valid]
    return result
```

- [x] **Step 4: Run the focused tests and verify they pass**

Run the command from Step 2.

Expected: all four tests pass.

- [x] **Step 5: Preserve changes without an intermediate commit**

The service and test files predated this task as untracked user work, so staging
them would also stage unrelated existing implementation. Leave them uncommitted.

```bash
git add realtime_info_service.py tests/test_realtime_info_service.py
git commit -m "fix: derive missing realtime volume ratio"
```

### Task 2: Connect fallback to the realtime confluence pipeline

**Files:**
- Modify: `realtime_info_service.py`
- Test: `tests/test_realtime_info_service.py`

**Interfaces:**
- Consumes: `_fill_missing_realtime_volume_ratio(...)` from Task 1.
- Produces: `_build_realtime_intraday_section(...)` uses one enriched market frame for sector ranking, candidate loading, minute-price application, and signal generation.

- [x] **Step 1: Write a failing integration test**

Add a service test based on the existing realtime reconstruction fixture, but provide `volume_ratio=None`, current `vol=225_000`, and five historical volumes of `100_000`. At 14:30 the estimated ratio is approximately 2.5714, so the existing strict filter should allow the MACD signal:

```python
@patch("realtime_info_service.build_overnight_monitor")
@patch("realtime_info_service._cached_minute_bars")
@patch("realtime_info_service.rank_sector_potential")
@patch("realtime_info_service.load_recent_daily")
@patch("realtime_info_service.load_market_snapshot")
@patch("realtime_info_service.sync_cached_market_data")
@patch("realtime_info_service.get_trade_dates", return_value=["20260729"])
def test_realtime_intraday_uses_estimated_volume_ratio_when_snapshot_value_is_missing(
    self,
    _get_trade_dates,
    sync_cached_market_data,
    load_market_snapshot,
    load_recent_daily,
    rank_sector_potential,
    cached_minute_bars,
    build_overnight_monitor,
):
    sync_cached_market_data.return_value = {
        "data_trade_date": "20260729",
        "cache_updated": True,
    }
    load_market_snapshot.return_value = pd.DataFrame([{
        "trade_date": "20260729",
        "ts_code": "600201.SH",
        "name": "量比回退",
        "industry": "机器人",
        "close": 18.6,
        "high": 19.1,
        "pct_chg": 4.2,
        "turnover_rate": 5.2,
        "volume_ratio": None,
        "vol": 225_000,
        "amount": 620_000_000,
    }])
    load_recent_daily.return_value = pd.DataFrame([
        {"ts_code": "600201.SH", "trade_date": date, "close": 17.85, "vol": 100_000}
        for date in ["20260722", "20260723", "20260724", "20260725", "20260728"]
    ])
    rank_sector_potential.return_value = pd.DataFrame([{
        "industry_name": "机器人",
        "potential_score": 88.0,
    }])
    cached_minute_bars.return_value = build_60min_bars(
        "600201.SH",
        water_macd_kdj_cross_closes(),
    )
    build_overnight_monitor.return_value = {
        "trade_date": "20260729",
        "stocks": [],
    }

    result = build_realtime_info(now=datetime(2026, 7, 29, 14, 30))

    self.assertEqual(
        [row["ts_code"] for row in result["intraday"]["stocks"]],
        ["600201.SH"],
    )
    self.assertAlmostEqual(
        result["intraday"]["stocks"][0]["volume_ratio"],
        2.571429,
    )
```

- [x] **Step 2: Run the integration test and verify the expected failure**

Run:

```bash
env HOME=/tmp python3 -m unittest \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_realtime_intraday_uses_estimated_volume_ratio_when_snapshot_value_is_missing \
  -v
```

Expected: FAIL because the current pipeline sends the unfilled `None` volume ratio into `_load_realtime_intraday_signal_bars`, leaving `stocks` empty.

- [x] **Step 3: Use the enriched market frame throughout the pipeline**

At the start of `_build_realtime_intraday_section`, after resolving the cache and market phase:

```python
realtime_market = _fill_missing_realtime_volume_ratio(
    market,
    history,
    trade_date,
    now,
)
sector_potential = rank_sector_potential(
    realtime_market,
    history,
    limit=_REALTIME_SECTOR_LIMIT,
)
intraday_bars = _load_realtime_intraday_signal_bars(
    realtime_market,
    sector_potential,
    trade_date,
    now,
)
```

Use `realtime_market` instead of `market` for the base-date fallback bar load and for `_apply_minute_snapshots_to_market(...)`. Keep the remainder of the pipeline unchanged.

- [x] **Step 4: Run the integration test and the realtime test modules**

Run:

```bash
env HOME=/tmp python3 -m unittest \
  tests.test_realtime_info_service \
  tests.test_realtime_info_api \
  -v
```

Expected: all tests pass.

- [x] **Step 5: Preserve pipeline integration without an intermediate commit**

As in Task 1, leave the pre-existing untracked files unstaged.

```bash
git add realtime_info_service.py tests/test_realtime_info_service.py
git commit -m "fix: keep realtime confluence candidates with missing source ratio"
```

### Task 3: Verify the real-data funnel and related monitors

**Files:**
- Verify only: `realtime_info_service.py`
- Verify only: `tests/test_realtime_info_service.py`

**Interfaces:**
- Consumes: completed realtime volume-ratio fallback.
- Produces: verification evidence that candidate filtering is no longer empty because of missing source ratios.

- [x] **Step 1: Run related Python tests**

```bash
env HOME=/tmp python3 -m unittest \
  tests.test_realtime_info_service \
  tests.test_realtime_info_api \
  tests.test_intraday_monitor_service \
  tests.test_intraday_monitor_api \
  tests.test_overnight_monitor_service \
  tests.test_overnight_monitor_api \
  -v
```

Expected: all tests pass.

- [x] **Step 2: Run the current-cache funnel diagnostic**

```bash
env HOME=/tmp python3 -u -c "from datetime import datetime; from market_cache import load_market_snapshot, load_recent_daily; from realtime_info_service import _fill_missing_realtime_volume_ratio; from strategy import rank_sector_potential; import pandas as pd; d='20260729'; m=load_market_snapshot(d); h=load_recent_daily(d,100); enriched=_fill_missing_realtime_volume_ratio(m,h,d,datetime.now()); sectors=rank_sector_potential(enriched,h,limit=8); industries=set(sectors['industry_name'].dropna().astype(str)); candidates=enriched[enriched['industry'].astype(str).isin(industries)].copy(); candidates['turnover_rate']=pd.to_numeric(candidates['turnover_rate'],errors='coerce'); candidates['volume_ratio']=pd.to_numeric(candidates['volume_ratio'],errors='coerce'); qualified=candidates[candidates['turnover_rate'].between(2,10,inclusive='both') & (candidates['volume_ratio']>2)]; print({'sector_stocks':len(candidates),'qualified':len(qualified),'estimated_ratios':int(candidates['volume_ratio'].notna().sum())})"
```

Expected: `estimated_ratios` is greater than zero; `qualified` reflects actual volume activity and is not forced to zero by all-null source values.

- [x] **Step 3: Run the realtime service against current cached data**

```bash
env HOME=/tmp python3 -u -c "from realtime_info_service import build_realtime_info; r=build_realtime_info(limit=10); print({'trade_date':r.get('trade_date'),'intraday_count':len(r.get('intraday',{}).get('stocks',[])),'overnight_count':len(r.get('overnight',{}).get('stocks',[]))}); print([(x.get('ts_code'),x.get('name'),x.get('volume_ratio'),x.get('turnover_rate')) for x in r.get('intraday',{}).get('stocks',[])])"
```

Expected: the realtime result is no longer empty solely because the source omitted every volume ratio. A legitimately empty result is acceptable only if estimated ratios exist but no stock passes the unchanged MACD condition.

- [x] **Step 4: Review the final diff**

```bash
git diff --check
git status --short
git diff -- realtime_info_service.py tests/test_realtime_info_service.py
```

Expected: no whitespace errors; no unrelated user changes included in the implementation diff.
