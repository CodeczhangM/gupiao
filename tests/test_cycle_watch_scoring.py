import unittest

import pandas as pd

from cycle_watch_scoring import evaluate_cycle_entry, normalize_cycle_watch_code


def low_buy_daily_fixture() -> pd.DataFrame:
    closes = [10.0 + index * 0.04 for index in range(22)] + [
        10.72, 10.60, 10.50, 10.44, 10.40, 10.40, 10.42, 10.43,
    ]
    volumes = [1_000_000] * 25 + [520_000, 500_000, 480_000, 470_000, 460_000]
    return pd.DataFrame([
        {
            "ts_code": "600000.SH",
            "trade_date": f"202607{index + 1:02d}",
            "open": close,
            "high": close + 0.08,
            "low": close - 0.08,
            "close": close,
            "vol": volumes[index],
            "pct_chg": 0.1,
        }
        for index, close in enumerate(closes)
    ])


def confirming_60m_fixture() -> pd.DataFrame:
    closes = [10.8 - index * 0.03 for index in range(20)] + [10.19, 10.17, 10.18, 10.24]
    return pd.DataFrame([
        {
            "ts_code": "600000.SH",
            "trade_time": f"2026-07-31 {9 + index // 4:02d}:{30 + (index % 4) * 15:02d}:00",
            "close": close,
            "high": close + 0.03,
            "low": close - 0.03,
        }
        for index, close in enumerate(closes)
    ])


class CycleWatchScoringTests(unittest.TestCase):
    def test_normalizes_supported_codes(self):
        self.assertEqual(normalize_cycle_watch_code("600000"), "600000.SH")
        self.assertEqual(normalize_cycle_watch_code("688981.sh"), "688981.SH")
        self.assertEqual(normalize_cycle_watch_code("000001"), "000001.SZ")
        self.assertEqual(normalize_cycle_watch_code("300750.sz"), "300750.SZ")

    def test_rejects_unsupported_and_mismatched_codes(self):
        for raw in ("920001", "600000.SZ", "123", "ABCDEF"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    normalize_cycle_watch_code(raw)

    def test_shrinking_pullback_near_support_emits_low_buy(self):
        result = evaluate_cycle_entry(
            "600000.SH",
            low_buy_daily_fixture(),
            pd.DataFrame(),
            {"close": 10.43, "pct_chg": 0.1, "volume_ratio": 0.72},
        )

        self.assertEqual(result["status"], "low_buy")
        self.assertGreaterEqual(result["opportunity_score"], 65)
        self.assertIn("回撤缩量", result["matched_conditions"])
        self.assertIn("等待60分钟确认", result["missing_conditions"])

    def test_daily_setup_with_two_confirmations_emits_confirmed(self):
        result = evaluate_cycle_entry(
            "600000.SH",
            low_buy_daily_fixture(),
            confirming_60m_fixture(),
            {
                "close": 10.62,
                "pct_chg": 2.5,
                "volume_ratio": 1.25,
                "intraday_vwap": 10.50,
            },
        )

        self.assertEqual(result["status"], "confirmed")
        self.assertGreaterEqual(result["factors"]["confirmation_count"], 2)

    def test_volume_break_below_ma20_forces_watch(self):
        result = evaluate_cycle_entry(
            "600000.SH",
            low_buy_daily_fixture(),
            pd.DataFrame(),
            {"close": 9.80, "pct_chg": -5.2, "volume_ratio": 2.1},
        )

        self.assertEqual(result["status"], "watch")
        self.assertIn("放量跌破MA20", result["risk_items"])

    def test_insufficient_daily_history_is_data_delayed(self):
        result = evaluate_cycle_entry(
            "600000.SH",
            low_buy_daily_fixture().tail(10),
            pd.DataFrame(),
            {"close": 10.4, "pct_chg": 0.0, "volume_ratio": 1.0},
        )

        self.assertEqual(result["status"], "data_delayed")
        self.assertIn("日线", result["invalidation_reason"])


if __name__ == "__main__":
    unittest.main()
