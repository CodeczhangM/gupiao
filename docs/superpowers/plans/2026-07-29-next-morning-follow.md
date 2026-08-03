# Next-Morning Follow Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace only the outer “隔夜溢价” panel with an isolated two-stage “次日早盘跟进” monitor while leaving realtime information’s overnight selection unchanged.

**Architecture:** Build a new `morning_follow_service.py` that recomputes a prior-session setup pool and, on the next trading morning, confirms candidates with 9:30–10:00 minute bars. Expose it through new Python and Java endpoints; point only the outer Vue panel at the new endpoint. Reuse low-level minute-cache and indicator helpers, but do not modify the existing overnight service or realtime-information service.

**Tech Stack:** Python 3.10, pandas, `unittest`, FastAPI, Java/Spring MockMvc, Vue 3 global build

## Global Constraints

- Do not modify `realtime_info_service.py`, `build_overnight_monitor`, `/overnight-monitor`, or the realtime-information “隔夜选股” table.
- Exclude ST stocks and STAR Market codes beginning with `688` or `689`.
- Use the exact first-stage and 9:35 confirmation thresholds in the approved spec.
- Leaders receive only a score bonus and must pass every hard filter.
- Never use the setup day’s opening auction as next-morning confirmation.
- A missing minute-data dependency must never produce “可以跟进”.
- All entry and exit text must respect ordinary A-share T+1.
- Existing tracked files contain user changes; do not stage or commit implementation files.

---

### Task 1: Pure setup and morning-confirmation rules

**Files:**
- Create: `morning_follow_service.py`
- Create: `tests/test_morning_follow_service.py`

**Interfaces:**
- Produces: `_select_candidate_trade_date(trade_dates: list[str], now: datetime) -> str`.
- Produces: `_fill_effective_volume_ratio(market: pd.DataFrame, history: pd.DataFrame, trade_date: str) -> pd.DataFrame`.
- Produces: `_daily_follow_candidates(market: pd.DataFrame, history: pd.DataFrame, trade_date: str, leader_codes: set[str], max_fetch: int) -> pd.DataFrame`.
- Produces: `_morning_confirmation(setup: dict[str, Any], bars: pd.DataFrame, now: datetime, confirmation_trade_date: str | None) -> dict[str, Any]`.
- Produces: `_morning_follow_phase(now: datetime, candidate_trade_date: str, confirmation_trade_date: str | None) -> tuple[str, bool]`.

- [ ] **Step 1: Write failing tests for date selection and effective volume ratio**

Create `tests/test_morning_follow_service.py` with:

```python
import unittest
from datetime import datetime

import pandas as pd

from morning_follow_service import (
    _daily_follow_candidates,
    _fill_effective_volume_ratio,
    _morning_confirmation,
    _select_candidate_trade_date,
)


class MorningFollowServiceTests(unittest.TestCase):
    def test_candidate_date_switches_to_today_at_1430(self):
        dates = ["20260729", "20260728"]
        self.assertEqual(
            _select_candidate_trade_date(dates, datetime(2026, 7, 29, 14, 29)),
            "20260728",
        )
        self.assertEqual(
            _select_candidate_trade_date(dates, datetime(2026, 7, 29, 14, 30)),
            "20260729",
        )

    def test_missing_volume_ratio_uses_latest_five_prior_days(self):
        market = pd.DataFrame([{
            "ts_code": "600101.SH",
            "vol": 150_000,
            "volume_ratio": None,
        }])
        history = pd.DataFrame([
            {"ts_code": "600101.SH", "trade_date": date, "vol": 100_000}
            for date in ["20260722", "20260723", "20260724", "20260725", "20260728"]
        ])

        result = _fill_effective_volume_ratio(market, history, "20260729")

        self.assertEqual(result.iloc[0]["estimated_volume_ratio"], 1.5)
        self.assertEqual(result.iloc[0]["effective_volume_ratio"], 1.5)
```

- [ ] **Step 2: Write failing tests for hard-filter isolation**

Add:

```python
    def test_daily_filter_rejects_st_star_market_and_unqualified_leader(self):
        market = pd.DataFrame([
            {
                "ts_code": "600101.SH", "name": "合格股份", "close": 10,
                "pct_chg": 3, "turnover_rate": 6, "volume_ratio": 1.8,
                "vol": 180_000, "amount": 300_000,
            },
            {
                "ts_code": "688001.SH", "name": "科创股份", "close": 10,
                "pct_chg": 3, "turnover_rate": 6, "volume_ratio": 1.8,
                "vol": 180_000, "amount": 300_000,
            },
            {
                "ts_code": "600102.SH", "name": "ST样本", "close": 10,
                "pct_chg": 3, "turnover_rate": 6, "volume_ratio": 1.8,
                "vol": 180_000, "amount": 300_000,
            },
            {
                "ts_code": "600103.SH", "name": "弱势龙头", "close": 10,
                "pct_chg": 0.5, "turnover_rate": 6, "volume_ratio": 1.8,
                "vol": 180_000, "amount": 300_000,
            },
        ])

        result = _daily_follow_candidates(
            market,
            pd.DataFrame(),
            "20260729",
            leader_codes={"600103.SH"},
            max_fetch=30,
        )

        self.assertEqual(result["ts_code"].tolist(), ["600101.SH"])
        self.assertFalse(result.iloc[0]["morning_follow_sector_leader"])
```

- [ ] **Step 3: Write failing tests for all morning states**

Add this module-level helper above `MorningFollowServiceTests`:

```python
def morning_bars(closes, opens=None, volumes=None):
    opens = opens or closes
    volumes = volumes or [1000] * len(closes)
    return pd.DataFrame([
        {
            "ts_code": "600101.SH",
            "trade_time": f"2026-07-30 09:{30 + index:02d}:00",
            "open": opens[index],
            "high": max(opens[index], close),
            "low": min(opens[index], close),
            "close": close,
            "vol": volumes[index],
        }
        for index, close in enumerate(closes)
    ])
```

Add these methods inside `MorningFollowServiceTests`:

```python
    def test_morning_confirmation_waits_before_935(self):
        result = _morning_confirmation(
            {"close": 10.0, "previous_tail_support": 9.95},
            morning_bars([10.1, 10.12, 10.13]),
            datetime(2026, 7, 30, 9, 33),
            "20260730",
        )
        self.assertEqual(result["follow_status"], "等待9:35确认")

    def test_morning_confirmation_accepts_supported_price_above_vwap(self):
        result = _morning_confirmation(
            {"close": 10.0, "previous_tail_support": 9.95},
            morning_bars([10.1, 10.12, 10.14, 10.16, 10.18, 10.2]),
            datetime(2026, 7, 30, 9, 36),
            "20260730",
        )
        self.assertEqual(result["follow_status"], "可以跟进")
        self.assertTrue(result["morning_above_open"])
        self.assertTrue(result["morning_above_vwap"])

    def test_morning_confirmation_rejects_gap_above_three_percent(self):
        result = _morning_confirmation(
            {"close": 10.0, "previous_tail_support": 9.95},
            morning_bars([10.31, 10.32, 10.33, 10.34, 10.35, 10.36]),
            datetime(2026, 7, 30, 9, 36),
            "20260730",
        )
        self.assertEqual(result["follow_status"], "放弃")

    def test_morning_confirmation_never_accepts_missing_minutes(self):
        result = _morning_confirmation(
            {"close": 10.0, "previous_tail_support": 9.95},
            pd.DataFrame(),
            datetime(2026, 7, 30, 9, 36),
            "20260730",
        )
        self.assertEqual(result["follow_status"], "数据未就绪")

    def test_morning_confirmation_keeps_borderline_case_waiting(self):
        result = _morning_confirmation(
            {"close": 10.0, "previous_tail_support": 9.95},
            morning_bars([10.1, 10.08, 10.09, 10.1, 10.11, 10.09]),
            datetime(2026, 7, 30, 9, 36),
            "20260730",
        )
        self.assertEqual(result["follow_status"], "等待确认")
```

- [ ] **Step 4: Run tests and verify RED**

Run:

```bash
env HOME=/tmp python3 -m unittest tests.test_morning_follow_service -v
```

Expected: import failure because `morning_follow_service.py` does not exist.

- [ ] **Step 5: Implement date, ratio, daily filter, and confirmation helpers**

Create `morning_follow_service.py`. Import `datetime`, `math`, `Any`, and pandas. Implement:

```python
def _select_candidate_trade_date(trade_dates, now):
    dates = sorted({str(value) for value in trade_dates}, reverse=True)
    if not dates:
        raise LookupError("没有可用交易日")
    today = now.strftime("%Y%m%d")
    if today in dates and now.strftime("%H:%M:%S") >= "14:30:00":
        return today
    return next((date for date in dates if date < today), dates[0])


def _fill_effective_volume_ratio(market, history, trade_date):
    result = market.copy()
    source = pd.to_numeric(
        result["volume_ratio"] if "volume_ratio" in result else pd.Series(index=result.index, dtype=float),
        errors="coerce",
    )
    estimate = pd.Series(index=result.index, dtype=float)
    if (
        not result.empty
        and {"ts_code", "vol"}.issubset(result.columns)
        and history is not None
        and not history.empty
        and {"ts_code", "trade_date", "vol"}.issubset(history.columns)
    ):
        prior = history.copy()
        prior["trade_date"] = prior["trade_date"].astype(str)
        prior["vol"] = pd.to_numeric(prior["vol"], errors="coerce")
        prior = prior[(prior["trade_date"] < str(trade_date)) & (prior["vol"] > 0)]
        averages = (
            prior.sort_values(["ts_code", "trade_date"])
            .groupby("ts_code", group_keys=False).tail(5)
            .groupby("ts_code")["vol"].mean()
        )
        current = pd.to_numeric(result["vol"], errors="coerce")
        estimate = current / result["ts_code"].astype(str).map(averages)
    result["estimated_volume_ratio"] = estimate
    result["effective_volume_ratio"] = source.where(source > 0, estimate)
    return result


def _daily_follow_candidates(market, history, trade_date, leader_codes, max_fetch):
    data = _fill_effective_volume_ratio(market, history, trade_date)
    for column in ("close", "pct_chg", "turnover_rate", "amount"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    code = data["ts_code"].astype(str)
    name = data.get("name", pd.Series("", index=data.index)).astype(str)
    eligible = data[
        ~name.str.contains("ST", case=False, na=False)
        & ~code.str.startswith(("688", "689"))
        & (data["close"] > 3)
        & data["pct_chg"].between(1.5, 6.5, inclusive="both")
        & data["turnover_rate"].between(2, 12, inclusive="both")
        & data["effective_volume_ratio"].between(1.2, 3.2, inclusive="both")
        & (data["amount"] >= 200_000)
    ].copy()
    eligible["morning_follow_sector_leader"] = eligible["ts_code"].astype(str).isin(leader_codes)
    eligible["daily_follow_prefilter_score"] = (
        eligible["pct_chg"].clip(0, 6.5) * 4
        + eligible["effective_volume_ratio"].clip(0, 3.2) * 8
        + (12 - (eligible["turnover_rate"] - 7).abs()).clip(lower=0)
        + (eligible["amount"] / 200_000).clip(0, 10)
    )
    return eligible.sort_values("daily_follow_prefilter_score", ascending=False).head(max_fetch)
```

Implement `_morning_confirmation` with these exact derived fields and branch order:

```python
def _morning_confirmation(setup, bars, now, confirmation_trade_date):
    base = {
        "follow_status": "明日观察",
        "morning_open": None,
        "morning_current_price": None,
        "morning_open_gap_pct": None,
        "morning_current_gain_pct": None,
        "morning_first5_vwap": None,
        "morning_above_open": False,
        "morning_above_vwap": False,
        "morning_entry_plan": "次日9:35后确认承接，不参与集合竞价",
        "t1_exit_plan": "买入后最早下一交易日卖出；破位或冲高转弱时按计划处理",
    }
    if not confirmation_trade_date:
        return {**base, "follow_reason": "等待下一交易日早盘确认"}
    if now.strftime("%Y%m%d") != confirmation_trade_date:
        return {**base, "follow_reason": "当前不在确认交易日"}

    usable = normalize_numeric_bars_for_confirmation_date(
        bars, confirmation_trade_date, now
    )
    if usable.empty:
        return {**base, "follow_status": "数据未就绪", "follow_reason": "缺少确认日分钟数据"}

    opening = float(usable.iloc[0]["open"])
    current = float(usable.iloc[-1]["close"])
    first_five = usable[
        (usable["trade_time"].dt.strftime("%H:%M") >= "09:30")
        & (usable["trade_time"].dt.strftime("%H:%M") <= "09:34")
    ]
    if first_five.empty or first_five["vol"].sum() <= 0:
        return {**base, "follow_status": "数据未就绪", "follow_reason": "首5分钟VWAP数据不足"}
    vwap = float(
        (first_five["close"] * first_five["vol"]).sum()
        / first_five["vol"].sum()
    )
    previous_close = float(setup["close"])
    support = float(setup["previous_tail_support"])
    gap = (opening / previous_close - 1) * 100
    gain = (current / previous_close - 1) * 100
    metrics = {
        **base,
        "morning_open": opening,
        "morning_current_price": current,
        "morning_open_gap_pct": gap,
        "morning_current_gain_pct": gain,
        "morning_first5_vwap": vwap,
        "morning_above_open": current >= opening,
        "morning_above_vwap": current >= vwap,
    }

    if now.strftime("%H:%M") < "09:35":
        return {**metrics, "follow_status": "等待9:35确认", "follow_reason": "首5分钟尚未结束"}
    if gap < -1 or gap > 3:
        return {**metrics, "follow_status": "放弃", "follow_reason": "开盘缺口超出风控区间"}
    if gain > 4:
        return {**metrics, "follow_status": "放弃", "follow_reason": "当前涨幅超过4%，不追高"}
    if current < support:
        return {**metrics, "follow_status": "放弃", "follow_reason": "跌破前日尾盘支撑"}
    if current < opening and current < vwap:
        return {**metrics, "follow_status": "放弃", "follow_reason": "同时跌破开盘价和首5分钟VWAP"}
    if (
        -0.5 <= gap <= 2.5
        and gain <= 3.5
        and current >= previous_close
        and current >= opening
        and current >= vwap
        and current >= support
    ):
        return {**metrics, "follow_status": "可以跟进", "follow_reason": "开盘幅度适中且承接确认"}
    return {
        **metrics,
        "follow_status": "等待确认",
        "follow_reason": "未触发放弃条件，但确认条件尚未全部满足",
    }
```

`normalize_numeric_bars_for_confirmation_date` may be a private inline block or
helper, but it must parse `trade_time`, keep only 09:30–10:00 rows for the exact
confirmation date, discard rows after `now`, coerce `open/close/vol`, and reject
non-finite values. Do not accept an auction-only or wrong-date row.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run the command from Step 4.

Expected: all tests pass.

### Task 2: Setup scoring and two-stage service orchestration

**Files:**
- Modify: `morning_follow_service.py`
- Modify: `tests/test_morning_follow_service.py`

**Interfaces:**
- Consumes: Task 1 helpers plus read-only helpers imported from `overnight_monitor_service`.
- Produces: `_setup_row(stock, stock_bars, sector_signal, leader_codes) -> dict[str, Any] | None`.
- Produces: `build_morning_follow_monitor(limit: int = 10, max_fetch: int = 30, now: datetime | None = None) -> dict[str, Any]`.

- [ ] **Step 1: Write failing setup-row tests**

Patch `_macd_kdj_60m_signal` to return a complete controlled signal and assert:

```python
from morning_follow_service import _setup_row, build_morning_follow_monitor


    @patch("morning_follow_service._macd_kdj_60m_signal")
    def test_setup_row_requires_tail_rules_and_never_uses_opening_auction(self, signal_builder):
        signal_builder.return_value = {
            "tail_strength_score": 88,
            "tail_return_after_1430": 0.6,
            "tail_volume_ratio": 1.8,
            "tail_close_position": 0.9,
            "macd_above_zero_60m": True,
            "macd_recent_golden_cross_60m": True,
            "kdj_bullish_60m": True,
        }
        stock = {
            "ts_code": "600101.SH", "name": "合格股份", "industry": "机器人",
            "close": 10, "pct_chg": 3, "turnover_rate": 6,
            "effective_volume_ratio": 1.8, "amount": 300_000,
            "open": 9.2,
        }
        tail = pd.DataFrame([{"low": 9.95}, {"low": 10.0}])

        result = _setup_row(
            stock,
            {"60m": pd.DataFrame([{"close": 10}]), "tail_1m": tail},
            {
                "sector_macd_status": "板块60分MACD水上走强",
                "sector_macd_above_zero": True,
                "sector_macd_trending_up": True,
                "sector_60m_excluded": False,
            },
            leader_codes={"600101.SH"},
        )

        self.assertIsNotNone(result)
        self.assertGreaterEqual(result["follow_setup_score"], 70)
        self.assertEqual(result["previous_tail_support"], 9.95)
        self.assertNotIn("opening_auction_return", result)
        self.assertNotIn("集合竞价", result["follow_reason"])
```

Add one table-driven rejection test by taking the controlled signal/sector
objects above, changing one value per subtest, and asserting `_setup_row(...) is
None` for:

```python
[
    ("tail_return_after_1430", 1.3),
    ("tail_volume_ratio", 3.1),
    ("tail_close_position", 0.74),
]
```

Add separate assertions for `sector_60m_excluded=True`, an empty sector signal,
and `_macd_kdj_60m_signal.return_value=None`.

- [ ] **Step 2: Write a failing orchestration test**

Patch the service boundaries rather than pandas internals. Import `patch` from
`unittest.mock`, then use concrete frames so the test has no undefined fixtures:

```python
    @patch("morning_follow_service._morning_bars_for_candidate")
    @patch("morning_follow_service._load_setup_bars")
    @patch("morning_follow_service._sector_60m_signal_from_bars")
    @patch("morning_follow_service._leader_codes_from_sector_potential")
    @patch("morning_follow_service._load_follow_inputs")
    def test_monitor_builds_previous_day_pool_then_confirms_current_morning(
        self,
        load_inputs,
        leader_codes,
        sector_signal,
        load_setup_bars,
        morning_bars_for_candidate,
    ):
        market = pd.DataFrame([{
            "ts_code": "600101.SH", "name": "合格股份", "industry": "机器人",
            "close": 10.0, "pct_chg": 3.0, "turnover_rate": 6.0,
            "volume_ratio": 1.8, "vol": 180_000, "amount": 300_000,
        }])
        load_inputs.return_value = (
            market, pd.DataFrame(),
            {"candidate_trade_date": "20260729", "confirmation_trade_date": "20260730"},
        )
        leader_codes.return_value = {"600101.SH": {"leader_score": 90}}
        sector_signal.return_value = {
            "机器人": {
                "sector_macd_status": "板块60分MACD水上走强",
                "sector_macd_above_zero": True,
                "sector_macd_trending_up": True,
                "sector_60m_excluded": False,
            },
        }
        load_setup_bars.return_value = (
            {
                "600101.SH": {
                    "60m": pd.DataFrame([{"close": 10.0}]),
                    "tail_1m": pd.DataFrame([{"low": 9.95}, {"low": 10.0}]),
                },
            },
            pd.DataFrame([{"ts_code": "600201.SH", "industry": "机器人"}]),
            {"600201.SH": pd.DataFrame([{"close": 100.0}])},
            [],
        )
        morning_bars_for_candidate.return_value = morning_bars(
            [10.1, 10.12, 10.14, 10.16, 10.18, 10.2]
        )

        with patch("morning_follow_service._macd_kdj_60m_signal") as stock_signal:
            stock_signal.return_value = {
                "tail_strength_score": 88,
                "tail_return_after_1430": 0.6,
                "tail_volume_ratio": 1.8,
                "tail_close_position": 0.9,
                "macd_above_zero_60m": True,
                "macd_recent_golden_cross_60m": True,
                "kdj_bullish_60m": True,
            }
            result = build_morning_follow_monitor(
                limit=10,
                now=datetime(2026, 7, 30, 9, 36),
            )

        self.assertEqual(result["candidate_trade_date"], "20260729")
        self.assertEqual(result["confirmation_trade_date"], "20260730")
        self.assertEqual(result["stocks"][0]["follow_status"], "可以跟进")
```

Define `_load_setup_bars` to return exactly
`(candidate_bars_by_code, sector_representatives, sector_60m_bars_by_code,
warnings)`, as used above.

- [ ] **Step 3: Run Task 2 tests and verify RED**

Run:

```bash
env HOME=/tmp python3 -m unittest \
  tests.test_morning_follow_service.MorningFollowServiceTests.test_setup_row_requires_tail_rules_and_never_uses_opening_auction \
  tests.test_morning_follow_service.MorningFollowServiceTests.test_monitor_builds_previous_day_pool_then_confirms_current_morning \
  -v
```

Expected: import or attribute failure because `_setup_row` and `build_morning_follow_monitor` are absent.

- [ ] **Step 4: Implement setup scoring**

Import these helpers without changing their source module:

```python
from overnight_monitor_service import (
    _cached_minute_bars,
    _datetime_window,
    _history_window,
    _json_safe,
    _leader_codes_from_sector_potential,
    _sector_60m_signal_from_bars,
    _sector_representative_universe,
)
from strategy import _macd_kdj_60m_signal
```

Implement `_setup_row` with this fixed scoring contract:

```python
signal = _macd_kdj_60m_signal(pd.Series(stock), stock_bars)
if signal is None:
    return None
tail_return = number(signal["tail_return_after_1430"])
tail_volume = number(signal["tail_volume_ratio"])
tail_position = number(signal["tail_close_position"])

if not sector_signal or sector_signal.get("sector_60m_excluded"):
    return None
if not (0.15 <= tail_return <= 1.20):
    return None
if not (1.20 <= tail_volume <= 3.00):
    return None
if tail_position < 0.75:
    return None
if not (
    signal.get("macd_above_zero_60m")
    or signal.get("macd_recent_golden_cross_60m")
):
    return None

tail_score = 30
stock_score = (
    10 * bool(signal.get("macd_above_zero_60m"))
    + 6 * bool(signal.get("macd_recent_golden_cross_60m"))
    + 4 * bool(signal.get("kdj_bullish_60m"))
)
sector_score = (
    10 * bool(sector_signal.get("sector_macd_above_zero"))
    + 10 * bool(sector_signal.get("sector_macd_trending_up"))
)
volume_score = 8 if 1.5 <= effective_volume_ratio <= 2.5 else 5
turnover_score = 5 if 4 <= turnover_rate <= 9 else 3
amount_score = 7 if amount >= 500_000 else 4
leader_score = 10 if ts_code in leader_codes else 0
follow_setup_score = (
    tail_score + stock_score + sector_score
    + volume_score + turnover_score + amount_score + leader_score
)
if follow_setup_score < 70:
    return None
```

All `number(...)` conversions must reject missing/non-finite values rather than
silently treating them as zero. Compute `previous_tail_support` as the minimum
numeric low in the setup-day 14:30–15:00 frame. Return the daily fields, tail
metrics, sector status, score breakdown, `follow_status="明日观察"`,
`morning_entry_plan="次日9:35后确认承接，不参与集合竞价"`, and a T+1-safe
`t1_exit_plan`.

- [ ] **Step 5: Implement orchestration and isolated cache**

Add an independent `_MORNING_FOLLOW_RESULT_CACHE` and implement these exact
boundaries:

```python
def _load_follow_inputs(now):
    # Return (candidate-day market, recent history, metadata).
    # Query enough calendar days through now + 14 days to identify both the
    # candidate date and its next open date. metadata includes
    # candidate_trade_date and confirmation_trade_date. Use get_trade_dates,
    # sync_cached_market_data, load_market_snapshot, and load_recent_daily.
    # Sync the current snapshot only when candidate_trade_date is today during
    # the 14:30–15:00 build window; otherwise load that date's cached snapshot.


def _load_setup_bars(market, candidates, candidate_trade_date, now):
    # Return (candidate_bars_by_code, sector_representatives,
    # sector_60m_bars_by_code, warnings).
    # Each code maps to {"60m": frame, "tail_1m": frame}; tail_1m is restricted
    # to 14:30–15:00 of candidate_trade_date. Select representatives with
    # _sector_representative_universe(market, candidates), then fetch their 60m
    # bars into a plain code->frame mapping for _sector_60m_signal_from_bars.


def _morning_bars_for_candidate(ts_code, confirmation_trade_date, now):
    # Return confirmation-date 1m bars between 09:30 and
    # min(now minus one complete minute, 10:00).


def _morning_follow_phase(now, candidate_trade_date, confirmation_trade_date):
    today = now.strftime("%Y%m%d")
    clock = now.strftime("%H:%M")
    if today == candidate_trade_date and "14:30" <= clock < "15:00":
        return "观察池构建中", True
    if today == confirmation_trade_date and "09:30" <= clock < "09:35":
        return "等待9:35确认", True
    if today == confirmation_trade_date and "09:35" <= clock <= "10:00":
        return "早盘确认", True
    if today == confirmation_trade_date and "10:00" < clock < "15:00":
        return "确认结束", False
    return "明日观察池", False


def build_morning_follow_monitor(limit=10, max_fetch=30, now=None):
    now = now or datetime.now()
    market, history, metadata = _load_follow_inputs(now)
    leader_map = _leader_codes_from_sector_potential(market, history)
    leader_codes = set(leader_map)
    candidates = _daily_follow_candidates(
        market, history, metadata["candidate_trade_date"], leader_codes, max_fetch
    )
    bars_by_code, sector_representatives, sector_60m_bars, warnings = _load_setup_bars(
        market, candidates, metadata["candidate_trade_date"], now
    )
    sector_signals = _sector_60m_signal_from_bars(
        sector_representatives, sector_60m_bars
    )

    setups = []
    for stock in candidates.to_dict("records"):
        industry = str(stock.get("industry") or "")
        sector_signal = sector_signals.get(industry, {})
        setup = _setup_row(
            stock, bars_by_code.get(stock["ts_code"], {}), sector_signal, leader_codes
        )
        if setup is not None:
            setups.append(setup)

    for setup in setups:
        bars = _morning_bars_for_candidate(
            setup["ts_code"], metadata.get("confirmation_trade_date"), now
        )
        setup.update(_morning_confirmation(
            setup, bars, now, metadata.get("confirmation_trade_date")
        ))

    status_order = {"可以跟进": 0, "等待确认": 1, "等待9:35确认": 2,
                    "明日观察": 3, "数据未就绪": 4, "放弃": 5}
    setups.sort(key=lambda row: (
        status_order.get(row["follow_status"], 9),
        -row["follow_setup_score"],
    ))
    return {
        **metadata,
        "market_phase": _morning_follow_phase(
            now,
            metadata["candidate_trade_date"],
            metadata.get("confirmation_trade_date"),
        )[0],
        "stocks": [_json_safe(row) for row in setups[:limit]],
        "count": min(len(setups), limit),
        "warnings": warnings,
    }
```

The real implementation may factor out small helpers, but preserve these return
shapes. Add cache keys covering candidate date, confirmation date, market phase,
`limit`, and `max_fetch`; expire independently in 9:30–10:00 and 14:30–15:00
refresh windows. Catch per-stock minute/signal failures, append a warning, and
continue processing other stocks. A candidate with missing setup bars is omitted;
missing confirmation bars must remain visible as `数据未就绪`.

Do not call `build_overnight_monitor`.

- [ ] **Step 6: Run the full new service tests**

```bash
env HOME=/tmp python3 -m unittest tests.test_morning_follow_service -v
```

Expected: all tests pass.

### Task 3: Add isolated Python and Java API paths

**Files:**
- Modify: `app.py`
- Create: `tests/test_morning_follow_api.py`
- Modify: `quantServer/quantServer/src/main/java/com/codec/quantserver/controller/QuantController.java`
- Modify: `quantServer/quantServer/src/main/java/com/codec/quantserver/service/QuantPythonClient.java`
- Modify: `quantServer/quantServer/src/test/java/com/codec/quantserver/controller/QuantControllerTest.java`

**Interfaces:**
- Produces: Python `GET /api/morning-follow-monitor?limit=10`.
- Produces: Java `GET /api/quant/morning-follow-monitor?limit=10`.

- [ ] **Step 1: Write failing Python endpoint tests**

Create `tests/test_morning_follow_api.py`:

```python
import unittest
from unittest.mock import patch
from fastapi import HTTPException
import app


class MorningFollowApiTests(unittest.TestCase):
    @patch("app.build_morning_follow_monitor", return_value={"stocks": [{"ts_code": "600101.SH"}]})
    def test_endpoint_returns_new_service_payload(self, service):
        result = app.morning_follow_monitor(limit=12)
        self.assertEqual(result["stocks"][0]["ts_code"], "600101.SH")
        service.assert_called_once_with(limit=12)

    @patch("app.build_morning_follow_monitor", side_effect=RuntimeError("minutes unavailable"))
    def test_endpoint_maps_failure_to_502(self, _service):
        with self.assertRaises(HTTPException) as raised:
            app.morning_follow_monitor()
        self.assertEqual(raised.exception.status_code, 502)
```

- [ ] **Step 2: Write a failing Java controller test**

Add:

```java
@Test
void morningFollowMonitorForwardsLimitToPythonClient() throws Exception {
    QuantPythonClient client = mock(QuantPythonClient.class);
    when(client.morningFollowMonitor(10)).thenReturn(Map.of("market_phase", "早盘确认"));
    MockMvc mockMvc = MockMvcBuilders.standaloneSetup(new QuantController(client)).build();

    mockMvc.perform(get("/api/quant/morning-follow-monitor").param("limit", "10"))
            .andExpect(status().isOk());

    verify(client).morningFollowMonitor(10);
}
```

- [ ] **Step 3: Run API tests and verify RED**

```bash
env HOME=/tmp python3 -m unittest tests.test_morning_follow_api -v
cd quantServer/quantServer && mvn -q -Dtest=QuantControllerTest test
```

Expected: Python import/patch failure and Java compile failure because the new endpoint methods are absent.

- [ ] **Step 4: Implement both API paths**

In `app.py`, import `build_morning_follow_monitor` and add:

```python
@app.get("/api/morning-follow-monitor")
def morning_follow_monitor(limit: int = Query(10, ge=1, le=100)):
    try:
        return build_morning_follow_monitor(limit=limit)
    except Exception as exc:
        logger.exception("获取次日早盘跟进失败")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
```

Add Java controller/client methods mirroring `realtimeInfo`, using the new path and the same `1..100` limit clamp.

- [ ] **Step 5: Run Python and Java API tests**

Run the commands from Step 3.

Expected: both pass.

### Task 4: Replace only the outer panel

**Files:**
- Modify: `quantClient/index.html`
- Modify: `quantClient/main.js`

**Interfaces:**
- Consumes: `/morning-follow-monitor`.
- Preserves: realtime-information `realtimeOvernightRows` and its existing table.

- [ ] **Step 1: Change only outer navigation/title/request**

In `index.html`, change the outer navigation label and panel title from “隔夜溢价”/“隔夜溢价候选” to “次日早盘跟进”. In `main.js`, change only:

```javascript
overnight_monitor: '次日早盘跟进',
```

and:

```javascript
this.overnightMonitor = (await this.request('/morning-follow-monitor?limit=10')) || {};
```

- [ ] **Step 2: Replace the outer table columns**

For the section guarded by `activeTab === 'overnight_monitor'`, render:

```text
股票 | 板块 | 前日涨幅 | 换手率 | 量比 | 前日尾盘 | 尾盘量能 |
板块60m | 观察分 | 早盘状态 | 今日开盘 | 当前涨幅 | VWAP |
跟进条件 / T+1计划
```

Use existing number, signed, tail-volume, and badge helpers. Add a `morningFollowBadgeClass(status)` method mapping “可以跟进” to `strong`, “放弃/数据未就绪” to `risk`, and all other statuses to `watch`/`muted`. Set the empty-row colspan to 14 and update its copy.

Do not edit the section guarded by `activeTab === 'realtime_info'`.

- [ ] **Step 3: Run frontend syntax and utility tests**

```bash
node --check quantClient/main.js
for test_file in quantClient/*.test.js; do node "$test_file"; done
```

Expected: every command exits 0.

- [ ] **Step 4: Verify page isolation**

Use `git diff -- quantClient/index.html quantClient/main.js` and confirm:

- the realtime-information “隔夜选股” block is byte-for-byte unchanged by this task;
- only the outer block requests and renders morning-follow fields.

### Task 5: Regression, real-data funnel, and isolation proof

**Files:**
- Verify only: `morning_follow_service.py`
- Verify only: all modified API/UI files

- [ ] **Step 1: Run related Python tests**

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

Expected: all pass.

- [ ] **Step 2: Run Java tests**

```bash
cd quantServer/quantServer && mvn -q -Dtest=QuantControllerTest test
```

Expected: pass.

- [ ] **Step 3: Run current-cache setup funnel**

Call `build_morning_follow_monitor(limit=10, now=datetime(2026, 7, 29, 15, 10))` and print:

- daily rows;
- daily hard-filter count;
- tail hard-filter count;
- score-qualified count;
- final codes and scores.

Expected: every returned stock satisfies all hard thresholds. An empty result is valid evidence that thresholds are strict; it must not be widened without a multi-day evaluation.

- [ ] **Step 4: Prove realtime information remains on the old service**

Run:

```bash
env HOME=/tmp python3 -m unittest \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_realtime_info_syncs_current_market_and_enriches_both_sections \
  -v
git diff --exit-code -- realtime_info_service.py
```

The first command must pass. For the second command, compare against the state captured before this task: this task must add no new diff to `realtime_info_service.py`; because that file is pre-existing untracked work, verify its checksum before and after implementation instead of staging it.

- [ ] **Step 5: Final syntax and working-tree review**

```bash
python3 -m py_compile morning_follow_service.py tests/test_morning_follow_service.py tests/test_morning_follow_api.py
node --check quantClient/main.js
git status --short
```

Expected: no syntax errors and no implementation files staged or committed.
