import copy
import unittest

from position_strategy_settings import DEFAULT_POSITION_STRATEGY_SETTINGS


def settings():
    return copy.deepcopy(DEFAULT_POSITION_STRATEGY_SETTINGS)


def plan():
    return {
        "support_price": 8.54, "pressure_low": 8.82, "pressure_high": 8.90,
        "breakout_trigger": 8.91, "breakout_confirm": 8.97,
        "invalid_price": 8.42, "target_price": 10.10,
    }


def bar(close=8.80, high=8.85, low=8.70, open_price=8.75, vol=120):
    return {"open": open_price, "high": high, "low": low, "close": close, "vol": vol}


class BreakoutStateTests(unittest.TestCase):
    def test_not_triggered_and_touching_are_distinct(self):
        from breakout_trade_evaluation import evaluate_breakout

        untouched = evaluate_breakout(bar(close=8.70, high=8.78), plan(), {"avg_volume_5": 100}, settings())
        touching = evaluate_breakout(bar(close=8.86, high=8.89), plan(), {"avg_volume_5": 100}, settings())
        self.assertEqual(untouched["breakout_state"], "NOT_TRIGGERED")
        self.assertIsNone(untouched["breakout_quality_score"])
        self.assertEqual(touching["breakout_state"], "TOUCHING")

    def test_trigger_requires_confirmation_quality(self):
        from breakout_trade_evaluation import evaluate_breakout

        weak = evaluate_breakout(
            bar(close=8.98, high=9.10, low=8.80, vol=105), plan(),
            {"avg_volume_5": 100}, settings(),
        )
        self.assertEqual(weak["breakout_state"], "TRIGGERED")
        self.assertNotEqual(weak["breakout_quality_label"], "强突破")

    def test_confirmed_breakout_requires_price_volume_and_close_position(self):
        from breakout_trade_evaluation import evaluate_breakout

        result = evaluate_breakout(
            bar(close=9.08, high=9.10, low=8.85, vol=160), plan(),
            {"avg_volume_5": 100, "sector_hot_score": 15}, settings(),
        )
        self.assertEqual(result["breakout_state"], "CONFIRMED")
        self.assertGreaterEqual(result["breakout_quality_score"], 65)

    def test_close_back_inside_pressure_is_failed(self):
        from breakout_trade_evaluation import evaluate_breakout

        result = evaluate_breakout(
            bar(close=8.86, high=9.05, low=8.80, vol=150), plan(),
            {"avg_volume_5": 100}, settings(),
        )
        self.assertEqual(result["breakout_state"], "FAILED")
        self.assertEqual(result["false_breakout_risk"], "HIGH")

    def test_far_above_confirmed_price_is_overextended(self):
        from breakout_trade_evaluation import evaluate_breakout

        result = evaluate_breakout(
            bar(close=9.30, high=9.32, low=9.05, vol=160), plan(),
            {"avg_volume_5": 100}, settings(),
        )
        self.assertEqual(result["breakout_state"], "OVEREXTENDED")


class FalseBreakoutRiskTests(unittest.TestCase):
    def test_risk_evidence_is_separate_from_quality(self):
        from breakout_trade_evaluation import evaluate_breakout

        result = evaluate_breakout(
            bar(close=8.89, high=9.10, low=8.80, vol=90), plan(),
            {
                "avg_volume_5": 100, "failed_pressure_attacks": 3,
                "tail_return_after_1430": -0.9, "above_vwap": False,
            }, settings(),
        )
        evidence = "；".join(result["false_breakout_evidence"])
        self.assertIn("跌回压力区", evidence)
        self.assertIn("未明显放量", evidence)
        self.assertIn("尾盘明显回落", evidence)
        self.assertIn("VWAP", evidence)
        self.assertEqual(result["false_breakout_risk"], "HIGH")
        self.assertIn("breakout_quality_score", result)


class RiskRewardTests(unittest.TestCase):
    def test_ratio_and_score_use_real_plan_prices(self):
        from breakout_trade_evaluation import calculate_risk_reward

        result = calculate_risk_reward(plan(), "NOT_TRIGGERED", 8.80, settings())
        expected = (10.10 - 8.97) / (8.97 - 8.42)
        self.assertAlmostEqual(result["risk_reward_ratio"], expected, places=2)
        self.assertEqual(result["risk_reward_label"], "良好")
        self.assertEqual(result["risk_reward_evidence"]["entry_price"], 8.97)

    def test_missing_or_invalid_prices_return_unavailable(self):
        from breakout_trade_evaluation import calculate_risk_reward

        missing = calculate_risk_reward({**plan(), "target_price": None}, "TRIGGERED", 9.0, settings())
        invalid = calculate_risk_reward({**plan(), "target_price": 8.8}, "TRIGGERED", 9.0, settings())
        self.assertIsNone(missing["risk_reward_ratio"])
        self.assertIsNone(invalid["risk_reward_ratio"])


if __name__ == "__main__":
    unittest.main()
