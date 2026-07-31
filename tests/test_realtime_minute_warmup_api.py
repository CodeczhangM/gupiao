import unittest
from unittest.mock import patch

import app


class RealtimeMinuteWarmupApiTests(unittest.TestCase):
    @patch("app.get_realtime_minute_warmup_status", return_value={"running": True})
    def test_realtime_minute_warmup_status_returns_service_payload(self, status):
        response = app.realtime_minute_warmup_status()

        self.assertTrue(response["running"])
        status.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
