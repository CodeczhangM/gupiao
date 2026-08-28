import unittest
from datetime import datetime
from unittest.mock import patch

from cycle_watch_service import (
    add_cycle_watch,
    check_cycle_watchlist,
    edit_cycle_watch,
)


CHECK_TIME = datetime(2026, 8, 28, 10, 35)


def stock(code="600000.SH"):
    return {
        "ts_code": code, "name": "样本", "note": None,
        "planned_low_price": None, "planned_high_price": None, "enabled": True,
    }


def evaluation(status, score, code="600000.SH"):
    labels = {
        "watch": "继续观察", "low_buy": "低吸提示",
        "confirmed": "确认介入", "data_delayed": "数据延迟",
    }
    return {
        "ts_code": code, "status": status, "status_label": labels[status],
        "opportunity_score": score, "current_price": 10.0, "pct_chg": 0.2,
        "support_price": 9.9, "matched_conditions": [], "missing_conditions": [],
        "risk_items": [], "invalidation_reason": "跌破MA20失效",
        "factors": {"confirmation_count": 2, "relative_strength": 1.0,
                    "support_distance_pct": 1.0, "volume_contraction": 0.7},
    }


class CycleWatchServiceTests(unittest.TestCase):
    @patch("cycle_watch_service.upsert_watch_stock")
    @patch("cycle_watch_service._lookup_stock_name", return_value="浦发银行")
    def test_add_normalizes_code(self, _lookup, upsert):
        upsert.side_effect = lambda payload: payload

        result = add_cycle_watch({"ts_code": "600000", "note": "银行周期"})

        self.assertEqual(result["ts_code"], "600000.SH")
        self.assertEqual(result["name"], "浦发银行")

    def test_rejects_reversed_price_range_and_long_note(self):
        with self.assertRaises(ValueError):
            add_cycle_watch({
                "ts_code": "600000", "planned_low_price": 10.2,
                "planned_high_price": 9.8,
            })
        with self.assertRaises(ValueError):
            edit_cycle_watch("600000", {"note": "x" * 501})

    @patch("cycle_watch_service.save_cycle_evaluation")
    @patch("cycle_watch_service.latest_effective_evaluation")
    @patch("cycle_watch_service._evaluate_watch_stock")
    @patch("cycle_watch_service.list_watch_stocks")
    @patch("cycle_watch_service.get_trade_dates", return_value=["20260828"])
    def test_equal_state_does_not_alert_but_upgrade_does(
        self, _dates, list_stocks, evaluate, latest, save,
    ):
        list_stocks.return_value = [stock()]
        save.side_effect = lambda row, slot: {**row, "id": 1, "schedule_slot": slot}
        latest.return_value = {"status": "low_buy"}
        evaluate.return_value = evaluation("low_buy", 68)

        equal = check_cycle_watchlist(now=CHECK_TIME, schedule_slot="1035")
        self.assertFalse(equal["stocks"][0]["is_new_alert"])

        evaluate.return_value = evaluation("confirmed", 82)
        upgraded = check_cycle_watchlist(
            now=datetime(2026, 8, 28, 11, 25), schedule_slot="1125",
        )
        self.assertTrue(upgraded["stocks"][0]["is_new_alert"])

    @patch("cycle_watch_service.save_cycle_evaluation")
    @patch("cycle_watch_service.latest_effective_evaluation", return_value={"status": "watch"})
    @patch("cycle_watch_service._evaluate_watch_stock")
    @patch("cycle_watch_service.list_watch_stocks")
    @patch("cycle_watch_service.get_trade_dates", return_value=["20260828"])
    def test_one_stock_failure_is_isolated_as_data_delayed(
        self, _dates, list_stocks, evaluate, _latest, save,
    ):
        list_stocks.return_value = [stock("600000.SH"), stock("000001.SZ")]
        evaluate.side_effect = [evaluation("low_buy", 68), RuntimeError("行情超时")]
        save.side_effect = lambda row, slot: {**row, "id": 1, "schedule_slot": slot}

        result = check_cycle_watchlist(now=CHECK_TIME, schedule_slot="1035")

        self.assertEqual(result["success_count"], 1)
        self.assertEqual(result["failure_count"], 1)
        self.assertEqual(result["delayed_stocks"][0]["ts_code"], "000001.SZ")
        self.assertFalse(result["delayed_stocks"][0]["is_new_alert"])

    @patch("cycle_watch_service._evaluate_watch_stock")
    @patch("cycle_watch_service.list_watch_stocks", return_value=[stock()])
    @patch("cycle_watch_service.get_trade_dates", return_value=["20260827"])
    def test_non_trading_day_skips_evaluation(self, _dates, _stocks, evaluate):
        result = check_cycle_watchlist(now=CHECK_TIME, schedule_slot="1035")

        self.assertTrue(result["skipped"])
        evaluate.assert_not_called()

    @patch("cycle_watch_service.save_cycle_evaluation")
    @patch("cycle_watch_service.latest_effective_evaluation", return_value=None)
    @patch("cycle_watch_service._evaluate_watch_stock")
    @patch("cycle_watch_service.list_watch_stocks")
    @patch("cycle_watch_service.get_trade_dates", return_value=["20260828"])
    def test_groups_are_sorted_independently(
        self, _dates, list_stocks, evaluate, _latest, save,
    ):
        list_stocks.return_value = [stock("600001.SH"), stock("600002.SH"), stock("600003.SH")]
        evaluate.side_effect = [
            evaluation("watch", 55, "600001.SH"),
            evaluation("low_buy", 70, "600002.SH"),
            evaluation("confirmed", 88, "600003.SH"),
        ]
        save.side_effect = lambda row, slot: {**row, "id": 1, "schedule_slot": slot}

        result = check_cycle_watchlist(now=CHECK_TIME, schedule_slot="1035")

        self.assertEqual(result["confirmed_stocks"][0]["ts_code"], "600003.SH")
        self.assertEqual(result["low_buy_stocks"][0]["ts_code"], "600002.SH")
        self.assertEqual(result["watch_stocks"][0]["ts_code"], "600001.SH")


if __name__ == "__main__":
    unittest.main()
