import unittest
from unittest.mock import patch

from fastapi import HTTPException

import app


class TrendBoxTargetApiTests(unittest.TestCase):
    @patch("app.analyze_trend_box_target", return_value={"identity": {"ts_code": "600001.SH"}})
    def test_trend_box_endpoint_returns_service_result(self, service):
        result = app.stock_trend_box_target(
            "600001.SH",
            end_trade_date="20260615",
            lookback_days=90,
            auto_detect=True,
            box_start=None,
            box_end=None,
        )

        self.assertEqual(result, {"identity": {"ts_code": "600001.SH"}})
        service.assert_called_once_with("600001.SH", "20260615", 90, manual_box=None)

    @patch("app.analyze_trend_box_target", return_value={"manual_box": {"sideways_ended": True}})
    def test_trend_box_endpoint_passes_manual_box_when_auto_detect_disabled(self, service):
        result = app.stock_trend_box_target(
            "600667.SH",
            end_trade_date="20260814",
            lookback_days=120,
            auto_detect=False,
            box_start="20260722",
            box_end="20260804",
        )

        self.assertEqual(result, {"manual_box": {"sideways_ended": True}})
        service.assert_called_once_with(
            "600667.SH",
            "20260814",
            120,
            manual_box={"start": "20260722", "end": "20260804"},
        )

    @patch("app.analyze_trend_box_target", side_effect=ValueError("invalid ts_code"))
    def test_trend_box_endpoint_maps_invalid_input_to_422(self, _service):
        with self.assertRaises(HTTPException) as exc:
            app.stock_trend_box_target("bad", end_trade_date="20260615")

        self.assertEqual(exc.exception.status_code, 422)
        self.assertEqual(exc.exception.detail, "invalid ts_code")


if __name__ == "__main__":
    unittest.main()
