from unittest.mock import patch
import unittest

from fastapi import HTTPException

import app


class MarketNewsSummaryApiTests(unittest.TestCase):
    @patch("app.build_market_news_summary", return_value={"summary_text": "ok"})
    def test_market_news_summary_endpoint_forwards_query(self, service):
        result = app.market_news_summary(
            market="all",
            limit=5,
            force_refresh=True,
            use_ai=False,
        )

        self.assertEqual(result, {"summary_text": "ok"})
        service.assert_called_once_with(
            market="all",
            limit=5,
            force_refresh=True,
            use_ai=False,
        )

    @patch("app.build_market_news_summary", side_effect=RuntimeError("news down"))
    def test_market_news_summary_endpoint_maps_failure_to_502(self, _service):
        with self.assertRaises(HTTPException) as raised:
            app.market_news_summary()
        self.assertEqual(raised.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
