import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app


class StockDetailApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app)

    @patch("app.get_stock_technical_detail", return_value={"trade_date": "20260615", "prompt": "x"})
    def test_technical_endpoint_returns_service_detail(self, service):
        response = self.client.get(
            "/api/stocks/600001.SH/technical",
            params={"trade_date": "20260615", "report_id": 3},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"trade_date": "20260615", "prompt": "x"})
        service.assert_called_once_with("600001.SH", "20260615", 3)

    @patch("app.get_stock_technical_detail", side_effect=ValueError("invalid stock code"))
    def test_technical_endpoint_maps_invalid_service_input_to_422(self, _service):
        response = self.client.get(
            "/api/stocks/600001.SH/technical", params={"trade_date": "20260615"}
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "invalid stock code")

    @patch("app.get_stock_technical_detail", side_effect=LookupError("report not found"))
    def test_technical_endpoint_maps_absent_report_to_404(self, _service):
        response = self.client.get(
            "/api/stocks/600001.SH/technical", params={"trade_date": "20260615"}
        )

        self.assertEqual(response.status_code, 404)

    @patch("app.get_stock_technical_detail", side_effect=RuntimeError("tushare unavailable"))
    def test_technical_endpoint_maps_upstream_failure_to_502(self, _service):
        response = self.client.get(
            "/api/stocks/600001.SH/technical", params={"trade_date": "20260615"}
        )

        self.assertEqual(response.status_code, 502)

    def test_technical_endpoint_requires_eight_digit_trade_date(self):
        response = self.client.get(
            "/api/stocks/600001.SH/technical", params={"trade_date": "2026-06-15"}
        )

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
