import unittest
from unittest.mock import patch

from fastapi import HTTPException

import app


class OvernightMonitorApiTests(unittest.TestCase):
    @patch("app.build_overnight_monitor", return_value={"stocks": [{"ts_code": "600101.SH"}]})
    def test_overnight_monitor_endpoint_returns_service_payload(self, service):
        response = app.overnight_monitor(limit=20)

        self.assertEqual(response["stocks"][0]["ts_code"], "600101.SH")
        service.assert_called_once_with(limit=20)

    @patch("app.build_overnight_monitor", side_effect=RuntimeError("tushare unavailable"))
    def test_overnight_monitor_endpoint_maps_failure_to_502(self, _service):
        with self.assertRaises(HTTPException) as raised:
            app.overnight_monitor()

        self.assertEqual(raised.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
