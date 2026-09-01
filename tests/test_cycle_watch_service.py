import unittest
from datetime import datetime
from unittest.mock import patch

import pandas as pd

from cycle_watch_service import (
    _evaluate_watch_stock,
    _lookup_stock_name,
    add_cycle_watch,
    check_cycle_watchlist,
    edit_cycle_watch,
    get_cycle_watchlist,
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
    @patch("realtime_market_source.load_eastmoney_market_snapshot")
    @patch("cycle_watch_service.load_market_snapshot", return_value=pd.DataFrame())
    @patch("cycle_watch_service._latest_trade_date", return_value="20260828")
    def test_name_lookup_prefers_realtime_snapshot(
        self, _trade_date, _cached_snapshot, realtime_snapshot,
    ):
        realtime_snapshot.return_value = (
            pd.DataFrame([{"ts_code": "600199.SH", "name": "金种子酒", "close": 8.01}]),
            None,
        )

        self.assertEqual(_lookup_stock_name("600199.SH"), "金种子酒")

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

    @patch("cycle_watch_service.list_cycle_history", return_value=[])
    @patch("cycle_watch_service.list_watch_stocks")
    def test_unchecked_stock_is_visible_in_watch_group(self, list_stocks, _history):
        list_stocks.return_value = [stock("600199.SH")]

        result = get_cycle_watchlist()

        self.assertEqual(result["watch_stocks"][0]["ts_code"], "600199.SH")
        self.assertEqual(result["watch_stocks"][0]["status_label"], "等待首次检查")

    @patch("cycle_watch_service._lookup_stock_name", return_value="金种子酒")
    @patch("cycle_watch_service.list_cycle_history", return_value=[])
    @patch("cycle_watch_service.list_watch_stocks")
    def test_list_fills_missing_name_for_existing_stock(
        self, list_stocks, _history, lookup_name,
    ):
        missing_name = {**stock("600199.SH"), "name": None}
        list_stocks.return_value = [missing_name]

        result = get_cycle_watchlist()

        self.assertEqual(result["stocks"][0]["name"], "金种子酒")
        lookup_name.assert_called_once_with("600199.SH")

    @patch("cycle_watch_service._load_cycle_minute_bars", return_value=pd.DataFrame())
    @patch("realtime_market_source.load_eastmoney_market_snapshot")
    @patch("cycle_watch_service.load_market_snapshot")
    @patch("cycle_watch_service.load_recent_daily")
    def test_evaluation_uses_realtime_price_over_cached_close(
        self, recent_daily, cached_snapshot, realtime_snapshot, _bars,
    ):
        recent_daily.return_value = pd.DataFrame([
            {
                "ts_code": "600199.SH", "trade_date": f"202607{index + 1:02d}",
                "close": 7.0, "high": 7.1, "low": 6.9, "vol": 1000,
            }
            for index in range(25)
        ])
        cached_snapshot.return_value = pd.DataFrame([{
            "ts_code": "600199.SH", "name": "金种子酒", "close": 7.24,
            "pct_chg": 0.0,
        }])
        realtime_snapshot.return_value = (pd.DataFrame([{
            "ts_code": "600199.SH", "name": "金种子酒", "close": 8.01,
            "pct_chg": 10.64,
        }]), None)

        result = _evaluate_watch_stock(stock("600199.SH"), "20260828", CHECK_TIME)

        self.assertEqual(result["current_price"], 8.01)
        self.assertEqual(result["pct_chg"], 10.64)

    @patch("cycle_watch_service._load_cycle_minute_bars")
    @patch("realtime_market_source.load_eastmoney_market_snapshot")
    @patch("cycle_watch_service.load_market_snapshot")
    @patch("cycle_watch_service.load_recent_daily")
    def test_evaluation_uses_latest_current_day_minute_when_snapshot_fails(
        self, recent_daily, cached_snapshot, realtime_snapshot, minute_bars,
    ):
        recent_daily.return_value = pd.DataFrame([
            {
                "ts_code": "600199.SH", "trade_date": f"202607{index + 1:02d}",
                "close": 7.0, "high": 7.1, "low": 6.9, "vol": 1000,
            }
            for index in range(25)
        ])
        cached_snapshot.return_value = pd.DataFrame([{
            "ts_code": "600199.SH", "name": "金种子酒", "close": 7.24,
            "pre_close": 7.20, "pct_chg": 0.56,
        }])
        realtime_snapshot.return_value = (pd.DataFrame(), "实时快照连接失败")
        minute_bars.side_effect = lambda *args, **_kwargs: (
            pd.DataFrame([
                {"ts_code": "600199.SH", "trade_time": "2026-08-28 10:34:00", "close": 8.00},
                {"ts_code": "600199.SH", "trade_time": "2026-08-28 10:35:00", "close": 8.02},
            ])
            if args[3] == "1min"
            else pd.DataFrame()
        )

        result = _evaluate_watch_stock(stock("600199.SH"), "20260828", CHECK_TIME)

        self.assertEqual(result["current_price"], 8.02)
        self.assertEqual(result["pct_chg"], 11.3889)

    @patch("cycle_watch_service._load_cycle_minute_bars", return_value=pd.DataFrame())
    @patch("realtime_market_source.load_eastmoney_market_snapshot")
    @patch("cycle_watch_service.load_market_snapshot")
    @patch("cycle_watch_service.load_recent_daily")
    def test_evaluation_rejects_daily_close_when_all_realtime_sources_fail(
        self, recent_daily, cached_snapshot, realtime_snapshot, _minute_bars,
    ):
        recent_daily.return_value = pd.DataFrame([
            {
                "ts_code": "600199.SH", "trade_date": f"202607{index + 1:02d}",
                "close": 7.0, "high": 7.1, "low": 6.9, "vol": 1000,
            }
            for index in range(25)
        ])
        cached_snapshot.return_value = pd.DataFrame([{
            "ts_code": "600199.SH", "name": "金种子酒", "close": 7.24,
            "pre_close": 7.20, "pct_chg": 0.56,
        }])
        realtime_snapshot.return_value = (pd.DataFrame(), "实时快照连接失败")

        with self.assertRaisesRegex(RuntimeError, "实时价格不可用"):
            _evaluate_watch_stock(stock("600199.SH"), "20260828", CHECK_TIME)

    @patch("realtime_info_service._persistent_minute_result")
    def test_cycle_minute_loader_reuses_realtime_info_database_cache(self, persistent):
        from cycle_watch_service import _load_cycle_minute_bars

        persistent.return_value = type("Loaded", (), {
            "bars": pd.DataFrame([{
                "ts_code": "600199.SH",
                "trade_time": "2026-08-28 10:35:00",
                "close": 8.02,
            }])
        })()

        result = _load_cycle_minute_bars(
            "600199.SH",
            "2026-08-28 09:30:00",
            "2026-08-28 10:35:00",
            "1min",
            "20260828",
            CHECK_TIME,
        )

        self.assertEqual(result.iloc[-1]["close"], 8.02)

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
    @patch("cycle_watch_service.latest_effective_evaluation", return_value=None)
    @patch("cycle_watch_service._evaluate_watch_stock")
    @patch("cycle_watch_service.list_watch_stocks")
    @patch("cycle_watch_service.get_trade_dates", return_value=["20260828"])
    def test_manual_check_generates_millisecond_slot(
        self, _dates, list_stocks, evaluate, _latest, save,
    ):
        list_stocks.return_value = [stock()]
        evaluate.return_value = evaluation("watch", 63)
        save.side_effect = lambda row, slot: {**row, "id": 1, "schedule_slot": slot}

        result = check_cycle_watchlist(
            now=datetime(2026, 8, 28, 10, 35, 12, 345678),
        )

        self.assertEqual(result["schedule_slot"], "manual-103512345")

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
