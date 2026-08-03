import unittest
from unittest.mock import patch

from fastapi import HTTPException

import app


class MorningFollowApiTests(unittest.TestCase):
    @patch(
        "app.build_morning_follow_monitor",
        return_value={"stocks": [{"ts_code": "600101.SH"}]},
    )
    def test_endpoint_returns_new_service_payload(self, service):
        result = app.morning_follow_monitor(limit=12)

        self.assertEqual(result["stocks"][0]["ts_code"], "600101.SH")
        service.assert_called_once_with(limit=12)

    @patch(
        "app.build_morning_follow_monitor",
        side_effect=RuntimeError("minutes unavailable"),
    )
    def test_endpoint_maps_failure_to_502(self, _service):
        with self.assertRaises(HTTPException) as raised:
            app.morning_follow_monitor()

        self.assertEqual(raised.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
