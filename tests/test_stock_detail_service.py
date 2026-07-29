import unittest
from unittest.mock import patch
import json
import math

import pandas as pd

import data_service
from stock_detail_service import (
    build_ai_prompt,
    build_technical_snapshot,
    find_strategy_signals,
    get_stock_technical_detail,
)


class StockDailyHistoryTests(unittest.TestCase):
    @patch("data_service.get_trade_dates", return_value=["20260615", "20260613", "20260612"])
    @patch("data_service._query_tushare")
    def test_loads_requested_stock_history_in_ascending_trade_date_order(
        self,
        query_tushare,
        get_trade_dates,
    ):
        query_tushare.return_value = pd.DataFrame([
            {"ts_code": "600001.SH", "trade_date": "20260615", "open": 12, "high": 13, "low": 11, "close": 12.5, "vol": 100, "pct_chg": 1},
            {"ts_code": "600001.SH", "trade_date": "20260613", "open": 11, "high": 12, "low": 10, "close": 11.5, "vol": 90, "pct_chg": 2},
            {"ts_code": "600001.SH", "trade_date": "20260613", "open": 99, "high": 99, "low": 99, "close": 99, "vol": 99, "pct_chg": 99},
            {"ts_code": "600001.SH", "trade_date": "20260611", "open": 10, "high": 11, "low": 9, "close": 10.5, "vol": 80, "pct_chg": 3},
        ])

        history = data_service.get_stock_daily_history("600001.SH", "20260615", n=3)

        get_trade_dates.assert_called_once_with(n=3, end_date="20260615")
        query_tushare.assert_called_once_with(
            "daily",
            ts_code="600001.SH",
            start_date="20260612",
            end_date="20260615",
            fields="ts_code,trade_date,open,high,low,close,vol,pct_chg",
        )
        self.assertEqual(
            list(history.columns),
            ["ts_code", "trade_date", "open", "high", "low", "close", "vol", "pct_chg"],
        )
        self.assertEqual(history["trade_date"].tolist(), ["20260613", "20260615"])
        self.assertEqual(history.loc[history["trade_date"] == "20260613", "close"].iloc[0], 11.5)


def _history(days=120):
    dates = pd.date_range("2026-01-01", periods=days, freq="B")
    close = pd.Series(range(10, 10 + days), dtype=float)
    return pd.DataFrame({
        "ts_code": "600001.SH",
        "trade_date": dates.strftime("%Y%m%d"),
        "open": close - 0.5,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "vol": close * 100,
        "pct_chg": 1.0,
    })


class StockDetailServiceTests(unittest.TestCase):
    @patch("stock_detail_service.get_stock_daily_history")
    @patch("stock_detail_service.get_report")
    def test_orchestrates_selected_report_history_and_prompt(self, get_report, get_history):
        report_stock = {
            "ts_code": "600001.SH",
            "name": "示例股份",
            "industry": "电子",
            "turnover_rate": 6.8,
            "breakout_score": 91,
        }
        get_report.return_value = {
            "id": 7,
            "trade_date": "20260615",
            "pools": {"breakout": [report_stock]},
        }
        get_history.return_value = _history(2).drop(columns=["turnover_rate"], errors="ignore")

        detail = get_stock_technical_detail("600001.SH", "20260615", report_id=7)

        get_report.assert_called_once_with(7)
        get_history.assert_called_once_with("600001.SH", "20260615", n=120)
        self.assertEqual(detail["report_id"], 7)
        self.assertEqual(detail["identity"], {"ts_code": "600001.SH", "name": "示例股份", "industry": "电子"})
        self.assertEqual(detail["strategy_signals"][0]["strategy"], "breakout")
        self.assertEqual(detail["latest"]["ohlcv"]["turnover_rate"], 6.8)
        self.assertFalse(detail["history_complete"])
        self.assertIn("示例股份", detail["prompt"])

    @patch("stock_detail_service.get_latest_report", return_value=None)
    def test_orchestration_rejects_invalid_code_and_absent_report(self, get_latest_report):
        with self.assertRaisesRegex(ValueError, "ts_code"):
            get_stock_technical_detail("bad", "20260615")

        with self.assertRaisesRegex(LookupError, "report"):
            get_stock_technical_detail("600001.SH", "20260615")
        get_latest_report.assert_called_once_with()

    def test_builds_complete_snapshot_for_exactly_120_candles(self):
        history = _history()
        history["turnover_rate"] = 3.25
        snapshot = build_technical_snapshot(history)

        self.assertTrue(snapshot["history_complete"])
        self.assertEqual(len(snapshot["candles"]), 120)
        self.assertEqual(snapshot["candles"][-1]["trade_date"], "20260617")
        self.assertEqual(snapshot["latest"]["moving_averages"]["ma5"], 127.0)
        self.assertEqual(snapshot["latest"]["volume"]["ma5"], 12700.0)
        self.assertEqual(snapshot["latest"]["support_resistance"]["support20"], 109.0)
        self.assertEqual(snapshot["latest"]["support_resistance"]["resistance60"], 130.0)
        self.assertIn("dif", snapshot["latest"]["macd"])
        self.assertIn("k", snapshot["latest"]["kdj"])
        self.assertIn("upper", snapshot["latest"]["bollinger"])
        self.assertEqual(snapshot["latest"]["ohlcv"]["turnover_rate"], 3.25)
        self.assertEqual(snapshot["latest"]["volume"]["turnover_rate"], 3.25)
        candle = snapshot["candles"][-1]
        self.assertEqual(candle["open"], 128.5)
        self.assertEqual(candle["vol"], 12900.0)
        self.assertEqual(candle["turnover_rate"], 3.25)
        self.assertEqual(candle["ma5"], 127.0)
        self.assertIn("dif", candle)
        self.assertIn("dea", candle)
        self.assertIn("histogram", candle)
        self.assertIn("k", candle)
        self.assertIn("d", candle)
        self.assertIn("j", candle)
        self.assertIn("rsi6", candle)
        self.assertIn("rsi12", candle)
        self.assertIn("rsi24", candle)
        self.assertIn("boll_upper", candle)
        self.assertIn("boll_middle", candle)
        self.assertIn("boll_lower", candle)
        self.assertEqual(candle["volume_ma5"], 12700.0)
        self.assertEqual(candle["support_20"], 109.0)
        self.assertEqual(candle["resistance_60"], 130.0)
        self.assertEqual(json.loads(json.dumps(snapshot)), snapshot)

    def test_uses_simple_rolling_average_gain_and_loss_for_rsi(self):
        close = [10, 12, 11, 14, 13, 15, 14]
        history = _history(len(close))
        history["close"] = close
        history["open"] = close
        history["high"] = [value + 1 for value in close]
        history["low"] = [value - 1 for value in close]

        snapshot = build_technical_snapshot(history)

        # Six changes: gains 2 + 3 + 2 and losses 1 + 1 + 1 => RSI = 70.
        self.assertEqual(snapshot["latest"]["rsi"]["rsi6"], 70.0)
        self.assertEqual(snapshot["candles"][-1]["rsi6"], 70.0)

    def test_short_history_keeps_unavailable_indicators_json_safe(self):
        history = _history(3)
        history.loc[2, "close"] = math.nan

        snapshot = build_technical_snapshot(history)

        self.assertFalse(snapshot["history_complete"])
        self.assertEqual(len(snapshot["candles"]), 3)
        self.assertIsNone(snapshot["candles"][-1]["close"])
        self.assertIsNone(snapshot["candles"][-1]["turnover_rate"])
        self.assertIsNone(snapshot["latest"]["ohlcv"]["turnover_rate"])
        self.assertIsNone(snapshot["latest"]["volume"]["turnover_rate"])
        self.assertIsNone(snapshot["latest"]["moving_averages"]["ma5"])
        self.assertIsNone(snapshot["latest"]["bollinger"]["middle"])

    def test_finds_stock_in_pools_and_legacy_report_fields(self):
        pool_stock = {"ts_code": "600001.SH", "name": "示例股"}
        legacy_stock = {"ts_code": "000001.SZ", "name": "旧字段股"}
        report = {
            "pools": {"breakout": [pool_stock]},
            "dip": [legacy_stock],
        }

        self.assertEqual(find_strategy_signals(report, "600001.SH"), [{"strategy": "breakout", **pool_stock}])
        self.assertEqual(find_strategy_signals(report, "000001.SZ"), [{"strategy": "reversal", **legacy_stock}])
        self.assertEqual(find_strategy_signals({}, "600001.SH"), [])

    def test_builds_chinese_prompt_without_calling_an_ai(self):
        snapshot = build_technical_snapshot(_history())
        detail = {
            "identity": {"ts_code": "600001.SH", "name": "示例股"},
            "trade_date": "20260617",
            **snapshot,
            "strategy_signals": [{"strategy": "breakout", "ts_code": "600001.SH", "score": 88}],
            "signals": [{"strategy": "obsolete", "ts_code": "600001.SH"}],
        }

        prompt = build_ai_prompt(detail)

        self.assertIn("600001.SH", prompt)
        self.assertIn("20260617", prompt)
        self.assertIn("MACD", prompt)
        self.assertIn("breakout", prompt)
        self.assertNotIn("obsolete", prompt)
        self.assertIn("建仓", prompt)
        self.assertIn("减仓", prompt)
        self.assertIn("止损", prompt)
        self.assertIn("不执行交易、不保证收益", prompt)


if __name__ == "__main__":
    unittest.main()
