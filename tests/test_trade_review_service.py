import unittest
from unittest.mock import patch

import pandas as pd

from trade_review_service import build_trade_review_prompt, review_trade


def _history(days=150):
    dates = pd.date_range("2025-12-01", periods=days, freq="B")
    close = pd.Series([10 + index * 0.05 for index in range(days)], dtype=float)
    return pd.DataFrame({
        "ts_code": "600001.SH",
        "trade_date": dates.strftime("%Y%m%d"),
        "open": close - 0.1,
        "high": close + 0.2,
        "low": close - 0.3,
        "close": close,
        "vol": 1000 + close * 100,
        "pct_chg": 0.5,
    })


class TradeReviewServiceTests(unittest.TestCase):
    @patch("trade_review_service.analyze_prompt", return_value="复盘结论")
    @patch("trade_review_service.get_stock_daily_history_range")
    def test_builds_review_from_buy_to_sell_kline(self, get_history, analyze_prompt):
        history = _history()
        get_history.return_value = history
        buy_date = history.iloc[90]["trade_date"]
        sell_date = history.iloc[120]["trade_date"]

        review = review_trade({
            "tsCode": "600001.SH",
            "buyDate": buy_date,
            "buyPrice": 14.5,
            "positionStatus": "sold",
            "sellDate": sell_date,
            "sellPrice": 15.2,
            "lossStatus": "曾浮亏，未设止损",
            "holdingNote": "突破后追入",
        })

        self.assertEqual(review["ai_summary"], "复盘结论")
        self.assertEqual(review["trade"]["entry_trade_date"], buy_date)
        self.assertEqual(review["trade"]["exit_trade_date"], sell_date)
        self.assertEqual(len(review["trade_kline"]), 31)
        self.assertEqual(review["metrics"]["exit_or_current_price"], 15.2)
        self.assertIn("买入复盘", analyze_prompt.call_args.args[0])

    def test_rejects_sold_trade_without_exit_details(self):
        with self.assertRaisesRegex(ValueError, "卖出日期"):
            review_trade({
                "tsCode": "600001.SH",
                "buyDate": "20260601",
                "buyPrice": 10,
                "positionStatus": "sold",
            })

    def test_prompt_mentions_all_review_phases(self):
        prompt = build_trade_review_prompt({
            "trade": {"ts_code": "600001.SH"},
            "metrics": {"return_pct": -5},
            "entry_snapshot": {},
            "exit_snapshot": {},
            "trade_kline": [],
        })
        self.assertIn("买入复盘", prompt)
        self.assertIn("持仓复盘", prompt)
        self.assertIn("卖出复盘", prompt)
        self.assertIn("不保证收益", prompt)


if __name__ == "__main__":
    unittest.main()
