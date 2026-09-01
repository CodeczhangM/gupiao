import unittest

import pandas as pd

from position_strategy_settings import DEFAULT_POSITION_STRATEGY_SETTINGS


def settings():
    import copy
    return copy.deepcopy(DEFAULT_POSITION_STRATEGY_SETTINGS)


class PressureCandidateTests(unittest.TestCase):
    def test_atr_includes_previous_close_gap(self):
        from pressure_zone_service import calculate_atr

        bars = pd.DataFrame([
            {"high": 10, "low": 9, "close": 9.5},
            {"high": 12, "low": 11, "close": 11.5},
        ])
        self.assertAlmostEqual(calculate_atr(bars, period=2), 1.75)

    def test_extracts_repeated_rejected_platform_highs(self):
        from pressure_zone_service import extract_pressure_candidates

        dates = pd.bdate_range("2026-07-01", periods=24)
        rows = []
        pivot_indexes = {8, 13, 18}
        for index, date in enumerate(dates):
            high = 10.9 if index in pivot_indexes else 10.45
            low = 9.95 if index in {9, 14, 19} else 10.1
            rows.append({
                "trade_date": date.strftime("%Y%m%d"), "open": 10.2,
                "high": high, "low": low, "close": 10.25, "vol": 100,
            })
        candidates = extract_pressure_candidates(
            pd.DataFrame(rows), {}, {}, settings()
        )
        platform = [item for item in candidates if item["source"] == "局部高点回落"]
        self.assertGreaterEqual(len(platform), 3)
        self.assertTrue(all(item["rejection_pct"] >= 2 for item in platform))

    def test_volume_rejection_and_chip_upper_are_distinct_sources(self):
        from pressure_zone_service import extract_pressure_candidates

        dates = pd.bdate_range("2026-07-01", periods=20)
        rows = [{
            "trade_date": date.strftime("%Y%m%d"), "open": 10,
            "high": 10.3, "low": 9.9, "close": 10.1, "vol": 100,
        } for date in dates]
        rows[15].update({"high": 11.2, "close": 10.2, "vol": 220})
        rows[16].update({"low": 9.8, "close": 9.9})
        candidates = extract_pressure_candidates(
            pd.DataFrame(rows), {}, {
                "chip_pressure_data_available": True,
                "chip_pressure_high": 10.95,
            }, settings()
        )
        sources = {item["source"] for item in candidates}
        self.assertIn("放量冲高回落", sources)
        self.assertIn("筹码密集区上沿", sources)

    def test_n_day_high_is_only_fallback(self):
        from pressure_zone_service import extract_pressure_candidates

        bars = pd.DataFrame([
            {"trade_date": f"202608{day:02d}", "open": 10, "high": 10 + day / 100,
             "low": 9.9, "close": 10, "vol": 100}
            for day in range(1, 21)
        ])
        candidates = extract_pressure_candidates(bars, {}, {}, settings())
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["source"], "N日最高价兜底")

    def test_missing_volume_keeps_daily_structure_available(self):
        from pressure_zone_service import extract_pressure_candidates

        bars = pd.DataFrame([
            {"trade_date": f"202608{day:02d}", "high": 10 + day / 100,
             "low": 9.9, "close": 10}
            for day in range(1, 21)
        ])
        candidates = extract_pressure_candidates(bars, {}, {}, settings())
        self.assertEqual(candidates[0]["source"], "N日最高价兜底")


class PressureClusteringTests(unittest.TestCase):
    def test_atr_adaptive_cluster_merges_near_prices_and_caps_width(self):
        from pressure_zone_service import cluster_pressure_candidates

        candidates = [
            {"price": 8.84, "source": "局部高点回落", "date": "20260801", "rejection_pct": 3, "volume_ratio": 1},
            {"price": 8.88, "source": "放量冲高回落", "date": "20260810", "rejection_pct": 4, "volume_ratio": 1.8},
            {"price": 8.91, "source": "涨停后平台上沿", "date": "20260820", "rejection_pct": 3, "volume_ratio": 1.2},
            {"price": 9.30, "source": "局部高点回落", "date": "20260825", "rejection_pct": 2, "volume_ratio": 1},
        ]
        zones = cluster_pressure_candidates(candidates, atr=0.3, settings=settings())
        near = next(zone for zone in zones if zone["lower"] < 8.9)
        self.assertEqual(near["touch_count"], 3)
        self.assertEqual(near["lower"], 8.84)
        self.assertEqual(near["upper"], 8.91)
        self.assertLessEqual((near["upper"] / near["lower"] - 1) * 100, 2)
        self.assertGreater(near["strength_score"], 50)


class PressureSelectionTests(unittest.TestCase):
    def test_near_actionable_zone_beats_stronger_remote_zone(self):
        from pressure_zone_service import select_actionable_pressure_zone

        zones = [
            {"lower": 10.18, "upper": 10.20, "strength_score": 82, "touch_count": 3, "sources": ["平台"]},
            {"lower": 12.40, "upper": 12.50, "strength_score": 90, "touch_count": 4, "sources": ["平台"]},
        ]
        selected, reason = select_actionable_pressure_zone(zones, 10.0, settings())
        self.assertEqual(selected["upper"], 10.20)
        self.assertIn("2.00%", reason)
        self.assertIn("远端", reason)

    def test_trade_plan_separates_zone_trigger_confirm_stop_and_target(self):
        from pressure_zone_service import build_breakout_trade_plan

        plan = build_breakout_trade_plan(
            {"lower": 8.50, "upper": 8.58, "price": 8.54},
            {"lower": 8.82, "upper": 8.90, "strength_score": 80},
            [{"lower": 10.0, "upper": 10.1, "strength_score": 70}],
            current_price=8.81,
            atr=0.20,
            settings=settings(),
        )
        self.assertEqual(plan["support_price"], 8.54)
        self.assertLessEqual(plan["pressure_high"], plan["breakout_trigger"])
        self.assertLess(plan["breakout_trigger"], plan["breakout_confirm"])
        self.assertLess(plan["invalid_price"], plan["support_price"])
        self.assertEqual(plan["target_price"], 10.0)
        self.assertGreater(plan["distance_to_trigger_pct"], 0)


if __name__ == "__main__":
    unittest.main()
