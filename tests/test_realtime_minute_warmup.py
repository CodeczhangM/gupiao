import unittest
from datetime import datetime
from unittest.mock import patch

import pandas as pd

from realtime_minute_warmup import (
    get_realtime_minute_warmup_status,
    start_realtime_minute_warmup,
    warm_realtime_minute_cache,
)


class RealtimeMinuteWarmupTests(unittest.TestCase):
    def test_warmup_skips_fetch_when_cached_minutes_cover_requested_window(self):
        cached = pd.DataFrame([
            {
                "ts_code": "600001.SH",
                "trade_time": "2026-07-31 14:25:00",
                "close": 10.0,
            },
            {
                "ts_code": "600001.SH",
                "trade_time": "2026-07-31 14:57:00",
                "close": 10.2,
            },
        ])
        with (
            patch("realtime_minute_warmup.load_minute_cache", return_value=cached),
            patch("realtime_minute_warmup.load_minutes_with_fallback") as loader,
            patch("realtime_minute_warmup.save_minute_cache") as saver,
        ):
            result = warm_realtime_minute_cache(
                now=datetime(2026, 7, 31, 14, 58, 10),
                candidate_codes=["600001.SH"],
                frequencies=("1min",),
            )

        self.assertEqual(result["fetched_count"], 0)
        self.assertEqual(result["cache_hit_count"], 1)
        loader.assert_not_called()
        saver.assert_not_called()

    def test_warmup_fetches_only_missing_tail_minutes_after_cache_end(self):
        cached = pd.DataFrame([
            {
                "ts_code": "600001.SH",
                "trade_time": "2026-07-31 14:25:00",
                "close": 10.0,
            },
            {
                "ts_code": "600001.SH",
                "trade_time": "2026-07-31 14:42:00",
                "close": 10.1,
            },
        ])
        loaded = pd.DataFrame([
            {
                "ts_code": "600001.SH",
                "trade_time": "2026-07-31 14:43:00",
                "open": 10.1,
                "high": 10.2,
                "low": 10.1,
                "close": 10.2,
                "vol": 1000,
                "amount": 10200,
            },
        ])
        with (
            patch("realtime_minute_warmup.load_minute_cache", return_value=cached),
            patch("realtime_minute_warmup.load_minutes_with_fallback") as loader,
            patch("realtime_minute_warmup.save_minute_cache") as saver,
            patch("realtime_minute_warmup.get_stock_minute_bars", return_value=pd.DataFrame()),
        ):
            loader.return_value = type(
                "MinuteLoadResult",
                (),
                {"bars": loaded, "source": "eastmoney_fallback", "warnings": []},
            )()
            result = warm_realtime_minute_cache(
                now=datetime(2026, 7, 31, 14, 58, 10),
                candidate_codes=["600001.SH"],
                frequencies=("1min",),
            )

        self.assertEqual(result["fetched_count"], 1)
        self.assertEqual(result["cache_hit_count"], 0)
        loader.assert_called_once()
        self.assertEqual(loader.call_args.args[1], "2026-07-31 14:43:00")
        self.assertEqual(loader.call_args.args[2], "2026-07-31 14:57:00")
        saver.assert_called_once()

    def test_start_realtime_minute_warmup_is_idempotent(self):
        with patch("realtime_minute_warmup.threading.Thread") as thread_class:
            first = start_realtime_minute_warmup(interval_seconds=30)
            second = start_realtime_minute_warmup(interval_seconds=30)

        self.assertTrue(first["enabled"])
        self.assertTrue(second["enabled"])
        self.assertTrue(second["already_running"])
        thread_class.assert_called_once()
        thread_class.return_value.start.assert_called_once()
        status = get_realtime_minute_warmup_status()
        self.assertTrue(status["running"])


if __name__ == "__main__":
    unittest.main()
