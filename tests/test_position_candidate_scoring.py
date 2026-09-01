import unittest

from position_candidate_scoring import (
    rank_position_candidates,
    score_position_candidate,
)


def _base_row(ts_code="600001.SH"):
    return {
        "ts_code": ts_code,
        "name": "示例股份",
        "industry": "通信设备",
        "close": 12.5,
        "pct_chg": 3.2,
        "vol": 2_000_000,
        "amount": 180_000_000,
        "sector_rank": 2,
        "sector_avg_pct_chg": 2.2,
        "sector_up_ratio": 0.76,
        "sector_limit_up_count": 3,
        "sector_macd_status": "水上多头",
        "volume_ratio": 1.8,
        "turnover_rate": 6.0,
        "price_volume_confirmed": True,
        "price_volume_stagnation": False,
        "main_force_status": "主力抢筹",
        "macd_golden_cross": True,
        "macd_above_zero": True,
        "intraday_signal_tier": "strong",
        "intraday_signal_reason": "60分MACD水上金叉",
        "chip_washout_score": 82,
        "chip_data_complete": True,
        "chip_build_position": True,
        "chip_concentration_70_pct": 9.0,
        "chip_price_distance_pct": 2.0,
        "chip_winner_rate": 45.0,
        "realtime_relative_strength_score": 82,
        "relative_strength": 2.0,
        "market_resonance_state": "up",
        "tail_after_1430_available": True,
        "tail_return_after_1430": 0.65,
        "tail_strength_score": 82,
        "tail_close_position": 0.9,
        "tail_volume_ratio": 1.5,
        "resonance_stage": "launch",
        "resonance_type": "底部放量启动",
        "bottom_setup_score": 82,
        "bottom_breakout_strength": 2.5,
        "bottom_volume_expansion": 1.8,
        "limit_gene_eligible": True,
        "latest_limit_up_date": "20260825",
        "resonance_events": [{"type": "daily_macd", "contribution": 20}],
        "historical_resonance_score": 20,
        "support_held": True,
        "pullback_state": "回踩关键位未破",
        "primary_support": 12.1,
        "support_distance_pct": 3.3,
        "primary_support_strength": 17,
        "breakout_confirmed": True,
        "breakout_pct": 0.8,
        "price_volume_confirmation": True,
        "confirmation_price": 12.4,
        "pressure_low": 12.25,
        "pressure_high": 12.35,
        "breakout_trigger": 12.37,
        "breakout_confirm": 12.4,
        "distance_to_trigger_pct": -1.04,
        "distance_to_confirm_pct": -0.8,
        "breakout_state": "CONFIRMED",
        "breakout_quality_score": 88,
        "false_breakout_risk": "LOW",
        "false_breakout_risk_score": 10,
        "risk_reward_ratio": 2.4,
        "risk_reward_score": 72,
        "pressure_strength_score": 85,
        "pressure_sources": ["近20日3次局部高点聚类"],
    }


class PositionCandidateScoringTests(unittest.TestCase):
    def test_hot_sector_and_healthy_volume_beat_stronger_chip_only_row(self):
        strong_context = score_position_candidate(_base_row("600001.SH"))
        chip_only = score_position_candidate({
            **_base_row("600002.SH"),
            "sector_rank": 35,
            "sector_avg_pct_chg": -0.2,
            "sector_up_ratio": 0.35,
            "sector_limit_up_count": 0,
            "sector_macd_status": "水下空头",
            "volume_ratio": 0.7,
            "turnover_rate": 2.0,
            "price_volume_confirmed": False,
            "main_force_status": "观察",
            "chip_washout_score": 96,
        })

        self.assertGreater(
            strong_context["position_score"],
            chip_only["position_score"],
        )
        self.assertGreater(
            strong_context["sector_hot_score"],
            chip_only["sector_hot_score"],
        )
        self.assertGreater(
            strong_context["price_volume_score"],
            chip_only["price_volume_score"],
        )

    def test_score_exposes_complete_explainable_breakdown(self):
        result = score_position_candidate(_base_row())

        expected = {
            "position_score", "position_level", "position_level_reason",
            "sector_hot_score", "price_volume_score", "macd_score",
            "chip_peak_score", "relative_tail_score",
            "support_pullback_score", "historical_resonance_score",
            "position_risk_penalty",
            "position_risk_items", "position_positive_reasons",
            "position_missing_confirmations",
        }
        self.assertTrue(expected.issubset(result))
        self.assertEqual(result["build_position_level"], "A+")

    def test_score_exposes_new_breakout_breakdown(self):
        result = score_position_candidate(_base_row())

        self.assertTrue({
            "stock_quality_score", "entry_timing_score",
            "breakout_quality_score",
            "data_confidence", "final_score", "build_position_level",
            "build_position_status", "score_components", "macd_strength",
        }.issubset(result))
        self.assertEqual(result["position_score"], result["final_score"])
        self.assertEqual(result["build_position_level"], "A+")
        self.assertIn(result["macd_strength"], {"强", "中", "弱"})

    def test_immediate_entry_requires_breakout_confirmation(self):
        result = score_position_candidate({
            **_base_row(),
            "breakout_confirmed": False,
            "breakout_pct": -0.2,
            "breakout_state": "TRIGGERED",
            "breakout_quality_score": 55,
        })
        self.assertNotEqual(result["build_position_level"], "A+")

    def test_immediate_entry_requires_non_weak_sector(self):
        result = score_position_candidate({
            **_base_row(),
            "sector_rank": 30,
            "sector_avg_pct_chg": 0.3,
            "sector_up_ratio": 0.45,
            "sector_limit_up_count": 0,
            "sector_macd_status": "",
        })
        self.assertLess(result["sector_hot_score"], 9)
        self.assertNotEqual(result["build_position_level"], "A+")

    def test_limit_gene_and_recent_resonance_are_hard_gates(self):
        no_gene = score_position_candidate({**_base_row(), "limit_gene_eligible": False})
        no_event = score_position_candidate({
            **_base_row(), "resonance_events": [], "historical_resonance_score": 0,
        })
        self.assertEqual(no_gene["build_position_level"], "X")
        self.assertEqual(no_gene["position_filter_reason"], "前1至10日无涨停基因")
        self.assertEqual(no_event["build_position_level"], "X")
        self.assertEqual(no_event["position_filter_reason"], "近20日无有效共振")

    def test_today_limit_up_and_down_have_precise_reasons(self):
        up = score_position_candidate({**_base_row(), "pct_chg": 9.8})
        down = score_position_candidate({**_base_row(), "pct_chg": -9.7})
        self.assertIn("当日涨停或接近涨停：9.80%", up["position_filter_reason"])
        self.assertIn("当日跌停或接近跌停：-9.70%", down["position_filter_reason"])

    def test_missing_chip_confirmation_downgrades_immediate_entry(self):
        result = score_position_candidate({
            **_base_row(),
            "chip_data_complete": False,
            "chip_build_position": False,
            "chip_washout_score": 0,
        })

        self.assertNotEqual(result["build_position_level"], "A+")
        self.assertIn("筹码数据缺失", result["position_missing_confirmations"])

    def test_after_1430_missing_tail_confirmation_blocks_immediate_entry(self):
        result = score_position_candidate(
            {
                **_base_row(),
                "tail_after_1430_available": False,
                "tail_return_after_1430": None,
                "tail_strength_score": None,
            },
            market_phase="收盘最终结果",
        )

        self.assertNotEqual(result["build_position_level"], "A+")
        self.assertIn("尾盘确认缺失", result["position_missing_confirmations"])

    def test_pre_1430_missing_tail_does_not_add_risk_penalty(self):
        row = {
            **_base_row(),
            "tail_after_1430_available": False,
            "tail_return_after_1430": None,
            "tail_strength_score": None,
        }
        result = score_position_candidate(row, market_phase="盘中观察")

        self.assertNotIn("尾盘确认缺失", result["position_risk_items"])
        self.assertNotEqual(result["build_position_level"], "A+")

    def test_high_risk_turnover_is_hidden_even_with_strong_positive_factors(self):
        result = score_position_candidate({
            **_base_row(),
            "turnover_rate": 25.0,
            "price_volume_stagnation": True,
        })

        self.assertEqual(result["build_position_level"], "X")
        self.assertTrue(result["position_high_risk_veto"])

    def test_ranking_caps_at_ten_without_backfilling_weak_rows(self):
        strong_rows = [
            {**_base_row(f"600{index:03d}.SH"), "relative_strength": index / 10}
            for index in range(12)
        ]
        capped = rank_position_candidates(strong_rows, limit=20)
        weak = rank_position_candidates([
            {
                **_base_row("600999.SH"),
                "sector_rank": 99,
                "sector_avg_pct_chg": -2,
                "sector_up_ratio": 0.1,
                "sector_limit_up_count": 0,
                "sector_macd_status": "水下空头",
                "volume_ratio": 0.4,
                "price_volume_confirmed": False,
                "main_force_status": "观察",
                "macd_golden_cross": False,
                "macd_above_zero": False,
                "intraday_signal_tier": "weak",
                "intraday_signal_reason": "水下MACD死叉",
                "chip_data_complete": False,
                "chip_build_position": False,
                "chip_washout_score": 0,
                "realtime_relative_strength_score": 0,
                "relative_strength": -2,
                "resonance_stage": None,
                "pullback_state": "",
                "primary_support_strength": 0,
            }
        ])

        self.assertEqual(len(capped), 10)
        self.assertEqual(weak, [])

    def test_distance_boundaries_drive_a_b_plus_c_and_x(self):
        critical = score_position_candidate({
            **_base_row(), "breakout_state": "NOT_TRIGGERED",
            "breakout_confirmed": False, "breakout_quality_score": None,
            "distance_to_trigger_pct": 1.5,
        })
        waiting = score_position_candidate({
            **_base_row(), "breakout_state": "NOT_TRIGGERED",
            "breakout_confirmed": False, "breakout_quality_score": None,
            "distance_to_trigger_pct": 3.0,
        })
        observe = score_position_candidate({
            **_base_row(), "breakout_state": "NOT_TRIGGERED",
            "breakout_confirmed": False, "breakout_quality_score": None,
            "distance_to_trigger_pct": 5.0,
        })
        distant = score_position_candidate({
            **_base_row(), "breakout_state": "NOT_TRIGGERED",
            "breakout_confirmed": False, "breakout_quality_score": None,
            "distance_to_trigger_pct": 5.01,
        })
        self.assertEqual(critical["build_position_level"], "A")
        self.assertEqual(waiting["build_position_level"], "B+")
        self.assertEqual(observe["build_position_level"], "C")
        self.assertEqual(distant["build_position_level"], "X")

    def test_risk_reward_does_not_affect_score_level_or_ranking(self):
        low_rr = score_position_candidate({**_base_row(), "risk_reward_ratio": 1.49})
        high_rr = score_position_candidate({**_base_row(), "risk_reward_ratio": 5.0})
        self.assertEqual(low_rr["build_position_level"], high_rr["build_position_level"])
        self.assertEqual(low_rr["final_score"], high_rr["final_score"])

    def test_high_false_breakout_is_a_hard_veto(self):
        false_breakout = score_position_candidate({
            **_base_row(), "false_breakout_risk": "HIGH",
            "breakout_state": "FAILED",
        })
        self.assertEqual(false_breakout["build_position_level"], "X")

    def test_missing_chip_lowers_confidence_without_zeroing_stock_quality(self):
        complete = score_position_candidate(_base_row())
        missing = score_position_candidate({
            **_base_row(), "chip_data_complete": False,
            "chip_build_position": False,
        })
        self.assertLess(missing["data_confidence"], complete["data_confidence"])
        self.assertGreater(missing["stock_quality_score"], 0)


if __name__ == "__main__":
    unittest.main()
