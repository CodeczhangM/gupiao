import unittest
from unittest.mock import patch

import pandas as pd

from trend_box_target_service import analyze_trend_box_target


def _rows(values):
    return pd.DataFrame(
        [
            {
                "ts_code": "600001.SH",
                "trade_date": f"202601{idx + 1:02d}",
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "vol": 1000 + idx,
                "pct_chg": pct_chg,
            }
            for idx, (open_, high, low, close, pct_chg) in enumerate(values)
        ]
    )


def _rows_for(ts_code, values):
    rows = _rows(values)
    rows["ts_code"] = ts_code
    return rows


def _dated_rows(ts_code, rows):
    return pd.DataFrame(
        [
            {
                "ts_code": ts_code,
                "trade_date": trade_date,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "vol": 1000 + idx,
                "pct_chg": pct_chg,
            }
            for idx, (trade_date, open_, high, low, close, pct_chg) in enumerate(rows)
        ]
    )


class TrendBoxTargetServiceTests(unittest.TestCase):
    def test_remeasures_a_strong_wave_from_a_relay_box(self):
        values = []
        for close in [10.2, 10.0, 9.8, 9.7, 9.9, 10.1, 10.0, 9.8, 10.2, 10.3, 10.0, 9.9, 10.1, 10.2, 10.4]:
            values.append((close - 0.1, close + 0.25, close - 0.35, close, 0.4))
        values.extend(
            [
                (10.6, 10.9, 10.4, 10.8, 3.8),
                (11.1, 11.9, 10.9, 11.8, 9.3),
                (12.0, 12.7, 11.8, 12.5, 5.9),
                (12.4, 12.9, 12.1, 12.6, 0.8),
                (12.7, 13.2, 12.2, 12.9, 2.4),
                (12.6, 13.1, 12.1, 12.8, -0.8),
                (12.8, 13.3, 12.4, 13.0, 1.6),
                (13.7, 14.6, 13.5, 14.4, 10.8),
                (14.6, 15.7, 14.4, 15.4, 6.9),
                (15.3, 16.0, 15.0, 15.8, 2.6),
                (15.9, 16.3, 15.5, 16.1, 1.9),
                (16.0, 16.2, 15.6, 15.9, -1.2),
            ]
        )

        result = analyze_trend_box_target(_rows(values), ts_code="600001.SH")

        self.assertTrue(result["trend"]["is_uptrend"])
        self.assertTrue(result["trend"]["has_reversal_trend"])
        self.assertTrue(result["trend"]["sideways_ended"])
        self.assertTrue(result["trend"]["target_available"])
        self.assertEqual(result["trend"]["state_label"], "最近K线反转，横盘已结束")
        self.assertGreaterEqual(len(result["segments"]), 1)
        self.assertEqual(result["segments"][0]["segment"], "base")
        self.assertEqual(result["segments"][0]["raw_result"], "超出")

    def test_manual_box_without_breakout_does_not_inherit_auto_trend_status(self):
        rows = []
        for idx, close in enumerate([10.2, 10.0, 9.8, 9.7, 9.9, 10.1, 10.0, 9.8, 10.2, 10.3, 10.0, 9.9, 10.1, 10.2, 10.4], 1):
            rows.append((f"202607{idx:02d}", close - 0.1, close + 0.25, close - 0.35, close, 0.4))
        rows.extend(
            [
                ("20260716", 10.6, 10.9, 10.4, 10.8, 3.8),
                ("20260717", 11.1, 11.9, 10.9, 11.8, 9.3),
                ("20260718", 12.0, 12.7, 11.8, 12.5, 5.9),
                ("20260719", 12.4, 12.9, 12.1, 12.6, 0.8),
                ("20260720", 12.7, 13.2, 12.2, 12.9, 2.4),
                ("20260721", 12.6, 13.1, 12.1, 12.8, -0.8),
                ("20260722", 12.8, 13.3, 12.4, 13.0, 1.6),
                ("20260723", 13.7, 14.6, 13.5, 14.4, 10.8),
                ("20260724", 14.6, 15.7, 14.4, 15.4, 6.9),
                ("20260725", 15.3, 16.0, 15.0, 15.8, 2.6),
                ("20260726", 15.9, 16.3, 15.5, 15.9, 0.6),
            ]
        )

        result = analyze_trend_box_target(
            _dated_rows("600001.SH", rows),
            ts_code="600001.SH",
            manual_box={"start": "20260724", "end": "20260726"},
        )

        self.assertFalse(result["manual_box"]["sideways_ended"])
        self.assertFalse(result["trend"]["has_reversal_trend"])
        self.assertFalse(result["trend"]["sideways_ended"])
        self.assertFalse(result["trend"]["target_available"])
        self.assertEqual(result["trend"]["state_label"], "最近K线未形成反转")
        self.assertIsNone(result["current_target"])

    @patch("trend_box_target_service.load_recent_daily", side_effect=RuntimeError("cache unavailable"))
    @patch("trend_box_target_service.get_stock_daily_history")
    def test_service_loads_stock_history_and_reports_recent_wave_validation(self, get_history, _cache):
        values = []
        for close in [20.1, 20.0, 19.7, 19.6, 19.9, 20.2, 20.0, 19.8, 20.1, 20.3, 20.2, 19.9, 20.1, 20.3, 20.4]:
            values.append((close - 0.1, close + 0.3, close - 0.4, close, 0.3))
        values.extend(
            [
                (20.5, 21.0, 20.4, 20.9, 2.5),
                (21.0, 21.8, 20.9, 21.7, 3.8),
                (21.8, 22.4, 21.5, 22.1, 1.8),
                (22.2, 22.8, 21.9, 22.6, 2.3),
                (22.5, 23.0, 22.1, 22.8, 0.9),
            ]
        )
        get_history.return_value = _rows(values)

        result = analyze_trend_box_target("600001.SH", end_trade_date="20260201", lookback_days=60)

        get_history.assert_called_once_with("600001.SH", "20260201", n=60)
        self.assertEqual(result["identity"]["ts_code"], "600001.SH")
        self.assertTrue(result["trend"]["is_uptrend"])
        self.assertEqual(result["wave_backtest"]["available_segments"], 1)
        self.assertEqual(result["current_target"]["source_segment"], "base")

    @patch("trend_box_target_service.get_stock_daily_history")
    @patch("trend_box_target_service.load_recent_daily")
    def test_service_prefers_cached_history_before_remote_daily_query(self, load_recent_daily, get_history):
        values = []
        for close in [20.1, 20.0, 19.7, 19.6, 19.9, 20.2, 20.0, 19.8, 20.1, 20.3, 20.2, 19.9, 20.1, 20.3, 20.4]:
            values.append((close - 0.1, close + 0.3, close - 0.4, close, 0.3))
        values.extend(
            [
                (20.5, 21.0, 20.4, 20.9, 2.5),
                (21.0, 21.8, 20.9, 21.7, 3.8),
                (21.8, 22.4, 21.5, 22.1, 1.8),
                (22.2, 22.8, 21.9, 22.6, 2.3),
                (22.5, 23.0, 22.1, 22.8, 0.9),
            ]
        )
        load_recent_daily.return_value = _rows(values)

        result = analyze_trend_box_target("600001.SH", end_trade_date="20260201", lookback_days=60)

        load_recent_daily.assert_called_once_with("20260201", 60)
        get_history.assert_not_called()
        self.assertEqual(result["identity"]["ts_code"], "600001.SH")

    @patch("trend_box_target_service.get_stock_daily_history")
    @patch("trend_box_target_service.load_recent_daily")
    def test_service_normalizes_mistyped_exchange_suffix_from_code_prefix(self, load_recent_daily, get_history):
        values = []
        for close in [20.1, 20.0, 19.7, 19.6, 19.9, 20.2, 20.0, 19.8, 20.1, 20.3, 20.2, 19.9, 20.1, 20.3, 20.4]:
            values.append((close - 0.1, close + 0.3, close - 0.4, close, 0.3))
        values.extend(
            [
                (20.5, 21.0, 20.4, 20.9, 2.5),
                (21.0, 21.8, 20.9, 21.7, 3.8),
                (21.8, 22.4, 21.5, 22.1, 1.8),
                (22.2, 22.8, 21.9, 22.6, 2.3),
                (22.5, 23.0, 22.1, 22.8, 0.9),
            ]
        )
        load_recent_daily.return_value = _rows_for("000975.SZ", values)

        result = analyze_trend_box_target("000975.SH", end_trade_date="20260201", lookback_days=60)

        get_history.assert_not_called()
        self.assertEqual(result["identity"]["requested_ts_code"], "000975.SH")
        self.assertEqual(result["identity"]["ts_code"], "000975.SZ")

    def test_wave_peak_stops_before_next_wave_after_sharp_drop_and_allows_three_day_relay(self):
        rows = []
        for idx, close in enumerate([8.8, 8.7, 8.6, 8.5, 8.7, 8.8, 8.6, 8.7, 8.9, 8.8, 8.7, 8.8, 8.9, 8.8, 8.9], 1):
            rows.append((f"202605{idx:02d}", close, close + 0.2, close - 0.3, close, 0.2))
        rows.extend(
            [
                ("20260516", 9.0, 9.5, 8.9, 9.3, 4.5),
                ("20260517", 9.4, 10.2, 9.3, 10.1, 8.6),
                ("20260518", 10.2, 11.2, 10.0, 10.8, 6.9),
                ("20260519", 10.8, 12.0, 10.7, 11.7, 8.3),
                ("20260520", 11.5, 12.8, 11.2, 11.9, 1.7),
                ("20260521", 11.1, 11.2, 10.1, 10.2, -8.9),
                ("20260522", 10.0, 10.6, 9.7, 10.0, -2.0),
                ("20260523", 10.1, 10.5, 9.8, 10.2, 2.0),
                ("20260524", 10.2, 10.7, 9.9, 10.4, 2.0),
                ("20260525", 10.6, 11.4, 10.5, 11.2, 7.7),
                ("20260526", 11.3, 12.2, 11.0, 12.0, 7.1),
                ("20260527", 12.1, 12.6, 11.7, 12.3, 2.5),
                ("20260528", 11.2, 11.3, 10.8, 11.0, -10.5),
            ]
        )

        result = analyze_trend_box_target(
            _dated_rows("600707.SH", rows),
            ts_code="600707.SH",
            params={"relay_search_starts_after_confirm_days": 1},
        )

        self.assertGreaterEqual(len(result["segments"]), 2)
        self.assertEqual(result["segments"][0]["peak_date"], "20260520")
        self.assertEqual(result["segments"][0]["wave_end_date"], "20260520")
        self.assertEqual(result["segments"][1]["box_start"], "20260522")
        self.assertEqual(result["segments"][1]["box_end"], "20260524")
        self.assertEqual(result["segments"][1]["peak_date"], "20260527")

    def test_manual_box_interval_marks_breakout_confirmation_and_target(self):
        rows = _dated_rows(
            "600667.SH",
            [
                ("20260722", 16.62, 17.77, 16.49, 16.69, 0.0),
                ("20260723", 16.98, 17.27, 16.13, 16.23, -2.75),
                ("20260724", 15.80, 16.93, 15.78, 16.29, 0.37),
                ("20260727", 16.78, 17.92, 16.00, 17.92, 10.0),
                ("20260728", 17.92, 19.45, 17.88, 18.37, 2.51),
                ("20260729", 18.00, 18.80, 16.53, 17.40, -5.28),
                ("20260730", 16.85, 17.37, 15.66, 15.66, -10.0),
                ("20260731", 17.23, 17.23, 16.58, 17.23, 10.02),
                ("20260803", 16.73, 17.21, 16.11, 16.28, -5.51),
                ("20260804", 16.52, 17.26, 15.81, 17.15, 5.34),
                ("20260805", 17.20, 18.87, 17.19, 18.45, 7.58),
                ("20260806", 17.80, 19.60, 17.75, 19.17, 3.90),
                ("20260807", 18.90, 19.98, 18.45, 19.53, 1.88),
                ("20260810", 19.58, 19.90, 18.60, 19.27, -1.33),
                ("20260811", 18.84, 21.20, 18.80, 21.20, 10.02),
                ("20260812", 22.00, 23.00, 21.32, 22.71, 7.12),
                ("20260813", 22.80, 24.88, 21.91, 23.78, 4.71),
                ("20260814", 23.50, 24.15, 22.61, 22.95, -3.49),
            ],
        )

        result = analyze_trend_box_target(
            rows,
            ts_code="600667.SH",
            manual_box={"start": "20260722", "end": "20260804"},
        )

        manual = result["manual_box"]
        self.assertTrue(manual["sideways_ended"])
        self.assertEqual(manual["confirm_date"], "20260806")
        self.assertEqual(manual["box_max"], 19.45)
        self.assertEqual(manual["box_min"], 15.66)
        self.assertAlmostEqual(manual["distance"], 3.79)
        self.assertAlmostEqual(manual["target_low"], 23.24)
        self.assertAlmostEqual(manual["target_high"], 27.03)
        self.assertEqual(manual["peak_date"], "20260813")
        self.assertEqual(manual["raw_result"], "命中")
        self.assertEqual(result["current_target"]["source_segment"], "manual")

    def test_manual_box_probe_outputs_predictive_target_before_full_confirmation(self):
        rows = _dated_rows(
            "601288.SH",
            [
                ("20260806", 6.49, 6.53, 6.41, 6.50, 0.31),
                ("20260807", 6.46, 6.51, 6.41, 6.47, -0.46),
                ("20260810", 6.43, 6.52, 6.41, 6.46, -0.15),
                ("20260811", 6.46, 6.51, 6.42, 6.48, 0.31),
                ("20260812", 6.44, 6.46, 6.36, 6.44, -0.62),
                ("20260813", 6.39, 6.56, 6.39, 6.56, 1.86),
                ("20260814", 6.51, 6.55, 6.46, 6.47, -1.37),
            ],
        )

        result = analyze_trend_box_target(
            rows,
            ts_code="601288.SH",
            manual_box={"start": "20260806", "end": "20260812"},
        )

        manual = result["manual_box"]
        self.assertEqual(manual["breakout_stage"], "probe")
        self.assertTrue(manual["breakout_started"])
        self.assertFalse(manual["sideways_ended"])
        self.assertEqual(manual["confirm_date"], "20260813")
        self.assertAlmostEqual(manual["target_low"], 6.70)
        self.assertAlmostEqual(manual["target_high"], 6.87)
        self.assertEqual(result["trend"]["state_label"], "突破试探，目标待确认")
        self.assertTrue(result["trend"]["target_available"])
        self.assertEqual(result["current_target"]["target_status"], "预测")


if __name__ == "__main__":
    unittest.main()
