import unittest
from datetime import datetime
from unittest.mock import patch

import pandas as pd

from intraday_monitor_service import build_intraday_monitor
from tests.test_advantage_stock_scoring import (
    build_60min_bars,
    build_tail_1min_bars,
    water_macd_kdj_continuation_closes,
)


def latest_report_fixture():
    return {
        "id": 7,
        "trade_date": "20260728",
        "sector_potential": [
            {
                "industry_name": "酒店餐饮",
                "intraday_signal_stocks": [
                    {
                        "ts_code": "301073.SZ",
                        "name": "君亭酒店",
                        "pre_close": 10.0,
                        "pct_chg": 8.0,
                        "turnover_rate": 5.0,
                        "volume_ratio": 2.5,
                        "amount": 330_000_000,
                    }
                ],
            },
            {
                "industry_name": "日用化工",
                "intraday_signal_stocks": [
                    {
                        "ts_code": "301108.SZ",
                        "name": "洁雅股份",
                        "pre_close": 10.0,
                        "pct_chg": 4.0,
                        "turnover_rate": 4.0,
                        "volume_ratio": 2.3,
                        "amount": 260_000_000,
                    }
                ],
            },
        ],
    }


class IntradayMonitorServiceTests(unittest.TestCase):
    def setUp(self):
        self.database_minutes = patch(
            "intraday_monitor_service.load_minute_cache",
            return_value=pd.DataFrame(),
        )
        self.database_minute_saves = patch(
            "intraday_monitor_service.save_minute_cache",
        )
        self.database_results = patch(
            "intraday_monitor_service.load_result_cache",
            return_value=None,
            create=True,
        )
        self.database_result_saves = patch(
            "intraday_monitor_service.save_result_cache",
            create=True,
        )
        self.database_prune = patch(
            "intraday_monitor_service.prune_realtime_cache",
            create=True,
        )
        self.complete_dates = patch(
            "intraday_monitor_service.get_complete_dates",
            return_value=[
                "20260727",
                "20260724",
                "20260723",
                "20260722",
                "20260721",
            ],
        )
        self.database_minutes.start()
        self.database_minute_saves.start()
        self.database_results.start()
        self.database_result_saves.start()
        self.database_prune.start()
        self.complete_dates.start()
        self.addCleanup(self.database_minutes.stop)
        self.addCleanup(self.database_minute_saves.stop)
        self.addCleanup(self.database_results.stop)
        self.addCleanup(self.database_result_saves.stop)
        self.addCleanup(self.database_prune.stop)
        self.addCleanup(self.complete_dates.stop)

    def test_persistent_minutes_use_fresh_database_before_provider(self):
        import intraday_monitor_service

        cached = pd.DataFrame(
            [
                {
                    "ts_code": "301073.SZ",
                    "trade_time": "2026-07-28 09:30:00",
                    "close": 10,
                },
                {
                    "ts_code": "301073.SZ",
                    "trade_time": "2026-07-28 14:39:00",
                    "close": 10.1,
                },
            ]
        )
        with (
            patch(
                "intraday_monitor_service.load_minute_cache",
                return_value=cached,
                create=True,
            ),
            patch(
                "intraday_monitor_service.minute_cache_is_fresh",
                return_value=True,
                create=True,
            ),
            patch(
                "intraday_monitor_service._cached_minute_bars"
            ) as provider,
        ):
            result = intraday_monitor_service._persistent_minute_bars(
                "301073.SZ",
                "2026-07-28 09:30:00",
                "2026-07-28 14:40:00",
                "1min",
                datetime(2026, 7, 28, 14, 40),
            )

        self.assertEqual(result.iloc[-1]["close"], 10.1)
        provider.assert_not_called()

    def test_database_result_returns_without_loading_report(self):
        with (
            patch(
                "intraday_monitor_service.load_result_cache",
                return_value={
                    "payload": {
                        "trade_date": "20260728",
                        "stocks": [{"ts_code": "301073.SZ"}],
                    },
                    "updated_at": "2026-07-28 14:40:00",
                },
                create=True,
            ),
            patch(
                "intraday_monitor_service.get_latest_report"
            ) as report,
        ):
            result = build_intraday_monitor(
                now=datetime(2026, 7, 28, 14, 41),
                force_refresh=False,
            )

        self.assertEqual(result["cache_source"], "database")
        self.assertEqual(
            result["cache_updated_at"],
            "2026-07-28 14:40:00",
        )
        self.assertTrue(result["result_cache_hit"])
        report.assert_not_called()

    def test_force_refresh_bypasses_database_result_fast_path(self):
        with patch(
            "intraday_monitor_service.load_result_cache",
            return_value={
                "payload": {
                    "trade_date": "20260728",
                    "stocks": [{"ts_code": "cached"}],
                },
                "updated_at": "2026-07-28 14:40:00",
            },
            create=True,
        ) as database_result:
            result = build_intraday_monitor(
                fetch_realtime=False,
                now=datetime(2026, 7, 28, 15, 10),
                force_refresh=True,
            )

        self.assertNotEqual(result["stocks"][0]["ts_code"], "cached")
        database_result.assert_not_called()

    @patch("intraday_monitor_service._cached_minute_bars")
    @patch("intraday_monitor_service.get_trade_dates", return_value=["20260728"])
    @patch("intraday_monitor_service.get_latest_report")
    def test_monitor_flattens_latest_sector_intraday_stocks_and_updates_tail_state(
        self,
        get_latest_report,
        _get_trade_dates,
        cached_minute_bars,
    ):
        get_latest_report.return_value = latest_report_fixture()

        def fake_bars(ts_code, start_datetime, end_datetime, freq="60min"):
            if freq == "60min":
                return build_60min_bars(ts_code, water_macd_kdj_continuation_closes())
            if ts_code == "301073.SZ":
                bars = build_tail_1min_bars(
                    ts_code,
                    [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.05, 10.08, 10.1, 10.15, 10.2, 10.25, 10.35],
                    [1000, 1000, 1000, 1000, 1000, 1000, 2500, 2800, 3200, 3600, 4200, 5000, 8000],
                )
            else:
                bars = build_tail_1min_bars(
                    ts_code,
                    [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 9.98, 9.96, 9.94, 9.92, 9.9, 9.88, 9.84],
                    [3000, 3000, 3000, 3000, 3000, 3000, 3000, 3200, 3400, 3600, 3800, 4000, 6000],
                )
            bars["trade_time"] = bars["trade_time"].astype(str).str.replace(
                "2026-07-27",
                "2026-07-28",
                regex=False,
            )
            return bars

        cached_minute_bars.side_effect = fake_bars

        result = build_intraday_monitor(now=datetime(2026, 7, 28, 14, 50))

        self.assertEqual(result["report_id"], 7)
        self.assertEqual(result["trade_date"], "20260728")
        self.assertEqual(result["latest_trade_date"], "20260728")
        self.assertTrue(result["data_current"])
        self.assertEqual(result["data_as_of"], "2026-07-28 15:00:00")
        self.assertTrue(result["auto_refresh_enabled"])
        self.assertEqual(len(result["stocks"]), 2)
        by_code = {row["ts_code"]: row for row in result["stocks"]}
        self.assertEqual(by_code["301073.SZ"]["industry"], "酒店餐饮")
        self.assertAlmostEqual(by_code["301073.SZ"]["pct_chg"], 3.5)
        self.assertEqual(by_code["301073.SZ"]["next_day_bias"], "高开偏强")
        self.assertEqual(by_code["301073.SZ"]["main_force_status"], "主力抢筹")
        self.assertEqual(by_code["301108.SZ"]["next_day_bias"], "低开风险")
        self.assertEqual(by_code["301108.SZ"]["main_force_status"], "放量分歧")
        self.assertTrue(by_code["301073.SZ"]["tail_after_1430_available"])
        self.assertTrue(by_code["301073.SZ"]["tail_auction_available"])
        called_windows = [
            (*call.args, call.kwargs.get("freq"))
            for call in cached_minute_bars.call_args_list
        ]
        self.assertIn(("301073.SZ", "2026-07-28 09:30:00", "2026-07-28 14:49:00", "60min"), called_windows)
        self.assertIn(("301073.SZ", "2026-07-28 14:25:00", "2026-07-28 14:49:00", "1min"), called_windows)

    @patch("intraday_monitor_service._cached_minute_bars")
    @patch("intraday_monitor_service.get_trade_dates", return_value=["20260728"])
    @patch("intraday_monitor_service.get_latest_report")
    def test_monitor_hides_1430_tail_fields_before_time(
        self,
        get_latest_report,
        _get_trade_dates,
        cached_minute_bars,
    ):
        get_latest_report.return_value = latest_report_fixture()
        cached_minute_bars.return_value = pd.DataFrame()

        result = build_intraday_monitor(now=datetime(2026, 7, 28, 14, 10))

        row = result["stocks"][0]
        self.assertFalse(row["tail_after_1430_available"])
        self.assertTrue(row["tail_auction_available"])
        self.assertIsNone(row["tail_return_after_1430"])
        self.assertIsNone(row["tail_strength_score"])

    @patch("intraday_monitor_service.get_trade_dates", return_value=["20260728"])
    @patch("intraday_monitor_service.get_latest_report", return_value=latest_report_fixture())
    def test_monitor_disables_auto_refresh_after_close(self, _get_latest_report, _get_trade_dates):
        result = build_intraday_monitor(fetch_realtime=False, now=datetime(2026, 7, 28, 15, 10))

        self.assertFalse(result["auto_refresh_enabled"])
        self.assertEqual(result["market_phase"], "收盘结果")

    @patch("intraday_monitor_service.get_trade_dates", return_value=["20260728"])
    @patch("intraday_monitor_service.get_latest_report")
    def test_monitor_marks_report_stale_when_latest_trade_date_is_newer(
        self,
        get_latest_report,
        _get_trade_dates,
    ):
        report = latest_report_fixture()
        report["trade_date"] = "20260727"
        get_latest_report.return_value = report

        result = build_intraday_monitor(fetch_realtime=False, now=datetime(2026, 7, 28, 14, 10))

        self.assertFalse(result["data_current"])
        self.assertEqual(result["latest_trade_date"], "20260728")


if __name__ == "__main__":
    unittest.main()
