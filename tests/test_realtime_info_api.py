import unittest
import inspect
from unittest.mock import patch

from fastapi import HTTPException

import app


class RealtimeInfoApiTests(unittest.TestCase):
    @patch("app.build_daily_position_candidate_info", return_value={
        "data_source": "database_daily", "intraday": {"position_candidates": []},
    })
    def test_daily_position_endpoint_forwards_refresh_and_debug(self, service):
        response = app.daily_position_candidate_info(
            limit=10, force_refresh=True, debug=True
        )
        self.assertEqual(response["data_source"], "database_daily")
        service.assert_called_once_with(
            limit=10, force_refresh=True, debug=True
        )
    @patch(
        "app.build_realtime_info",
        return_value={
            "trade_date": "20260731",
            "intraday": {"stocks": []},
            "overnight": {"stocks": []},
        },
    )
    def test_realtime_info_defaults_to_tail_premium_top20(self, service):
        default = inspect.signature(
            app.realtime_info
        ).parameters["limit"].default
        self.assertEqual(default.default, 20)
        response = app.realtime_info(limit=20)

        self.assertEqual(response["trade_date"], "20260731")
        service.assert_called_once_with(limit=20, force_refresh=False)

    @patch("app.build_realtime_info", return_value={"trade_date": "20260729", "intraday": {"stocks": []}, "overnight": {"stocks": []}})
    def test_realtime_info_endpoint_returns_service_payload(self, service):
        response = app.realtime_info(limit=10)

        self.assertEqual(response["trade_date"], "20260729")
        service.assert_called_once_with(limit=10, force_refresh=False)

    @patch("app.build_realtime_info", return_value={"trade_date": "20260730", "intraday": {"stocks": []}, "overnight": {"stocks": []}})
    def test_realtime_info_endpoint_forwards_force_refresh(self, service):
        response = app.realtime_info(limit=10, force_refresh=True)

        self.assertEqual(response["trade_date"], "20260730")
        service.assert_called_once_with(limit=10, force_refresh=True)

    @patch("app.build_realtime_info", return_value={"trade_date": "20260730", "intraday": {"stocks": []}, "overnight": {"stocks": []}})
    def test_realtime_info_endpoint_forwards_debug(self, service):
        response = app.realtime_info(limit=10, debug=True)

        self.assertEqual(response["trade_date"], "20260730")
        service.assert_called_once_with(limit=10, force_refresh=False, debug=True)

    @patch("app.build_realtime_info", return_value={
        "trade_date": "20260831",
        "intraday": {
            "stocks": [{"ts_code": "600001.SH"}],
            "position_candidates": [{"ts_code": "600001.SH", "position_score": 82}],
            "position_candidate_count": 1,
            "observation_stocks": [], "trigger_stocks": [], "launch_stocks": [],
        },
        "overnight": {"stocks": []},
    })
    def test_realtime_info_endpoint_preserves_unified_and_legacy_fields(self, service):
        response = app.realtime_info(limit=10)
        intraday = response["intraday"]

        self.assertEqual(intraday["position_candidate_count"], 1)
        self.assertLessEqual(len(intraday["position_candidates"]), 10)
        self.assertIn("observation_stocks", intraday)
        self.assertIn("trigger_stocks", intraday)
        self.assertIn("launch_stocks", intraday)
        service.assert_called_once_with(limit=10, force_refresh=False)

    @patch("app.build_realtime_tail_premium_info", return_value={"stocks": [{"ts_code": "603118.SH"}]})
    def test_tail_premium_endpoint_refreshes_only_overnight_section(self, service):
        response = app.realtime_tail_premium_info(
            limit=20,
            force_refresh=True,
            debug=True,
        )

        self.assertEqual(response["stocks"][0]["ts_code"], "603118.SH")
        service.assert_called_once_with(limit=20, force_refresh=True, debug=True)

    @patch("app.build_realtime_info", side_effect=RuntimeError("tushare unavailable"))
    def test_realtime_info_endpoint_maps_failure_to_502(self, _service):
        with self.assertRaises(HTTPException) as raised:
            app.realtime_info()

        self.assertEqual(raised.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
