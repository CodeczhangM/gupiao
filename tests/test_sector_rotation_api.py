import unittest
from unittest.mock import patch

from fastapi import HTTPException

import app


class SectorRotationApiTests(unittest.TestCase):
    @patch(
        "app.build_tomorrow_sector_rotation",
        return_value={
            "trade_date": "20260812",
            "continuation_inflow": [],
            "rotation_rebound": [],
        },
    )
    def test_tomorrow_endpoint_forwards_parameters(self, service):
        result = app.sector_rotation_tomorrow(
            trade_date="20260812",
            limit=7,
            stocks_per_sector=4,
        )

        self.assertEqual(result["trade_date"], "20260812")
        service.assert_called_once_with(
            trade_date="20260812",
            limit=7,
            stocks_per_sector=4,
        )

    @patch(
        "app.build_tomorrow_sector_rotation",
        side_effect=LookupError("完整交易日不足"),
    )
    def test_lookup_error_maps_to_404(self, _service):
        with self.assertRaises(HTTPException) as raised:
            app.sector_rotation_tomorrow()

        self.assertEqual(raised.exception.status_code, 404)

    @patch(
        "app.build_tomorrow_sector_rotation",
        side_effect=ValueError("trade_date 格式错误"),
    )
    def test_value_error_maps_to_422(self, _service):
        with self.assertRaises(HTTPException) as raised:
            app.sector_rotation_tomorrow(trade_date="bad")

        self.assertEqual(raised.exception.status_code, 422)

    @patch(
        "app.build_tomorrow_sector_rotation",
        side_effect=RuntimeError("数据库不可用"),
    )
    def test_unknown_error_maps_to_502(self, _service):
        with self.assertRaises(HTTPException) as raised:
            app.sector_rotation_tomorrow()

        self.assertEqual(raised.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
