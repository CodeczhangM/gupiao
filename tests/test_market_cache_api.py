import unittest
from unittest.mock import patch

import app


class MarketCacheApiTests(unittest.TestCase):
    @patch("app.sync_cached_market_data", return_value={"cache_updated": True})
    def test_sync_cache(self, sync):
        response = app.cache_sync(force_current=True)
        self.assertTrue(response["cache_updated"])
        sync.assert_called_once_with(force_current=True)

    @patch("app.get_cache_status", return_value={"complete_dates": 120})
    def test_cache_status(self, _status):
        response = app.cache_status()
        self.assertEqual(response["complete_dates"], 120)
