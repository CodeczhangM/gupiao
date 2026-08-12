import unittest
import inspect
from unittest.mock import patch

from fastapi import HTTPException

import app


class RealtimeInfoApiTests(unittest.TestCase):
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

    @patch("app.build_realtime_info", side_effect=RuntimeError("tushare unavailable"))
    def test_realtime_info_endpoint_maps_failure_to_502(self, _service):
        with self.assertRaises(HTTPException) as raised:
            app.realtime_info()

        self.assertEqual(raised.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
