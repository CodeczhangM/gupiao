# Tomorrow Sector Rotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an independent `明日轮动` module that ranks tomorrow's likely sector money-flow directions and recommends sorted attack/catch-up stocks for each sector.

**Architecture:** Add a focused Python service for sector rotation scoring, expose it through FastAPI and the existing Spring proxy, then render it as a separate Vue tab. The scoring service reuses cached market snapshots, recent daily history, and `moneyflow_ind_dc` sector moneyflow data; it does not introduce new external data sources or database tables.

**Tech Stack:** Python 3, pandas, FastAPI, unittest, Java Spring Boot `RestClient` and MockMvc, Vue via `quantClient/main.js`, CommonJS browser utilities.

## Global Constraints

- Use an independent frontend tab named `明日轮动`.
- Return two sorted 0-100 sector lists: `continuation_inflow` and `rotation_rebound`.
- Use the latest two complete trading days as the primary signal and 5/20 trading days as trend context.
- Every sector row must include `attack_leaders` and `catchup_candidates`, each sorted by its own score.
- Do not use news, research reports, social media, AI sentiment, or deterministic buy/sell language.
- When moneyflow data is incomplete, degrade confidence and return warnings instead of failing hard.
- Do not modify existing `板块潜力`, `自由复盘`, or realtime modules except to add navigation or reuse utilities.
- Follow TDD: every production change in this plan has a failing test first.

---

## File Structure

- Create `sector_rotation_service.py`: pure scoring and orchestration for tomorrow sector rotation. It owns normalization, sector aggregation, moneyflow merge, confidence/warnings, sector score calculation, and stock recommendation.
- Create `tests/test_sector_rotation_service.py`: unit tests for service scoring, ordering, degradation, and stock selection.
- Modify `app.py`: import the service and expose `GET /api/sector-rotation/tomorrow`.
- Create `tests/test_sector_rotation_api.py`: direct FastAPI route function tests for forwarding and error mapping.
- Modify `quantServer/quantServer/src/main/java/com/codec/quantserver/service/QuantPythonClient.java`: add a `sectorRotationTomorrow` proxy method.
- Modify `quantServer/quantServer/src/main/java/com/codec/quantserver/controller/QuantController.java`: add `GET /api/quant/sector-rotation/tomorrow`.
- Modify `quantServer/quantServer/src/test/java/com/codec/quantserver/controller/QuantControllerTest.java`: controller forwarding regression.
- Create `quantClient/sector-rotation-utils.js`: formatting and view-model helpers for the new page.
- Create `quantClient/sector-rotation-utils.test.js`: utility regression tests.
- Modify `quantClient/index.html`: add navigation button and the `明日轮动` page markup.
- Modify `quantClient/main.js`: add state, API loading, page title branch, refresh behavior, and view methods.
- Modify `quantClient/styles.css`: add compact dashboard styles for the new module.
- Create `quantClient/sector-rotation-layout.test.js`: static layout regression for navigation and key page labels.

---

### Task 1: Backend Scoring Service

**Files:**
- Create: `sector_rotation_service.py`
- Create: `tests/test_sector_rotation_service.py`

**Interfaces:**
- Consumes:
  - `market_cache.get_complete_dates(limit: int) -> list[str]`
  - `market_cache.load_market_snapshot(trade_date: str) -> pandas.DataFrame`
  - `market_cache.load_recent_daily(end_trade_date: str, n: int) -> pandas.DataFrame`
  - `market_cache.load_moneyflow(trade_date: str) -> pandas.DataFrame`
- Produces:
  - `build_tomorrow_sector_rotation(trade_date: str | None = None, limit: int = 10, stocks_per_sector: int = 5) -> dict[str, Any]`
  - Return keys: `trade_date`, `lookback_trade_dates`, `context_trade_dates`, `moneyflow_trade_dates`, `source`, `warnings`, `continuation_inflow`, `rotation_rebound`

- [ ] **Step 1: Write failing service tests**

Create `tests/test_sector_rotation_service.py`:

```python
import unittest
from unittest.mock import patch

import pandas as pd


def market_rows(trade_date="20260812"):
    rows = []
    for industry, base_amount, pct_values in [
        ("机器人", 300000.0, [6.2, 4.5, 2.1, 1.5, 0.8, -0.4, 3.2, 5.1]),
        ("半导体", 260000.0, [2.2, 1.8, 0.5, -0.2, 3.4, 1.1, -0.5, 0.2]),
        ("医药", 180000.0, [-1.0, -0.8, 0.2, 0.5, -0.4, 0.1, -0.2, 0.0]),
    ]:
        for index, pct_chg in enumerate(pct_values):
            code = f"{index:06d}.{industry[:1]}"
            rows.append({
                "trade_date": trade_date,
                "ts_code": code,
                "name": f"{industry}{index}",
                "industry": industry,
                "close": 10 + index,
                "pct_chg": pct_chg,
                "amount": base_amount + index * 10000,
                "turnover_rate": 2.0 + index * 0.3,
                "volume_ratio": 1.1 + index * 0.2,
                "total_mv": 800000 + index * 50000,
                "list_status": "L",
                "list_date": "20200101",
            })
    return pd.DataFrame(rows)


def history_rows():
    dates = ["20260716", "20260717", "20260720", "20260721", "20260722",
             "20260723", "20260724", "20260727", "20260728", "20260729",
             "20260730", "20260731", "20260803", "20260804", "20260805",
             "20260806", "20260807", "20260810", "20260811", "20260812"]
    current = market_rows()
    rows = []
    for _, stock in current.iterrows():
        industry = stock["industry"]
        for offset, trade_date in enumerate(dates):
            trend = {"机器人": 0.45, "半导体": 0.18, "医药": -0.08}[industry]
            close = 8 + offset * trend + (_ % 3) * 0.1
            if trade_date == "20260811":
                pct = {"机器人": 1.2, "半导体": -1.5, "医药": -0.6}[industry]
                amount = float(stock["amount"]) * {"机器人": 0.72, "半导体": 0.75, "医药": 0.9}[industry]
            elif trade_date == "20260812":
                pct = float(stock["pct_chg"])
                amount = float(stock["amount"])
            else:
                pct = trend
                amount = float(stock["amount"]) * 0.65
            rows.append({
                "trade_date": trade_date,
                "ts_code": stock["ts_code"],
                "close": close,
                "high": close * 1.03,
                "low": close * 0.97,
                "pct_chg": pct,
                "amount": amount,
                "vol": amount / 10,
            })
    return pd.DataFrame(rows)


def moneyflow_for(date):
    if date == "20260811":
        return pd.DataFrame([
            {"trade_date": date, "name": "机器人", "net_amount": 800000000, "net_amount_rate": 3.1},
            {"trade_date": date, "name": "半导体", "net_amount": -600000000, "net_amount_rate": -2.3},
            {"trade_date": date, "name": "医药", "net_amount": -300000000, "net_amount_rate": -1.1},
        ])
    return pd.DataFrame([
        {"trade_date": date, "name": "机器人", "net_amount": 1300000000, "net_amount_rate": 4.8},
        {"trade_date": date, "name": "半导体", "net_amount": 450000000, "net_amount_rate": 1.7},
        {"trade_date": date, "name": "医药", "net_amount": -260000000, "net_amount_rate": -0.9},
    ])


class SectorRotationServiceTests(unittest.TestCase):
    def test_scores_and_sorts_continuation_and_rotation_lists(self):
        import sector_rotation_service

        with patch("sector_rotation_service.get_complete_dates",
                   return_value=["20260812", "20260811", "20260810"]):
            with patch("sector_rotation_service.load_market_snapshot",
                       return_value=market_rows()):
                with patch("sector_rotation_service.load_recent_daily",
                           return_value=history_rows()):
                    with patch("sector_rotation_service.load_moneyflow",
                               side_effect=lambda date: moneyflow_for(date)):
                        result = sector_rotation_service.build_tomorrow_sector_rotation(
                            limit=5,
                            stocks_per_sector=3,
                        )

        self.assertEqual(result["trade_date"], "20260812")
        self.assertEqual(result["lookback_trade_dates"], ["20260811", "20260812"])
        self.assertEqual(result["moneyflow_trade_dates"], ["20260811", "20260812"])
        self.assertEqual(result["continuation_inflow"][0]["industry_name"], "机器人")
        self.assertEqual(result["rotation_rebound"][0]["industry_name"], "半导体")
        self.assertGreater(
            result["continuation_inflow"][0]["continuation_score"],
            result["continuation_inflow"][-1]["continuation_score"],
        )
        self.assertGreater(
            result["rotation_rebound"][0]["rotation_score"],
            result["rotation_rebound"][-1]["rotation_score"],
        )
        self.assertTrue(result["continuation_inflow"][0]["attack_leaders"])
        self.assertTrue(result["rotation_rebound"][0]["catchup_candidates"])
        attack_scores = [
            row["attack_score"]
            for row in result["continuation_inflow"][0]["attack_leaders"]
        ]
        self.assertEqual(attack_scores, sorted(attack_scores, reverse=True))
        catchup_scores = [
            row["catchup_score"]
            for row in result["rotation_rebound"][0]["catchup_candidates"]
        ]
        self.assertEqual(catchup_scores, sorted(catchup_scores, reverse=True))

    def test_missing_one_moneyflow_day_degrades_confidence(self):
        import sector_rotation_service

        def partial_moneyflow(date):
            if date == "20260811":
                return pd.DataFrame()
            return moneyflow_for(date)

        with patch("sector_rotation_service.get_complete_dates",
                   return_value=["20260812", "20260811", "20260810"]):
            with patch("sector_rotation_service.load_market_snapshot",
                       return_value=market_rows()):
                with patch("sector_rotation_service.load_recent_daily",
                           return_value=history_rows()):
                    with patch("sector_rotation_service.load_moneyflow",
                               side_effect=partial_moneyflow):
                        result = sector_rotation_service.build_tomorrow_sector_rotation()

        self.assertTrue(result["warnings"])
        self.assertIn("20260811", result["warnings"][0])
        confidences = {
            row["confidence"]
            for row in result["continuation_inflow"] + result["rotation_rebound"]
        }
        self.assertNotIn("高", confidences)

    def test_no_moneyflow_returns_empty_lists_with_warning(self):
        import sector_rotation_service

        with patch("sector_rotation_service.get_complete_dates",
                   return_value=["20260812", "20260811"]):
            with patch("sector_rotation_service.load_market_snapshot",
                       return_value=market_rows()):
                with patch("sector_rotation_service.load_recent_daily",
                           return_value=history_rows()):
                    with patch("sector_rotation_service.load_moneyflow",
                               return_value=pd.DataFrame()):
                        result = sector_rotation_service.build_tomorrow_sector_rotation()

        self.assertEqual(result["continuation_inflow"], [])
        self.assertEqual(result["rotation_rebound"], [])
        self.assertIn("暂无足够资金流数据", " ".join(result["warnings"]))

    def test_less_than_two_complete_dates_returns_empty_lists(self):
        import sector_rotation_service

        with patch("sector_rotation_service.get_complete_dates",
                   return_value=["20260812"]):
            result = sector_rotation_service.build_tomorrow_sector_rotation()

        self.assertEqual(result["continuation_inflow"], [])
        self.assertEqual(result["rotation_rebound"], [])
        self.assertIn("完整交易日不足", " ".join(result["warnings"]))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run service tests to verify RED**

Run: `python3 -m unittest tests.test_sector_rotation_service -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'sector_rotation_service'`.

- [ ] **Step 3: Implement `sector_rotation_service.py`**

Create `sector_rotation_service.py`:

```python
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from market_cache import (
    get_complete_dates,
    load_market_snapshot,
    load_moneyflow,
    load_recent_daily,
)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _normalize(value: Any, low: float, high: float) -> float:
    number = _num(value)
    if high == low:
        return 0.0
    return _clip((number - low) / (high - low) * 100.0)


def _normalize_series(series: pd.Series, low: float, high: float) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").fillna(0)
    if high == low:
        return pd.Series(0.0, index=values.index)
    return ((values - low) / (high - low) * 100.0).clip(0, 100)


def _percentile(series: pd.Series) -> pd.Series:
    if series.empty:
        return series
    return series.rank(pct=True).fillna(0) * 100.0


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    clean = frame.astype(object).where(pd.notna(frame), None)
    return clean.to_dict(orient="records")


def _empty_payload(
    trade_date: str | None,
    lookback_dates: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "trade_date": trade_date,
        "lookback_trade_dates": lookback_dates,
        "context_trade_dates": {"short": [], "medium_count": 0},
        "moneyflow_trade_dates": [],
        "source": "moneyflow_ind_dc",
        "warnings": warnings,
        "continuation_inflow": [],
        "rotation_rebound": [],
    }


def _prepare_market(market_df: pd.DataFrame) -> pd.DataFrame:
    if market_df is None or market_df.empty:
        return pd.DataFrame()
    market = market_df.copy()
    if "industry" not in market or "ts_code" not in market:
        return pd.DataFrame()
    market["industry"] = market["industry"].fillna("").astype(str)
    market = market[market["industry"] != ""].copy()
    for column in [
        "pct_chg", "amount", "turnover_rate", "volume_ratio",
        "close", "total_mv",
    ]:
        if column not in market:
            market[column] = 0
        market[column] = pd.to_numeric(market[column], errors="coerce")
    return market


def _prepare_history(history_df: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
    if history_df is None or history_df.empty:
        return pd.DataFrame()
    if "ts_code" not in history_df or "trade_date" not in history_df:
        return pd.DataFrame()
    history = history_df.copy()
    history["ts_code"] = history["ts_code"].astype(str)
    info = market[["ts_code", "industry"]].drop_duplicates("ts_code")
    history = history.merge(info, on="ts_code", how="inner")
    history["trade_date"] = history["trade_date"].astype(str)
    for column in ["pct_chg", "amount", "close", "high", "low"]:
        if column not in history:
            history[column] = history.get("close", 0)
        history[column] = pd.to_numeric(history[column], errors="coerce")
    return history


def _load_moneyflow_by_date(dates: list[str]) -> tuple[pd.DataFrame, list[str], list[str]]:
    frames = []
    used_dates = []
    warnings = []
    for trade_date in dates:
        frame = load_moneyflow(trade_date)
        if frame is None or frame.empty:
            warnings.append(f"{trade_date} 板块资金流缺失")
            continue
        current = frame.copy()
        current["trade_date"] = str(trade_date)
        if "name" not in current:
            warnings.append(f"{trade_date} 板块资金流缺少 name 字段")
            continue
        for column in ["net_amount", "net_amount_rate", "pct_change"]:
            if column not in current:
                current[column] = 0
            current[column] = pd.to_numeric(current[column], errors="coerce")
        frames.append(current)
        used_dates.append(str(trade_date))
    if not frames:
        return pd.DataFrame(), used_dates, warnings
    return pd.concat(frames, ignore_index=True), used_dates, warnings


def _sector_market_metrics(market: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    latest = market.groupby("industry").agg(
        stock_count=("ts_code", "count"),
        avg_pct_chg=("pct_chg", "mean"),
        up_ratio=("pct_chg", lambda s: float((s > 0).mean())),
        strong_ratio=("pct_chg", lambda s: float((s >= 5).mean())),
        limit_up_count=("pct_chg", lambda s: int((s >= 9.8).sum())),
        amount_sum=("amount", "sum"),
        turnover_rate=("turnover_rate", "mean"),
        volume_ratio=("volume_ratio", "mean"),
    ).reset_index()
    if history.empty:
        latest["prev_amount_sum"] = pd.NA
        latest["ret_5"] = 0
        latest["ret_20"] = 0
        latest["position_20"] = 0.5
        return latest

    dates = sorted(history["trade_date"].dropna().unique().tolist())
    prev_date = dates[-2] if len(dates) >= 2 else dates[-1]
    previous = history[history["trade_date"] == prev_date]
    prev = previous.groupby("industry").agg(
        prev_amount_sum=("amount", "sum"),
        prev_avg_pct_chg=("pct_chg", "mean"),
    ).reset_index()
    latest = latest.merge(prev, on="industry", how="left")

    for window in [5, 20]:
        window_dates = dates[-window:]
        chunk = history[history["trade_date"].isin(window_dates)]
        returns = chunk.groupby("industry").agg(
            ret=("pct_chg", "mean"),
        ).reset_index()
        returns[f"ret_{window}"] = returns["ret"] * min(len(window_dates), window)
        latest = latest.merge(
            returns[["industry", f"ret_{window}"]],
            on="industry",
            how="left",
        )

    position_rows = []
    for industry, chunk in history.groupby("industry"):
        recent = chunk.sort_values("trade_date").tail(20)
        high = _num(recent["high"].max())
        low = _num(recent["low"].min())
        latest_close = _num(recent["close"].iloc[-1]) if not recent.empty else 0
        position = 0.5 if high <= low else (latest_close - low) / (high - low)
        position_rows.append({"industry": industry, "position_20": _clip(position, 0, 1)})
    latest = latest.merge(pd.DataFrame(position_rows), on="industry", how="left")
    latest["amount_expand_rate"] = (
        latest["amount_sum"] / latest["prev_amount_sum"].replace(0, pd.NA)
    ).fillna(1.0)
    return latest


def _sector_moneyflow_metrics(moneyflow: pd.DataFrame, dates: list[str]) -> pd.DataFrame:
    if moneyflow.empty or not dates:
        return pd.DataFrame()
    prev_date = dates[0] if len(dates) > 1 else None
    today = dates[-1]
    today_df = moneyflow[moneyflow["trade_date"] == today].copy()
    today_df = today_df.rename(columns={
        "name": "industry",
        "net_amount": "net_amount_today",
        "net_amount_rate": "net_amount_rate_today",
    })
    result = today_df[[
        "industry", "net_amount_today", "net_amount_rate_today",
    ]].copy()
    if prev_date:
        prev_df = moneyflow[moneyflow["trade_date"] == prev_date].copy()
        prev_df = prev_df.rename(columns={
            "name": "industry",
            "net_amount": "net_amount_prev",
            "net_amount_rate": "net_amount_rate_prev",
        })
        result = result.merge(
            prev_df[["industry", "net_amount_prev", "net_amount_rate_prev"]],
            on="industry",
            how="left",
        )
    else:
        result["net_amount_prev"] = 0.0
        result["net_amount_rate_prev"] = 0.0
    result["net_amount_change"] = (
        result["net_amount_today"].fillna(0)
        - result["net_amount_prev"].fillna(0)
    )
    result["net_amount_rate_change"] = (
        result["net_amount_rate_today"].fillna(0)
        - result["net_amount_rate_prev"].fillna(0)
    )
    result["turned_positive"] = (
        (result["net_amount_prev"].fillna(0) < 0)
        & (result["net_amount_today"].fillna(0) > 0)
    )
    result["outflow_narrowed"] = (
        (result["net_amount_prev"].fillna(0) < 0)
        & (result["net_amount_today"].fillna(0) < 0)
        & (result["net_amount_today"].abs() < result["net_amount_prev"].abs())
    )
    result["two_day_positive"] = (
        (result["net_amount_prev"].fillna(0) > 0)
        & (result["net_amount_today"].fillna(0) > 0)
    )
    return result


def _score_sectors(sectors: pd.DataFrame, complete_moneyflow: bool) -> pd.DataFrame:
    if sectors.empty:
        return sectors
    result = sectors.copy()
    result["flow_today_pct"] = _percentile(result["net_amount_today"].fillna(0))
    result["flow_change_pct"] = _percentile(result["net_amount_change"].fillna(0))
    result["flow_rate_pct"] = _percentile(result["net_amount_rate_today"].fillna(0))
    extension_penalty = (
        ((result["position_20"].fillna(0.5) > 0.92) & (result["ret_5"].fillna(0) > 12)).astype(int) * 12
        + ((result["avg_pct_chg"].fillna(0) > 7) & (result["up_ratio"].fillna(0) < 0.45)).astype(int) * 10
    )
    weak_penalty = (
        ((result["ret_20"].fillna(0) < -8) & (result["net_amount_today"].fillna(0) <= 0)).astype(int) * 12
        + (result["amount_sum"].fillna(0) <= 0).astype(int) * 20
    )
    result["continuation_score"] = (
        result["two_day_positive"].astype(int) * 30
        + result["flow_today_pct"] * 0.20
        + result["flow_change_pct"] * 0.15
        + _normalize_series(result["avg_pct_chg"], -4, 8) * 0.08
        + _normalize_series(result["up_ratio"], 0, 1) * 0.07
        + _normalize_series(result["amount_expand_rate"], 0.6, 1.8) * 0.10
        + _normalize_series(result["strong_ratio"], 0, 0.6) * 0.10
        - extension_penalty
    ).clip(0, 100).round(2)
    result["rotation_score"] = (
        result["flow_change_pct"] * 0.30
        + (result["turned_positive"].astype(int) * 20)
        + (result["outflow_narrowed"].astype(int) * 12)
        + _normalize_series(result["avg_pct_chg"], -3, 5) * 0.08
        + _normalize_series(result["up_ratio"], 0, 1) * 0.07
        + _normalize_series(result["ret_5"], -8, 10) * 0.08
        + _normalize_series(result["ret_20"], -15, 20) * 0.07
        + (100 - _normalize_series(result["position_20"], 0.45, 1.0)) * 0.10
        + _normalize_series(result["amount_expand_rate"], 0.6, 1.6) * 0.10
        - weak_penalty
    ).clip(0, 100).round(2)
    result["confidence"] = "高" if complete_moneyflow else "中"
    result.loc[result["stock_count"].fillna(0) < 8, "confidence"] = "低"
    result["signal"] = result.apply(_sector_signal, axis=1)
    result["reason"] = result.apply(_sector_reasons, axis=1)
    return result


def _sector_signal(row: pd.Series) -> str:
    if bool(row.get("two_day_positive")) and _num(row.get("net_amount_change")) > 0:
        return "连续流入放量扩散"
    if bool(row.get("turned_positive")):
        return "资金由流出转流入"
    if bool(row.get("outflow_narrowed")):
        return "流出收窄观察"
    return "资金改善观察"


def _sector_reasons(row: pd.Series) -> list[str]:
    reasons = []
    if bool(row.get("two_day_positive")):
        reasons.append("两日连续净流入")
    if bool(row.get("turned_positive")):
        reasons.append("由流出转流入")
    if _num(row.get("net_amount_change")) > 0:
        reasons.append("今日资金改善")
    if _num(row.get("amount_expand_rate"), 1) >= 1.2:
        reasons.append("成交额放大")
    if _num(row.get("up_ratio")) >= 0.6:
        reasons.append("上涨家数扩散")
    if not reasons:
        reasons.append("资金和行情信号偏观察")
    return reasons


def _stock_scores(
    market: pd.DataFrame,
    history: pd.DataFrame,
    industry: str,
    sector_row: pd.Series,
    stocks_per_sector: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    stocks = market[market["industry"] == industry].copy()
    if stocks.empty:
        return [], []
    stocks["amount_rank_score"] = _percentile(stocks["amount"].fillna(0))
    stocks["pct_rank_score"] = _percentile(stocks["pct_chg"].fillna(0))
    stocks["activity_score"] = (
        stocks["volume_ratio"].fillna(0).map(lambda v: _normalize(v, 0.8, 2.8)) * 0.5
        + stocks["turnover_rate"].fillna(0).map(lambda v: _normalize(v, 0.5, 8)) * 0.5
    )
    rel5 = _relative_five_day_strength(history, stocks)
    stocks = stocks.merge(rel5, on="ts_code", how="left")
    stocks["position_score"] = (
        100 - _normalize_series(stocks["pct_chg"], 0, 10)
    ).clip(0, 100)
    stocks["attack_score"] = (
        stocks["pct_rank_score"] * 0.25
        + stocks["amount_rank_score"] * 0.20
        + stocks["activity_score"] * 0.15
        + stocks["relative_strength_5"].fillna(50) * 0.15
        + (stocks["pct_chg"].fillna(0) >= 5).astype(int) * 15
        + stocks["amount_rank_score"] * 0.10
        - (stocks["pct_chg"].fillna(0) >= 9.5).astype(int) * 8
    ).clip(0, 100).round(2)
    stocks["catchup_score"] = (
        _normalize(sector_row.get("net_amount_change"), -500000000, 1000000000) * 0.20
        + stocks["position_score"] * 0.20
        + stocks["activity_score"] * 0.15
        + _normalize_series(stocks["pct_chg"], -2, 4) * 0.15
        + (100 - stocks["relative_strength_5"].fillna(50)) * 0.15
        + stocks["amount_rank_score"] * 0.10
        + 50 * 0.05
    ).clip(0, 100).round(2)
    attack = _stock_records(stocks.sort_values("attack_score", ascending=False), "attack_score", stocks_per_sector)
    catchup_pool = stocks[stocks["pct_chg"].fillna(0) < 6].copy()
    catchup = _stock_records(catchup_pool.sort_values("catchup_score", ascending=False), "catchup_score", stocks_per_sector)
    return attack, catchup


def _relative_five_day_strength(history: pd.DataFrame, stocks: pd.DataFrame) -> pd.DataFrame:
    if history.empty:
        return pd.DataFrame({"ts_code": stocks["ts_code"], "relative_strength_5": 50.0})
    dates = sorted(history["trade_date"].dropna().unique().tolist())[-5:]
    recent = history[history["trade_date"].isin(dates)].copy()
    if recent.empty:
        return pd.DataFrame({"ts_code": stocks["ts_code"], "relative_strength_5": 50.0})
    strength = recent.groupby("ts_code").agg(ret_5_stock=("pct_chg", "sum")).reset_index()
    strength["relative_strength_5"] = _percentile(strength["ret_5_stock"])
    return strength[["ts_code", "relative_strength_5"]]


def _stock_records(stocks: pd.DataFrame, score_key: str, limit: int) -> list[dict[str, Any]]:
    rows = []
    for row in stocks.head(limit).to_dict("records"):
        reasons = []
        if _num(row.get("pct_chg")) > 0:
            reasons.append(f"涨幅{_num(row.get('pct_chg')):.2f}%")
        if _num(row.get("volume_ratio")) >= 1.5:
            reasons.append("量比活跃")
        if _num(row.get("amount")) > 0:
            reasons.append("成交额靠前")
        rows.append({
            "ts_code": row.get("ts_code"),
            "name": row.get("name"),
            "industry": row.get("industry"),
            "close": row.get("close"),
            "pct_chg": row.get("pct_chg"),
            "amount": row.get("amount"),
            "turnover_rate": row.get("turnover_rate"),
            "volume_ratio": row.get("volume_ratio"),
            score_key: row.get(score_key),
            "free_review_score": row.get("free_review_score"),
            "reason": "、".join(reasons) if reasons else "板块内相对占优",
            "pool_tag": "",
        })
    return rows


def _sector_output(row: pd.Series, rank: int) -> dict[str, Any]:
    metrics = {
        "net_amount_today": row.get("net_amount_today"),
        "net_amount_prev": row.get("net_amount_prev"),
        "net_amount_change": row.get("net_amount_change"),
        "net_amount_rate_today": row.get("net_amount_rate_today"),
        "avg_pct_chg": row.get("avg_pct_chg"),
        "up_ratio": row.get("up_ratio"),
        "amount_expand_rate": row.get("amount_expand_rate"),
        "ret_5": row.get("ret_5"),
        "ret_20": row.get("ret_20"),
        "position_20": row.get("position_20"),
    }
    return {
        "rank": rank,
        "industry_name": row.get("industry"),
        "continuation_score": row.get("continuation_score"),
        "rotation_score": row.get("rotation_score"),
        "confidence": row.get("confidence"),
        "signal": row.get("signal"),
        "reason": row.get("reason"),
        "metrics": metrics,
        "attack_leaders": row.get("attack_leaders") or [],
        "catchup_candidates": row.get("catchup_candidates") or [],
    }


def build_tomorrow_sector_rotation(
    trade_date: str | None = None,
    limit: int = 10,
    stocks_per_sector: int = 5,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 30))
    stocks_per_sector = max(1, min(int(stocks_per_sector), 10))
    complete_dates = [str(date) for date in get_complete_dates(20)]
    if trade_date:
        trade_date = str(trade_date)
        usable_dates = [date for date in complete_dates if date <= trade_date]
    else:
        usable_dates = complete_dates
    if len(usable_dates) < 2:
        return _empty_payload(
            trade_date or (usable_dates[0] if usable_dates else None),
            usable_dates[:2],
            ["完整交易日不足 2 天，无法生成明日轮动榜"],
        )
    ordered_dates = list(reversed(usable_dates))
    current = trade_date or usable_dates[0]
    lookback_dates = ordered_dates[-2:]
    short_context = ordered_dates[-5:]
    market = _prepare_market(load_market_snapshot(current))
    history = _prepare_history(load_recent_daily(current, n=20), market)
    if market.empty:
        return _empty_payload(current, lookback_dates, ["市场快照为空，无法生成明日轮动榜"])

    moneyflow, moneyflow_dates, warnings = _load_moneyflow_by_date(lookback_dates)
    if moneyflow.empty:
        payload = _empty_payload(current, lookback_dates, warnings + ["暂无足够资金流数据"])
        payload["context_trade_dates"] = {
            "short": short_context,
            "medium_count": len(ordered_dates[-20:]),
        }
        return payload

    market_metrics = _sector_market_metrics(market, history)
    moneyflow_metrics = _sector_moneyflow_metrics(moneyflow, moneyflow_dates)
    sectors = market_metrics.merge(moneyflow_metrics, on="industry", how="inner")
    sectors = sectors[sectors["stock_count"] >= 8].copy()
    complete_moneyflow = len(moneyflow_dates) >= 2
    sectors = _score_sectors(sectors, complete_moneyflow)
    if sectors.empty:
        return _empty_payload(current, lookback_dates, warnings + ["无满足样本数量的板块"])

    attack_lists = []
    catchup_lists = []
    for _, row in sectors.iterrows():
        attack, catchup = _stock_scores(
            market,
            history,
            str(row["industry"]),
            row,
            stocks_per_sector,
        )
        attack_lists.append(attack)
        catchup_lists.append(catchup)
    sectors["attack_leaders"] = attack_lists
    sectors["catchup_candidates"] = catchup_lists

    continuation = sectors.sort_values(
        "continuation_score", ascending=False
    ).head(limit).reset_index(drop=True)
    rotation = sectors.sort_values(
        "rotation_score", ascending=False
    ).head(limit).reset_index(drop=True)

    return {
        "trade_date": current,
        "lookback_trade_dates": lookback_dates,
        "context_trade_dates": {
            "short": short_context,
            "medium_count": len(ordered_dates[-20:]),
        },
        "moneyflow_trade_dates": moneyflow_dates,
        "source": "moneyflow_ind_dc",
        "warnings": warnings,
        "continuation_inflow": [
            _sector_output(row, index + 1)
            for index, row in continuation.iterrows()
        ],
        "rotation_rebound": [
            _sector_output(row, index + 1)
            for index, row in rotation.iterrows()
        ],
    }
```

- [ ] **Step 4: Run service tests to verify GREEN**

Run: `python3 -m unittest tests.test_sector_rotation_service -v`

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add sector_rotation_service.py tests/test_sector_rotation_service.py
git commit -m "feat: add tomorrow sector rotation scoring"
```

---

### Task 2: Python API Endpoint

**Files:**
- Modify: `app.py`
- Create: `tests/test_sector_rotation_api.py`

**Interfaces:**
- Consumes: `sector_rotation_service.build_tomorrow_sector_rotation(trade_date: str | None, limit: int, stocks_per_sector: int) -> dict[str, Any]`
- Produces: FastAPI route function `sector_rotation_tomorrow(trade_date: str | None = None, limit: int = Query(10, ge=1, le=30), stocks_per_sector: int = Query(5, ge=1, le=10))`

- [ ] **Step 1: Write failing API tests**

Create `tests/test_sector_rotation_api.py`:

```python
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import app


class SectorRotationApiTests(unittest.TestCase):
    @patch(
        "app.build_tomorrow_sector_rotation",
        return_value={
            "trade_date": "20260812",
            "continuation_inflow": [],
            "rotation_rebound": [],
        },
    )
    def test_tomorrow_endpoint_forwards_parameters(self, service):
        result = app.sector_rotation_tomorrow(
            trade_date="20260812",
            limit=7,
            stocks_per_sector=4,
        )

        self.assertEqual(result["trade_date"], "20260812")
        service.assert_called_once_with(
            trade_date="20260812",
            limit=7,
            stocks_per_sector=4,
        )

    @patch(
        "app.build_tomorrow_sector_rotation",
        side_effect=LookupError("完整交易日不足"),
    )
    def test_lookup_error_maps_to_404(self, _service):
        with self.assertRaises(HTTPException) as raised:
            app.sector_rotation_tomorrow()

        self.assertEqual(raised.exception.status_code, 404)

    @patch(
        "app.build_tomorrow_sector_rotation",
        side_effect=ValueError("trade_date 格式错误"),
    )
    def test_value_error_maps_to_422(self, _service):
        with self.assertRaises(HTTPException) as raised:
            app.sector_rotation_tomorrow(trade_date="bad")

        self.assertEqual(raised.exception.status_code, 422)

    @patch(
        "app.build_tomorrow_sector_rotation",
        side_effect=RuntimeError("数据库不可用"),
    )
    def test_unknown_error_maps_to_502(self, _service):
        with self.assertRaises(HTTPException) as raised:
            app.sector_rotation_tomorrow()

        self.assertEqual(raised.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run API tests to verify RED**

Run: `python3 -m unittest tests.test_sector_rotation_api -v`

Expected: FAIL because `app.sector_rotation_tomorrow` or `app.build_tomorrow_sector_rotation` is not defined.

- [ ] **Step 3: Add FastAPI route**

In `app.py`, add the import near the other service imports:

```python
from sector_rotation_service import build_tomorrow_sector_rotation
```

Add the route after `market_news_summary` or near other market-monitor endpoints:

```python
@app.get("/api/sector-rotation/tomorrow")
def sector_rotation_tomorrow(
    trade_date: str | None = Query(None, pattern=r"^\d{8}$"),
    limit: int = Query(10, ge=1, le=30),
    stocks_per_sector: int = Query(5, ge=1, le=10),
):
    try:
        return build_tomorrow_sector_rotation(
            trade_date=trade_date,
            limit=limit,
            stocks_per_sector=stocks_per_sector,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("获取明日板块轮动失败")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
```

- [ ] **Step 4: Run API tests to verify GREEN**

Run: `python3 -m unittest tests.test_sector_rotation_api -v`

Expected: PASS.

- [ ] **Step 5: Run backend focused tests**

Run: `python3 -m unittest tests.test_sector_rotation_service tests.test_sector_rotation_api -v`

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

```bash
git add app.py tests/test_sector_rotation_api.py
git commit -m "feat: expose tomorrow sector rotation api"
```

---

### Task 3: Spring Proxy Endpoint

**Files:**
- Modify: `quantServer/quantServer/src/main/java/com/codec/quantserver/service/QuantPythonClient.java`
- Modify: `quantServer/quantServer/src/main/java/com/codec/quantserver/controller/QuantController.java`
- Modify: `quantServer/quantServer/src/test/java/com/codec/quantserver/controller/QuantControllerTest.java`

**Interfaces:**
- Consumes: Python endpoint `GET /api/sector-rotation/tomorrow?trade_date=&limit=&stocks_per_sector=`
- Produces:
  - `QuantPythonClient.sectorRotationTomorrow(String tradeDate, int limit, int stocksPerSector) -> Map<String, Object>`
  - Controller endpoint `GET /api/quant/sector-rotation/tomorrow`

- [ ] **Step 1: Write failing Spring controller test**

Add this test method to `QuantControllerTest.java`:

```java
@Test
void sectorRotationTomorrowForwardsParameters() throws Exception {
    QuantPythonClient client = mock(QuantPythonClient.class);
    when(client.sectorRotationTomorrow("20260812", 7, 4))
            .thenReturn(Map.of("trade_date", "20260812"));
    MockMvc mockMvc = MockMvcBuilders
            .standaloneSetup(new QuantController(client)).build();

    mockMvc.perform(get("/api/quant/sector-rotation/tomorrow")
                    .param("trade_date", "20260812")
                    .param("limit", "7")
                    .param("stocks_per_sector", "4"))
            .andExpect(status().isOk());

    verify(client).sectorRotationTomorrow("20260812", 7, 4);
}
```

- [ ] **Step 2: Run Spring test to verify RED**

Run from `quantServer/quantServer`:

```bash
mvn -q -Dtest=QuantControllerTest#sectorRotationTomorrowForwardsParameters test
```

Expected: FAIL because `sectorRotationTomorrow` does not exist.

- [ ] **Step 3: Add client proxy method**

In `QuantPythonClient.java`, add:

```java
public Map<String, Object> sectorRotationTomorrow(
        String tradeDate, int limit, int stocksPerSector) {
    int safeLimit = Math.max(1, Math.min(limit, 30));
    int safeStocksPerSector = Math.max(1, Math.min(stocksPerSector, 10));
    return restClient.get()
            .uri(uriBuilder -> {
                var builder = uriBuilder
                        .path("/api/sector-rotation/tomorrow")
                        .queryParam("limit", safeLimit)
                        .queryParam("stocks_per_sector", safeStocksPerSector);
                if (tradeDate != null && !tradeDate.isBlank()) {
                    builder.queryParam("trade_date", tradeDate);
                }
                return builder.build();
            })
            .retrieve()
            .body(mapType());
}
```

- [ ] **Step 4: Add controller endpoint**

In `QuantController.java`, add:

```java
@GetMapping("/sector-rotation/tomorrow")
public Map<String, Object> sectorRotationTomorrow(
        @RequestParam(name = "trade_date", required = false) String tradeDate,
        @RequestParam(defaultValue = "10") int limit,
        @RequestParam(name = "stocks_per_sector", defaultValue = "5") int stocksPerSector) {
    return quantPythonClient.sectorRotationTomorrow(
            tradeDate, limit, stocksPerSector);
}
```

- [ ] **Step 5: Run Spring test to verify GREEN**

Run from `quantServer/quantServer`:

```bash
mvn -q -Dtest=QuantControllerTest#sectorRotationTomorrowForwardsParameters test
```

Expected: PASS.

- [ ] **Step 6: Run full controller tests**

Run from `quantServer/quantServer`:

```bash
mvn -q -Dtest=QuantControllerTest test
```

Expected: PASS.

- [ ] **Step 7: Commit Task 3**

```bash
git add quantServer/quantServer/src/main/java/com/codec/quantserver/service/QuantPythonClient.java \
  quantServer/quantServer/src/main/java/com/codec/quantserver/controller/QuantController.java \
  quantServer/quantServer/src/test/java/com/codec/quantserver/controller/QuantControllerTest.java
git commit -m "feat: proxy tomorrow sector rotation"
```

---

### Task 4: Frontend Utilities

**Files:**
- Create: `quantClient/sector-rotation-utils.js`
- Create: `quantClient/sector-rotation-utils.test.js`

**Interfaces:**
- Consumes: API rows containing `continuation_score`, `rotation_score`, `metrics`, `attack_leaders`, `catchup_candidates`
- Produces:
  - `formatRotationMoney(value) -> string`
  - `formatRotationPercent(value, digits = 2) -> string`
  - `rotationSectorSummary(row, mode) -> string`
  - `rotationStockText(stock, scoreKey) -> string`

- [ ] **Step 1: Write failing utility tests**

Create `quantClient/sector-rotation-utils.test.js`:

```javascript
const assert = require('assert');
const {
  formatRotationMoney,
  formatRotationPercent,
  rotationSectorSummary,
  rotationStockText,
} = require('./sector-rotation-utils.js');

assert.strictEqual(formatRotationMoney(1230000000), '12.30亿');
assert.strictEqual(formatRotationMoney(null), '--');
assert.strictEqual(formatRotationPercent(0.723, 1), '72.3%');
assert.strictEqual(formatRotationPercent(null), '--');

const sector = {
  industry_name: '机器人',
  continuation_score: 86.5,
  rotation_score: 54.2,
  signal: '连续流入放量扩散',
  metrics: {
    net_amount_today: 1230000000,
    net_amount_change: 550000000,
    up_ratio: 0.72,
  },
};
const summary = rotationSectorSummary(sector, 'continuation');
assert(summary.includes('机器人'));
assert(summary.includes('86.5'));
assert(summary.includes('12.30亿'));
assert(summary.includes('连续流入放量扩散'));

const stockText = rotationStockText({
  name: '示例股份',
  ts_code: '600001.SH',
  pct_chg: 3.21,
  attack_score: 78.9,
  reason: '量比活跃',
}, 'attack_score');
assert(stockText.includes('示例股份'));
assert(stockText.includes('600001.SH'));
assert(stockText.includes('78.9'));
assert(stockText.includes('量比活跃'));

console.log('sector rotation utils ok');
```

- [ ] **Step 2: Run utility tests to verify RED**

Run: `node quantClient/sector-rotation-utils.test.js`

Expected: FAIL because `sector-rotation-utils.js` does not exist.

- [ ] **Step 3: Implement frontend utility module**

Create `quantClient/sector-rotation-utils.js`:

```javascript
(function exposeSectorRotationUtils(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.formatRotationMoney = api.formatRotationMoney;
  root.formatRotationPercent = api.formatRotationPercent;
  root.rotationSectorSummary = api.rotationSectorSummary;
  root.rotationStockText = api.rotationStockText;
}(typeof globalThis !== 'undefined' ? globalThis : this, function buildSectorRotationUtils() {
  function numberOrNull(value) {
    if (value === null || value === undefined || value === '') return null;
    const number = Number(value);
    return Number.isNaN(number) ? null : number;
  }

  function formatNumber(value, digits = 2) {
    const number = numberOrNull(value);
    if (number === null) return '--';
    return number.toLocaleString('zh-CN', {
      minimumFractionDigits: 0,
      maximumFractionDigits: digits,
    });
  }

  function formatRotationMoney(value) {
    const number = numberOrNull(value);
    if (number === null) return '--';
    return `${formatNumber(number / 100000000, 2)}亿`;
  }

  function formatRotationPercent(value, digits = 2) {
    const number = numberOrNull(value);
    if (number === null) return '--';
    return `${formatNumber(number * 100, digits)}%`;
  }

  function rotationSectorSummary(row, mode) {
    if (!row) return '--';
    const scoreKey = mode === 'rotation' ? 'rotation_score' : 'continuation_score';
    const metrics = row.metrics || {};
    return [
      row.industry_name || '--',
      `分数 ${formatNumber(row[scoreKey], 1)}`,
      `今日 ${formatRotationMoney(metrics.net_amount_today)}`,
      `变化 ${formatRotationMoney(metrics.net_amount_change)}`,
      row.signal || '观察',
    ].join(' · ');
  }

  function rotationStockText(stock, scoreKey) {
    if (!stock) return '--';
    const identity = [];
    if (stock.name) identity.push(stock.name);
    if (stock.ts_code && stock.ts_code !== stock.name) identity.push(stock.ts_code);
    const parts = [identity.length ? identity.join(' ') : '--'];
    parts.push(`分 ${formatNumber(stock[scoreKey], 1)}`);
    if (stock.pct_chg !== undefined && stock.pct_chg !== null) {
      parts.push(`${formatNumber(stock.pct_chg)}%`);
    }
    if (stock.reason) parts.push(stock.reason);
    return parts.join(' · ');
  }

  return {
    formatRotationMoney,
    formatRotationPercent,
    rotationSectorSummary,
    rotationStockText,
  };
}));
```

- [ ] **Step 4: Run utility tests to verify GREEN**

Run: `node quantClient/sector-rotation-utils.test.js`

Expected: PASS and print `sector rotation utils ok`.

- [ ] **Step 5: Commit Task 4**

```bash
git add quantClient/sector-rotation-utils.js quantClient/sector-rotation-utils.test.js
git commit -m "feat: add sector rotation frontend utilities"
```

---

### Task 5: Frontend Page

**Files:**
- Modify: `quantClient/index.html`
- Modify: `quantClient/main.js`
- Modify: `quantClient/styles.css`
- Create: `quantClient/sector-rotation-layout.test.js`

**Interfaces:**
- Consumes:
  - `GET /sector-rotation/tomorrow?limit=&stocks_per_sector=` through existing `request()` method.
  - `formatRotationMoney`, `formatRotationPercent`, `rotationStockText` from `sector-rotation-utils.js`
- Produces:
  - New tab key: `sector_rotation`
  - New state object: `sectorRotation`
  - New method: `loadSectorRotation()`

- [ ] **Step 1: Write failing layout test**

Create `quantClient/sector-rotation-layout.test.js`:

```javascript
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = __dirname;
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const main = fs.readFileSync(path.join(root, 'main.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'styles.css'), 'utf8');

assert(html.includes("activeTab === 'sector_rotation'"), 'navigation should include sector_rotation tab');
assert(html.includes('明日轮动'), 'page should show 明日轮动 copy');
assert(html.includes('延续流入榜'), 'page should show continuation board');
assert(html.includes('轮动回流榜'), 'page should show rotation board');
assert(html.includes('进攻龙头'), 'page should show attack leaders');
assert(html.includes('补涨候选'), 'page should show catch-up candidates');
assert(html.includes('sector-rotation-utils.js'), 'index should load sector rotation utilities');
assert(main.includes('sectorRotation:'), 'main state should include sectorRotation');
assert(main.includes('loadSectorRotation'), 'main should include loadSectorRotation method');
assert(main.includes('/sector-rotation/tomorrow'), 'main should call sector rotation API');
assert(css.includes('.sector-rotation-page'), 'css should include sector rotation page styles');

console.log('sector rotation layout ok');
```

- [ ] **Step 2: Run layout test to verify RED**

Run: `node quantClient/sector-rotation-layout.test.js`

Expected: FAIL because the tab and page do not exist.

- [ ] **Step 3: Load utility script**

In `quantClient/index.html`, add this script before `main.js` and after related utility scripts:

```html
<script src="./sector-rotation-utils.js?v=20260812-sector-rotation-v1"></script>
```

- [ ] **Step 4: Add navigation button**

In `quantClient/index.html`, add a button near `板块潜力`:

```html
<button :class="{ active: activeTab === 'sector_rotation' }" @click="activeTab = 'sector_rotation'; loadSectorRotation(false)">明日轮动</button>
```

- [ ] **Step 5: Add page markup**

In `quantClient/index.html`, add a new section outside existing pages:

```html
<section v-show="activeTab === 'sector_rotation'" class="sector-rotation-page">
  <div class="metrics sector-rotation-summary">
    <article><span>交易日</span><strong>{{ sectorRotation.trade_date || '--' }}</strong></article>
    <article><span>资金流日期</span><strong class="small-value">{{ (sectorRotation.moneyflow_trade_dates || []).join(' / ') || '--' }}</strong></article>
    <article><span>延续最高分</span><strong>{{ sectorRotationTopScore('continuation_inflow', 'continuation_score') }}</strong></article>
    <article><span>回流最高分</span><strong>{{ sectorRotationTopScore('rotation_rebound', 'rotation_score') }}</strong></article>
  </div>

  <section v-if="sectorRotation.warnings && sectorRotation.warnings.length" class="cache-warning">
    <strong>轮动数据提示</strong>
    <span v-for="warning in sectorRotation.warnings" :key="warning">{{ warning }}</span>
  </section>

  <div class="sector-rotation-grid">
    <div class="panel sector-rotation-board">
      <div class="panel-head">
        <div>
          <h3>延续流入榜</h3>
          <span>连续流入、放量扩散、龙头强度靠前</span>
        </div>
        <button class="secondary" @click="loadSectorRotation(true)" :disabled="sectorRotationLoading">
          {{ sectorRotationLoading ? '刷新中' : '刷新' }}
        </button>
      </div>
      <table>
        <thead>
          <tr>
            <th>排名</th>
            <th>板块</th>
            <th>分数</th>
            <th>信心</th>
            <th>今日净流入</th>
            <th>变化</th>
            <th>上涨率</th>
            <th>推荐票</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in sectorRotation.continuation_inflow || []" :key="'continuation-' + row.industry_name">
            <td>{{ row.rank }}</td>
            <td><strong>{{ row.industry_name }}</strong><small>{{ row.signal }}</small></td>
            <td><strong>{{ formatNumber(row.continuation_score, 1) }}</strong><small>回流 {{ formatNumber(row.rotation_score, 1) }}</small></td>
            <td><span class="signal-badge">{{ row.confidence || '低' }}</span></td>
            <td>{{ formatSectorRotationMoney(row.metrics && row.metrics.net_amount_today) }}</td>
            <td>{{ formatSectorRotationMoney(row.metrics && row.metrics.net_amount_change) }}</td>
            <td>{{ formatSectorRotationPercent(row.metrics && row.metrics.up_ratio, 0) }}</td>
            <td>
              <div class="rotation-stock-groups">
                <strong>进攻龙头</strong>
                <span v-for="stock in row.attack_leaders || []" :key="stock.ts_code">{{ rotationStockText(stock, 'attack_score') }}</span>
                <strong>补涨候选</strong>
                <span v-for="stock in row.catchup_candidates || []" :key="'c-' + stock.ts_code">{{ rotationStockText(stock, 'catchup_score') }}</span>
              </div>
            </td>
          </tr>
          <tr v-if="!(sectorRotation.continuation_inflow || []).length">
            <td colspan="8" class="empty-line">暂无足够资金流数据</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel sector-rotation-board">
      <div class="panel-head">
        <div>
          <h3>轮动回流榜</h3>
          <span>流出转改善、趋势未破坏、补涨空间靠前</span>
        </div>
      </div>
      <table>
        <thead>
          <tr>
            <th>排名</th>
            <th>板块</th>
            <th>分数</th>
            <th>信心</th>
            <th>今日净流入</th>
            <th>变化</th>
            <th>上涨率</th>
            <th>推荐票</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in sectorRotation.rotation_rebound || []" :key="'rotation-' + row.industry_name">
            <td>{{ row.rank }}</td>
            <td><strong>{{ row.industry_name }}</strong><small>{{ row.signal }}</small></td>
            <td><strong>{{ formatNumber(row.rotation_score, 1) }}</strong><small>延续 {{ formatNumber(row.continuation_score, 1) }}</small></td>
            <td><span class="signal-badge">{{ row.confidence || '低' }}</span></td>
            <td>{{ formatSectorRotationMoney(row.metrics && row.metrics.net_amount_today) }}</td>
            <td>{{ formatSectorRotationMoney(row.metrics && row.metrics.net_amount_change) }}</td>
            <td>{{ formatSectorRotationPercent(row.metrics && row.metrics.up_ratio, 0) }}</td>
            <td>
              <div class="rotation-stock-groups">
                <strong>进攻龙头</strong>
                <span v-for="stock in row.attack_leaders || []" :key="stock.ts_code">{{ rotationStockText(stock, 'attack_score') }}</span>
                <strong>补涨候选</strong>
                <span v-for="stock in row.catchup_candidates || []" :key="'c-' + stock.ts_code">{{ rotationStockText(stock, 'catchup_score') }}</span>
              </div>
            </td>
          </tr>
          <tr v-if="!(sectorRotation.rotation_rebound || []).length">
            <td colspan="8" class="empty-line">暂无足够资金流数据</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</section>
```

- [ ] **Step 6: Add Vue state**

In `quantClient/main.js`, add to `data()`:

```javascript
sectorRotation: {
  trade_date: '',
  lookback_trade_dates: [],
  moneyflow_trade_dates: [],
  warnings: [],
  continuation_inflow: [],
  rotation_rebound: [],
},
sectorRotationLoading: false,
```

- [ ] **Step 7: Add page title branch**

In `pageTitle`, add:

```javascript
if (this.activeTab === 'sector_rotation') return '明日轮动';
```

- [ ] **Step 8: Add loading method**

In `methods`, add:

```javascript
async loadSectorRotation(force) {
  if (this.sectorRotationLoading && !force) return;
  this.sectorRotationLoading = true;
  this.error = '';
  try {
    this.sectorRotation = await this.request(
      `/sector-rotation/tomorrow?limit=${this.limit || 10}&stocks_per_sector=5`
    );
  } catch (err) {
    this.error = err.message || '明日轮动加载失败';
  } finally {
    this.sectorRotationLoading = false;
  }
},
sectorRotationTopScore(key, scoreKey) {
  const rows = this.sectorRotation && Array.isArray(this.sectorRotation[key])
    ? this.sectorRotation[key]
    : [];
  if (!rows.length) return '--';
  return this.formatNumber(rows[0][scoreKey], 1);
},
formatSectorRotationMoney(value) {
  return formatRotationMoney(value);
},
formatSectorRotationPercent(value, digits) {
  return formatRotationPercent(value, digits);
},
rotationStockText(stock, scoreKey) {
  return rotationStockText(stock, scoreKey);
},
```

- [ ] **Step 9: Include sector rotation in refresh behavior**

In `refreshAll`, after loading the latest report or near other active-tab checks, add:

```javascript
if (this.activeTab === 'sector_rotation') {
  await this.loadSectorRotation(true);
  return;
}
```

If the app has a watcher or mounted branch for active tabs, add:

```javascript
if (this.activeTab === 'sector_rotation') this.loadSectorRotation(false);
```

- [ ] **Step 10: Add CSS**

In `quantClient/styles.css`, add:

```css
.sector-rotation-page {
  display: grid;
  gap: 16px;
}

.sector-rotation-summary .small-value {
  font-size: 13px;
  line-height: 1.35;
}

.sector-rotation-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  align-items: start;
}

.sector-rotation-board {
  overflow-x: auto;
}

.sector-rotation-board table {
  min-width: 980px;
}

.sector-rotation-board td strong {
  display: block;
}

.sector-rotation-board td small {
  display: block;
  color: var(--muted);
  margin-top: 4px;
}

.rotation-stock-groups {
  display: grid;
  gap: 4px;
  min-width: 260px;
}

.rotation-stock-groups strong {
  color: var(--text);
  font-size: 12px;
}

.rotation-stock-groups span {
  color: var(--muted);
  line-height: 1.35;
}

@media (max-width: 1180px) {
  .sector-rotation-grid {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 11: Run frontend tests to verify GREEN**

Run:

```bash
node quantClient/sector-rotation-layout.test.js
node quantClient/sector-rotation-utils.test.js
```

Expected: both PASS.

- [ ] **Step 12: Commit Task 5**

```bash
git add quantClient/index.html quantClient/main.js quantClient/styles.css \
  quantClient/sector-rotation-layout.test.js
git commit -m "feat: add tomorrow sector rotation page"
```

---

### Task 6: End-to-End Verification

**Files:**
- No new files expected.
- May modify: `DEPLOY_SERVER.md` only if the verification command list needs the new endpoint documented.

**Interfaces:**
- Consumes all prior tasks.
- Produces verified working API and frontend static regressions.

- [ ] **Step 1: Run Python focused tests**

Run:

```bash
python3 -m unittest tests.test_sector_rotation_service tests.test_sector_rotation_api -v
```

Expected: PASS.

- [ ] **Step 2: Run broader Python API smoke tests**

Run:

```bash
python3 -m unittest tests.test_sector_rotation_api tests.test_free_review_api tests.test_market_cache_api -v
```

Expected: PASS. If unrelated current-worktree syntax errors in `app.py` block import, stop and report the pre-existing broken file state before touching unrelated code.

- [ ] **Step 3: Run Spring controller tests**

Run from `quantServer/quantServer`:

```bash
mvn -q -Dtest=QuantControllerTest test
```

Expected: PASS.

- [ ] **Step 4: Run frontend tests**

Run:

```bash
node quantClient/sector-rotation-utils.test.js
node quantClient/sector-rotation-layout.test.js
node quantClient/sector-potential-utils.test.js
```

Expected: PASS.

- [ ] **Step 5: Run API manually against Python if local service is available**

If Python service is already running on port 8000, run:

```bash
curl -i 'http://127.0.0.1:8000/api/sector-rotation/tomorrow?limit=3&stocks_per_sector=3'
```

Expected: HTTP 200 with JSON keys `continuation_inflow` and `rotation_rebound`.

If no service is running, do not start a long-lived service for this plan step; report that manual HTTP verification was skipped.

- [ ] **Step 6: Run API manually through Spring if local service is available**

If Spring is already running on port 8080, run:

```bash
curl -i 'http://127.0.0.1:8080/api/quant/sector-rotation/tomorrow?limit=3&stocks_per_sector=3'
```

Expected: HTTP 200 with JSON keys `continuation_inflow` and `rotation_rebound`.

If no service is running, do not start a long-lived service for this plan step; report that manual Spring HTTP verification was skipped.

- [ ] **Step 7: Inspect final diff**

Run:

```bash
git diff --stat HEAD~5..HEAD
git status --short
```

Expected: only task-related source, test, and optional doc changes are included in the new commits. Pre-existing unrelated dirty files may still appear and must not be reverted.

- [ ] **Step 8: Commit optional docs update**

Only if `DEPLOY_SERVER.md` was updated:

```bash
git add DEPLOY_SERVER.md
git commit -m "docs: add sector rotation verification endpoint"
```

Expected: docs-only commit.
