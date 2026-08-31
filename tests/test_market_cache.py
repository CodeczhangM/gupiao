import os
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pandas as pd

import market_cache
import data_service


class MarketCachePolicyTests(unittest.TestCase):
    def test_config_defaults_to_120_bootstrap_and_100_required_days(self):
        with patch.dict(os.environ, {}, clear=True):
            config = market_cache.get_cache_config()
        self.assertEqual(config.bootstrap_days, 120)
        self.assertEqual(config.required_days, 100)
        self.assertTrue(config.enabled)

    def test_config_rejects_bootstrap_shorter_than_required(self):
        with patch.dict(os.environ, {
            "MARKET_CACHE_BOOTSTRAP_DAYS": "90",
            "MARKET_CACHE_REQUIRED_DAYS": "100",
        }, clear=True):
            with self.assertRaisesRegex(ValueError, "不得小于"):
                market_cache.get_cache_config()

    def test_current_trade_date_refreshes_before_1530_only(self):
        tz = ZoneInfo("Asia/Shanghai")
        self.assertTrue(market_cache.should_refresh_current_date(
            "20260713", ["20260713"], {"20260713"}, datetime(2026, 7, 13, 14, 0, tzinfo=tz)
        ))
        self.assertFalse(market_cache.should_refresh_current_date(
            "20260713", ["20260713"], {"20260713"}, datetime(2026, 7, 13, 16, 0, tzinfo=tz)
        ))
        self.assertFalse(market_cache.should_refresh_current_date(
            "20260712", ["20260710"], set(), datetime(2026, 7, 12, 14, 0, tzinfo=tz)
        ))

    def test_normalize_records_converts_nan_to_none(self):
        rows = market_cache.dataframe_records(pd.DataFrame([{"ts_code": "600001.SH", "close": float("nan")}]))
        self.assertIsNone(rows[0]["close"])

    @patch("market_cache.get_complete_dates", return_value=["20260715"] + ["20260710"] * 41)
    @patch("market_cache.init_market_cache")
    def test_status_includes_progress_targets(self, _init, _complete_dates):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        conn = MagicMock()
        conn.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
        with patch("market_cache.get_connection", return_value=conn), patch.dict(os.environ, {
            "MARKET_CACHE_BOOTSTRAP_DAYS": "150",
            "MARKET_CACHE_REQUIRED_DAYS": "90",
        }):
            status = market_cache.get_cache_status()
        self.assertEqual(status["complete_dates"], 42)
        self.assertEqual(status["latest_complete_date"], "20260715")
        self.assertEqual(status["target_days"], 150)
        self.assertEqual(status["required_days"], 90)

    @patch("market_cache.init_market_cache")
    def test_status_progress_counts_current_trade_window_missing_dates(self, _init):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        conn = MagicMock()
        conn.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor

        with patch("market_cache.get_connection", return_value=conn), patch.dict(os.environ, {
            "MARKET_CACHE_BOOTSTRAP_DAYS": "4",
            "MARKET_CACHE_REQUIRED_DAYS": "3",
        }):
            status = market_cache.get_cache_status(
                trade_date_loader=lambda **_kwargs: ["20260717", "20260716", "20260715", "20260714"],
                complete_date_loader=lambda _limit: ["20260715", "20260714", "20260713", "20260710"],
            )

        self.assertEqual(status["complete_dates"], 2)
        self.assertEqual(status["missing_dates"], 2)
        self.assertEqual(status["target_days"], 4)
        self.assertEqual(status["latest_complete_date"], "20260715")

    def test_read_frame_preserves_dict_cursor_values(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            {"trade_date": "20260710", "ts_code": "000001.SZ", "close": 12.3},
            {"trade_date": "20260709", "ts_code": "000001.SZ", "close": 12.1},
        ]
        conn = MagicMock()
        conn.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor

        with patch("market_cache.get_connection", return_value=conn):
            frame = market_cache._read_frame(
                "SELECT trade_date,ts_code,close FROM market_daily WHERE trade_date=%s",
                ("20260710",),
            )

        cursor.execute.assert_called_once_with(
            "SELECT trade_date,ts_code,close FROM market_daily WHERE trade_date=%s",
            ("20260710",),
        )
        self.assertEqual(frame["trade_date"].tolist(), ["20260710", "20260709"])
        self.assertEqual(frame["ts_code"].tolist(), ["000001.SZ", "000001.SZ"])

    @patch("market_cache._read_frame", return_value=pd.DataFrame())
    def test_market_snapshot_exposes_full_review_fields(self, read_frame):
        market_cache.load_market_snapshot("20260730")

        sql = read_frame.call_args.args[0]
        for column in (
            "turnover_rate_f", "ps", "ps_ttm", "dv_ratio", "dv_ttm",
            "area", "market", "list_status", "list_date",
        ):
            self.assertIn(column, sql)

    @patch("market_cache._read_frame", return_value=pd.DataFrame())
    @patch("market_cache.get_complete_dates", return_value=["20260710", "20260709"])
    def test_recent_daily_reads_only_strategy_columns(self, _dates, read_frame):
        market_cache.load_recent_daily("20260710", 2)

        sql = read_frame.call_args.args[0]
        self.assertIn("SELECT ts_code,trade_date,open,high,low,close,vol,amount,pct_chg FROM market_daily", sql)
        self.assertNotIn("SELECT *", sql)

    @patch("market_cache.get_complete_dates", return_value=[])
    @patch("market_cache.replace_daily_source")
    @patch("market_cache.init_market_cache")
    def test_sync_bootstraps_all_requested_dates(self, _init, replace_source, _complete):
        dates = ["20260702", "20260701"]
        def fetcher(api_name, **kwargs):
            if api_name == "stock_basic":
                return pd.DataFrame([{"ts_code": "600001.SH", "name": "测试", "industry": "银行"}])
            return pd.DataFrame([{"trade_date": kwargs["trade_date"], "ts_code": "600001.SH"}])

        with patch.dict(os.environ, {"MARKET_CACHE_ENABLED": "true"}):
            result = market_cache.sync_market_cache(fetcher, lambda **_kwargs: dates)

        self.assertTrue(result["cache_updated"])
        self.assertEqual(result["data_trade_date"], "20260702")
        self.assertEqual(replace_source.call_count, 8)
        self.assertEqual(replace_source.call_args_list[0].args[1], "20260702")

    @patch("market_cache.get_complete_dates", side_effect=[
        ["20260715", "20260714"],
        ["20260715", "20260714"],
    ])
    @patch("market_cache.replace_daily_source")
    @patch("market_cache.init_market_cache")
    def test_sync_retries_zero_row_moneyflow_for_complete_daily_date(
        self, _init, replace_source, _complete
    ):
        dates = ["20260715", "20260714"]
        calls = []

        def fetcher(api_name, **kwargs):
            calls.append((api_name, kwargs.get("trade_date")))
            if api_name == "stock_basic":
                return pd.DataFrame([{"ts_code": "600001.SH", "name": "测试", "industry": "银行"}])
            return pd.DataFrame([{"trade_date": kwargs["trade_date"], "ts_code": "600001.SH"}])

        with patch.dict(os.environ, {"MARKET_CACHE_ENABLED": "true"}):
            market_cache.sync_market_cache(fetcher, lambda **_kwargs: dates)

        self.assertIn(("moneyflow_ind_dc", "20260715"), calls)
        self.assertIn(("moneyflow_ind_dc", "20260714"), calls)
        self.assertEqual(replace_source.call_count, 8)

    @patch("market_cache.get_complete_dates", side_effect=[
        ["20260715", "20260714"],
        ["20260715", "20260714"],
    ])
    @patch("market_cache.replace_daily_source")
    @patch("market_cache.init_market_cache")
    def test_sync_can_skip_recent_complete_dates_for_realtime_refresh(
        self, _init, replace_source, _complete
    ):
        dates = ["20260715", "20260714"]
        calls = []

        def fetcher(api_name, **kwargs):
            calls.append((api_name, kwargs.get("trade_date")))
            return pd.DataFrame()

        with patch.dict(os.environ, {"MARKET_CACHE_ENABLED": "true"}):
            result = market_cache.sync_market_cache(
                fetcher,
                lambda **_kwargs: dates,
                retry_recent=False,
            )

        self.assertEqual(calls, [])
        self.assertEqual(replace_source.call_count, 0)
        self.assertFalse(result["cache_updated"])

    @patch("market_cache.sync_market_cache")
    @patch("market_cache.init_market_cache")
    @patch("market_cache.get_complete_dates", return_value=[f"2026{index:04d}" for index in range(70)])
    def test_scan_fails_fast_when_cache_has_fewer_than_required_days(self, _dates, _init, sync):
        with patch.dict(os.environ, {
            "MARKET_CACHE_ENABLED": "true",
            "MARKET_CACHE_BOOTSTRAP_DAYS": "120",
            "MARKET_CACHE_REQUIRED_DAYS": "100",
        }):
            with self.assertRaisesRegex(RuntimeError, "仅有 70 个完整交易日"):
                market_cache.ensure_market_cache(None, None)
        sync.assert_not_called()

    @patch("market_cache.sync_market_cache")
    @patch("market_cache.init_market_cache")
    @patch("market_cache.get_complete_dates", return_value=["20260710"] + [f"2026{index:04d}" for index in range(99)])
    def test_scan_uses_ready_cache_without_remote_sync(self, _dates, _init, sync):
        with patch.dict(os.environ, {
            "MARKET_CACHE_ENABLED": "true",
            "MARKET_CACHE_BOOTSTRAP_DAYS": "120",
            "MARKET_CACHE_REQUIRED_DAYS": "100",
        }):
            result = market_cache.ensure_market_cache(None, None)
        self.assertEqual(result["data_trade_date"], "20260710")
        self.assertFalse(result["cache_updated"])
        sync.assert_not_called()

    @patch("data_service.load_recent_daily", return_value=pd.DataFrame([{"trade_date": "20260701"}]))
    @patch("data_service.load_market_snapshot", return_value=pd.DataFrame([{"ts_code": "600001.SH"}]))
    @patch("data_service.ensure_market_cache", return_value={
        "data_trade_date": "20260701", "cache_updated": True, "cache_warnings": []
    })
    def test_scan_inputs_are_loaded_from_cache(self, _sync, _snapshot, _history):
        with patch.dict(os.environ, {"MARKET_CACHE_REQUIRED_DAYS": "1", "MARKET_CACHE_BOOTSTRAP_DAYS": "1"}):
            market, history, metadata = data_service.get_cached_scan_inputs(1)
        self.assertEqual(metadata["data_trade_date"], "20260701")
        self.assertEqual(len(market), 1)
        self.assertEqual(len(history), 1)


if __name__ == "__main__":
    unittest.main()
