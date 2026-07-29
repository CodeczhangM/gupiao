import unittest
import os
from unittest.mock import patch

import pandas as pd

os.environ["MARKET_CACHE_ENABLED"] = "false"

import ai_agent
import data_service
import main
import quant_service
from database import _decode_report
from strategy import (
    _attach_intraday_signal_stocks,
    _build_strong_history_stats,
    _classify_trend_stage,
    pick_breakout_stocks,
    pick_dip_sectors,
    pick_first_limit_stocks,
    pick_sector_tail_buy_stocks,
    pick_strong_base_candidates,
    pick_stocks,
    rank_sector_potential,
    select_stock_pools,
)


def build_history(
    ts_code="600001.SH",
    start_close=20.0,
    prior_close=10.0,
    latest_close=12.0,
    prior_volume=100.0,
    latest_volume=150.0,
    days=61,
    latest_pct_chg=2.0,
):
    rows = []
    for index in range(days):
        is_first = index == 0
        is_latest = index == days - 1
        close = start_close if is_first else latest_close if is_latest else prior_close
        rows.append({
            "ts_code": ts_code,
            "trade_date": f"2026{index + 1:04d}",
            "pct_chg": latest_pct_chg if is_latest else 0.0,
            "close": close,
            "high": close,
            "low": close,
            "vol": latest_volume if is_latest else prior_volume,
        })
    return pd.DataFrame(rows)


def build_market(
    ts_code="600001.SH",
    industry="热点行业",
    close=12.0,
    pct_chg=2.0,
    vol=150.0,
    turnover_rate=9.0,
    volume_ratio=2.5,
    amount=300_000_000,
    total_mv=1_000_000,
):
    return pd.DataFrame([{
        "ts_code": ts_code,
        "name": "测试股份",
        "industry": industry,
        "close": close,
        "high": close,
        "low": close,
        "pct_chg": pct_chg,
        "turnover_rate": turnover_rate,
        "volume_ratio": volume_ratio,
        "vol": vol,
        "amount": amount,
        "total_mv": total_mv,
    }])


def build_sector_potential_fixture():
    dates = pd.date_range("2026-02-02", periods=100, freq="B").strftime("%Y%m%d").tolist()
    sector_specs = {
        "能源强势": {"base": 10.0, "daily": 0.035, "latest": 6.0, "prev": -0.2, "amount": 600_000_000},
        "有色反包": {"base": 12.0, "daily": 0.015, "latest": 5.0, "prev": -4.8, "amount": 520_000_000},
        "科技弱势": {"base": 18.0, "daily": -0.01, "latest": -0.8, "prev": -5.5, "amount": 380_000_000},
    }
    market_rows = []
    history_rows = []
    for sector_index, (industry, spec) in enumerate(sector_specs.items()):
        for stock_index in range(10):
            ts_code = f"600{sector_index}{stock_index:02d}.SH"
            close = spec["base"]
            for date_index, trade_date in enumerate(dates):
                close = close * (1 + spec["daily"] / 100)
                pct_chg = spec["daily"]
                is_quality_leader = industry == "能源强势" and stock_index < 3
                if date_index == len(dates) - 2:
                    pct_chg = spec["prev"]
                    close = close * (1 + pct_chg / 100)
                if date_index == len(dates) - 1:
                    pct_chg = 1.5 if is_quality_leader else spec["latest"] + (1.2 if stock_index == 0 else 0.0)
                    close = close * (1 + pct_chg / 100)
                if is_quality_leader and date_index == len(dates) - 12:
                    pct_chg = 9.8
                    close = close * (1 + pct_chg / 100)
                if is_quality_leader and len(dates) - 11 <= date_index <= len(dates) - 3:
                    pct_chg = [-0.6, 0.4, -0.5, 0.3, -0.4, 0.2, -0.3, 0.1, -0.2][date_index - (len(dates) - 11)]
                    close = close * (1 + pct_chg / 100)
                vol = 100 + date_index + stock_index
                if is_quality_leader and date_index == len(dates) - 12:
                    vol = 260 + stock_index
                if is_quality_leader and date_index >= len(dates) - 3:
                    vol = 130 + stock_index
                history_rows.append({
                    "ts_code": ts_code,
                    "trade_date": trade_date,
                    "close": round(close, 4),
                    "high": round(close * 1.01, 4),
                    "low": round(close * 0.99, 4),
                    "vol": vol,
                    "amount": spec["amount"] * (1.5 if date_index == len(dates) - 1 else 1.0) / 10,
                    "pct_chg": pct_chg,
                })
            latest = history_rows[-1]
            market_rows.append({
                "ts_code": ts_code,
                "name": f"{industry}{stock_index}",
                "industry": industry,
                "close": latest["close"],
                "high": latest["high"],
                "low": latest["low"],
                "pct_chg": latest["pct_chg"],
                "turnover_rate": 8.0 + stock_index,
                "volume_ratio": 1.3 + stock_index / 10,
                "amount": latest["amount"],
                "total_mv": 2_500_000 + stock_index * 1000 if industry == "能源强势" and stock_index < 3 else 1_000_000 + stock_index * 1000,
            })
    return pd.DataFrame(market_rows), pd.DataFrame(history_rows)


def build_60min_bars(ts_code, closes):
    rows = []
    for index, close in enumerate(closes):
        rows.append({
            "ts_code": ts_code,
            "trade_time": f"2026-07-{1 + index // 4:02d} {10 + index % 4:02d}:30:00",
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "vol": 100000 + index,
            "amount": close * (100000 + index),
        })
    return pd.DataFrame(rows)


def build_tail_1min_bars(ts_code, prices, volumes):
    rows = []
    compact_times = [
        "14:25:00", "14:26:00", "14:27:00", "14:28:00", "14:29:00", "14:30:00",
        "14:51:00", "14:52:00", "14:53:00", "14:54:00", "14:55:00", "14:56:00",
        "15:00:00",
    ]
    for index, (price, volume) in enumerate(zip(prices, volumes)):
        if len(prices) == len(compact_times):
            time_text = compact_times[index]
        else:
            minute = 25 + index
            hour = 14
            if minute >= 60:
                hour = 15
                minute = 0
            time_text = f"{hour:02d}:{minute:02d}:00"
        rows.append({
            "ts_code": ts_code,
            "trade_time": f"2026-07-27 {time_text}",
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "vol": volume,
            "amount": price * volume,
        })
    return pd.DataFrame(rows)


def water_macd_kdj_cross_closes():
    closes = [10 + index * 0.03 for index in range(30)]
    closes += [closes[-1] - 0.02 * index for index in range(1, 13)]
    closes += [closes[-1] + 0.75]
    return closes


def water_macd_kdj_continuation_closes():
    return water_macd_kdj_cross_closes() + [11.45, 11.55, 11.7]


def underwater_macd_kdj_cross_closes():
    return [10] * 35 + [9.5, 9.0, 8.7, 8.6, 8.8, 9.2, 9.8, 10.6]


def build_confirmed_reversal_history(ts_code="600001.SH"):
    closes = (
        [12 - index * 0.05 for index in range(40)] +
        [10.1 + index * 0.12 for index in range(54)] +
        [16.0, 15.8, 15.4, 15.5, 15.3, 16.6]
    )
    rows = []
    for index, close in enumerate(closes):
        # Keep the former high inside the latest 60-day window so the
        # high-drawdown hard filter is exercised by this fixture.
        high = 26.0 if index == 40 else close + 0.2
        is_latest = index == len(closes) - 1
        is_washout = len(closes) - 6 <= index < len(closes) - 1
        rows.append({
            "ts_code": ts_code,
            "trade_date": f"2026{index + 1:04d}",
            "pct_chg": 1.0 if is_latest else 0.0,
            "close": close,
            "high": high,
            "low": close - 0.2,
            "vol": 160.0 if is_latest else 70.0 if is_washout else 100.0,
        })
    return pd.DataFrame(rows)


def build_daily_breakout_market(include_prev_sector_amount=True):
    rows = [
        build_market(
            ts_code="600001.SH",
            industry="强势板块",
            close=13.0,
            pct_chg=10.0,
            vol=180.0,
            turnover_rate=9.0,
            volume_ratio=2.2,
            amount=600_000_000,
        ),
        build_market(ts_code="600002.SH", industry="强势板块", close=11.0, pct_chg=9.8, amount=450_000_000),
        build_market(ts_code="600003.SH", industry="强势板块", close=12.0, pct_chg=9.7, amount=420_000_000),
        build_market(ts_code="600004.SH", industry="强势板块", close=10.0, pct_chg=3.0, amount=300_000_000),
        build_market(ts_code="600005.SH", industry="普通板块", close=10.0, pct_chg=1.0, amount=300_000_000),
    ]
    market = pd.concat(rows, ignore_index=True)
    market.loc[market["ts_code"] == "600001.SH", ["high", "low"]] = [13.1, 11.8]
    if include_prev_sector_amount:
        market["prev_sector_amount_yuan"] = market["industry"].map({
            "强势板块": 1_000_000_000,
            "普通板块": 300_000_000,
        })
    return market


def build_daily_breakout_history(ts_code="600001.SH"):
    history = build_history(
        ts_code=ts_code,
        start_close=8.0,
        prior_close=11.0,
        latest_close=13.0,
        prior_volume=100.0,
        latest_volume=180.0,
        days=100,
        latest_pct_chg=10.0,
    )
    history.loc[history.index[-21:-1], "high"] = 12.0
    history.loc[history.index[-3:-1], "pct_chg"] = [1.0, 2.0]
    history.loc[history.index[-1], ["close", "high", "low"]] = [13.0, 13.1, 11.8]
    return history


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
    history.loc[history.index[-1], ["close", "high", "low", "pct_chg", "vol"]] = [
        13.2, 13.35, 12.2, 8.0, 230.0,
    ]
    if flat_boll:
        history.loc[history.index[-20:], ["close", "high", "low"]] = [11.0, 11.05, 10.95]
        history.loc[history.index[-1], ["close", "high", "low", "pct_chg", "vol"]] = [
            11.02, 11.05, 10.95, 3.0, 230.0,
        ]
    if weekly_down:
        history.loc[history.index[-1], ["close", "high", "low", "pct_chg", "vol"]] = [
            12.0, 12.2, 11.5, 6.0, 230.0,
        ]
    if macd_deep_below_zero:
        history.loc[history.index[-1], ["close", "high", "low", "pct_chg", "vol"]] = [
            9.7, 9.85, 9.4, 4.0, 230.0,
        ]
    return history


def build_relaxed_daily_breakout_market():
    rows = [
        build_market(ts_code="600002.SH", industry="强势板块", close=11.0, pct_chg=10.0, amount=420_000_000),
        build_market(ts_code="600003.SH", industry="强势板块", close=12.0, pct_chg=9.8, amount=390_000_000),
        build_market(ts_code="600004.SH", industry="强势板块", close=10.0, pct_chg=6.0, amount=260_000_000),
        build_market(ts_code="600005.SH", industry="强势板块", close=10.0, pct_chg=5.0, amount=240_000_000),
        build_market(
            ts_code="600001.SH",
            industry="强势板块",
            close=13.0,
            pct_chg=4.0,
            vol=180.0,
            turnover_rate=9.0,
            volume_ratio=2.2,
            amount=300_000_000,
        ),
        build_market(ts_code="600006.SH", industry="普通板块", close=10.0, pct_chg=1.0, amount=300_000_000),
    ]
    market = pd.concat(rows, ignore_index=True)
    market.loc[market["ts_code"] == "600001.SH", ["high", "low"]] = [13.1, 11.8]
    market["prev_sector_amount_yuan"] = market["industry"].map({
        "强势板块": 1_450_000_000,
        "普通板块": 300_000_000,
    })
    return market


def build_cost_zone_breakout_history(
    ts_code="600001.SH",
    platform_close=49.0,
    latest_close=51.0,
    platform_high=50.0,
):
    history = build_history(
        ts_code=ts_code,
        start_close=42.0,
        prior_close=platform_close,
        latest_close=latest_close,
        prior_volume=100.0,
        latest_volume=180.0,
        days=100,
        latest_pct_chg=4.0,
    )
    history.loc[history.index[-21:-1], "high"] = platform_high
    history.loc[history.index[-21:-1], "low"] = platform_close * 0.98
    history.loc[history.index[-3:-1], "pct_chg"] = [1.0, 1.0]
    history.loc[history.index[-1], ["close", "high", "low"]] = [
        latest_close,
        latest_close * 1.01,
        latest_close * 0.97,
    ]
    return history


def build_downward_ma20_bullish_history(ts_code="600001.SH"):
    closes = [30 - index * 0.08 for index in range(80)]
    closes += [22 - index * 0.02 for index in range(15)]
    rebound_start = closes[-1]
    closes += [rebound_start + (index + 1) * 0.3 for index in range(5)]

    rows = []
    for index, close in enumerate(closes):
        is_latest = index == len(closes) - 1
        rows.append({
            "ts_code": ts_code,
            "trade_date": f"2026{index + 1:04d}",
            "pct_chg": 3.5 if is_latest else 0.0,
            "close": close,
            "high": close,
            "low": close,
            "vol": 180.0 if is_latest else 100.0,
        })
    history = pd.DataFrame(rows)
    history.loc[history.index[-21:-1], "high"] = history.loc[history.index[-21:-1], "close"].max() - 0.5
    latest_close = history.loc[history.index[-1], "close"]
    history.loc[history.index[-1], ["high", "low"]] = [latest_close * 1.01, latest_close * 0.98]
    return history


def build_main_wave_start_history(ts_code="600001.SH"):
    closes = []
    price = 10.0
    for _ in range(70):
        price += 0.03
        closes.append(round(price, 2))
    for close in [12.0, 12.1, 12.2, 12.15, 12.25, 12.3, 12.4, 12.55, 12.7, 12.85]:
        closes.append(close)
    for close in [13.2, 13.5, 13.35, 13.6, 13.8, 14.1, 14.35, 14.65, 14.95, 15.3]:
        closes.append(close)

    rows = []
    for index, close in enumerate(closes):
        is_recent = index >= len(closes) - 5
        is_breakout = index == len(closes) - 10
        rows.append({
            "ts_code": ts_code,
            "trade_date": f"2026{index + 1:04d}",
            "pct_chg": 4.0 if is_breakout else 2.0 if is_recent else 0.5,
            "close": close,
            "high": close + 0.15,
            "low": close - 0.2,
            "vol": 180.0 if is_breakout or is_recent else 90.0,
        })
    history = pd.DataFrame(rows)
    history.loc[history.index[-20:-10], "high"] = 12.9
    history.loc[history.index[-5:], "low"] = [13.9, 14.1, 14.35, 14.65, 14.95]
    history.loc[history.index[-4:-1], "vol"] = [80.0, 78.0, 76.0]
    history.loc[history.index[-1], "vol"] = 160.0
    return history


class AdvantageStockScoringTests(unittest.TestCase):
    def test_reversal_requires_five_of_six_confirmation_indicators(self):
        history = build_confirmed_reversal_history()
        market = build_market(close=history.iloc[-1]["close"], vol=150.0)

        result = pick_stocks(market, history)

        self.assertEqual(len(result), 1)
        row = result.iloc[0]
        self.assertEqual(row["reversal_indicator_count"], 6)
        self.assertTrue(bool(row["bottom_position_qualified"]))
        self.assertTrue(bool(row["rebound_volume_confirmed"]))
        self.assertTrue(bool(row["washout_volume_shrink"]))
        self.assertTrue(bool(row["trend_repairing"]))
        self.assertLessEqual(row["bottom_position_60"], 0.50)
        self.assertEqual(row["trend_stage"], "S2")
        self.assertEqual(row["stage_label"], "启动突破阶段")
        self.assertEqual(row["stage_action"], "可试仓")
        self.assertIn("放量", row["stage_reason"])
        for field in (
            "ma60_above", "ma60_improving", "rsi6_below_75", "kdj_golden_cross",
            "macd_trend_or_above_zero", "volume_above_ma5_1_5",
        ):
            self.assertTrue(bool(row[field]), field)
        self.assertLessEqual(row["high_drawdown_60"], -20)
        self.assertIn("底部放量反弹", row["strong_reason"])

    def test_reversal_excludes_stock_with_fewer_than_five_confirmations(self):
        history = build_confirmed_reversal_history()
        history.loc[history.index[-1], "vol"] = 100.0
        history.loc[history.index[-1], "close"] = 12.0
        history.loc[history.index[-1], "high"] = 12.2

        result = pick_stocks(build_market(close=12.0, vol=100.0), history)

        self.assertTrue(result.empty)

    def test_reversal_requires_drawdown_turnover_and_amount_liquidity_filters(self):
        history = build_confirmed_reversal_history()
        market = build_market(close=history.iloc[-1]["close"], vol=150.0)

        no_drawdown = history.copy()
        no_drawdown.loc[no_drawdown.index[40], "high"] = 16.0
        self.assertTrue(pick_stocks(market, no_drawdown).empty)
        self.assertTrue(pick_stocks(build_market(close=history.iloc[-1]["close"], turnover_rate=3.0), history).empty)
        self.assertTrue(pick_stocks(build_market(close=history.iloc[-1]["close"], amount=200_000_000), history).empty)

    def test_reversal_requires_bottom_position(self):
        history = build_confirmed_reversal_history()
        history.loc[history.index[40], "high"] = 19.0
        market = build_market(close=history.iloc[-1]["close"], vol=160.0)

        result = pick_stocks(market, history)

        self.assertTrue(result.empty)

    def test_reversal_requires_pre_rebound_washout_volume_shrink(self):
        history = build_confirmed_reversal_history()
        history.loc[history.index[-6:-1], "vol"] = 120.0
        market = build_market(close=history.iloc[-1]["close"], vol=160.0)

        result = pick_stocks(market, history)

        self.assertTrue(result.empty)

    def test_reversal_requires_volume_rebound(self):
        history = build_confirmed_reversal_history()
        history.loc[history.index[-1], "vol"] = 90.0
        market = build_market(close=history.iloc[-1]["close"], vol=90.0)

        result = pick_stocks(market, history)

        self.assertTrue(result.empty)

    def test_reversal_does_not_hard_require_full_ma_repair(self):
        market = build_market(close=10.0, pct_chg=3.0, vol=180.0)
        stats = pd.DataFrame([{
            "ts_code": "600001.SH",
            "hist_days": 100,
            "previous_close": 9.7,
            "high_drawdown_60": -30.0,
            "bottom_position_60": 0.30,
            "recent_low_60": 9.0,
            "recent_high_60": 14.0,
            "ret60": -25.0,
            "volume_expand_rate": 1.8,
            "washout_volume_ratio": 0.75,
            "washout_volume_shrink": True,
            "ma20": 10.5,
            "ma20_flat_or_up": False,
            "ma30_not_fast_down": False,
            "ma60": 10.8,
            "ma60_trend": "走平",
            "ma60_decline_slowing": False,
            "rsi6": 55.0,
            "kdj_j": 70.0,
            "kdj_k": 55.0,
            "kdj_d": 50.0,
            "previous_kdj_k": 48.0,
            "previous_kdj_d": 50.0,
            "macd_dif": 0.2,
            "macd_dea": 0.1,
            "macd": 0.2,
            "previous_macd": 0.1,
        }])

        with patch("strategy._build_strong_history_stats", return_value=stats):
            result = pick_stocks(market, pd.DataFrame([{"ts_code": "600001.SH"}]))

        self.assertEqual(len(result), 1)
        row = result.iloc[0]
        self.assertFalse(bool(row["trend_repairing"]))
        self.assertEqual(row["trend_stage"], "S2")

    def test_reversal_accepts_four_of_six_confirmation_indicators(self):
        market = build_market(close=10.0, pct_chg=3.0, vol=180.0)
        stats = pd.DataFrame([{
            "ts_code": "600001.SH",
            "hist_days": 100,
            "previous_close": 9.7,
            "high_drawdown_60": -30.0,
            "bottom_position_60": 0.30,
            "recent_low_60": 9.0,
            "recent_high_60": 14.0,
            "ret60": -25.0,
            "volume_expand_rate": 1.8,
            "washout_volume_ratio": 0.75,
            "washout_volume_shrink": True,
            "ma20": 10.5,
            "ma20_flat_or_up": False,
            "ma30_not_fast_down": False,
            "ma60": 10.8,
            "ma60_trend": "走平",
            "ma60_decline_slowing": False,
            "rsi6": 80.0,
            "kdj_j": 70.0,
            "kdj_k": 55.0,
            "kdj_d": 50.0,
            "previous_kdj_k": 48.0,
            "previous_kdj_d": 50.0,
            "macd_dif": -0.1,
            "macd_dea": 0.1,
            "macd": 0.1,
            "previous_macd": 0.0,
        }])

        with patch("strategy._build_strong_history_stats", return_value=stats):
            result = pick_stocks(market, pd.DataFrame([{"ts_code": "600001.SH"}]))

        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["reversal_indicator_count"], 4)

    def test_reversal_requires_kdj_golden_cross(self):
        market = build_market(close=10.0, pct_chg=3.0, vol=180.0)
        stats = pd.DataFrame([{
            "ts_code": "600001.SH",
            "hist_days": 100,
            "previous_close": 9.7,
            "high_drawdown_60": -30.0,
            "bottom_position_60": 0.30,
            "recent_low_60": 9.0,
            "recent_high_60": 14.0,
            "ret60": -25.0,
            "volume_expand_rate": 1.8,
            "washout_volume_ratio": 0.75,
            "washout_volume_shrink": True,
            "ma20": 10.5,
            "ma20_flat_or_up": False,
            "ma30_not_fast_down": False,
            "ma60": 10.8,
            "ma60_trend": "走平",
            "ma60_decline_slowing": True,
            "rsi6": 55.0,
            "kdj_j": 70.0,
            "kdj_k": 55.0,
            "kdj_d": 50.0,
            "previous_kdj_k": 52.0,
            "previous_kdj_d": 50.0,
            "macd_dif": -0.1,
            "macd_dea": 0.1,
            "macd": 0.2,
            "previous_macd": 0.1,
        }])

        with patch("strategy._build_strong_history_stats", return_value=stats):
            result = pick_stocks(market, pd.DataFrame([{"ts_code": "600001.SH"}]))

        self.assertTrue(result.empty)

    def test_reversal_accepts_macd_trending_up_before_zero_axis(self):
        market = build_market(close=10.0, pct_chg=3.0, vol=180.0)
        stats = pd.DataFrame([{
            "ts_code": "600001.SH",
            "hist_days": 100,
            "previous_close": 9.7,
            "high_drawdown_60": -30.0,
            "bottom_position_60": 0.30,
            "recent_low_60": 9.0,
            "recent_high_60": 14.0,
            "ret60": -25.0,
            "volume_expand_rate": 1.8,
            "washout_volume_ratio": 0.75,
            "washout_volume_shrink": True,
            "ma20": 10.5,
            "ma20_flat_or_up": False,
            "ma30_not_fast_down": False,
            "ma60": 10.8,
            "ma60_trend": "走平",
            "ma60_decline_slowing": True,
            "rsi6": 55.0,
            "kdj_j": 70.0,
            "kdj_k": 55.0,
            "kdj_d": 50.0,
            "previous_kdj_k": 48.0,
            "previous_kdj_d": 50.0,
            "macd_dif": -0.1,
            "macd_dea": 0.1,
            "macd": -0.1,
            "previous_macd": -0.2,
        }])

        with patch("strategy._build_strong_history_stats", return_value=stats):
            result = pick_stocks(market, pd.DataFrame([{"ts_code": "600001.SH"}]))

        self.assertEqual(len(result), 1)
        self.assertTrue(bool(result.iloc[0]["macd_trend_or_above_zero"]))

    def test_history_shorter_than_100_days_is_omitted(self):
        result = pick_stocks(build_market(), build_history(days=99))
        self.assertTrue(result.empty)

    def test_select_stock_pools_returns_three_named_pools(self):
        history = build_confirmed_reversal_history()
        market = build_market(close=history.iloc[-1]["close"], vol=150.0)

        pools = select_stock_pools(market, history)

        self.assertEqual(set(pools.keys()), {"reversal", "breakout", "first_limit"})
        self.assertEqual(len(pools["reversal"]), 1)
        self.assertTrue(pools["breakout"].empty)
        self.assertTrue(pools["first_limit"].empty)

    def test_select_stock_pools_reuses_history_stats(self):
        history = build_confirmed_reversal_history()
        market = build_market(close=history.iloc[-1]["close"], vol=150.0)
        stats = _build_strong_history_stats(history)

        with patch("strategy._build_strong_history_stats", return_value=stats) as build_stats:
            select_stock_pools(market, history)

        self.assertEqual(build_stats.call_count, 1)

    def test_select_stock_pools_builds_stats_for_base_candidates_only(self):
        history = pd.concat([
            build_confirmed_reversal_history("600001.SH"),
            build_confirmed_reversal_history("688001.SH"),
        ], ignore_index=True)
        market = pd.concat([
            build_market(ts_code="600001.SH", close=history[history["ts_code"] == "600001.SH"].iloc[-1]["close"], vol=150.0),
            build_market(ts_code="688001.SH", close=20.0),
        ], ignore_index=True)
        stats = _build_strong_history_stats(history[history["ts_code"] == "600001.SH"])

        with patch("strategy._build_strong_history_stats", return_value=stats) as build_stats:
            select_stock_pools(market, history)

        input_codes = set(build_stats.call_args.args[0]["ts_code"])
        self.assertEqual(input_codes, {"600001.SH"})

    def test_breakout_confluence_is_skipped_when_pre_filters_fail(self):
        history = build_confirmed_reversal_history()
        market = build_market(close=history.iloc[-1]["close"], vol=150.0, pct_chg=-2.0)
        stats = _build_strong_history_stats(history)

        with patch("strategy._build_breakout_confluence_stats", return_value=pd.DataFrame()) as confluence_stats:
            result = pick_breakout_stocks(market, history, history_stats=stats)

        self.assertTrue(result.empty)
        confluence_stats.assert_not_called()

    def test_sector_tail_buy_reuses_existing_breakout_pool(self):
        market = build_market(ts_code="600001.SH")
        history = build_history(ts_code="600001.SH", days=100)
        rep_stocks = pd.DataFrame([{"ts_code": "600001.SH"}])
        breakout = pd.DataFrame([{"ts_code": "600001.SH", "score": 88}])

        with patch("strategy.pick_breakout_stocks") as pick_breakout:
            result = pick_sector_tail_buy_stocks(market, history, rep_stocks, breakout_pool=breakout)

        pick_breakout.assert_not_called()
        self.assertEqual(result["ts_code"].tolist(), ["600001.SH"])

    def test_base_candidates_exclude_star_market(self):
        market = pd.concat([
            build_market(ts_code="688001.SH", close=12.0),
            build_market(ts_code="600001.SH", close=12.0),
        ], ignore_index=True)

        result = pick_strong_base_candidates(market)

        self.assertEqual(result["ts_code"].tolist(), ["600001.SH"])

    def test_breakout_pool_scores_volume_platform_breakout(self):
        history = build_daily_breakout_history()
        market = build_daily_breakout_market()

        result = pick_breakout_stocks(market, history)

        self.assertEqual(len(result), 1)
        row = result.iloc[0]
        self.assertTrue(bool(row["breakout_new_high"]))
        self.assertTrue(bool(row["volume_breakout"]))
        self.assertGreaterEqual(row["breakout_score"], 80)
        self.assertEqual(row["trend_stage"], "S2")
        self.assertEqual(row["stage_label"], "启动突破阶段")
        self.assertEqual(row["stage_action"], "可试仓")
        self.assertEqual(row["trade_state"], "等待回踩")
        self.assertIn("放量突破", row["breakout_reason"])

    def test_breakout_pool_prefers_daily_tail_auction_premium_setup(self):
        history = build_daily_breakout_history()
        market = build_daily_breakout_market()

        result = pick_breakout_stocks(market, history)

        self.assertEqual(len(result), 1)
        row = result.iloc[0]
        self.assertTrue(bool(row["close_near_high"]))
        self.assertTrue(bool(row["close_top20_position"]))
        self.assertTrue(bool(row["daily_tail_strength"]))
        self.assertGreaterEqual(row["overnight_premium_score"], 80)
        self.assertEqual(row["trade_state"], "等待回踩")
        self.assertIn("尾盘强势", row["breakout_reason"])

    def test_breakout_pool_allows_daily_proxy_score_when_tail_fields_are_missing(self):
        history = build_daily_breakout_history()
        market = build_daily_breakout_market()

        result = pick_breakout_stocks(market, history)

        self.assertEqual(len(result), 1)
        row = result.iloc[0]
        self.assertTrue(bool(row["tail_data_missing"]))
        self.assertTrue(bool(row["daily_proxy_qualified"]))
        self.assertEqual(row["breakout_entry_threshold"], 70)
        self.assertGreaterEqual(row["overnight_premium_score"], 70)

    def test_breakout_pool_accepts_boll_weekly_kdj_macd_confluence(self):
        market = build_daily_breakout_market()
        market.loc[market["ts_code"] == "600001.SH", ["close", "high", "low", "pct_chg", "vol"]] = [
            13.2, 13.35, 12.2, 8.0, 230.0,
        ]
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

    def test_breakout_pool_rejects_when_weekly_trend_is_down(self):
        market = build_daily_breakout_market()
        market.loc[market["ts_code"] == "600001.SH", ["close", "high", "low", "pct_chg", "vol"]] = [
            12.0, 12.2, 11.5, 6.0, 230.0,
        ]
        history = build_breakout_confluence_history(weekly_down=True)

        result = pick_breakout_stocks(market, history)

        self.assertTrue(result.empty)

    def test_breakout_pool_rejects_when_bollinger_width_is_not_expanding(self):
        market = build_daily_breakout_market()
        market.loc[market["ts_code"] == "600001.SH", ["close", "high", "low", "pct_chg", "vol"]] = [
            11.02, 11.05, 10.95, 3.0, 230.0,
        ]
        history = build_breakout_confluence_history(flat_boll=True)

        result = pick_breakout_stocks(market, history)

        self.assertTrue(result.empty)

    def test_breakout_pool_rejects_when_macd_is_far_below_zero_axis(self):
        market = build_daily_breakout_market()
        market.loc[market["ts_code"] == "600001.SH", ["close", "high", "low", "pct_chg", "vol"]] = [
            9.7, 9.85, 9.4, 4.0, 230.0,
        ]
        history = build_breakout_confluence_history(macd_deep_below_zero=True)

        result = pick_breakout_stocks(market, history)

        self.assertTrue(result.empty)

    def test_breakout_pool_rejects_long_upper_shadow_without_tail_strength(self):
        history = build_history(
            start_close=10.0,
            prior_close=11.0,
            latest_close=13.0,
            prior_volume=100.0,
            latest_volume=180.0,
            latest_pct_chg=5.0,
        )
        history.loc[history.index[-21:-1], "high"] = 12.0
        history.loc[history.index[-1], ["close", "high", "low"]] = [13.0, 13.8, 12.7]
        market = build_market(
            close=13.0,
            pct_chg=5.0,
            vol=180.0,
            turnover_rate=7.0,
            volume_ratio=2.2,
            amount=500_000_000,
        )
        market.loc[market.index[0], ["high", "low"]] = [13.8, 12.7]

        result = pick_breakout_stocks(market, history)

        self.assertTrue(result.empty)

    def test_breakout_pool_accepts_daily_proxy_volume_breakout_hard_rules(self):
        market = build_daily_breakout_market()
        history = build_daily_breakout_history()

        result = pick_breakout_stocks(market, history)

        self.assertEqual(result["ts_code"].tolist(), ["600001.SH"])
        row = result.iloc[0]
        self.assertTrue(bool(row["daily_tail_strength"]))
        self.assertTrue(bool(row["volume_breakout"]))
        self.assertTrue(bool(row["sector_leading"]))
        self.assertTrue(bool(row["trend_upward"]))
        self.assertIn("尾盘强势", row["breakout_reason"])

    def test_breakout_pool_accepts_relaxed_liquidity_and_sector_thresholds(self):
        market = build_relaxed_daily_breakout_market()
        history = build_daily_breakout_history()

        result = pick_breakout_stocks(market, history)

        self.assertEqual(result["ts_code"].tolist(), ["600001.SH"])
        row = result.iloc[0]
        self.assertGreaterEqual(row["amount_yuan"], 300_000_000)
        self.assertGreaterEqual(row["sector_limit_up_count"], 2)
        self.assertGreaterEqual(row["sector_amount_expand_rate"], 1.1)
        self.assertLessEqual(row["relative_strength_rank"], 5)

    def test_breakout_pool_scores_observation_candidate_without_previous_sector_amount(self):
        market = build_daily_breakout_market(include_prev_sector_amount=False)
        history = build_daily_breakout_history()

        result = pick_breakout_stocks(market, history)

        self.assertEqual(result["ts_code"].tolist(), ["600001.SH"])
        row = result.iloc[0]
        self.assertEqual(row["breakout_status"], "等待回踩")
        self.assertFalse(bool(row["sector_amount_expanded"]))
        self.assertGreaterEqual(row["breakout_score"], 80)

    def test_breakout_pool_infers_nearby_main_cost_zone_for_building_position(self):
        market = build_daily_breakout_market()
        market.loc[market["ts_code"] == "600001.SH", ["close", "high", "low", "pct_chg"]] = [51.0, 51.5, 49.5, 4.0]
        history = build_cost_zone_breakout_history()

        result = pick_breakout_stocks(market, history)

        self.assertEqual(result["ts_code"].tolist(), ["600001.SH"])
        row = result.iloc[0]
        self.assertLessEqual(row["main_cost_distance_pct"], 5)
        self.assertGreaterEqual(row["main_cost_score"], 8)
        self.assertIn("主力成本", row["main_cost_label"])
        self.assertIn("距离", row["main_cost_label"])
        self.assertEqual(row["trade_state"], "立即建仓")
        self.assertEqual(row["breakout_status"], row["trade_state"])
        self.assertEqual(row["trend_stage"], "S2")
        self.assertEqual(row["stage_label"], "启动突破阶段")
        self.assertEqual(row["stage_action"], "可试仓")

    def test_breakout_trade_state_does_not_fall_back_to_s1(self):
        stage = _classify_trend_stage(pd.Series({
            "breakout_score": 86,
            "trade_state": "立即建仓",
            "main_cost_distance_pct": 3.0,
            "risk_reject": False,
            "strong_consolidation": False,
            "volume_breakout": False,
            "rebound_volume_confirmed": False,
            "ma_bullish": True,
            "ma20_upward": True,
            "recent5_return": 8.0,
            "bottom_position_qualified": False,
            "washout_volume_shrink": False,
        }))

        self.assertEqual(stage["trend_stage"], "S2")
        self.assertEqual(stage["stage_label"], "启动突破阶段")
        self.assertEqual(stage["stage_action"], "可试仓")

    def test_breakout_pool_lowers_state_when_far_above_main_cost_zone(self):
        market = build_daily_breakout_market()
        market.loc[market["ts_code"] == "600001.SH", ["close", "high", "low", "pct_chg"]] = [52.0, 52.5, 50.0, 4.0]
        history = build_cost_zone_breakout_history(
            platform_close=30.0,
            latest_close=52.0,
            platform_high=31.0,
        )

        result = pick_breakout_stocks(market, history)

        self.assertEqual(result["ts_code"].tolist(), ["600001.SH"])
        row = result.iloc[0]
        self.assertGreater(row["main_cost_distance_pct"], 20)
        self.assertLessEqual(row["main_cost_score"], 2)
        self.assertIn(row["trade_state"], {"等待回踩", "加入观察池"})

    def test_breakout_pool_rejects_when_trend_is_not_formed(self):
        market = build_daily_breakout_market()
        history = build_daily_breakout_history()
        history.loc[history.index[-10:], "close"] = 10.0

        result = pick_breakout_stocks(market, history)

        self.assertTrue(result.empty)

    def test_breakout_pool_rejects_short_term_bullish_ma_stack_when_ma20_still_falls(self):
        market = build_daily_breakout_market()
        history = build_downward_ma20_bullish_history()
        latest_close = history.loc[history.index[-1], "close"]
        market.loc[market["ts_code"] == "600001.SH", ["close", "high", "low", "pct_chg", "vol"]] = [
            latest_close,
            latest_close * 1.01,
            latest_close * 0.98,
            3.5,
            180.0,
        ]

        result = pick_breakout_stocks(market, history)

        self.assertTrue(result.empty)

    def test_ma30_negative_slope_is_not_accepted_as_trend_ok(self):
        history = build_downward_ma20_bullish_history()

        stats = _build_strong_history_stats(history)

        row = stats.iloc[0]
        self.assertLess(row["ma30_slope_5"], 0)
        self.assertFalse(bool(row["ma30_not_fast_down"]))

    def test_breakout_pool_rejects_three_day_overheated_move(self):
        market = build_daily_breakout_market()
        history = build_daily_breakout_history()
        history.loc[history.index[-4:], "close"] = [10.0, 11.0, 11.8, 13.0]
        history.loc[history.index[-3:], "pct_chg"] = [10.0, 7.3, 10.2]

        result = pick_breakout_stocks(market, history)

        self.assertTrue(result.empty)

    def test_main_wave_start_pool_requires_five_of_seven_confirmations(self):
        history = build_main_wave_start_history()
        market = build_market(
            close=15.3,
            pct_chg=2.0,
            vol=160.0,
            turnover_rate=12.0,
            volume_ratio=3.0,
            amount=800_000_000,
        )

        result = pick_first_limit_stocks(market, history)

        self.assertEqual(len(result), 1)
        row = result.iloc[0]
        self.assertGreaterEqual(row["main_wave_confirmation_count"], 5)
        self.assertTrue(bool(row["main_wave_start"]))
        self.assertTrue(bool(row["ma_up_confirmed"]))
        self.assertTrue(bool(row["higher_lows_5d"]))
        self.assertGreaterEqual(row["first_limit_score"], 70)
        self.assertIn("主升浪启动", row["first_limit_reason"])

    def test_main_wave_start_pool_requires_core_inflow_leader_and_5b_market_cap(self):
        history = build_main_wave_start_history()
        market = build_market(
            industry="机器人概念",
            close=15.3,
            pct_chg=2.0,
            vol=160.0,
            turnover_rate=12.0,
            volume_ratio=3.0,
            amount=800_000_000,
            total_mv=600_000,
        )

        result = pick_first_limit_stocks(market, history, core_inflow_sectors=["机器人概念"])

        self.assertEqual(len(result), 1)
        row = result.iloc[0]
        self.assertTrue(bool(row["core_inflow_sector"]))
        self.assertTrue(bool(row["leader_market_cap_ok"]))
        self.assertTrue(bool(row["sector_leader_rank_ok"]))
        self.assertTrue(bool(row["kdj_after_golden_cross"]))
        self.assertTrue(bool(row["macd_after_golden_cross"]))
        self.assertTrue(bool(row["trend_upward"]))
        self.assertGreaterEqual(row["total_mv_yuan"], 5_000_000_000)
        self.assertIn("核心流入板块", row["first_limit_reason"])

        self.assertTrue(pick_first_limit_stocks(market, history, core_inflow_sectors=["新能源车"]).empty)

        low_cap_market = market.copy()
        low_cap_market.loc[0, "total_mv"] = 499_999
        self.assertTrue(pick_first_limit_stocks(low_cap_market, history, core_inflow_sectors=["机器人概念"]).empty)

        concept_market = market.copy()
        concept_market.loc[0, "industry"] = "专用设备"
        concept_market.loc[0, "concept"] = "机器人概念;人形机器人"
        self.assertEqual(
            len(pick_first_limit_stocks(concept_market, history, core_inflow_sectors=["人形机器人"])),
            1,
        )

    def test_main_wave_start_accepts_core_sector_liquidity_leader_when_pct_rank_is_lower(self):
        history = build_main_wave_start_history()
        target = build_market(
            industry="汽车配件",
            close=15.3,
            pct_chg=2.0,
            vol=160.0,
            turnover_rate=12.0,
            volume_ratio=3.0,
            amount=900_000_000,
            total_mv=800_000,
        )
        peers = [
            build_market(
                ts_code=f"60000{index}.SH",
                industry="汽车配件",
                pct_chg=3.0 + index,
                amount=100_000_000 + index,
                total_mv=200_000 + index,
            )
            for index in range(1, 7)
        ]
        market = pd.concat([target, *peers], ignore_index=True)

        result = pick_first_limit_stocks(market, history, core_inflow_sectors=["汽车"])

        self.assertEqual(len(result), 1)
        row = result.iloc[0]
        self.assertGreater(row["relative_strength_rank"], 5)
        self.assertEqual(row["core_sector_amount_rank"], 1)
        self.assertTrue(bool(row["sector_leader_rank_ok"]))

    def test_main_wave_start_accepts_new_hard_conditions_without_five_old_confirmations(self):
        market = build_market(
            industry="汽车配件",
            close=20.0,
            pct_chg=6.0,
            vol=160.0,
            turnover_rate=9.0,
            volume_ratio=3.0,
            amount=900_000_000,
            total_mv=800_000,
        )
        stats = pd.DataFrame([{
            "ts_code": "600001.SH",
            "ma5": 19.5,
            "ma10": 18.8,
            "ma20": 18.0,
            "ma5_upward": True,
            "ma10_upward": True,
            "ma20_upward": True,
            "ma30_not_fast_down": True,
            "pullback_not_break_key": False,
            "volume_expand_rate": 1.0,
            "pullback_volume_contracting": False,
            "main_cost_upward": True,
            "price_above_main_cost": True,
            "higher_lows_5d": False,
            "breakout_hold": False,
            "kdj_k": 60.0,
            "kdj_d": 50.0,
            "macd_golden_cross": True,
        }])

        with patch("strategy._build_strong_history_stats", return_value=stats):
            result = pick_first_limit_stocks(
                market,
                pd.DataFrame([{"ts_code": "600001.SH"}]),
                core_inflow_sectors=["汽车"],
            )

        self.assertEqual(len(result), 1)
        row = result.iloc[0]
        self.assertEqual(row["main_wave_confirmation_count"], 3)
        self.assertTrue(bool(row["core_leader_technical_start"]))
        self.assertTrue(bool(row["main_wave_start"]))


class SectorPotentialRankingTests(unittest.TestCase):
    def test_rank_sector_potential_prefers_resilient_volume_expanding_sector(self):
        market, history = build_sector_potential_fixture()

        result = rank_sector_potential(market, history, limit=5, leaders_per_sector=3)

        self.assertFalse(result.empty)
        self.assertEqual(result.iloc[0]["industry_name"], "能源强势")
        self.assertGreater(result.iloc[0]["short_score"], result.iloc[1]["short_score"])
        self.assertEqual(result.iloc[0]["signal_type"], "短线延续")
        self.assertGreaterEqual(len(result.iloc[0]["leader_stocks"]), 3)

    def test_rank_sector_potential_labels_rebound_sector(self):
        market, history = build_sector_potential_fixture()

        result = rank_sector_potential(market, history, limit=5, leaders_per_sector=3)
        rebound = result[result["industry_name"] == "有色反包"].iloc[0]

        self.assertEqual(rebound["signal_type"], "超跌反包")
        self.assertGreater(rebound["avg_pct_chg"], 4)
        self.assertLess(rebound["prev_avg_pct_chg"], -3)

    def test_rank_sector_potential_marks_existing_pool_leaders(self):
        market, history = build_sector_potential_fixture()
        breakout_pool = pd.DataFrame([{"ts_code": "600000.SH"}])
        first_limit_pool = pd.DataFrame([{"ts_code": "600001.SH"}])

        result = rank_sector_potential(
            market,
            history,
            breakout_pool=breakout_pool,
            first_limit_pool=first_limit_pool,
            limit=5,
            leaders_per_sector=3,
        )
        leaders = result.iloc[0]["leader_stocks"]
        tags = {item["ts_code"]: item.get("pool_tag") for item in leaders}

        self.assertEqual(tags.get("600000.SH"), "趋势突破")
        self.assertEqual(tags.get("600001.SH"), "主升浪启动")

    def test_rank_sector_potential_filters_leaders_by_large_cap_low_limit_pullback_box(self):
        market, history = build_sector_potential_fixture()

        result = rank_sector_potential(market, history, limit=5, leaders_per_sector=5)
        leaders = result.iloc[0]["leader_stocks"]

        self.assertEqual([item["ts_code"] for item in leaders[:3]], ["600002.SH", "600001.SH", "600000.SH"])
        self.assertTrue(all(item["total_mv_yuan"] >= 20_000_000_000 for item in leaders[:3]))
        self.assertTrue(all("总市值200亿以上" in item["leader_reason"] for item in leaders[:3]))
        self.assertTrue(all("低位涨停" in item["leader_reason"] for item in leaders[:3]))
        self.assertTrue(all("3日内回踩确认" in item["leader_reason"] for item in leaders[:3]))
        self.assertTrue(all("箱体震荡" in item["leader_reason"] for item in leaders[:3]))

    def test_rank_sector_potential_rejects_leader_without_recent_pullback_confirmation(self):
        market, history = build_sector_potential_fixture()
        recent_dates = sorted(history["trade_date"].unique())[-3:]
        mask = (history["ts_code"] == "600000.SH") & history["trade_date"].isin(recent_dates)
        history.loc[mask, ["close", "high", "low"]] = [14.0, 14.2, 13.8]

        result = rank_sector_potential(market, history, limit=5, leaders_per_sector=5)
        leaders = result.iloc[0]["leader_stocks"]

        self.assertNotIn("600000.SH", [item["ts_code"] for item in leaders])

    def test_rank_sector_potential_accepts_relaxed_leader_without_low_limit_up(self):
        market, history = build_sector_potential_fixture()
        dates = sorted(history["trade_date"].unique())
        relaxed_history = pd.DataFrame([
            {
                "ts_code": "600099.SH",
                "trade_date": trade_date,
                "close": 20 + index * 0.03,
                "high": 20 + index * 0.03 + 0.15,
                "low": 20 + index * 0.03 - 0.15,
                "vol": 180 + index,
                "amount": 600_000_000,
                "pct_chg": 2.5 if index == len(dates) - 1 else 0.3,
            }
            for index, trade_date in enumerate(dates)
        ])
        history = pd.concat([history, relaxed_history], ignore_index=True)
        relaxed_market = pd.DataFrame([{
            "ts_code": "600099.SH",
            "name": "放宽龙头",
            "industry": "能源强势",
            "close": relaxed_history.iloc[-1]["close"],
            "high": relaxed_history.iloc[-1]["high"],
            "low": relaxed_history.iloc[-1]["low"],
            "pct_chg": 2.5,
            "turnover_rate": 6.5,
            "volume_ratio": 1.9,
            "amount": 900_000_000,
            "total_mv": 1_500_000,
        }])
        market = pd.concat([market, relaxed_market], ignore_index=True)

        result = rank_sector_potential(market, history, limit=5, leaders_per_sector=20)
        leaders = result.iloc[0]["leader_stocks"]
        relaxed = [item for item in leaders if item["ts_code"] == "600099.SH"]

        self.assertEqual(len(relaxed), 1)
        self.assertIn("放宽龙头", relaxed[0]["leader_reason"])
        self.assertIn("形态2项", relaxed[0]["leader_reason"])

    def test_rank_sector_potential_returns_empty_for_missing_history(self):
        market, _history = build_sector_potential_fixture()

        result = rank_sector_potential(market, pd.DataFrame(), limit=5)

        self.assertTrue(result.empty)

    def test_attach_intraday_signal_stocks_filters_and_ranks_macd_kdj_crosses(self):
        sector_rows = []
        minute_bars = {}
        for index in range(7):
            ts_code = f"6009{index:02d}.SH"
            sector_rows.append({
                "ts_code": ts_code,
                "name": f"共振{index}",
                "industry": "能源强势",
                "close": 10 + index,
                "pct_chg": 1 + index,
                "turnover_rate": 5.0,
                "volume_ratio": 3.0 + index / 10,
                "amount": 300_000_000 + index * 10_000_000,
            })
            minute_bars[ts_code] = build_60min_bars(
                ts_code,
                water_macd_kdj_cross_closes() if index in (0, 2, 4, 6) else underwater_macd_kdj_cross_closes(),
            )
        sector_rows.append({
            "ts_code": "600998.SH",
            "name": "低量比",
            "industry": "能源强势",
            "close": 9.8,
            "pct_chg": 8.0,
            "turnover_rate": 5.0,
            "volume_ratio": 1.9,
            "amount": 900_000_000,
        })
        minute_bars["600998.SH"] = build_60min_bars("600998.SH", water_macd_kdj_cross_closes())
        sector_rows.append({
            "ts_code": "600999.SH",
            "name": "高换手",
            "industry": "能源强势",
            "close": 9.9,
            "pct_chg": 9.0,
            "turnover_rate": 11.0,
            "volume_ratio": 4.0,
            "amount": 950_000_000,
        })
        minute_bars["600999.SH"] = build_60min_bars("600999.SH", water_macd_kdj_cross_closes())
        market = pd.DataFrame(sector_rows)
        sector_potential = pd.DataFrame([{"industry_name": "能源强势", "potential_score": 90.0}])

        result = _attach_intraday_signal_stocks(sector_potential, market, minute_bars, per_sector=5)
        picks = result.iloc[0]["intraday_signal_stocks"]

        self.assertEqual(len(picks), 5)
        self.assertNotIn("600998.SH", [item["ts_code"] for item in picks])
        self.assertNotIn("600999.SH", [item["ts_code"] for item in picks])
        self.assertTrue(all(item["macd_golden_cross_60m"] for item in picks))
        self.assertTrue(picks[0]["macd_above_zero_60m"])
        self.assertTrue(picks[0]["kdj_golden_cross_60m"])
        self.assertGreaterEqual(picks[0]["intraday_signal_score"], picks[-1]["intraday_signal_score"])

    def test_attach_intraday_signal_stocks_accepts_recent_macd_cross_continuation(self):
        market = pd.DataFrame([{
            "ts_code": "600777.SH",
            "name": "延续走强",
            "industry": "能源强势",
            "close": 11.7,
            "pct_chg": 4.0,
            "turnover_rate": 5.0,
            "volume_ratio": 2.4,
            "amount": 260_000_000,
        }])
        sector_potential = pd.DataFrame([{"industry_name": "能源强势", "potential_score": 90.0}])
        minute_bars = {
            "600777.SH": build_60min_bars("600777.SH", water_macd_kdj_continuation_closes()),
        }

        result = _attach_intraday_signal_stocks(sector_potential, market, minute_bars, per_sector=5)
        picks = result.iloc[0]["intraday_signal_stocks"]

        self.assertEqual([item["ts_code"] for item in picks], ["600777.SH"])
        self.assertFalse(picks[0]["macd_golden_cross_60m"])
        self.assertTrue(picks[0]["macd_recent_golden_cross_60m"])
        self.assertTrue(picks[0]["macd_above_zero_60m"])
        self.assertTrue(picks[0]["macd_bullish_60m"])
        self.assertTrue(picks[0]["kdj_bullish_60m"])

    def test_attach_intraday_signal_stocks_scores_strong_tail_as_high_open_bias(self):
        market = pd.DataFrame([{
            "ts_code": "600778.SH",
            "name": "尾盘强",
            "industry": "能源强势",
            "close": 10.8,
            "pct_chg": 5.0,
            "turnover_rate": 5.0,
            "volume_ratio": 2.8,
            "amount": 360_000_000,
        }])
        sector_potential = pd.DataFrame([{"industry_name": "能源强势", "potential_score": 90.0}])
        prices = [10.0, 10.0, 10.0, 10.0, 10.0, 10.05, 10.08, 10.12, 10.2, 10.35, 10.5, 10.62, 10.8]
        volumes = [1000, 1000, 1000, 1000, 1000, 2500, 3000, 3200, 3800, 4200, 4500, 4700, 9000]
        minute_bars = {
            "600778.SH": {
                "60m": build_60min_bars("600778.SH", water_macd_kdj_continuation_closes()),
                "tail_1m": build_tail_1min_bars("600778.SH", prices, volumes),
            },
        }

        result = _attach_intraday_signal_stocks(sector_potential, market, minute_bars, per_sector=5)
        pick = result.iloc[0]["intraday_signal_stocks"][0]

        self.assertEqual(pick["next_day_bias"], "高开偏强")
        self.assertGreaterEqual(pick["tail_strength_score"], 75)
        self.assertGreater(pick["tail_return_after_1430"], 0.5)
        self.assertGreater(pick["tail_auction_return"], 0)
        self.assertIn("14:30后上涨", pick["next_day_bias_reason"])

    def test_attach_intraday_signal_stocks_marks_late_selloff_as_low_open_risk(self):
        market = pd.DataFrame([{
            "ts_code": "600779.SH",
            "name": "尾盘弱",
            "industry": "能源强势",
            "close": 9.75,
            "pct_chg": 4.0,
            "turnover_rate": 5.0,
            "volume_ratio": 2.6,
            "amount": 320_000_000,
        }])
        sector_potential = pd.DataFrame([{"industry_name": "能源强势", "potential_score": 90.0}])
        prices = [10.0, 10.0, 10.0, 10.0, 10.0, 9.98, 9.95, 9.92, 9.9, 9.86, 9.82, 9.78, 9.75]
        volumes = [1000, 1000, 1000, 1000, 1000, 2600, 3000, 3600, 4200, 4600, 5200, 5800, 9000]
        minute_bars = {
            "600779.SH": {
                "60m": build_60min_bars("600779.SH", water_macd_kdj_continuation_closes()),
                "tail_1m": build_tail_1min_bars("600779.SH", prices, volumes),
            },
        }

        result = _attach_intraday_signal_stocks(sector_potential, market, minute_bars, per_sector=5)
        pick = result.iloc[0]["intraday_signal_stocks"][0]

        self.assertEqual(pick["next_day_bias"], "低开风险")
        self.assertLessEqual(pick["tail_strength_score"], 45)
        self.assertLess(pick["tail_return_after_1430"], -0.5)
        self.assertIn("尾盘回落", pick["next_day_bias_reason"])

    def test_attach_intraday_signal_stocks_treats_auction_lift_to_tail_high_as_high_open_bias(self):
        market = pd.DataFrame([{
            "ts_code": "600780.SH",
            "name": "竞价抬价",
            "industry": "能源强势",
            "close": 10.15,
            "pct_chg": 8.0,
            "turnover_rate": 5.0,
            "volume_ratio": 2.5,
            "amount": 330_000_000,
        }])
        sector_potential = pd.DataFrame([{"industry_name": "能源强势", "potential_score": 90.0}])
        prices = [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.03, 10.04, 10.05, 10.06, 10.07, 10.07, 10.15]
        volumes = [3000, 3000, 3000, 3000, 3000, 3000, 1200, 1100, 1300, 1000, 1400, 1300, 2600]
        minute_bars = {
            "600780.SH": {
                "60m": build_60min_bars("600780.SH", water_macd_kdj_continuation_closes()),
                "tail_1m": build_tail_1min_bars("600780.SH", prices, volumes),
            },
        }

        result = _attach_intraday_signal_stocks(sector_potential, market, minute_bars, per_sector=5)
        pick = result.iloc[0]["intraday_signal_stocks"][0]

        self.assertEqual(pick["next_day_bias"], "高开偏强")
        self.assertGreater(pick["tail_auction_return"], 0.2)
        self.assertGreaterEqual(pick["tail_close_position"], 0.9)


class MainWiringTests(unittest.TestCase):
    @patch("main.analyze_stocks", return_value="分析完成")
    @patch("main.format_sectors_for_ai", return_value="板块文本")
    @patch("main.format_for_ai", return_value="股票文本")
    @patch("main.pick_dip_sectors", return_value=(pd.DataFrame(), pd.DataFrame()))
    @patch("main.get_sector_data", return_value=None)
    @patch("main.select_stock_pools", return_value={
        "reversal": pd.DataFrame(),
        "breakout": pd.DataFrame(),
        "first_limit": pd.DataFrame(),
    })
    @patch("main.get_recent_daily_data")
    @patch("main.get_market_data")
    def test_main_reuses_100_day_history_for_three_pools(
        self,
        get_market_data,
        get_recent_daily_data,
        select_stock_pools_mock,
        _get_sector_data,
        _pick_dip_sectors,
        _format_for_ai,
        _format_sectors_for_ai,
        _analyze_stocks,
    ):
        market = build_market()
        history = build_history()
        get_market_data.return_value = (market, "20260615")
        get_recent_daily_data.return_value = history

        main.main()

        get_recent_daily_data.assert_called_once_with("20260615", n=100)
        self.assertIs(select_stock_pools_mock.call_args.args[1], history)


class QuantServicePoolTests(unittest.TestCase):
    def test_dataframe_records_serializes_timestamp_values(self):
        frame = pd.DataFrame([{
            "ts_code": "600001.SH",
            "created_at": pd.Timestamp("2026-07-12 09:30:00"),
        }])

        records = quant_service.dataframe_to_records(frame)

        self.assertEqual(records[0]["created_at"], "2026-07-12 09:30:00")

    @patch("quant_service.analyze_stocks")
    @patch("quant_service.format_sectors_for_ai", return_value="板块文本")
    @patch("quant_service.format_for_ai", return_value="股票文本")
    @patch("quant_service.pick_dip_sectors", return_value=(pd.DataFrame(), pd.DataFrame()))
    @patch("quant_service.get_sector_data", return_value=None)
    @patch("quant_service.select_stock_pools", return_value={
        "reversal": pd.DataFrame(),
        "breakout": pd.DataFrame(),
        "first_limit": pd.DataFrame(),
    })
    @patch("quant_service.get_moneyflow_summary", return_value={
        "top_inflow": [
            {"name": "机器人概念", "net_amount": 14_374_000_000},
            {"name": "新能源车", "net_amount": 11_609_000_000},
        ],
        "top_outflow": [],
    })
    @patch("quant_service.get_recent_daily_data")
    @patch("quant_service.get_market_data")
    def test_scan_passes_moneyflow_top_inflow_to_stock_pools(
        self,
        get_market_data,
        get_recent_daily_data,
        _get_moneyflow_summary,
        select_stock_pools,
        _get_sector_data,
        _pick_dip_sectors,
        _format_for_ai,
        _format_sectors_for_ai,
        _analyze_stocks,
    ):
        history = build_main_wave_start_history()
        market = build_market(industry="机器人概念", close=15.3, vol=160.0)
        get_market_data.return_value = (market, "20260615")
        get_recent_daily_data.return_value = history

        quant_service.run_quant_scan(include_ai=False, limit=20)

        self.assertEqual(
            select_stock_pools.call_args.kwargs["core_inflow_sectors"],
            ["机器人概念", "新能源车"],
        )

    @patch("quant_service.analyze_stocks")
    @patch("quant_service.format_sectors_for_ai", return_value="板块文本")
    @patch("quant_service.format_for_ai", return_value="股票文本")
    @patch("quant_service.pick_dip_sectors", return_value=(pd.DataFrame(), pd.DataFrame()))
    @patch("quant_service.get_sector_data", return_value=None)
    @patch("quant_service.select_stock_pools", return_value={
        "reversal": pd.DataFrame(),
        "breakout": pd.DataFrame(),
        "first_limit": pd.DataFrame(),
    })
    @patch("quant_service.get_moneyflow_summary", return_value={
        "top_inflow": [],
        "top_outflow": [],
    })
    @patch("quant_service.get_recent_daily_data")
    @patch("quant_service.get_market_data")
    def test_scan_limits_history_to_100_days_needed_by_indicators(
        self,
        get_market_data,
        get_recent_daily_data,
        _get_moneyflow_summary,
        _select_stock_pools,
        _get_sector_data,
        _pick_dip_sectors,
        _format_for_ai,
        _format_sectors_for_ai,
        _analyze_stocks,
    ):
        history = build_main_wave_start_history()
        market = build_market(close=15.3, vol=160.0)
        get_market_data.return_value = (market, "20260615")
        get_recent_daily_data.return_value = history

        quant_service.run_quant_scan(include_ai=False, limit=20)

        get_recent_daily_data.assert_called_once_with("20260615", n=100)

    @patch("quant_service.analyze_stocks")
    @patch("quant_service.format_sectors_for_ai", return_value="板块文本")
    @patch("quant_service.format_for_ai", return_value="股票文本")
    @patch("quant_service.pick_dip_sectors", return_value=(pd.DataFrame(), pd.DataFrame()))
    @patch("quant_service.get_sector_data", return_value=None)
    @patch("quant_service.get_moneyflow_summary", return_value={})
    @patch("quant_service.get_recent_daily_data")
    @patch("quant_service.get_market_data")
    def test_scan_returns_three_pool_payload_with_legacy_fields(
        self,
        get_market_data,
        get_recent_daily_data,
        _get_moneyflow_summary,
        _get_sector_data,
        _pick_dip_sectors,
        _format_for_ai,
        _format_sectors_for_ai,
        _analyze_stocks,
    ):
        history = build_confirmed_reversal_history()
        market = build_market(close=history.iloc[-1]["close"], vol=150.0)
        get_market_data.return_value = (market, "20260615")
        get_recent_daily_data.return_value = history

        report = quant_service.run_quant_scan(include_ai=False, limit=20)

        self.assertEqual(set(report["pools"].keys()), {"reversal", "breakout", "first_limit"})
        self.assertEqual(len(report["pools"]["reversal"]), 1)
        self.assertEqual(report["dip"], report["pools"]["reversal"])
        self.assertEqual(report["strong"], report["pools"]["breakout"])
        self.assertEqual(report["first_limit"], report["pools"]["first_limit"])

    @patch("quant_service.analyze_stocks")
    @patch("quant_service.format_sectors_for_ai", return_value="板块文本")
    @patch("quant_service.format_for_ai", return_value="股票文本")
    @patch("quant_service.pick_dip_sectors", return_value=(pd.DataFrame(), pd.DataFrame()))
    @patch("quant_service.get_sector_data", return_value=None)
    @patch("quant_service.get_moneyflow_summary", return_value={"total_net_amount": 100.0})
    @patch("quant_service.get_recent_daily_data")
    @patch("quant_service.get_market_data")
    def test_scan_returns_moneyflow_summary(
        self,
        get_market_data,
        get_recent_daily_data,
        get_moneyflow_summary,
        _get_sector_data,
        _pick_dip_sectors,
        _format_for_ai,
        _format_sectors_for_ai,
        _analyze_stocks,
    ):
        history = build_confirmed_reversal_history()
        market = build_market(close=history.iloc[-1]["close"], vol=150.0)
        get_market_data.return_value = (market, "20260615")
        get_recent_daily_data.return_value = history

        report = quant_service.run_quant_scan(include_ai=False, limit=20)

        get_moneyflow_summary.assert_called_once_with("20260615")
        self.assertEqual(report["moneyflow_summary"]["total_net_amount"], 100.0)

    @patch("quant_service.rank_sector_potential", return_value=pd.DataFrame([{
        "rank": 1,
        "industry_name": "能源强势",
        "potential_score": 88.0,
        "short_score": 91.0,
        "swing_score": 85.0,
        "leader_stocks": [],
    }]))
    @patch("quant_service.analyze_stocks")
    @patch("quant_service.format_sectors_for_ai", return_value="板块文本")
    @patch("quant_service.format_for_ai", return_value="股票文本")
    @patch("quant_service.pick_dip_sectors", return_value=(pd.DataFrame(), pd.DataFrame()))
    @patch("quant_service.get_sector_data", return_value=None)
    @patch("quant_service.select_stock_pools", return_value={
        "reversal": pd.DataFrame(),
        "breakout": pd.DataFrame(),
        "first_limit": pd.DataFrame(),
    })
    @patch("quant_service.get_moneyflow_summary", return_value={})
    @patch("quant_service.get_recent_daily_data", return_value=pd.DataFrame([{
        "ts_code": "600001.SH",
        "trade_date": "20260714",
        "close": 10,
        "high": 10,
        "low": 9,
        "vol": 1,
        "amount": 1,
        "pct_chg": 1,
    }]))
    @patch("quant_service.get_market_data", return_value=(build_market(), "20260714"))
    def test_scan_returns_sector_potential(
        self,
        _get_market_data,
        _get_recent_daily_data,
        _get_moneyflow_summary,
        _select_stock_pools,
        _get_sector_data,
        _pick_dip_sectors,
        _format_for_ai,
        _format_sectors_for_ai,
        _analyze_stocks,
        rank_sector_potential_mock,
    ):
        report = quant_service.run_quant_scan(include_ai=False, limit=20)

        self.assertEqual(report["sector_potential"][0]["industry_name"], "能源强势")
        rank_sector_potential_mock.assert_called_once()


class DatabaseSectorPotentialTests(unittest.TestCase):
    def test_decode_report_defaults_missing_sector_potential_to_empty_list(self):
        row = {
            "id": 1,
            "trade_date": "20260714",
            "status": "success",
            "include_ai": 0,
            "strong_json": "[]",
            "dip_json": "[]",
            "sectors_json": "[]",
            "rep_stocks_json": "[]",
            "moneyflow_json": "{}",
            "ai_analysis": None,
            "error_message": None,
            "created_at": pd.Timestamp("2026-07-14 15:00:00").to_pydatetime(),
        }

        decoded = _decode_report(row)

        self.assertEqual(decoded["sector_potential"], [])


class AiPromptTests(unittest.TestCase):
    def test_prompt_compares_three_pools_for_near_term_position_priority(self):
        prompt = ai_agent._build_prompt(
            breakout_text="趋势突破池：突破股A",
            reversal_text="超跌反转池：反转股B",
            trade_date="20260707",
            first_limit_text="主升浪启动池：启动股C",
        )

        self.assertIn("趋势突破池：突破股A", prompt)
        self.assertIn("超跌反转池：反转股B", prompt)
        self.assertIn("主升浪启动池：启动股C", prompt)
        self.assertIn("三个池子", prompt)
        self.assertIn("最近建仓优先级", prompt)
        self.assertIn("Top 3", prompt)
        self.assertIn("最值得最近建仓", prompt)


class MoneyflowDataTests(unittest.TestCase):
    @patch("data_service._query_tushare")
    def test_moneyflow_summary_uses_dc_sector_moneyflow(self, query_tushare):
        query_tushare.return_value = pd.DataFrame([
            {
                "trade_date": "20260701",
                "content_type": "行业",
                "ts_code": "BK001.DC",
                "name": "强板块",
                "net_amount": 12_000_000_000,
                "net_amount_rate": 8.0,
                "buy_elg_amount": 7_000_000_000,
                "buy_lg_amount": 5_000_000_000,
                "rank": 1,
            },
            {
                "trade_date": "20260701",
                "content_type": "行业",
                "ts_code": "BK002.DC",
                "name": "弱板块",
                "net_amount": -3_000_000_000,
                "net_amount_rate": -4.0,
                "buy_elg_amount": -1_000_000_000,
                "buy_lg_amount": -2_000_000_000,
                "rank": 88,
            },
        ])

        summary = data_service.get_moneyflow_summary("20260701", limit=1)

        query_tushare.assert_called_once_with("moneyflow_ind_dc", trade_date="20260701")
        self.assertEqual(summary["trade_date"], "20260701")
        self.assertEqual(summary["source"], "moneyflow_ind_dc")
        self.assertEqual(summary["inflow_count"], 1)
        self.assertEqual(summary["outflow_count"], 1)
        self.assertEqual(summary["total_net_amount"], 9_000_000_000)
        self.assertEqual(summary["top_inflow"][0]["name"], "强板块")
        self.assertEqual(summary["top_outflow"][0]["name"], "弱板块")

    @patch("data_service.get_trade_dates", return_value=["20260702", "20260701"])
    @patch("data_service._query_tushare")
    def test_moneyflow_summary_falls_back_to_recent_available_trade_date(
        self,
        query_tushare,
        get_trade_dates,
    ):
        query_tushare.side_effect = [
            pd.DataFrame(),
            pd.DataFrame([{
                "trade_date": "20260701",
                "content_type": "行业",
                "ts_code": "BK001.DC",
                "name": "可用板块",
                "net_amount": 1_500_000_000,
                "net_amount_rate": 3.5,
                "rank": 1,
            }]),
        ]

        summary = data_service.get_moneyflow_summary("20260702", limit=1)

        get_trade_dates.assert_called_once_with(n=5, end_date="20260702")
        self.assertEqual(
            [call.kwargs["trade_date"] for call in query_tushare.call_args_list],
            ["20260702", "20260701"],
        )
        self.assertEqual(summary["requested_trade_date"], "20260702")
        self.assertEqual(summary["trade_date"], "20260701")
        self.assertEqual(summary["top_inflow"][0]["name"], "可用板块")


class HistoricalDataContractTests(unittest.TestCase):
    @patch("data_service.get_trade_dates", return_value=["20260615"])
    @patch("data_service._query_tushare")
    def test_recent_daily_data_requests_low_price(
        self,
        query_tushare,
        _get_trade_dates,
    ):
        query_tushare.return_value = pd.DataFrame([{
            "ts_code": "600001.SH",
            "trade_date": "20260615",
            "close": 10,
            "high": 11,
            "low": 9,
            "vol": 100,
            "pct_chg": 1,
        }])

        data_service.get_recent_daily_data("20260615", n=1)

        fields = query_tushare.call_args.kwargs["fields"].split(",")
        self.assertIn("low", fields)
        self.assertIn("amount", fields)


class SectorSelectionTests(unittest.TestCase):
    def test_sector_selection_returns_strongest_sectors_and_representative_winners(self):
        sector_df = pd.DataFrame([
            {"industry_name": "弱板块", "avg_pct_chg": -2.0, "stock_count": 8, "max_pct_chg": 1.0},
            {"industry_name": "第二强", "avg_pct_chg": 3.2, "stock_count": 6, "max_pct_chg": 6.0},
            {"industry_name": "最强板块", "avg_pct_chg": 4.5, "stock_count": 5, "max_pct_chg": 8.0},
        ])
        stock_merged = pd.DataFrame([
            {"ts_code": "600001.SH", "name": "强一", "industry": "最强板块", "close": 10, "pct_chg": 8.0, "turnover_rate": 9},
            {"ts_code": "600002.SH", "name": "强二", "industry": "最强板块", "close": 11, "pct_chg": 5.0, "turnover_rate": 6},
            {"ts_code": "600003.SH", "name": "弱一", "industry": "弱板块", "close": 12, "pct_chg": -1.0, "turnover_rate": 4},
            {"ts_code": "600004.SH", "name": "二强", "industry": "第二强", "close": 13, "pct_chg": 6.0, "turnover_rate": 7},
        ])

        sectors, reps = pick_dip_sectors(sector_df, stock_merged, top_n=2)

        self.assertEqual(sectors["industry_name"].tolist(), ["最强板块", "第二强"])
        self.assertEqual(sectors["sector_rank"].tolist(), [1, 2])
        self.assertGreater(sectors["sector_score"].iloc[0], sectors["sector_score"].iloc[1])
        self.assertEqual(reps["industry"].tolist(), ["最强板块", "最强板块", "第二强"])
        self.assertEqual(reps["ts_code"].tolist()[0], "600001.SH")

    def test_sector_tail_buy_stocks_filters_representatives_through_breakout_model(self):
        market = pd.concat([
            build_market(
                ts_code="600001.SH",
                industry="最强板块",
                close=13.0,
                pct_chg=10.0,
                vol=180.0,
                turnover_rate=9.0,
                volume_ratio=2.2,
                amount=600_000_000,
            ),
            build_market(
                ts_code="600002.SH",
                industry="最强板块",
                close=11.0,
                pct_chg=9.8,
                vol=100.0,
                turnover_rate=4.0,
                volume_ratio=1.0,
                amount=450_000_000,
            ),
            build_market(ts_code="600003.SH", industry="最强板块", close=12.0, pct_chg=9.7, amount=420_000_000),
            build_market(ts_code="600004.SH", industry="最强板块", close=10.0, pct_chg=3.0, amount=300_000_000),
            build_market(ts_code="600005.SH", industry="其他板块", pct_chg=4.0, amount=300_000_000),
        ], ignore_index=True)
        market.loc[market["ts_code"] == "600001.SH", ["high", "low"]] = [13.1, 11.8]
        market["prev_sector_amount_yuan"] = market["industry"].map({
            "最强板块": 1_000_000_000,
            "其他板块": 300_000_000,
        })
        rep_stocks = market[market["ts_code"].isin(["600001.SH", "600002.SH"])].copy()

        good_history = build_history(
            ts_code="600001.SH",
            start_close=8.0,
            prior_close=11.0,
            latest_close=13.0,
            prior_volume=100.0,
            latest_volume=180.0,
            days=100,
            latest_pct_chg=10.0,
        )
        good_history.loc[good_history.index[-21:-1], "high"] = 12.0
        good_history.loc[good_history.index[-3:-1], "pct_chg"] = [1.0, 2.0]
        good_history.loc[good_history.index[-1], ["close", "high", "low"]] = [13.0, 13.1, 11.8]
        weak_history = build_history(
            ts_code="600002.SH",
            start_close=10.0,
            prior_close=10.0,
            latest_close=10.0,
            prior_volume=100.0,
            latest_volume=100.0,
            days=100,
            latest_pct_chg=1.0,
        )
        history = pd.concat([good_history, weak_history], ignore_index=True)

        result = pick_sector_tail_buy_stocks(market, history, rep_stocks)

        self.assertEqual(result["ts_code"].tolist(), ["600001.SH"])
        self.assertGreaterEqual(result["overnight_premium_score"].iloc[0], 85)


if __name__ == "__main__":
    unittest.main()
