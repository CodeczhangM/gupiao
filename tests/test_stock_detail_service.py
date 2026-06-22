import unittest
from unittest.mock import patch

import pandas as pd

import data_service


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


if __name__ == "__main__":
    unittest.main()
