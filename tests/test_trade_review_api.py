import unittest
from unittest.mock import patch

from fastapi import HTTPException

import app


class TradeReviewApiTests(unittest.TestCase):
    @patch("app.review_trade", return_value={"ai_summary": "ok"})
    def test_review_endpoint_forwards_json_body(self, service):
        payload = {"tsCode": "600001.SH", "buyDate": "20260601", "buyPrice": 10}

        self.assertEqual(app.trade_review(payload), {"ai_summary": "ok"})
        service.assert_called_once_with(payload)

    @patch("app.review_trade", side_effect=ValueError("买入日期错误"))
    def test_review_endpoint_maps_invalid_input_to_422(self, _service):
        with self.assertRaises(HTTPException) as context:
            app.trade_review({})
        self.assertEqual(context.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
