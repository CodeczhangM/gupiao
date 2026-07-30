import unittest
from unittest.mock import patch

from fastapi import HTTPException

import app


class IntradayMonitorApiTests(unittest.TestCase):
    @patch(
        "app.build_intraday_monitor",
        return_value={"stocks": []},
    )
    def test_endpoint_forwards_force_refresh(self, service):
        app.intraday_monitor(force_refresh=True)

        service.assert_called_once_with(force_refresh=True)

    @patch("app.build_intraday_monitor", return_value={"stocks": [{"ts_code": "600001.SH"}]})
    def test_intraday_monitor_endpoint_returns_service_payload(self, service):
        response = app.intraday_monitor()

        self.assertEqual(response["stocks"][0]["ts_code"], "600001.SH")
        service.assert_called_once_with(force_refresh=False)

    @patch("app.build_intraday_monitor", side_effect=LookupError("还没有选股报告"))
    def test_intraday_monitor_endpoint_maps_missing_report_to_404(self, _service):
        with self.assertRaises(HTTPException) as raised:
            app.intraday_monitor()
        self.assertEqual(raised.exception.status_code, 404)

    @patch("app.build_intraday_monitor", side_effect=RuntimeError("tushare unavailable"))
    def test_intraday_monitor_endpoint_maps_upstream_failure_to_502(self, _service):
        with self.assertRaises(HTTPException) as raised:
            app.intraday_monitor()
        self.assertEqual(raised.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
