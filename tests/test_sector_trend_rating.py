import unittest

import pandas as pd

from sector_rotation_service import (
    CAPITAL_TREND_TEXT,
    _capital_flow_trend,
    _macd_strength,
    _weekly_macd_state,
    select_sector_trend,
)


def macd_state(
    status="golden_cross",
    zero_axis="above",
    histogram_trend="red_expand",
    ready=True,
):
    return {
        "ready": ready,
        "weeks_available": 24 if ready else 0,
        "dif": 0.82 if ready else None,
        "dea": 0.61 if ready else None,
        "histogram": 0.42 if ready else None,
        "previous_dif": 0.55 if ready else None,
        "previous_dea": 0.63 if ready else None,
        "previous_histogram": 0.30 if ready else None,
        "status": status if ready else None,
        "zero_axis": zero_axis if ready else None,
        "histogram_trend": histogram_trend if ready else None,
    }


class CapitalFlowTrendTests(unittest.TestCase):
    def test_three_positive_days_is_inflow(self):
        self.assertEqual(_capital_flow_trend([1.0, 2.0, 3.0]), ("inflow", CAPITAL_TREND_TEXT["inflow"]))

    def test_three_negative_days_is_outflow(self):
        self.assertEqual(_capital_flow_trend([-1.0, -2.0, -3.0]), ("outflow", CAPITAL_TREND_TEXT["outflow"]))

    def test_mixed_days_is_oscillation(self):
        for values in ([1.0, -2.0, 3.0], [-1.0, 2.0, -3.0], [1.0, 2.0, -3.0]):
            self.assertEqual(_capital_flow_trend(values), ("oscillation", CAPITAL_TREND_TEXT["oscillation"]))

    def test_missing_day_is_unknown(self):
        self.assertEqual(_capital_flow_trend([1.0, None, 3.0]), ("unknown", CAPITAL_TREND_TEXT["unknown"]))
        self.assertEqual(_capital_flow_trend([1.0, 2.0]), ("unknown", CAPITAL_TREND_TEXT["unknown"]))
        self.assertEqual(_capital_flow_trend([]), ("unknown", CAPITAL_TREND_TEXT["unknown"]))

    def test_zero_value_is_not_inflow_nor_outflow(self):
        self.assertEqual(_capital_flow_trend([0.0, 1.0, 2.0])[0], "oscillation")
        self.assertEqual(_capital_flow_trend([0.0, -1.0, -2.0])[0], "oscillation")
        self.assertEqual(_capital_flow_trend([0.0, 0.0, 0.0])[0], "oscillation")


class SectorTrendRatingTests(unittest.TestCase):
    def test_case1_strong_trend_resonance(self):
        rating, desc = select_sector_trend(
            "inflow", macd_state("golden_cross", "above", "red_expand")
        )
        self.assertEqual(rating, "strong")
        self.assertIn("金叉", desc)
        self.assertIn("红柱放大", desc)

    def test_case2_bullish_when_inflow_and_macd_below_zero(self):
        rating, desc = select_sector_trend(
            "inflow", macd_state("critical", "below", "flat")
        )
        self.assertEqual(rating, "bullish")
        self.assertIn("零轴下方", desc)

    def test_case3_watch_when_capital_not_confirmed(self):
        # 资金未形成连续流入 + MACD 偏强 -> 待确认
        rating, _ = select_sector_trend(
            "oscillation", macd_state("golden_cross", "above", "red_expand")
        )
        self.assertEqual(rating, "watch")
        # 资金数据未知 -> 待确认
        rating, _ = select_sector_trend(
            "unknown", macd_state("golden_cross", "above", "red_expand")
        )
        self.assertEqual(rating, "watch")

    def test_case4_weak_when_outflow_with_fading_macd(self):
        rating, _ = select_sector_trend(
            "outflow", macd_state("critical", "crossing", "green_expand")
        )
        self.assertEqual(rating, "weak")
        # 红柱缩短属于动能减弱，零轴上方不足以抵消
        rating, _ = select_sector_trend(
            "outflow", macd_state("critical", "above", "red_shrink")
        )
        self.assertEqual(rating, "weak")

    def test_case5_avoid_when_outflow_death_cross_green_expand_below(self):
        rating, desc = select_sector_trend(
            "outflow", macd_state("death_cross", "below", "green_expand")
        )
        self.assertEqual(rating, "avoid")
        self.assertIn("暂不关注", desc)

    def test_case6_missing_data_always_watch(self):
        for macd in (None, {}, macd_state(ready=False)):
            rating, desc = select_sector_trend("inflow", macd)
            self.assertEqual(rating, "watch")
            self.assertIn("周线MACD数据不足", desc)
        rating, _ = select_sector_trend("unknown", macd_state())
        self.assertEqual(rating, "watch")
        # DIF/DEA 缺失视为未就绪
        broken = macd_state()
        broken["dif"] = None
        broken["ready"] = False
        rating, _ = select_sector_trend("inflow", broken)
        self.assertEqual(rating, "watch")

    def test_case7_conflict_never_strong(self):
        # 资金连续流出 + MACD 金叉：不得判强趋势，降级待确认
        rating, _ = select_sector_trend(
            "outflow", macd_state("golden_cross", "above", "red_expand")
        )
        self.assertEqual(rating, "watch")
        # 资金流入 + MACD 明确走弱：冲突 -> 待确认
        rating, _ = select_sector_trend(
            "inflow", macd_state("death_cross", "below", "green_expand")
        )
        self.assertEqual(rating, "watch")

    def test_case8_boundary_values(self):
        # DIF == DEA -> 临界，不强行归类金叉/死叉
        rating, _ = select_sector_trend(
            "inflow", macd_state("critical", "crossing", "flat")
        )
        self.assertEqual(rating, "bullish")
        # 流出 + 金叉（金叉/死叉边界抖动前的明确状态）-> 冲突待确认
        rating, _ = select_sector_trend("outflow", macd_state())
        self.assertEqual(rating, "watch")
        # 流出 + 零轴下方中性 -> 偏弱
        rating, _ = select_sector_trend(
            "outflow", macd_state("critical", "below", "flat")
        )
        self.assertEqual(rating, "weak")
        # 非法资金趋势按 unknown 处理
        rating, _ = select_sector_trend("bad-value", macd_state())
        self.assertEqual(rating, "watch")

    def test_watch_conflict_scenarios(self):
        # 资金流入但 MACD 明确走弱 -> 待确认（待确认区内排前）
        capital_first = select_sector_trend(
            "inflow", macd_state("critical", "crossing", "green_expand")
        )[0]
        # MACD 偏强但资金未连续流入 -> 待确认（排后）
        macd_first = select_sector_trend(
            "oscillation", macd_state("golden_cross", "above", "red_expand")
        )[0]
        self.assertEqual(capital_first, "watch")
        self.assertEqual(macd_first, "watch")


class MacdStrengthTests(unittest.TestCase):
    def test_strength_scores(self):
        self.assertGreater(_macd_strength(macd_state()), 0)
        self.assertLess(
            _macd_strength(macd_state("death_cross", "below", "green_expand")), 0
        )
        self.assertEqual(_macd_strength(None), 0.0)
        self.assertEqual(_macd_strength(macd_state(ready=False)), 0.0)


class WeeklyMacdStateTests(unittest.TestCase):
    def test_accelerating_up_is_golden_cross_red_expand_above(self):
        weeks = pd.Series(
            [8.0 + 0.5 * index + 0.08 * index * index for index in range(24)]
        )
        state = _weekly_macd_state(weeks)
        self.assertTrue(state["ready"])
        self.assertEqual(state["weeks_available"], 24)
        self.assertEqual(state["status"], "golden_cross")
        self.assertEqual(state["zero_axis"], "above")
        self.assertEqual(state["histogram_trend"], "red_expand")
        self.assertIsNotNone(state["previous_dif"])
        self.assertIsNotNone(state["previous_dea"])
        self.assertIsNotNone(state["previous_histogram"])

    def test_accelerating_down_is_death_cross_green_expand_below(self):
        weeks = pd.Series(
            [60.0 - 0.2 * index - 0.08 * index * index for index in range(24)]
        )
        state = _weekly_macd_state(weeks)
        self.assertTrue(state["ready"])
        self.assertEqual(state["status"], "death_cross")
        self.assertEqual(state["zero_axis"], "below")
        self.assertEqual(state["histogram_trend"], "green_expand")

    def test_insufficient_weeks_degrades(self):
        weeks = pd.Series([10.0 + 0.1 * index for index in range(10)])
        state = _weekly_macd_state(weeks)
        self.assertFalse(state["ready"])
        self.assertEqual(state["weeks_available"], 10)
        self.assertIsNone(state["status"])
        self.assertIsNone(state["dif"])

    def test_flat_series_hits_boundaries(self):
        weeks = pd.Series([10.0] * 24)
        state = _weekly_macd_state(weeks)
        self.assertTrue(state["ready"])
        # DIF == DEA == 0 -> 临界 + 零轴临界 + 柱体持平
        self.assertEqual(state["status"], "critical")
        self.assertEqual(state["zero_axis"], "crossing")
        self.assertEqual(state["histogram_trend"], "flat")

    def test_empty_series(self):
        state = _weekly_macd_state(None)
        self.assertFalse(state["ready"])
        state = _weekly_macd_state(pd.Series([], dtype=float))
        self.assertFalse(state["ready"])


if __name__ == "__main__":
    unittest.main()
