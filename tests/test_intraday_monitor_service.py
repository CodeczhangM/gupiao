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
    @patch("intraday_monitor_service.get_stock_minute_bars")
    @patch("intraday_monitor_service.get_trade_dates", return_value=["20260728"])
    @patch("intraday_monitor_service.get_latest_report")
    def test_monitor_flattens_latest_sector_intraday_stocks_and_updates_tail_state(
        self,
        get_latest_report,
        _get_trade_dates,
        get_stock_minute_bars,
    ):
        get_latest_report.return_value = latest_report_fixture()

        def fake_bars(ts_code, start_datetime, end_datetime, freq="60min"):
            if freq == "60min":
                return build_60min_bars(ts_code, water_macd_kdj_continuation_closes())
            if ts_code == "301073.SZ":
                return build_tail_1min_bars(
                    ts_code,
                    [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.05, 10.08, 10.1, 10.15, 10.2, 10.25, 10.35],
                    [3000, 3000, 3000, 3000, 3000, 3000, 2000, 2200, 2400, 2600, 2800, 3000, 7000],
                )
            return build_tail_1min_bars(
                ts_code,
                [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 9.98, 9.96, 9.94, 9.92, 9.9, 9.88, 9.84],
                [3000, 3000, 3000, 3000, 3000, 3000, 3000, 3200, 3400, 3600, 3800, 4000, 6000],
            )

        get_stock_minute_bars.side_effect = fake_bars

        result = build_intraday_monitor(now=datetime(2026, 7, 28, 14, 50))

        self.assertEqual(result["report_id"], 7)
        self.assertEqual(result["trade_date"], "20260728")
        self.assertEqual(result["latest_trade_date"], "20260728")
        self.assertTrue(result["data_current"])
        self.assertTrue(result["auto_refresh_enabled"])
        self.assertEqual(len(result["stocks"]), 2)
        by_code = {row["ts_code"]: row for row in result["stocks"]}
        self.assertEqual(by_code["301073.SZ"]["industry"], "酒店餐饮")
        self.assertEqual(by_code["301073.SZ"]["next_day_bias"], "高开偏强")
        self.assertEqual(by_code["301073.SZ"]["main_force_status"], "主力抢筹")
        self.assertEqual(by_code["301108.SZ"]["next_day_bias"], "低开风险")
        self.assertEqual(by_code["301108.SZ"]["main_force_status"], "放量分歧")

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
