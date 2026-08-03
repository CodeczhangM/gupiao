# Realtime Info Multi-Source Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 当 Tushare 当日快照或分钟行情不可用时，让“实时信息”页的实时共振和隔夜选股自动使用东方财富、新浪财经备用源，并且不再把昨日数据标记为实时。

**Architecture:** 新建 `realtime_market_source.py` 作为纯行情适配层，负责东方财富快照、东方财富/新浪分钟数据的解析、校验、缓存和三级降级编排。`realtime_info_service.py` 建立一次统一市场上下文并传给两个子表；`overnight_monitor_service.py` 通过可选运行时覆盖参数复用该上下文，默认调用路径保持不变。

**Tech Stack:** Python 3.10、pandas、标准库 `dataclasses/json/subprocess/time/urllib.parse`、`unittest` 与 `unittest.mock`、现有 FastAPI 和 Maven 转发层。

## Global Constraints

- Tushare 始终为第一数据源。
- 快照降级顺序为 `Tushare → 东方财富 → 上一交易日缓存`。
- 分钟线降级顺序为 `Tushare → 东方财富 → 新浪财经`。
- 不引入 AkShare、requests 或其他新依赖。
- 不修改实时共振和隔夜选股的评分阈值、候选条件或排序规则。
- 不修改 `morning_follow_service.py` 及其测试；执行前后哈希必须分别保持：
  - `morning_follow_service.py`: `67515ff0c8396a5b1a1a023cecb0253d80ec31c8dcba918dfc338a5f8cc8d415`
  - `tests/test_morning_follow_service.py`: `c14f89a3b7978335e18e918f6c816e40ef76e382c167aec3936a44c624ab60c3`
- 第三方快照不写入持久化市场缓存。
- 备用源不支持的证券代码必须返回告警，不能伪造数据。
- 1 分钟实时数据必须包含目标交易日；60 分钟信号至少包含 35 条有效收盘价。
- 上一日底座没有当日分钟覆盖时，`current_price` 和 `day_high` 必须为空。
- 工作区已有未提交且重叠的用户改动。实施时仅编辑计划列出的文件，不暂存、不提交，不清理其他改动。

---

### Task 1: Build and test external market payload normalizers

**Files:**

- Create: `realtime_market_source.py`
- Create: `tests/test_realtime_market_source.py`

**Interfaces:**

- Produces: `MinuteLoadResult(bars: pd.DataFrame, source: str, warnings: list[str])`
- Produces: `_eastmoney_secid(ts_code: str) -> str | None`
- Produces: `_sina_symbol(ts_code: str) -> str | None`
- Produces: `_parse_eastmoney_snapshot(payload: Any, trade_date: str) -> pd.DataFrame`
- Produces: `_parse_eastmoney_trends(payload: Any, ts_code: str, trade_date: str, end_datetime: str) -> pd.DataFrame`
- Produces: `_parse_eastmoney_klines(payload: Any, ts_code: str, trade_date: str, end_datetime: str) -> pd.DataFrame`
- Produces: `_parse_sina_klines(text: str, ts_code: str, trade_date: str, end_datetime: str) -> pd.DataFrame`

- [ ] **Step 1: Write failing parser and validation tests**

Create `tests/test_realtime_market_source.py` with literal fixtures:

```python
import unittest

from realtime_market_source import (
    _eastmoney_secid,
    _parse_eastmoney_klines,
    _parse_eastmoney_snapshot,
    _parse_eastmoney_trends,
    _parse_sina_klines,
    _sina_symbol,
)


class RealtimeMarketSourceTests(unittest.TestCase):
    def test_symbol_converters_support_shenzhen_and_shanghai_only(self):
        self.assertEqual(_eastmoney_secid("600298.SH"), "1.600298")
        self.assertEqual(_eastmoney_secid("300910.SZ"), "0.300910")
        self.assertEqual(_sina_symbol("600298.SH"), "sh600298")
        self.assertEqual(_sina_symbol("300910.SZ"), "sz300910")
        self.assertIsNone(_eastmoney_secid("830001.BJ"))
        self.assertIsNone(_sina_symbol("bad"))

    def test_eastmoney_snapshot_maps_fields_and_drops_invalid_rows(self):
        payload = {"data": {"diff": [
            {
                "f12": "600298", "f13": 1, "f14": "安琪酵母",
                "f2": 39.12, "f15": 39.80, "f16": 38.50,
                "f17": 38.70, "f18": 38.62, "f3": 1.29,
                "f8": 2.61, "f10": 1.47, "f5": 123456,
                "f6": 482000000, "f100": "食品饮料",
            },
            {"f12": "-", "f13": 1, "f14": "无效"},
        ]}}

        result = _parse_eastmoney_snapshot(payload, "20260730")

        self.assertEqual(result["ts_code"].tolist(), ["600298.SH"])
        self.assertEqual(result.iloc[0]["trade_date"], "20260730")
        self.assertEqual(result.iloc[0]["close"], 39.12)
        self.assertEqual(result.iloc[0]["pre_close"], 38.62)
        self.assertEqual(result.iloc[0]["industry"], "食品饮料")

    def test_minute_parsers_filter_old_future_and_duplicate_rows(self):
        trends = {"data": {"trends": [
            "2026-07-29 14:59,10,10,10,10,100,1000,10",
            "2026-07-30 14:29,10,10.1,10.2,9.9,100,1010,10",
            "2026-07-30 14:29,10,10.2,10.3,9.9,200,2040,10",
            "2026-07-30 14:31,10,10.4,10.5,9.9,100,1040,10",
        ]}}

        result = _parse_eastmoney_trends(
            trends, "600298.SH", "20260730", "2026-07-30 14:30:00"
        )

        self.assertEqual(result["trade_time"].dt.strftime("%H:%M").tolist(), ["14:29"])
        self.assertEqual(result.iloc[0]["close"], 10.2)

        klines = {"data": {"klines": [
            "2026-07-29 14:00,9.8,9.9,10,9.7,1000,9900",
            "2026-07-30 14:00,10,10.2,10.3,9.9,2000,20400",
        ]}}
        east = _parse_eastmoney_klines(
            klines, "600298.SH", "20260730", "2026-07-30 14:30:00"
        )
        self.assertEqual(len(east), 2)
        self.assertEqual(east.iloc[-1]["close"], 10.2)

        sina = (
            'var x=([{"day":"2026-07-30 14:00:00","open":"10",'
            '"high":"10.3","low":"9.9","close":"10.2",'
            '"volume":"2000","amount":"20400"}]);'
        )
        parsed_sina = _parse_sina_klines(
            sina, "600298.SH", "20260730", "2026-07-30 14:30:00"
        )
        self.assertEqual(parsed_sina.iloc[0]["close"], 10.2)
```

- [ ] **Step 2: Run the parser tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_realtime_market_source -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'realtime_market_source'`.

- [ ] **Step 3: Implement normalized result types and parsers**

Create `realtime_market_source.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import subprocess
import time
from typing import Any, Callable
from urllib.parse import urlencode

import pandas as pd


MINUTE_COLUMNS = [
    "ts_code", "trade_time", "open", "close",
    "high", "low", "vol", "amount",
]


@dataclass
class MinuteLoadResult:
    bars: pd.DataFrame
    source: str
    warnings: list[str]


def _eastmoney_secid(ts_code: str) -> str | None:
    text = str(ts_code or "").upper()
    if text.endswith(".SH") and text[:6].isdigit():
        return f"1.{text[:6]}"
    if text.endswith(".SZ") and text[:6].isdigit():
        return f"0.{text[:6]}"
    return None


def _sina_symbol(ts_code: str) -> str | None:
    text = str(ts_code or "").upper()
    if text.endswith(".SH") and text[:6].isdigit():
        return f"sh{text[:6]}"
    if text.endswith(".SZ") and text[:6].isdigit():
        return f"sz{text[:6]}"
    return None


def _normalize_minutes(
    records: list[dict[str, Any]],
    ts_code: str,
    end_datetime: str,
) -> pd.DataFrame:
    frame = pd.DataFrame(records, columns=MINUTE_COLUMNS)
    if frame.empty:
        return frame
    frame["ts_code"] = str(ts_code)
    frame["trade_time"] = pd.to_datetime(frame["trade_time"], errors="coerce")
    for column in ("open", "close", "high", "low", "vol", "amount"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["trade_time", "close"])
    frame = frame[frame["trade_time"] <= pd.Timestamp(end_datetime)]
    return (
        frame.drop_duplicates(["ts_code", "trade_time"], keep="last")
        .sort_values("trade_time")
        .reset_index(drop=True)
        .reindex(columns=MINUTE_COLUMNS)
    )
```

Implement the snapshot parser:

```python
def _parse_eastmoney_snapshot(payload: Any, trade_date: str) -> pd.DataFrame:
    data = payload.get("data") if isinstance(payload, dict) else None
    rows = data.get("diff") if isinstance(data, dict) else None
    records = []
    for row in rows if isinstance(rows, list) else []:
        code = str(row.get("f12") or "")
        suffix = {1: "SH", 0: "SZ"}.get(row.get("f13"))
        if len(code) != 6 or not code.isdigit() or suffix is None:
            continue
        records.append({
            "ts_code": f"{code}.{suffix}",
            "trade_date": str(trade_date),
            "name": row.get("f14"),
            "industry": row.get("f100"),
            "open": row.get("f17"),
            "close": row.get("f2"),
            "high": row.get("f15"),
            "low": row.get("f16"),
            "pre_close": row.get("f18"),
            "pct_chg": row.get("f3"),
            "turnover_rate": row.get("f8"),
            "volume_ratio": row.get("f10"),
            "vol": row.get("f5"),
            "amount": row.get("f6"),
        })
    frame = pd.DataFrame(records)
    if frame.empty:
        return frame
    numeric = [
        "open", "close", "high", "low", "pre_close", "pct_chg",
        "turnover_rate", "volume_ratio", "vol", "amount",
    ]
    frame[numeric] = frame[numeric].apply(pd.to_numeric, errors="coerce")
    return frame.dropna(subset=["ts_code", "close"]).reset_index(drop=True)
```

Implement minute parsers:

```python
def _parse_eastmoney_trends(payload, ts_code, trade_date, end_datetime):
    data = payload.get("data") if isinstance(payload, dict) else None
    trends = data.get("trends") if isinstance(data, dict) else None
    records = []
    for text in trends if isinstance(trends, list) else []:
        parts = str(text).split(",")
        if len(parts) < 7:
            continue
        records.append({
            "ts_code": ts_code, "trade_time": parts[0],
            "open": parts[1], "close": parts[2], "high": parts[3],
            "low": parts[4], "vol": parts[5], "amount": parts[6],
        })
    return _normalize_minutes(records, ts_code, end_datetime)


def _parse_eastmoney_klines(payload, ts_code, trade_date, end_datetime):
    data = payload.get("data") if isinstance(payload, dict) else None
    klines = data.get("klines") if isinstance(data, dict) else None
    records = []
    for text in klines if isinstance(klines, list) else []:
        parts = str(text).split(",")
        if len(parts) < 7:
            continue
        records.append({
            "ts_code": ts_code, "trade_time": parts[0],
            "open": parts[1], "close": parts[2], "high": parts[3],
            "low": parts[4], "vol": parts[5], "amount": parts[6],
        })
    return _normalize_minutes(records, ts_code, end_datetime)


def _parse_sina_klines(text, ts_code, trade_date, end_datetime):
    start = str(text or "").find("[")
    end = str(text or "").rfind("]")
    if start < 0 or end < start:
        return pd.DataFrame(columns=MINUTE_COLUMNS)
    try:
        payload = json.loads(str(text)[start:end + 1])
    except (TypeError, ValueError):
        return pd.DataFrame(columns=MINUTE_COLUMNS)
    records = [{
        "ts_code": ts_code,
        "trade_time": row.get("day"),
        "open": row.get("open"),
        "close": row.get("close"),
        "high": row.get("high"),
        "low": row.get("low"),
        "vol": row.get("volume"),
        "amount": row.get("amount"),
    } for row in payload if isinstance(row, dict)]
    return _normalize_minutes(records, ts_code, end_datetime)
```

Target-date validation remains in the orchestration task so historical 60-minute rows are
retained.

- [ ] **Step 4: Run the parser tests to verify GREEN**

Run:

```bash
python3 -m unittest tests.test_realtime_market_source -v
```

Expected: all Task 1 tests pass.

- [ ] **Step 5: Review without committing**

Run:

```bash
git diff --check -- realtime_market_source.py tests/test_realtime_market_source.py
```

Expected: no output. Do not stage or commit because the shared workspace contains overlapping user changes.

### Task 2: Implement external fetchers, cache, and the minute fallback chain

**Files:**

- Modify: `realtime_market_source.py`
- Modify: `tests/test_realtime_market_source.py`

**Interfaces:**

- Consumes: Task 1 parsers and `MinuteLoadResult`
- Produces: `load_eastmoney_market_snapshot(trade_date: str) -> tuple[pd.DataFrame, str | None]`
- Produces: `_fetch_eastmoney_minutes(ts_code: str, start_datetime: str, end_datetime: str, freq: str, trade_date: str) -> tuple[pd.DataFrame, str | None]`
- Produces: `_fetch_sina_minutes(ts_code: str, start_datetime: str, end_datetime: str, freq: str, trade_date: str) -> tuple[pd.DataFrame, str | None]`
- Produces: `load_minutes_with_fallback(ts_code: str, start_datetime: str, end_datetime: str, freq: str, trade_date: str, primary_loader: Callable[..., pd.DataFrame]) -> MinuteLoadResult`
- Produces: `clear_realtime_source_caches() -> None` for production cache lifecycle and test setup

- [ ] **Step 1: Add failing orchestration and cache tests**

Add tests that patch module-level fetch helpers rather than network calls:

```python
import json
from unittest.mock import patch
import pandas as pd

from realtime_market_source import (
    _fetch_eastmoney_minutes,
    _run_curl,
    clear_realtime_source_caches,
    load_minutes_with_fallback,
)


def minute_frame(day="2026-07-30", rows=35):
    return pd.DataFrame([
        {
            "ts_code": "600298.SH",
            "trade_time": f"{day} {9 + index // 5:02d}:{30 + index % 5:02d}:00",
            "open": 10, "close": 10 + index / 100,
            "high": 10.5, "low": 9.8, "vol": 1000, "amount": 10000,
        }
        for index in range(rows)
    ])


class RealtimeMarketSourceTests(unittest.TestCase):
    def setUp(self):
        clear_realtime_source_caches()

    @patch("realtime_market_source._fetch_sina_minutes")
    @patch("realtime_market_source._fetch_eastmoney_minutes")
    def test_valid_tushare_minutes_remain_primary(self, eastmoney, sina):
        result = load_minutes_with_fallback(
            "600298.SH", "2026-05-20 09:30:00",
            "2026-07-30 14:30:00", "60min", "20260730",
            primary_loader=lambda *args, **kwargs: minute_frame(),
        )
        self.assertEqual(result.source, "tushare")
        self.assertEqual(len(result.bars), 35)
        eastmoney.assert_not_called()
        sina.assert_not_called()

    @patch("realtime_market_source._fetch_sina_minutes")
    @patch("realtime_market_source._fetch_eastmoney_minutes")
    def test_empty_tushare_uses_eastmoney(self, eastmoney, sina):
        eastmoney.return_value = (minute_frame(), None)
        result = load_minutes_with_fallback(
            "600298.SH", "2026-05-20 09:30:00",
            "2026-07-30 14:30:00", "60min", "20260730",
            primary_loader=lambda *args, **kwargs: pd.DataFrame(),
        )
        self.assertEqual(result.source, "eastmoney_fallback")
        sina.assert_not_called()

    @patch("realtime_market_source._fetch_sina_minutes")
    @patch("realtime_market_source._fetch_eastmoney_minutes")
    def test_eastmoney_failure_uses_sina(self, eastmoney, sina):
        eastmoney.return_value = (pd.DataFrame(), "东方财富请求失败")
        sina.return_value = (minute_frame(), None)
        result = load_minutes_with_fallback(
            "600298.SH", "2026-05-20 09:30:00",
            "2026-07-30 14:30:00", "60min", "20260730",
            primary_loader=lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("Tushare限流")
            ),
        )
        self.assertEqual(result.source, "sina_fallback")
        self.assertIn("Tushare限流", "；".join(result.warnings))

    @patch("realtime_market_source._fetch_sina_minutes")
    @patch("realtime_market_source._fetch_eastmoney_minutes")
    def test_old_one_minute_rows_are_rejected_by_all_sources(self, eastmoney, sina):
        old = minute_frame(day="2026-07-29", rows=5)
        eastmoney.return_value = (old, None)
        sina.return_value = (old, None)
        result = load_minutes_with_fallback(
            "600298.SH", "2026-07-30 14:25:00",
            "2026-07-30 14:30:00", "1min", "20260730",
            primary_loader=lambda *args, **kwargs: old,
        )
        self.assertTrue(result.bars.empty)
        self.assertEqual(result.source, "unavailable")
```

Add a subprocess contract test:

```python
@patch("realtime_market_source.subprocess.run")
def test_run_curl_uses_bounded_retry_contract(self, run):
    run.return_value.stdout = "{}"

    self.assertEqual(_run_curl("https://example.invalid/data"), "{}")

    args = run.call_args.args[0]
    self.assertEqual(args[:3], ["curl", "-fsSL", "--max-time"])
    self.assertIn("6", args)
    self.assertIn("--retry", args)
    self.assertIn("2", args)
    self.assertEqual(run.call_args.kwargs["timeout"], 15)
    self.assertTrue(run.call_args.kwargs["check"])
```

Add a cache test:

```python
@patch("realtime_market_source._run_curl")
def test_eastmoney_minute_success_uses_short_cache(self, run_curl):
    run_curl.return_value = json.dumps({"data": {"trends": [
        "2026-07-30 14:29,10,10.2,10.3,9.9,200,2040,10"
    ]}})

    first, first_error = _fetch_eastmoney_minutes(
        "600298.SH", "2026-07-30 14:25:00",
        "2026-07-30 14:30:00", "1min", "20260730",
    )
    second, second_error = _fetch_eastmoney_minutes(
        "600298.SH", "2026-07-30 14:25:00",
        "2026-07-30 14:30:00", "1min", "20260730",
    )

    self.assertIsNone(first_error)
    self.assertIsNone(second_error)
    pd.testing.assert_frame_equal(first, second)
    self.assertEqual(run_curl.call_count, 1)
```

- [ ] **Step 2: Run the new tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_realtime_market_source -v
```

Expected: FAIL because fetchers and fallback orchestration do not exist.

- [ ] **Step 3: Implement fetchers and validation**

Add constants:

```python
EASTMONEY_SNAPSHOT_URL = "https://push2.eastmoney.com/api/qt/clist/get"
EASTMONEY_TRENDS_URL = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
SINA_KLINE_URL = (
    "https://quotes.sina.cn/cn/api/jsonp_v2.php/"
    "var%20x=/CN_MarketDataService.getKLineData"
)
SUCCESS_TTL_SECONDS = 20
FAILURE_TTL_SECONDS = 5
```

Use a `_run_curl(url: str) -> str` helper with the exact subprocess argument list:

```python
completed = subprocess.run(
    [
        "curl", "-fsSL", "--max-time", "6", "--retry", "2",
        "--retry-all-errors", "--retry-delay", "1", url,
    ],
    capture_output=True,
    text=True,
    check=True,
    timeout=15,
)
return completed.stdout
```

`load_eastmoney_market_snapshot` must request pages until the returned `diff` page is
shorter than `pz=500`, concatenate normalized frames, de-duplicate `ts_code`, and cache
success for 20 seconds or failure for 5 seconds.

Implement `_minutes_are_usable`:

```python
def _minutes_are_usable(
    bars: pd.DataFrame,
    freq: str,
    trade_date: str,
) -> bool:
    if bars is None or bars.empty or "trade_time" not in bars:
        return False
    parsed = pd.to_datetime(bars["trade_time"], errors="coerce")
    valid_close = pd.to_numeric(bars.get("close"), errors="coerce").notna().sum()
    has_target_date = parsed.dt.strftime("%Y%m%d").eq(str(trade_date)).any()
    if freq == "1min":
        return bool(has_target_date)
    return bool(valid_close >= 35)
```

`load_minutes_with_fallback` calls the passed Tushare loader first, catches exceptions,
then calls `_fetch_eastmoney_minutes`, then `_fetch_sina_minutes`. It returns the first
usable result and accumulates concise warnings for every failed stage.

- [ ] **Step 4: Run Task 2 tests to verify GREEN**

Run:

```bash
python3 -m unittest tests.test_realtime_market_source -v
```

Expected: all parser, chain, validation and cache tests pass.

- [ ] **Step 5: Check formatting without committing**

Run:

```bash
git diff --check -- realtime_market_source.py tests/test_realtime_market_source.py
```

Expected: no output.

### Task 3: Add current snapshot fallback to realtime information

**Files:**

- Modify: `realtime_info_service.py:175-190`
- Modify: `realtime_info_service.py:498-558`
- Modify: `tests/test_realtime_info_service.py`

**Interfaces:**

- Consumes: `load_eastmoney_market_snapshot`
- Produces: `_load_realtime_market_inputs(...) -> tuple[pd.DataFrame, pd.DataFrame, str, str, bool, list[str]]`
- Produces response metadata: `snapshot_data_source`, `fallback_warnings`, honest `data_current`

- [ ] **Step 1: Write failing snapshot fallback tests**

Add two tests:

```python
@patch("realtime_info_service.load_eastmoney_market_snapshot")
@patch("realtime_info_service.load_recent_daily")
@patch("realtime_info_service.load_market_snapshot")
def test_market_inputs_use_eastmoney_when_tushare_today_is_empty(
    self, load_snapshot, load_history, eastmoney
):
    load_snapshot.return_value = pd.DataFrame()
    eastmoney.return_value = (
        pd.DataFrame([{
            "ts_code": "600298.SH", "trade_date": "20260730",
            "close": 39.12, "high": 39.80,
        }]),
        None,
    )
    load_history.return_value = pd.DataFrame()

    market, history, base_date, source, current, warnings = (
        _load_realtime_market_inputs(
            "20260730", {"data_trade_date": "20260729"}
        )
    )

    self.assertEqual(base_date, "20260730")
    self.assertEqual(source, "eastmoney_snapshot_fallback")
    self.assertTrue(current)
    self.assertEqual(market.iloc[0]["close"], 39.12)
    self.assertEqual(warnings, [])


@patch("realtime_info_service.load_eastmoney_market_snapshot")
@patch("realtime_info_service.get_trade_dates", return_value=["20260730", "20260729"])
@patch("realtime_info_service.load_recent_daily")
@patch("realtime_info_service.load_market_snapshot")
def test_market_inputs_mark_previous_snapshot_stale(
    self, load_snapshot, load_history, _dates, eastmoney
):
    previous = pd.DataFrame([{
        "ts_code": "600298.SH", "trade_date": "20260729", "close": 38.62
    }])
    load_snapshot.side_effect = [pd.DataFrame(), previous]
    eastmoney.return_value = (pd.DataFrame(), "东方财富快照超时")
    load_history.return_value = pd.DataFrame()

    result = _load_realtime_market_inputs(
        "20260730", {"data_trade_date": "20260729"}
    )

    self.assertEqual(result[2], "20260729")
    self.assertEqual(result[3], "previous_snapshot")
    self.assertFalse(result[4])
    self.assertIn("东方财富快照超时", result[5])
```

Update existing mocks and tuple assertions affected by the expanded return signature.

- [ ] **Step 2: Run focused tests to verify RED**

Run:

```bash
python3 -m unittest \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_market_inputs_use_eastmoney_when_tushare_today_is_empty \
  tests.test_realtime_info_service.RealtimeInfoServiceTests.test_market_inputs_mark_previous_snapshot_stale -v
```

Expected: FAIL because the service does not call the external snapshot loader.

- [ ] **Step 3: Implement snapshot fallback and honest metadata**

Import `load_eastmoney_market_snapshot`. Validate the Tushare snapshot with a helper that
accepts missing `trade_date` for backward compatibility but rejects a present date column
when it has no target-date rows.

Return:

```python
return (
    market,
    history,
    data_trade_date,
    snapshot_data_source,
    snapshot_data_current,
    fallback_warnings,
)
```

In `build_realtime_info`, set top-level and intraday `data_current` from
`snapshot_data_current`, not an unconditional `True`. Preserve `latest_trade_date` as the
target date and expose `snapshot_data_source` plus `fallback_warnings`.

- [ ] **Step 4: Run the realtime service tests**

Run:

```bash
python3 -m unittest tests.test_realtime_info_service -v
```

Expected: all tests pass after updating existing expected source/current fields.

- [ ] **Step 5: Check formatting without committing**

Run:

```bash
git diff --check -- realtime_info_service.py tests/test_realtime_info_service.py
```

Expected: no output.

### Task 4: Use the minute fallback chain in realtime confluence

**Files:**

- Modify: `realtime_info_service.py:251-462`
- Modify: `tests/test_realtime_info_service.py`

**Interfaces:**

- Consumes: `load_minutes_with_fallback` and `MinuteLoadResult`
- Produces per-row: `minute_data_source`, `minute_data_current`, `minute_data_warnings`
- Produces subtable: `minute_data_sources`, `fallback_warnings`

- [ ] **Step 1: Add failing realtime confluence fallback tests**

Patch `realtime_info_service.load_minutes_with_fallback` and return literal
`MinuteLoadResult` objects. Test that:

```python
self.assertEqual(row["minute_data_source"], "eastmoney_fallback")
self.assertTrue(row["minute_data_current"])
self.assertEqual(row["current_price"], 18.68)
self.assertIn("eastmoney_fallback", result["intraday"]["minute_data_sources"])
```

Add a stale-base failure case where both 60-minute and tail loaders return
`MinuteLoadResult(pd.DataFrame(), "unavailable", ["三个分钟源不可用"])` and assert:

```python
self.assertFalse(row["minute_data_current"])
self.assertIsNone(row["current_price"])
self.assertIsNone(row["day_high"])
self.assertIsNone(row["tail_return_after_1430"])
self.assertEqual(row["minute_data_source"], "unavailable")
```

- [ ] **Step 2: Run the new tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_realtime_info_service -v
```

Expected: the new source and stale-price assertions fail.

- [ ] **Step 3: Route 60-minute and tail loading through the chain**

Replace direct `_cached_minute_bars` calls inside the realtime-only loader with:

```python
load_minutes_with_fallback(
    ts_code,
    start_datetime,
    end_datetime,
    freq,
    trade_date,
    primary_loader=_cached_minute_bars,
)
```

Keep bars and source metadata together per code:

```python
bars_by_code[ts_code] = {
    "60m": result.bars,
    "60m_source": result.source,
    "warnings": list(result.warnings),
}
```

Tail results append `tail_1m`, prefer the tail source for the latest-price source, and merge
warnings without duplicates. When the base snapshot is stale and no current-date minute
data exists, explicitly set `current_price`, `day_high`, `close`, `high`,
`tail_return_after_1430`, `tail_strength_score`, `tail_volume_ratio`, and
`tail_close_position` to `None` in the response row.

- [ ] **Step 4: Run focused and neighboring tests**

Run:

```bash
python3 -m unittest tests.test_realtime_info_service -v
python3 -m unittest tests.test_realtime_market_source -v
```

Expected: both modules pass.

- [ ] **Step 5: Check formatting without committing**

Run:

```bash
git diff --check -- realtime_info_service.py tests/test_realtime_info_service.py
```

Expected: no output.

### Task 5: Allow the realtime overnight subtable to share runtime market inputs

**Files:**

- Modify: `overnight_monitor_service.py:110-145`
- Modify: `overnight_monitor_service.py:475-575`
- Modify: `tests/test_overnight_monitor_service.py`

**Interfaces:**

- Consumes callable:
  `minute_loader(ts_code: str, start_datetime: str, end_datetime: str, freq: str, trade_date: str) -> MinuteLoadResult`
- Produces optional `build_overnight_monitor` parameters:
  `market_override`, `history_override`, `trade_date_override`, `minute_loader`,
  `source_metadata`
- Default behavior remains unchanged.

- [ ] **Step 1: Write failing override-isolation test**

Add a test that supplies current market/history and a fake loader:

```python
def fake_loader(ts_code, start_datetime, end_datetime, freq, trade_date):
    bars = (
        build_60min_bars(ts_code, water_macd_kdj_continuation_closes())
        if freq == "60min"
        else build_tail_1min_bars(
            ts_code,
            [10, 10, 10, 10, 10, 10.1, 10.2],
            [1000, 1000, 1000, 1000, 1000, 2400, 3200],
        )
    )
    return MinuteLoadResult(bars, "eastmoney_fallback", [])


@patch("overnight_monitor_service._load_overnight_inputs")
def test_runtime_overrides_bypass_default_input_loader(self, default_loader):
    result = build_overnight_monitor(
        limit=10,
        max_fetch=10,
        now=datetime(2026, 7, 30, 14, 50),
        market_override=market_fixture(),
        history_override=pd.DataFrame(),
        trade_date_override="20260730",
        minute_loader=fake_loader,
        source_metadata={
            "latest_trade_date": "20260730",
            "data_current": True,
            "data_source": "eastmoney_snapshot_fallback",
        },
    )
    default_loader.assert_not_called()
    self.assertEqual(result["data_source"], "eastmoney_snapshot_fallback")
    self.assertIn("eastmoney_fallback", result["minute_data_sources"])
```

Also add a legacy test calling `build_overnight_monitor(...)` without new arguments and
asserting `_load_overnight_inputs` is still called.

- [ ] **Step 2: Run focused tests to verify RED**

Run:

```bash
python3 -m unittest \
  tests.test_overnight_monitor_service.OvernightMonitorServiceTests.test_runtime_overrides_bypass_default_input_loader -v
```

Expected: FAIL with unexpected keyword argument `market_override`.

- [ ] **Step 3: Implement optional runtime inputs and minute loading**

Extend the signature with keyword-only arguments after `now`:

```python
def build_overnight_monitor(
    limit: int = 10,
    max_fetch: int = 30,
    max_leaders: int | None = None,
    now: datetime | None = None,
    *,
    market_override: pd.DataFrame | None = None,
    history_override: pd.DataFrame | None = None,
    trade_date_override: str | None = None,
    minute_loader: Callable[..., MinuteLoadResult] | None = None,
    source_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
```

Only use overrides when `market_override is not None` and `trade_date_override` is set.
Skip preload/result caches for override calls because their key does not encode the supplied
market context. Define a local loader wrapper: use the injected result when present,
otherwise wrap `_cached_minute_bars` as source `tushare`.

Pass the wrapper into `_build_row` so 60-minute and 1-minute data use the same chain.
Collect row sources into sorted `minute_data_sources`, attach per-row
`minute_data_source/minute_data_warnings`, and preserve the legacy path when arguments are
omitted.

- [ ] **Step 4: Run overnight monitor tests**

Run:

```bash
python3 -m unittest tests.test_overnight_monitor_service -v
```

Expected: all old and new tests pass.

- [ ] **Step 5: Check formatting without committing**

Run:

```bash
git diff --check -- overnight_monitor_service.py tests/test_overnight_monitor_service.py
```

Expected: no output.

### Task 6: Connect both realtime information subtables and expose source metadata

**Files:**

- Modify: `realtime_info_service.py:498-558`
- Modify: `tests/test_realtime_info_service.py`
- Verify: `tests/test_realtime_info_api.py`

**Interfaces:**

- Consumes: Task 3 market context, Task 4 minute loader, Task 5 overnight overrides
- Produces a single response whose `intraday` and `overnight` sections use the same target
  trade date, market DataFrame, history DataFrame and minute fallback chain.

- [ ] **Step 1: Write a failing integration test for both subtables**

In the existing `test_realtime_info_syncs_current_market_and_enriches_both_sections`, update
the overnight call expectation to include:

```python
build_overnight_monitor.assert_called_once()
kwargs = build_overnight_monitor.call_args.kwargs
self.assertIs(kwargs["market_override"], market)
self.assertIs(kwargs["history_override"], history)
self.assertEqual(kwargs["trade_date_override"], "20260730")
self.assertTrue(callable(kwargs["minute_loader"]))
self.assertEqual(
    kwargs["source_metadata"]["data_source"],
    "eastmoney_snapshot_fallback",
)
```

Add a case in which the snapshot loader returns Eastmoney data and both mocked subtable
builders return `minute_data_sources=["eastmoney_fallback"]`; assert both sections and the
top-level response preserve this source and target date.

- [ ] **Step 2: Run the integration tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_realtime_info_service -v
```

Expected: overnight call keyword assertions fail.

- [ ] **Step 3: Pass the unified context to the overnight builder**

Create the closure:

```python
def realtime_minute_loader(ts_code, start, end, freq, trade_date):
    return load_minutes_with_fallback(
        ts_code, start, end, freq, trade_date,
        primary_loader=_cached_minute_bars,
    )
```

Pass `market`, `history`, `intraday_trade_date`, the closure, and source metadata into
`build_overnight_monitor`. Merge warnings at the top level without duplicates and keep
`data_current` tied to the snapshot date. Do not change the FastAPI route or Java controller.

- [ ] **Step 4: Run service and API tests**

Run:

```bash
python3 -m unittest \
  tests.test_realtime_market_source \
  tests.test_realtime_info_service \
  tests.test_overnight_monitor_service \
  tests.test_realtime_info_api -v
```

Expected: all tests pass.

- [ ] **Step 5: Check formatting without committing**

Run:

```bash
git diff --check -- \
  realtime_market_source.py \
  realtime_info_service.py \
  overnight_monitor_service.py \
  tests/test_realtime_market_source.py \
  tests/test_realtime_info_service.py \
  tests/test_overnight_monitor_service.py
```

Expected: no output.

### Task 7: Verify live adapters, full regression, and module isolation

**Files:**

- Verify: `realtime_market_source.py`
- Verify: `realtime_info_service.py`
- Verify: `overnight_monitor_service.py`
- Verify unchanged: `morning_follow_service.py`
- Verify unchanged: `tests/test_morning_follow_service.py`

**Interfaces:**

- Consumes all previous tasks.
- Produces verification evidence only; no production changes unless a test exposes a defect.

- [ ] **Step 1: Run mocked fallback regression tests**

Run:

```bash
python3 -m unittest \
  tests.test_realtime_market_source \
  tests.test_realtime_info_service \
  tests.test_overnight_monitor_service \
  tests.test_realtime_info_api -v
```

Expected: all tests pass.

- [ ] **Step 2: Run the full Python suite**

Run:

```bash
python3 -m unittest discover -s tests -q
```

If sandboxing blocks Tushare initialization at `/root/tk.csv`, rerun the identical command
with approved sandbox escalation. Expected: exit code 0 and `OK`.

- [ ] **Step 3: Run Java and frontend regression checks**

Run:

```bash
mvn test
```

Working directory: `quantServer/quantServer`.

Then run:

```bash
node quantClient/realtime-info-utils.test.js
node quantClient/morning-follow-utils.test.js
node quantClient/morning-follow-layout.test.js
node --check quantClient/main.js
```

Expected: Maven build succeeds and all Node commands exit 0.

- [ ] **Step 4: Perform a bounded live-source smoke test**

Run a one-symbol smoke test for `600298.SH` that calls:

```python
load_eastmoney_market_snapshot("20260730")
load_minutes_with_fallback(
    "600298.SH",
    "2026-05-20 09:30:00",
    "2026-07-30 14:59:00",
    "60min",
    "20260730",
    primary_loader=lambda *args, **kwargs: pd.DataFrame(),
)
```

Print only source, row count, first/last timestamp and error summary; do not print tokens or
full payloads. Expected: at least one external source returns a non-empty target-date result.
If public providers are temporarily unavailable, keep mocked tests as the deterministic gate
and report the live failure separately.

- [ ] **Step 5: Verify external-morning isolation**

Run:

```bash
sha256sum morning_follow_service.py tests/test_morning_follow_service.py
```

Expected:

```text
67515ff0c8396a5b1a1a023cecb0253d80ec31c8dcba918dfc338a5f8cc8d415  morning_follow_service.py
c14f89a3b7978335e18e918f6c816e40ef76e382c167aec3936a44c624ab60c3  tests/test_morning_follow_service.py
```

- [ ] **Step 6: Review the exact implementation state**

Run:

```bash
git diff --check -- \
  realtime_market_source.py \
  realtime_info_service.py \
  overnight_monitor_service.py \
  tests/test_realtime_market_source.py \
  tests/test_realtime_info_service.py \
  tests/test_overnight_monitor_service.py
git status --short \
  realtime_market_source.py \
  realtime_info_service.py \
  overnight_monitor_service.py \
  tests/test_realtime_market_source.py \
  tests/test_realtime_info_service.py \
  tests/test_overnight_monitor_service.py
```

Expected: only the six implementation/test files listed above are attributable to this task.
Leave all changes unstaged and uncommitted as required by the shared dirty workspace constraint.
