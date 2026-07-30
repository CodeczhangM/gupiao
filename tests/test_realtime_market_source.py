import json
import unittest
from unittest.mock import patch

import pandas as pd

from realtime_market_source import (
    MinuteLoadResult,
    _eastmoney_secid,
    _fetch_eastmoney_minutes,
    _parse_eastmoney_klines,
    _parse_eastmoney_snapshot,
    _parse_eastmoney_trends,
    _parse_sina_klines,
    _run_curl,
    _sina_symbol,
    clear_realtime_source_caches,
    load_minutes_with_fallback,
)


def minute_frame(day="2026-07-30", rows=35):
    times = pd.date_range(f"{day} 09:30:00", periods=rows, freq="60min")
    return pd.DataFrame([{
        "ts_code": "600298.SH",
        "trade_time": stamp,
        "open": 10,
        "close": 10 + index / 100,
        "high": 10.5,
        "low": 9.8,
        "vol": 1000,
        "amount": 10000,
    } for index, stamp in enumerate(times)])


class RealtimeMarketSourceTests(unittest.TestCase):
    def setUp(self):
        clear_realtime_source_caches()

    def test_symbol_converters_support_shenzhen_and_shanghai_only(self):
        self.assertEqual(_eastmoney_secid("600298.SH"), "1.600298")
        self.assertEqual(_eastmoney_secid("300910.SZ"), "0.300910")
        self.assertEqual(_sina_symbol("600298.SH"), "sh600298")
        self.assertEqual(_sina_symbol("300910.SZ"), "sz300910")
        self.assertIsNone(_eastmoney_secid("830001.BJ"))
        self.assertIsNone(_sina_symbol("bad"))

    def test_eastmoney_snapshot_maps_fields_and_drops_invalid_rows(self):
        payload = {"data": {"diff": [{
            "f12": "600298", "f13": 1, "f14": "安琪酵母",
            "f2": 39.12, "f15": 39.80, "f16": 38.50,
            "f17": 38.70, "f18": 38.62, "f3": 1.29,
            "f8": 2.61, "f10": 1.47, "f5": 123456,
            "f6": 482000000, "f100": "食品饮料",
        }, {"f12": "-", "f13": 1, "f14": "无效"}]}}

        result = _parse_eastmoney_snapshot(payload, "20260730")

        self.assertEqual(result["ts_code"].tolist(), ["600298.SH"])
        self.assertEqual(result.iloc[0]["trade_date"], "20260730")
        self.assertEqual(result.iloc[0]["close"], 39.12)
        self.assertEqual(result.iloc[0]["industry"], "食品饮料")

    def test_minute_parsers_filter_old_future_and_duplicate_rows(self):
        payload = {"data": {"trends": [
            "2026-07-29 14:59,10,10,10,10,100,1000,10",
            "2026-07-30 14:29,10,10.1,10.2,9.9,100,1010,10",
            "2026-07-30 14:29,10,10.2,10.3,9.9,200,2040,10",
            "2026-07-30 14:31,10,10.4,10.5,9.9,100,1040,10",
        ]}}
        result = _parse_eastmoney_trends(
            payload, "600298.SH", "20260730", "2026-07-30 14:30:00"
        )
        self.assertEqual(
            result["trade_time"].dt.strftime("%Y-%m-%d %H:%M").tolist(),
            ["2026-07-29 14:59", "2026-07-30 14:29"],
        )
        self.assertEqual(result.iloc[-1]["close"], 10.2)

        klines = {"data": {"klines": [
            "2026-07-29 14:00,9.8,9.9,10,9.7,1000,9900",
            "2026-07-30 14:00,10,10.2,10.3,9.9,2000,20400",
        ]}}
        east = _parse_eastmoney_klines(
            klines, "600298.SH", "20260730", "2026-07-30 14:30:00"
        )
        self.assertEqual(east.iloc[-1]["close"], 10.2)

        sina = (
            'var x=([{"day":"2026-07-30 14:00:00","open":"10",'
            '"high":"10.3","low":"9.9","close":"10.2",'
            '"volume":"2000","amount":"20400"}]);'
        )
        parsed = _parse_sina_klines(
            sina, "600298.SH", "20260730", "2026-07-30 14:30:00"
        )
        self.assertEqual(parsed.iloc[0]["close"], 10.2)

    @patch("realtime_market_source._fetch_sina_minutes")
    @patch("realtime_market_source._fetch_eastmoney_minutes")
    def test_valid_tushare_minutes_remain_primary(self, eastmoney, sina):
        result = load_minutes_with_fallback(
            "600298.SH", "2026-05-20 09:30:00",
            "2026-07-30 14:30:00", "60min", "20260730",
            primary_loader=lambda *args, **kwargs: minute_frame(),
        )
        self.assertEqual(result.source, "tushare")
        eastmoney.assert_not_called()
        sina.assert_not_called()

    @patch("realtime_market_source._fetch_sina_minutes")
    @patch("realtime_market_source._fetch_eastmoney_minutes")
    def test_empty_tushare_uses_eastmoney(self, eastmoney, sina):
        eastmoney.return_value = (minute_frame(), None)
        result = load_minutes_with_fallback(
            "600298.SH", "2026-05-20 09:30:00",
            "2026-07-30 14:30:00", "60min", "20260730",
            primary_loader=lambda *args, **kwargs: pd.DataFrame(),
        )
        self.assertEqual(result.source, "eastmoney_fallback")
        sina.assert_not_called()

    @patch("realtime_market_source._fetch_sina_minutes")
    @patch("realtime_market_source._fetch_eastmoney_minutes")
    def test_eastmoney_failure_uses_sina(self, eastmoney, sina):
        eastmoney.return_value = (pd.DataFrame(), "东方财富请求失败")
        sina.return_value = (minute_frame(), None)
        result = load_minutes_with_fallback(
            "600298.SH", "2026-05-20 09:30:00",
            "2026-07-30 14:30:00", "60min", "20260730",
            primary_loader=lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("Tushare限流")
            ),
        )
        self.assertEqual(result.source, "sina_fallback")
        self.assertIn("Tushare限流", "；".join(result.warnings))

    @patch("realtime_market_source._fetch_sina_minutes")
    @patch("realtime_market_source._fetch_eastmoney_minutes")
    def test_old_one_minute_rows_are_rejected(self, eastmoney, sina):
        old = minute_frame(day="2026-07-29", rows=5)
        eastmoney.return_value = (old, None)
        sina.return_value = (old, None)
        result = load_minutes_with_fallback(
            "600298.SH", "2026-07-30 14:25:00",
            "2026-07-30 14:30:00", "1min", "20260730",
            primary_loader=lambda *args, **kwargs: old,
        )
        self.assertTrue(result.bars.empty)
        self.assertEqual(result.source, "unavailable")

    @patch("realtime_market_source.subprocess.run")
    def test_run_curl_uses_bounded_retry_contract(self, run):
        run.return_value.stdout = "{}"
        self.assertEqual(_run_curl("https://example.invalid/data"), "{}")
        args = run.call_args.args[0]
        self.assertEqual(args[:3], ["curl", "-fsSL", "--max-time"])
        self.assertIn("--retry", args)
        self.assertEqual(run.call_args.kwargs["timeout"], 15)

    @patch("realtime_market_source._run_curl")
    def test_eastmoney_minute_success_uses_short_cache(self, run_curl):
        run_curl.return_value = json.dumps({"data": {"trends": [
            "2026-07-30 14:29,10,10.2,10.3,9.9,200,2040,10"
        ]}})
        first, _ = _fetch_eastmoney_minutes(
            "600298.SH", "2026-07-30 14:25:00",
            "2026-07-30 14:30:00", "1min", "20260730",
        )
        second, _ = _fetch_eastmoney_minutes(
            "600298.SH", "2026-07-30 14:25:00",
            "2026-07-30 14:30:00", "1min", "20260730",
        )
        pd.testing.assert_frame_equal(first, second)
        self.assertEqual(run_curl.call_count, 1)

    def test_provider_failure_opens_circuit_for_other_symbols(self):
        with (
            patch("realtime_market_source.time.monotonic", return_value=100.0),
            patch("realtime_market_source._run_curl", side_effect=RuntimeError("down")) as run_curl,
        ):
            for code in ("600298.SH", "300910.SZ", "600001.SH"):
                _fetch_eastmoney_minutes(
                    code,
                    "2026-07-30 14:25:00",
                    "2026-07-30 14:30:00",
                    "1min",
                    "20260730",
                )

        self.assertEqual(run_curl.call_count, 2)

    def test_provider_is_retried_after_circuit_timeout(self):
        clock = [100.0]
        success_payload = json.dumps({"data": {"trends": [
            "2026-07-30 14:29,10,10.2,10.3,9.9,200,2040,10"
        ]}})
        with (
            patch("realtime_market_source.time.monotonic", side_effect=lambda: clock[0]),
            patch(
                "realtime_market_source._run_curl",
                side_effect=[RuntimeError("down"), RuntimeError("down"), success_payload],
            ) as run_curl,
        ):
            for code in ("600298.SH", "300910.SZ", "600001.SH"):
                _fetch_eastmoney_minutes(
                    code,
                    "2026-07-30 14:25:00",
                    "2026-07-30 14:30:00",
                    "1min",
                    "20260730",
                )
            self.assertEqual(run_curl.call_count, 2)

            clock[0] = 161.0
            recovered, error = _fetch_eastmoney_minutes(
                "600002.SH",
                "2026-07-30 14:25:00",
                "2026-07-30 14:30:00",
                "1min",
                "20260730",
            )

        self.assertFalse(recovered.empty)
        self.assertIsNone(error)
        self.assertEqual(run_curl.call_count, 3)


if __name__ == "__main__":
    unittest.main()
