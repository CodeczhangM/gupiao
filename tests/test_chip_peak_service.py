import unittest
from unittest.mock import patch

import pandas as pd

from chip_peak_service import (
    _washout_label,
    attach_chip_peak_fields,
    build_chip_peak_fields,
    calculate_concentration,
    clear_chip_peak_cache,
    extract_chip_peaks,
    load_chip_data,
)


def history_frame(low=8.0, high=12.0, ts_code="600001.SH"):
    return pd.DataFrame([
        {
            "ts_code": ts_code,
            "trade_date": f"202607{index + 1:02d}",
            "low": low,
            "high": high,
        }
        for index in range(20)
    ])


def perf_frame(
    cost_15=9.0,
    cost_85=11.0,
    cost_5=8.5,
    cost_95=11.5,
    winner=40.0,
):
    return pd.DataFrame([{
        "ts_code": "600001.SH",
        "trade_date": "20260730",
        "cost_15pct": cost_15,
        "cost_85pct": cost_85,
        "cost_5pct": cost_5,
        "cost_95pct": cost_95,
        "weight_avg": 10.1,
        "winner_rate": winner,
    }])


def valid_chips_frame(ts_code="600001.SH"):
    return pd.DataFrame([
        {
            "ts_code": ts_code,
            "trade_date": "20260730",
            "price": 8.7,
            "percent": 14.0,
        },
        {
            "ts_code": ts_code,
            "trade_date": "20260730",
            "price": 9.2,
            "percent": 8.0,
        },
    ])


class ChipPeakServiceTests(unittest.TestCase):
    def setUp(self):
        clear_chip_peak_cache()

    def test_concentration_accepts_ten_and_fifteen_percent_boundaries(self):
        self.assertAlmostEqual(calculate_concentration(90, 110), 10.0)
        self.assertAlmostEqual(calculate_concentration(85, 115), 15.0)

    def test_concentration_rejects_invalid_cost_ranges(self):
        self.assertIsNone(calculate_concentration(None, 110))
        self.assertIsNone(calculate_concentration(110, 90))
        self.assertIsNone(calculate_concentration(0, 0))

    def test_peak_extraction_skips_adjacent_bins_for_secondary_peak(self):
        chips = pd.DataFrame([
            {"price": 10.00, "percent": 12.0},
            {"price": 10.10, "percent": 11.5},
            {"price": 9.50, "percent": 9.0},
        ])

        result = extract_chip_peaks(chips)

        self.assertEqual(result["chip_peak_price"], 10.0)
        self.assertEqual(result["chip_peak_percent"], 12.0)
        self.assertEqual(result["chip_secondary_peak_price"], 9.5)
        self.assertEqual(result["chip_secondary_peak_percent"], 9.0)

    def test_dense_bottom_peak_with_washout_structure_is_buildable(self):
        chips = pd.DataFrame([
            {"price": 8.7, "percent": 14.0},
            {"price": 9.2, "percent": 8.0},
        ])
        row = {
            "ts_code": "600001.SH",
            "current_price": 9.0,
            "bottom_consolidation": True,
            "bottom_volume_contraction": 0.7,
            "bottom_ma_convergence_pct": 4.0,
        }

        result = build_chip_peak_fields(
            row,
            chips,
            perf_frame(),
            history_frame(),
        )

        self.assertEqual(result["chip_peak_bottom_position_pct"], 17.5)
        self.assertAlmostEqual(
            result["chip_price_distance_pct"],
            3.448275862,
            places=6,
        )
        self.assertGreaterEqual(result["chip_washout_score"], 80)
        self.assertTrue(result["chip_build_position"])
        self.assertEqual(
            result["chip_washout_label"],
            "底部洗盘 · 可建仓",
        )

    def test_score_label_boundaries_are_stable(self):
        self.assertEqual(_washout_label(80), "底部洗盘 · 可建仓")
        self.assertEqual(
            _washout_label(65),
            "底部筹码密集 · 等待确认",
        )
        self.assertEqual(_washout_label(45), "筹码整理")
        self.assertEqual(_washout_label(44.999), "筹码结构偏弱")

    def test_missing_peak_or_both_concentrations_returns_unavailable(self):
        result = build_chip_peak_fields(
            {"ts_code": "600001.SH", "current_price": 9.0},
            pd.DataFrame(),
            pd.DataFrame(),
            history_frame(),
        )

        self.assertFalse(result["chip_data_complete"])
        self.assertFalse(result["chip_build_position"])
        self.assertEqual(result["chip_washout_label"], "筹码数据暂缺")

    def test_attach_loads_each_unique_stock_once_and_keeps_failed_rows(self):
        calls = []

        def loader(ts_code, trade_date):
            calls.append((ts_code, trade_date))
            if ts_code == "600002.SH":
                raise RuntimeError("no permission")
            return valid_chips_frame(ts_code), perf_frame()

        rows = [
            {"ts_code": "600001.SH", "current_price": 9.0},
            {"ts_code": "600001.SH", "current_price": 9.1},
            {"ts_code": "600002.SH", "current_price": 8.0},
        ]
        history = pd.concat([
            history_frame(ts_code="600001.SH"),
            history_frame(ts_code="600002.SH"),
        ], ignore_index=True)

        enriched, warnings = attach_chip_peak_fields(
            rows,
            history,
            "20260730",
            loader=loader,
        )

        self.assertEqual(calls.count(("600001.SH", "20260730")), 1)
        self.assertEqual(calls.count(("600002.SH", "20260730")), 1)
        self.assertEqual(len(enriched), 3)
        self.assertEqual(enriched[2]["chip_washout_label"], "筹码数据暂缺")
        self.assertIn("600002.SH", warnings[0])

    @patch("chip_peak_service._query_tushare")
    def test_load_chip_data_caches_success_by_stock_and_trade_date(self, query):
        query.side_effect = [valid_chips_frame(), perf_frame()]

        first = load_chip_data("600001.SH", "20260730")
        second = load_chip_data("600001.SH", "20260730")

        self.assertEqual(query.call_count, 2)
        self.assertEqual(len(first[0]), 2)
        self.assertEqual(len(second[0]), 2)
        self.assertIsNot(first[0], second[0])


if __name__ == "__main__":
    unittest.main()
