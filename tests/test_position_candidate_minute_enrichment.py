import copy
import unittest

import pandas as pd

from position_strategy_settings import DEFAULT_POSITION_STRATEGY_SETTINGS


def settings():
    return copy.deepcopy(DEFAULT_POSITION_STRATEGY_SETTINGS)


def minutes():
    return pd.DataFrame([
        {"trade_time": "2026-08-31 14:29:00", "close": 8.89, "high": 8.90, "low": 8.87, "vol": 100, "amount": 889},
        {"trade_time": "2026-08-31 14:30:00", "close": 8.92, "high": 8.93, "low": 8.89, "vol": 100, "amount": 892},
        {"trade_time": "2026-08-31 14:45:00", "close": 8.96, "high": 8.97, "low": 8.91, "vol": 120, "amount": 1075.2},
        {"trade_time": "2026-08-31 15:00:00", "close": 9.00, "high": 9.01, "low": 8.95, "vol": 150, "amount": 1350},
    ])


class MinuteMetricTests(unittest.TestCase):
    def test_calculates_vwap_hold_ratio_and_tail_stability(self):
        from position_candidate_minute_enrichment import calculate_minute_breakout_context

        result = calculate_minute_breakout_context(
            minutes(), {"pressure_high": 8.90, "breakout_trigger": 8.91}
        )
        self.assertAlmostEqual(result["intraday_vwap"], 8.9494, places=3)
        self.assertTrue(result["above_vwap"])
        self.assertEqual(result["breakout_hold_ratio"], 1.0)
        self.assertTrue(result["tail_stable_above_pressure"])
        self.assertGreater(result["tail_return_after_1430"], 0)

    def test_invalid_timestamps_return_missing_evidence(self):
        from position_candidate_minute_enrichment import calculate_minute_breakout_context

        result = calculate_minute_breakout_context(
            pd.DataFrame([{"trade_time": "bad", "close": 9.0}]),
            {"pressure_high": 8.9, "breakout_trigger": 8.91},
        )
        self.assertFalse(result["minute_breakout_data_available"])
        self.assertIn("分钟", result["minute_breakout_warning"])


class MinuteBudgetTests(unittest.TestCase):
    def test_enrichment_requests_at_most_ten_and_preserves_failures(self):
        from position_candidate_minute_enrichment import enrich_position_candidates_with_minutes

        calls = []

        def loader(code, trade_date):
            calls.append((code, trade_date))
            if code == "600003.SH":
                raise RuntimeError("minute unavailable")
            return minutes()

        rows = [{
            "ts_code": f"600{index:03d}.SH", "pressure_high": 8.9,
            "breakout_trigger": 8.91,
        } for index in range(12)]
        enriched, warnings, stats = enrich_position_candidates_with_minutes(
            rows, "20260831", loader, settings()
        )
        self.assertEqual(len(calls), 10)
        self.assertEqual(len(enriched), 12)
        failed = next(row for row in enriched if row["ts_code"] == "600003.SH")
        self.assertFalse(failed["minute_breakout_data_available"])
        self.assertTrue(warnings)
        self.assertEqual(stats["minute_enrichment_attempted"], 10)
        self.assertEqual(stats["minute_enrichment_failed"], 1)


if __name__ == "__main__":
    unittest.main()
