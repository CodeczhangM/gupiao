import unittest
from datetime import datetime
import threading
import time
from unittest.mock import patch

import pandas as pd

from realtime_market_source import clear_realtime_source_caches
from realtime_info_service import (
    MinuteLoadResult,
    build_realtime_info,
    _REALTIME_INTRADAY_RESULT_CACHE,
    _fill_missing_realtime_volume_ratio,
    _load_realtime_market_inputs,
    _load_realtime_intraday_signal_bars,
    _minute_result_with_1459_fallback,
    _snapshot_supports_realtime_filters,
    _trading_session_progress,
)
from tests.test_advantage_stock_scoring import build_60min_bars, build_tail_1min_bars, water_macd_kdj_cross_closes


class RealtimeInfoServiceTests(unittest.TestCase):
    def setUp(self):
        _REALTIME_INTRADAY_RESULT_CACHE.clear()
        import realtime_info_service
        if hasattr(realtime_info_service, "_clear_realtime_result_caches"):
            realtime_info_service._clear_realtime_result_caches()
        clear_realtime_source_caches()
        self.snapshot_fallback = patch(
            "realtime_info_service.load_eastmoney_market_snapshot",
            return_value=(pd.DataFrame(), "测试中禁用外部快照"),
        )
        self.eastmoney_minutes = patch(
            "realtime_market_source._fetch_eastmoney_minutes",
            return_value=(pd.DataFrame(), "测试中禁用东方财富分钟"),
        )
        self.sina_minutes = patch(
            "realtime_market_source._fetch_sina_minutes",
            return_value=(pd.DataFrame(), "测试中禁用新浪分钟"),
        )
        self.database_minutes = patch(
            "realtime_info_service.load_minute_cache",
            return_value=pd.DataFrame(),
        )
        self.database_minute_saves = patch(
            "realtime_info_service.save_minute_cache",
        )
        self.database_results = patch(
            "realtime_info_service.load_result_cache",
            return_value=None,
            create=True,
        )
        self.database_result_saves = patch(
            "realtime_info_service.save_result_cache",
            create=True,
        )
        self.database_prune = patch(
            "realtime_info_service.prune_realtime_cache",
            create=True,
        )
        self.complete_dates = patch(
            "realtime_info_service.get_complete_dates",
            return_value=[
                "20260729",
                "20260728",
                "20260727",
                "20260724",
                "20260723",
            ],
        )
        self.snapshot_fallback.start()
        self.eastmoney_minutes.start()
        self.sina_minutes.start()
        self.database_minutes.start()
        self.database_minute_saves.start()
        self.database_results.start()
        self.database_result_saves.start()
        self.database_prune.start()
        self.complete_dates.start()
        self.addCleanup(self.snapshot_fallback.stop)
        self.addCleanup(self.eastmoney_minutes.stop)
        self.addCleanup(self.sina_minutes.stop)
        self.addCleanup(self.database_minutes.stop)
        self.addCleanup(self.database_minute_saves.stop)
        self.addCleanup(self.database_results.stop)
        self.addCleanup(self.database_result_saves.stop)
        self.addCleanup(self.database_prune.stop)
        self.addCleanup(self.complete_dates.stop)

    def test_trading_session_progress_stops_during_lunch_break(self):
        self.assertEqual(
            _trading_session_progress(datetime(2026, 7, 29, 12, 15)),
            0.5,
        )

    def test_derived_cache_keys_include_current_macd_parameter_key(self):
        import realtime_info_service

        with patch(
            "realtime_info_service.macd_parameter_key",
            return_value="macd-5-34-5-v8",
        ):
            memory_key = realtime_info_service._realtime_result_key(
                10,
                datetime(2026, 7, 30, 10, 0, 1),
            )
            database_key = (
                realtime_info_service._database_realtime_result_key(10)
            )

        self.assertIn("macd-5-34-5-v8", memory_key)
        self.assertIn("macd-5-34-5-v8", database_key)

    def test_screening_date_uses_base_snapshot_without_actual_minutes(self):
        import realtime_info_service

        self.assertEqual(
            realtime_info_service._screening_data_trade_date(
                None,
                "20260730",
                "20260729",
            ),
            "20260729",
        )
        self.assertEqual(
            realtime_info_service._screening_data_trade_date(
                "2026-07-30 14:49:00",
                "20260730",
                "20260729",
            ),
            "20260730",
        )

    def test_missing_realtime_volume_ratio_uses_prior_five_day_average(self):
        market = pd.DataFrame([{
            "ts_code": "600201.SH",
            "vol": 120_000,
            "volume_ratio": None,
        }])
        history = pd.DataFrame([
            {"ts_code": "600201.SH", "trade_date": date, "vol": volume}
            for date, volume in [
                ("20260722", 100_000),
                ("20260723", 100_000),
                ("20260724", 100_000),
                ("20260725", 100_000),
                ("20260728", 100_000),
            ]
        ])

        result = _fill_missing_realtime_volume_ratio(
            market,
            history,
            "20260729",
            datetime(2026, 7, 29, 14, 30),
        )

        self.assertAlmostEqual(result.iloc[0]["volume_ratio"], 48 / 35)

    def test_existing_positive_realtime_volume_ratio_is_preserved(self):
        market = pd.DataFrame([{
            "ts_code": "600201.SH",
            "vol": 120_000,
            "volume_ratio": 2.8,
        }])
        history = pd.DataFrame([{
            "ts_code": "600201.SH",
            "trade_date": "20260728",
            "vol": 100_000,
        }])

        result = _fill_missing_realtime_volume_ratio(
            market,
            history,
            "20260729",
            datetime(2026, 7, 29, 15, 10),
        )

        self.assertEqual(result.iloc[0]["volume_ratio"], 2.8)

    def test_missing_realtime_volume_ratio_stays_missing_without_history(self):
        market = pd.DataFrame([{
            "ts_code": "600201.SH",
            "vol": 120_000,
            "volume_ratio": None,
        }])

        result = _fill_missing_realtime_volume_ratio(
            market,
            pd.DataFrame(),
            "20260729",
            datetime(2026, 7, 29, 15, 10),
        )

        self.assertTrue(pd.isna(result.iloc[0]["volume_ratio"]))

    def test_request_minute_loader_reuses_identical_window(self):
        import realtime_info_service

        calls = []
        expected = build_60min_bars(
            "600201.SH", water_macd_kdj_cross_closes()
        )

        def counting_loader(ts_code, start, end, freq, trade_date):
            calls.append((ts_code, start, end, freq, trade_date))
            return MinuteLoadResult(expected.copy(), "tushare", [])

        request_loader = realtime_info_service._request_minute_loader(
            counting_loader
        )
        first = request_loader(
            "600201.SH",
            "2026-05-20 09:30:00",
            "2026-07-29 14:30:00",
            "60min",
            "20260729",
        )
        second = request_loader(
            "600201.SH",
            "2026-05-20 09:30:00",
            "2026-07-29 14:30:00",
            "60min",
            "20260729",
        )

        self.assertEqual(len(calls), 1)
        pd.testing.assert_frame_equal(first.bars, second.bars)
        self.assertIsNot(first.bars, second.bars)

    def test_persistent_minute_result_uses_fresh_database_bars(self):
        import realtime_info_service

        cached = pd.DataFrame(
            [
                {
                    "ts_code": "600201.SH",
                    "trade_time": "2026-07-30 09:30:00",
                    "close": 10,
                },
                {
                    "ts_code": "600201.SH",
                    "trade_time": "2026-07-30 14:39:00",
                    "close": 10.1,
                },
            ]
        )
        with (
            patch(
                "realtime_info_service.load_minute_cache",
                return_value=cached,
                create=True,
            ),
            patch(
                "realtime_info_service.minute_cache_is_fresh",
                return_value=True,
                create=True,
            ),
            patch(
                "realtime_info_service._minute_result_with_1459_fallback"
            ) as external,
        ):
            result = realtime_info_service._persistent_minute_result(
                "600201.SH",
                "2026-07-30 09:30:00",
                "2026-07-30 14:40:00",
                "1min",
                "20260730",
                datetime(2026, 7, 30, 14, 40),
            )

        self.assertEqual(result.source, "database")
        self.assertEqual(result.bars.iloc[-1]["close"], 10.1)
        external.assert_not_called()

    def test_force_refresh_bypasses_fresh_database_minutes(self):
        import realtime_info_service

        cached = pd.DataFrame([{
            "ts_code": "600201.SH",
            "trade_time": "2026-07-30 14:49:00",
            "close": 10,
        }])
        refreshed = pd.DataFrame([{
            "ts_code": "600201.SH",
            "trade_time": "2026-07-30 14:50:00",
            "close": 10.1,
        }])
        with (
            patch(
                "realtime_info_service.load_minute_cache",
                return_value=cached,
            ),
            patch(
                "realtime_info_service.minute_cache_is_fresh",
                return_value=True,
            ),
            patch(
                "realtime_info_service._minute_result_with_1459_fallback",
                return_value=MinuteLoadResult(
                    refreshed,
                    "eastmoney_fallback",
                    [],
                ),
            ) as provider,
        ):
            result = realtime_info_service._persistent_minute_result(
                "600201.SH",
                "2026-07-30 14:25:00",
                "2026-07-30 14:50:00",
                "1min",
                "20260730",
                datetime(2026, 7, 30, 14, 51),
                force_refresh=True,
            )

        provider.assert_called_once()
        self.assertEqual(result.source, "eastmoney_fallback")
        self.assertEqual(result.bars.iloc[-1]["close"], 10.1)

    def test_signal_minutes_use_at_most_four_workers(self):
        active = 0
        max_active = 0
        lock = threading.Lock()
        codes = [f"60020{index}.SH" for index in range(8)]
        market = pd.DataFrame([
            {
                "ts_code": code,
                "industry": "机器人",
                "turnover_rate": 5,
                "volume_ratio": 3,
                "amount": 800_000 - index * 10_000,
                "pct_chg": 4,
            }
            for index, code in enumerate(codes)
        ])
        sectors = pd.DataFrame([{"industry_name": "机器人"}])

        def minute_loader(ts_code, start, end, freq, trade_date):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            try:
                time.sleep(0.02)
                bars = build_60min_bars(
                    ts_code, water_macd_kdj_cross_closes()
                )
                return MinuteLoadResult(bars, "tushare", [])
            finally:
                with lock:
                    active -= 1

        result = _load_realtime_intraday_signal_bars(
            market,
            sectors,
            "20260729",
            datetime(2026, 7, 29, 14, 50),
            minute_loader=minute_loader,
        )

        self.assertGreater(max_active, 1)
        self.assertLessEqual(max_active, 4)
        self.assertEqual(list(result), codes[:6])

    def test_signal_minutes_skip_chinext_and_star_market_candidates(self):
        requested_codes = []
        market = pd.DataFrame([
            {
                "ts_code": "600201.SH",
                "industry": "机器人",
                "turnover_rate": 5,
                "volume_ratio": 3,
                "amount": 800_000,
                "pct_chg": 4,
            },
            {
                "ts_code": "300201.SZ",
                "industry": "机器人",
                "turnover_rate": 5,
                "volume_ratio": 4,
                "amount": 900_000,
                "pct_chg": 8,
            },
            {
                "ts_code": "688201.SH",
                "industry": "机器人",
                "turnover_rate": 5,
                "volume_ratio": 4,
                "amount": 950_000,
                "pct_chg": 9,
            },
        ])
        sectors = pd.DataFrame([{"industry_name": "机器人"}])

        def minute_loader(ts_code, start, end, freq, trade_date):
            requested_codes.append(ts_code)
            return MinuteLoadResult(
                build_60min_bars(ts_code, water_macd_kdj_cross_closes()),
                "fixture",
                [],
            )

        result = _load_realtime_intraday_signal_bars(
            market,
            sectors,
            "20260729",
            datetime(2026, 7, 29, 14, 50),
            minute_loader=minute_loader,
        )

        self.assertEqual(requested_codes, ["600201.SH"])
        self.assertEqual(list(result), ["600201.SH"])

    def test_signal_minutes_accept_relaxed_positive_mainboard_candidate(self):
        requested_codes = []
        market = pd.DataFrame([{
            "ts_code": "600202.SH",
            "industry": "机器人",
            "turnover_rate": 1.0,
            "volume_ratio": 1.0,
            "amount": 300_000,
            "pct_chg": 0.2,
        }])
        sectors = pd.DataFrame([{"industry_name": "机器人"}])

        def minute_loader(ts_code, start, end, freq, trade_date):
            requested_codes.append(ts_code)
            return MinuteLoadResult(
                build_60min_bars(ts_code, water_macd_kdj_cross_closes()),
                "fixture",
                [],
            )

        result = _load_realtime_intraday_signal_bars(
            market,
            sectors,
            "20260729",
            datetime(2026, 7, 29, 14, 50),
            minute_loader=minute_loader,
        )

        self.assertEqual(requested_codes, ["600202.SH"])
        self.assertEqual(list(result), ["600202.SH"])

    def test_snapshot_supports_relaxed_realtime_filter_candidates(self):
        market = pd.DataFrame([{
            "ts_code": "600202.SH",
            "pct_chg": 0.2,
            "turnover_rate": 1.0,
            "volume_ratio": 1.0,
            "amount": 100_000,
        }])

        self.assertTrue(_snapshot_supports_realtime_filters(market))

    @patch("realtime_info_service._load_tail_minute_bars_for_pick")
    def test_intraday_confluence_uses_today_market_when_sector_history_is_empty(self, tail_loader):
        import realtime_info_service

        market = pd.DataFrame([{
            "trade_date": "20260731",
            "ts_code": "600301.SH",
            "name": "今日共振",
            "industry": "机器人",
            "close": 12.6,
            "high": 12.8,
            "low": 12.1,
            "pct_chg": 3.2,
            "turnover_rate": 4.8,
            "volume_ratio": 1.5,
            "amount": 180_000_000,
        }])
        tail_loader.return_value = MinuteLoadResult(
            pd.DataFrame(), "not_available", []
        )

        def minute_loader(ts_code, start, end, freq, trade_date):
            self.assertEqual(freq, "60min")
            return MinuteLoadResult(
                build_60min_bars(ts_code, water_macd_kdj_cross_closes()),
                "fixture",
                [],
            )

        result = realtime_info_service._build_realtime_intraday_section(
            market,
            pd.DataFrame(),
            "20260731",
            datetime(2026, 7, 31, 14, 20),
            limit=10,
            minute_loader=minute_loader,
            force_refresh=True,
        )

        self.assertEqual(
            [row["ts_code"] for row in result["stocks"]],
            ["600301.SH"],
        )
        row = result["stocks"][0]
        self.assertTrue(row["macd_golden_cross_60m"])
        self.assertTrue(row["kdj_golden_cross_60m"])
        self.assertIn("多头", row["intraday_signal_reason"])

    def test_tail_minutes_are_limited_before_fetch(self):
        import realtime_info_service

        codes = [f"600{index:03d}.SH" for index in range(20)]
        market = pd.DataFrame([
            {
                "ts_code": code,
                "name": f"候选{index}",
                "industry": f"板块{index // 5}",
                "close": 10,
                "high": 10.2,
                "pct_chg": 4,
                "turnover_rate": 5,
                "volume_ratio": 3,
                "amount": 900_000 - index,
            }
            for index, code in enumerate(codes)
        ])
        sectors = pd.DataFrame([
            {
                "industry_name": f"板块{sector_index}",
                "intraday_signal_stocks": [
                    {
                        "ts_code": codes[sector_index * 5 + offset],
                        "intraday_signal_score": 100 - sector_index * 5 - offset,
                        "next_day_bias": "高开偏强",
                    }
                    for offset in range(5)
                ],
            }
            for sector_index in range(4)
        ])
        signal_bars = {
            code: {
                "60m": pd.DataFrame(),
                "60m_source": "tushare",
                "warnings": [],
            }
            for code in codes
        }

        with (
            patch(
                "realtime_info_service.rank_sector_potential",
                return_value=sectors.drop(columns=["intraday_signal_stocks"]),
            ),
            patch(
                "realtime_info_service._load_realtime_intraday_signal_bars",
                return_value=signal_bars,
            ),
            patch(
                "realtime_info_service._attach_intraday_signal_stocks",
                return_value=sectors,
            ),
            patch(
                "realtime_info_service._load_tail_minute_bars_for_pick",
                return_value=MinuteLoadResult(pd.DataFrame(), "unavailable", []),
            ) as tail_loader,
        ):
            result = realtime_info_service._build_realtime_intraday_section(
                market,
                pd.DataFrame(),
                "20260729",
                datetime(2026, 7, 29, 14, 50),
                limit=10,
            )

        self.assertEqual(tail_loader.call_count, 15)
        self.assertEqual(len(result["stocks"]), 10)

    def test_top_level_cache_precedes_market_sync(self):
        live_section = {
            "trade_date": "20260730",
            "stocks": [{"ts_code": "600298.SH", "name": "安琪酵母"}],
        }
        with (
            patch(
                "realtime_info_service.get_trade_dates",
                return_value=["20260730"],
            ) as trade_dates,
            patch(
                "realtime_info_service.sync_cached_market_data",
                return_value={"data_trade_date": "20260730"},
            ) as sync_market,
            patch(
                "realtime_info_service._load_realtime_market_inputs",
                return_value=(
                    pd.DataFrame(),
                    pd.DataFrame(),
                    "20260730",
                    "current_snapshot",
                    True,
                    [],
                ),
            ),
            patch(
                "realtime_info_service._build_realtime_intraday_section",
                return_value=live_section,
            ) as intraday_builder,
            patch(
                "realtime_info_service.build_realtime_tail_premium_monitor",
                return_value={"trade_date": "20260730", "stocks": []},
            ) as overnight_builder,
        ):
            first = build_realtime_info(
                now=datetime(2026, 7, 30, 14, 30, 2), limit=10
            )
            second = build_realtime_info(
                now=datetime(2026, 7, 30, 14, 30, 20), limit=10
            )

        self.assertFalse(first["result_cache_hit"])
        self.assertTrue(second["result_cache_hit"])
        self.assertEqual(trade_dates.call_count, 1)
        self.assertEqual(sync_market.call_count, 1)
        self.assertEqual(intraday_builder.call_count, 1)
        self.assertEqual(overnight_builder.call_count, 1)
        self.assertEqual(
            set(first["performance"]),
            {
                "market_sync_ms",
                "intraday_60m_ms",
                "tail_1m_ms",
                "overnight_ms",
                "minute_request_count",
                "minute_cache_hit_count",
                "provider_failure_count",
                "used_stale_fallback",
            },
        )
        self.assertFalse(first["performance"]["used_stale_fallback"])

    def test_database_result_cache_precedes_full_realtime_build(self):
        import realtime_info_service

        cached_payload = {
            "trade_date": "20260730",
            "data_trade_date": "20260730",
            "data_as_of": "2026-07-30 14:39:00",
            "data_status": "live",
            "intraday": {
                "stocks": [{"ts_code": "600298.SH"}],
            },
            "overnight": {"stocks": []},
        }
        with (
            patch(
                "realtime_info_service.load_result_cache",
                return_value={
                    "payload": cached_payload,
                    "updated_at": "2026-07-30 14:40:50",
                },
                create=True,
            ),
            patch(
                "realtime_info_service._build_realtime_info_uncached"
            ) as build,
        ):
            result = build_realtime_info(
                now=datetime(2026, 7, 30, 14, 41),
                limit=10,
                force_refresh=False,
            )

        self.assertEqual(result["cache_source"], "database")
        self.assertEqual(
            result["cache_updated_at"],
            "2026-07-30 14:40:50",
        )
        self.assertTrue(result["result_cache_hit"])
        self.assertEqual(
            result["intraday"]["stocks"][0]["ts_code"],
            "600298.SH",
        )
        build.assert_not_called()

    def test_database_result_expires_during_trading(self):
        cached_payload = {
            "trade_date": "20260730",
            "data_trade_date": "20260730",
            "data_as_of": "2026-07-30 14:40:20",
            "data_status": "live",
            "intraday": {"stocks": [{"ts_code": "cached"}]},
            "overnight": {"stocks": []},
        }
        fresh_payload = {
            "trade_date": "20260730",
            "data_trade_date": "20260730",
            "data_as_of": "2026-07-30 14:40:59",
            "intraday": {"stocks": [{"ts_code": "fresh"}]},
            "overnight": {"stocks": []},
        }
        with (
            patch(
                "realtime_info_service.load_result_cache",
                return_value={
                    "payload": cached_payload,
                    "updated_at": "2026-07-30 14:40:29",
                },
            ),
            patch(
                "realtime_info_service._build_realtime_info_uncached",
                return_value=fresh_payload,
            ) as build,
        ):
            result = build_realtime_info(
                now=datetime(2026, 7, 30, 14, 41, 0),
                limit=10,
            )

        build.assert_called_once()
        self.assertEqual(
            result["intraday"]["stocks"][0]["ts_code"],
            "fresh",
        )

    def test_database_result_rejects_prior_trade_date_as_live(self):
        cached_payload = {
            "trade_date": "20260729",
            "data_trade_date": "20260729",
            "data_as_of": "2026-07-29 15:00:00",
            "data_status": "live",
            "intraday": {"stocks": [{"ts_code": "cached"}]},
            "overnight": {"stocks": []},
        }
        with (
            patch(
                "realtime_info_service.load_result_cache",
                return_value={
                    "payload": cached_payload,
                    "updated_at": "2026-07-30 14:40:55",
                },
            ),
            patch(
                "realtime_info_service._build_realtime_info_uncached",
                return_value={
                    "trade_date": "20260730",
                    "data_trade_date": "20260730",
                    "intraday": {"stocks": [{"ts_code": "fresh"}]},
                    "overnight": {"stocks": []},
                },
            ) as build,
        ):
            result = build_realtime_info(
                now=datetime(2026, 7, 30, 14, 41, 0),
                limit=10,
            )

        build.assert_called_once()
        self.assertEqual(result["data_trade_date"], "20260730")

    def test_final_post_close_result_remains_reusable(self):
        payload = {
            "trade_date": "20260730",
            "data_trade_date": "20260730",
            "data_as_of": "2026-07-30 15:00:00",
            "data_status": "live",
            "intraday": {"stocks": [{"ts_code": "final"}]},
            "overnight": {"stocks": []},
        }
        with (
            patch(
                "realtime_info_service.load_result_cache",
                return_value={
                    "payload": payload,
                    "updated_at": "2026-07-30 15:00:10",
                },
            ),
            patch(
                "realtime_info_service._build_realtime_info_uncached",
            ) as build,
        ):
            result = build_realtime_info(
                now=datetime(2026, 7, 30, 16, 30),
                limit=10,
            )

        build.assert_not_called()
        self.assertEqual(
            result["intraday"]["stocks"][0]["ts_code"],
            "final",
        )

    def test_force_refresh_bypasses_database_result_fast_path(self):
        with (
            patch(
                "realtime_info_service.load_result_cache",
                return_value={
                    "payload": {
                        "intraday": {
                            "stocks": [{"ts_code": "cached"}],
                        },
                        "overnight": {"stocks": []},
                    },
                    "updated_at": "2026-07-30 14:40:00",
                },
                create=True,
            ) as database_result,
            patch(
                "realtime_info_service._build_realtime_info_uncached",
                return_value={
                    "trade_date": "20260730",
                    "intraday": {
                        "stocks": [{"ts_code": "fresh"}],
                    },
                    "overnight": {"stocks": []},
                },
            ) as build,
        ):
            result = build_realtime_info(
                now=datetime(2026, 7, 30, 14, 41),
                limit=10,
                force_refresh=True,
            )

        self.assertEqual(
            result["intraday"]["stocks"][0]["ts_code"],
            "fresh",
        )
        database_result.assert_not_called()
        build.assert_called_once_with(
            now=datetime(2026, 7, 30, 14, 41),
            limit=10,
            force_refresh=True,
        )

    def test_empty_live_refresh_returns_last_success_as_stale(self):
        live_section = {
            "trade_date": "20260730",
            "stocks": [{"ts_code": "600298.SH", "name": "安琪酵母"}],
        }
        common_inputs = (
            pd.DataFrame(),
            pd.DataFrame(),
            "20260730",
            "current_snapshot",
            True,
            [],
        )
        with (
            patch("realtime_info_service.get_trade_dates", return_value=["20260730"]),
            patch(
                "realtime_info_service.sync_cached_market_data",
                return_value={"data_trade_date": "20260730"},
            ),
            patch(
                "realtime_info_service._load_realtime_market_inputs",
                return_value=common_inputs,
            ),
            patch(
                "realtime_info_service._build_realtime_intraday_section",
                return_value=live_section,
            ),
            patch(
                "realtime_info_service.build_realtime_tail_premium_monitor",
                return_value={"trade_date": "20260730", "stocks": []},
            ),
        ):
            build_realtime_info(
                now=datetime(2026, 7, 30, 14, 30, 0), limit=10
            )

        with (
            patch("realtime_info_service.get_trade_dates", side_effect=RuntimeError("offline")),
            patch("realtime_info_service.sync_cached_market_data", side_effect=RuntimeError("offline")),
            patch(
                "realtime_info_service._load_realtime_market_inputs",
                return_value=common_inputs,
            ),
            patch(
                "realtime_info_service._build_realtime_intraday_section",
                return_value={"trade_date": "20260730", "stocks": []},
            ),
            patch(
                "realtime_info_service.build_realtime_tail_premium_monitor",
                return_value={"trade_date": "20260730", "stocks": []},
            ),
        ):
            result = build_realtime_info(
                now=datetime(2026, 7, 30, 14, 31, 35), limit=10
            )

        self.assertEqual(result["data_status"], "stale")
        self.assertEqual(result["data_status_label"], "备用缓存")
        self.assertEqual(result["data_updated_at"], "2026-07-30 14:30:00")
        self.assertEqual(result["stale_age_seconds"], 95)
        self.assertEqual(result["intraday"]["stocks"][0]["ts_code"], "600298.SH")

    def test_empty_live_refresh_without_history_is_unavailable(self):
        with (
            patch("realtime_info_service.get_trade_dates", side_effect=RuntimeError("offline")),
            patch("realtime_info_service.sync_cached_market_data", side_effect=RuntimeError("offline")),
            patch(
                "realtime_info_service._load_realtime_market_inputs",
                return_value=(
                    pd.DataFrame(),
                    pd.DataFrame(),
                    "20260730",
                    "current_snapshot",
                    False,
                    ["所有市场快照不可用"],
                ),
            ),
            patch(
                "realtime_info_service._build_realtime_intraday_section",
                return_value={"trade_date": "20260730", "stocks": []},
            ),
            patch(
                "realtime_info_service.build_realtime_tail_premium_monitor",
                return_value={"trade_date": "20260730", "stocks": []},
            ),
        ):
            result = build_realtime_info(
                now=datetime(2026, 7, 30, 14, 31, 35), limit=10
            )

        self.assertEqual(result["data_status"], "unavailable")
        self.assertEqual(result["data_status_label"], "数据不可用")
        self.assertEqual(result["intraday"]["stocks"], [])
        self.assertEqual(result["overnight"]["stocks"], [])

    @patch("realtime_info_service.load_eastmoney_market_snapshot")
    @patch("realtime_info_service.load_recent_daily")
    @patch("realtime_info_service.load_market_snapshot")
    def test_market_inputs_use_eastmoney_when_tushare_today_is_empty(
        self, load_snapshot, load_history, eastmoney
    ):
        load_snapshot.return_value = pd.DataFrame()
        eastmoney.return_value = (
            pd.DataFrame([{
                "ts_code": "600298.SH", "trade_date": "20260730",
                "close": 39.12, "high": 39.80, "industry": "食品",
                "pct_chg": 4.2, "turnover_rate": 5.1,
                "volume_ratio": 2.4, "amount": 620_000_000,
            }]),
            None,
        )
        load_history.return_value = pd.DataFrame()

        result = _load_realtime_market_inputs(
            "20260730", {"data_trade_date": "20260729"}
        )

        self.assertEqual(result[2], "20260730")
        self.assertEqual(result[3], "eastmoney_snapshot_fallback")
        self.assertTrue(result[4])
        self.assertEqual(result[0].iloc[0]["close"], 39.12)
        self.assertEqual(result[5], [])

    @patch("realtime_info_service.load_eastmoney_market_snapshot")
    @patch("realtime_info_service.load_recent_daily")
    @patch("realtime_info_service.load_market_snapshot")
    def test_market_inputs_reject_external_snapshot_without_filter_fields(
        self, load_snapshot, load_history, eastmoney
    ):
        previous = pd.DataFrame([{
            "ts_code": "600298.SH",
            "trade_date": "20260729",
            "close": 38.62,
            "industry": "食品",
            "pct_chg": 4.2,
            "turnover_rate": 5.1,
            "volume_ratio": 2.4,
            "amount": 620_000_000,
        }])
        load_snapshot.side_effect = [pd.DataFrame(), previous]
        eastmoney.return_value = (
            pd.DataFrame([{
                "ts_code": "600298.SH",
                "trade_date": "20260730",
                "close": 39.12,
                "turnover_rate": None,
                "volume_ratio": None,
            }]),
            None,
        )
        load_history.return_value = pd.DataFrame()

        result = _load_realtime_market_inputs(
            "20260730", {"data_trade_date": "20260729"}
        )

        self.assertEqual(result[2], "20260729")
        self.assertEqual(result[3], "previous_snapshot")
        self.assertFalse(result[4])
        self.assertIn("筛选字段不可用", "；".join(result[5]))

    @patch("realtime_info_service.load_eastmoney_market_snapshot")
    @patch("realtime_info_service.get_trade_dates", return_value=["20260730", "20260729"])
    @patch("realtime_info_service.load_recent_daily")
    @patch("realtime_info_service.load_market_snapshot")
    def test_market_inputs_mark_previous_snapshot_stale(
        self, load_snapshot, load_history, _dates, eastmoney
    ):
        previous = pd.DataFrame([{
            "ts_code": "600298.SH", "trade_date": "20260729", "close": 38.62
        }])
        load_snapshot.side_effect = [pd.DataFrame(), previous]
        eastmoney.return_value = (pd.DataFrame(), "东方财富快照超时")
        load_history.return_value = pd.DataFrame()

        result = _load_realtime_market_inputs(
            "20260730", {"data_trade_date": "20260729"}
        )

        self.assertEqual(result[2], "20260729")
        self.assertEqual(result[3], "previous_snapshot")
        self.assertFalse(result[4])
        self.assertIn("东方财富快照超时", result[5])

    @patch("realtime_info_service.load_minutes_with_fallback")
    def test_realtime_minute_loader_preserves_external_source(self, loader):
        bars = pd.DataFrame([{
            "ts_code": "600298.SH",
            "trade_time": "2026-07-30 14:29:00",
            "close": 39.12,
        }])
        loader.return_value = MinuteLoadResult(
            bars, "eastmoney_fallback", ["Tushare分钟为空"]
        )

        result = _minute_result_with_1459_fallback(
            "600298.SH",
            "2026-07-30 14:25:00",
            "2026-07-30 14:30:00",
            "1min",
            "20260730",
        )

        self.assertEqual(result.source, "eastmoney_fallback")
        self.assertEqual(result.warnings, ["Tushare分钟为空"])
        self.assertEqual(result.bars.iloc[0]["close"], 39.12)

    @patch(
        "realtime_info_service.build_realtime_tail_premium_monitor",
        return_value={"trade_date": "20260730", "stocks": []},
    )
    @patch(
        "realtime_info_service._build_realtime_intraday_section",
        return_value={"trade_date": "20260730", "stocks": []},
    )
    @patch("realtime_info_service.load_recent_daily", return_value=pd.DataFrame())
    @patch("realtime_info_service.load_market_snapshot", return_value=pd.DataFrame())
    @patch("realtime_info_service.load_eastmoney_market_snapshot")
    @patch(
        "realtime_info_service.sync_cached_market_data",
        side_effect=RuntimeError("Tushare同步失败"),
    )
    @patch(
        "realtime_info_service.get_trade_dates",
        side_effect=RuntimeError("Tushare交易日失败"),
    )
    def test_realtime_info_enters_fallback_when_tushare_entry_calls_raise(
        self,
        _dates,
        _sync,
        eastmoney,
        _snapshot,
        _history,
        _intraday,
        _overnight,
    ):
        eastmoney.return_value = (
            pd.DataFrame([{
                "ts_code": "600298.SH",
                "trade_date": "20260730",
                "close": 39.12,
                "high": 39.8,
                "industry": "食品",
                "pct_chg": 4.2,
                "turnover_rate": 5.1,
                "volume_ratio": 2.4,
                "amount": 620_000_000,
            }]),
            None,
        )

        result = build_realtime_info(
            now=datetime(2026, 7, 30, 10, 15)
        )

        self.assertEqual(result["trade_date"], "20260730")
        self.assertEqual(
            result["data_source"], "eastmoney_snapshot_fallback"
        )
        self.assertTrue(result["data_current"])
        self.assertIn(
            "Tushare交易日失败",
            "；".join(result["fallback_warnings"]),
        )
        self.assertIn(
            "Tushare同步失败",
            "；".join(result["fallback_warnings"]),
        )

    @patch("realtime_info_service.build_realtime_tail_premium_monitor")
    @patch("realtime_info_service._build_realtime_intraday_section")
    @patch("realtime_info_service.load_recent_daily")
    @patch("realtime_info_service.load_market_snapshot")
    @patch("realtime_info_service.sync_cached_market_data")
    @patch("realtime_info_service.get_trade_dates", return_value=["20260729"])
    def test_realtime_info_syncs_current_market_and_enriches_both_sections(
        self,
        _get_trade_dates,
        sync_cached_market_data,
        load_market_snapshot,
        load_recent_daily,
        build_realtime_intraday_section,
        build_realtime_tail_premium_monitor,
    ):
        sync_cached_market_data.return_value = {"data_trade_date": "20260729", "cache_updated": True}
        load_market_snapshot.return_value = pd.DataFrame([
            {"ts_code": "600101.SH", "close": 12.34, "high": 12.8},
            {"ts_code": "600102.SH", "close": 8.88, "high": 9.2},
        ])
        load_recent_daily.return_value = pd.DataFrame()
        build_realtime_intraday_section.return_value = {
            "trade_date": "20260729",
            "stocks": [{"ts_code": "600101.SH", "name": "实时共振"}],
        }
        build_realtime_tail_premium_monitor.return_value = {
            "trade_date": "20260729",
            "stocks": [{"ts_code": "600102.SH", "name": "隔夜候选"}],
        }

        result = build_realtime_info(now=datetime(2026, 7, 29, 14, 36))

        self.assertEqual(result["trade_date"], "20260729")
        self.assertTrue(result["data_current"])
        self.assertEqual(result["intraday"]["stocks"][0]["current_price"], 12.34)
        self.assertEqual(result["intraday"]["stocks"][0]["day_high"], 12.8)
        self.assertEqual(result["overnight"]["stocks"][0]["current_price"], 8.88)
        self.assertEqual(result["overnight"]["stocks"][0]["day_high"], 9.2)
        sync_cached_market_data.assert_called_once_with(force_current=True)
        load_recent_daily.assert_called_once_with("20260729", 100)
        build_realtime_intraday_section.assert_called_once()
        build_realtime_tail_premium_monitor.assert_called_once()
        kwargs = build_realtime_tail_premium_monitor.call_args.kwargs
        self.assertEqual(kwargs["trade_date_override"], "20260729")
        self.assertEqual(
            kwargs["source_metadata"]["data_source"],
            "current_snapshot",
        )
        self.assertTrue(callable(kwargs["minute_loader"]))
        self.assertEqual(
            kwargs["market_override"].iloc[0]["close"], 12.34
        )

    @patch("realtime_info_service.build_realtime_tail_premium_monitor")
    @patch("realtime_info_service._build_realtime_intraday_section")
    @patch("realtime_info_service.load_recent_daily")
    @patch("realtime_info_service.load_market_snapshot")
    @patch("realtime_info_service.sync_cached_market_data")
    @patch("realtime_info_service.get_trade_dates", return_value=["20260729"])
    def test_realtime_info_uses_row_price_fallback_and_masks_1430_fields_before_time(
        self,
        _get_trade_dates,
        sync_cached_market_data,
        load_market_snapshot,
        load_recent_daily,
        build_realtime_intraday_section,
        build_realtime_tail_premium_monitor,
    ):
        sync_cached_market_data.return_value = {"data_trade_date": "20260729", "cache_updated": True}
        load_market_snapshot.return_value = pd.DataFrame([
            {"ts_code": "600101.SH", "close": float("nan"), "high": float("nan")},
        ])
        load_recent_daily.return_value = pd.DataFrame()
        build_realtime_intraday_section.return_value = {
            "trade_date": "20260729",
            "stocks": [{
                "ts_code": "600101.SH",
                "close": 11.2,
                "high": 11.8,
                "tail_return_after_1430": 0.6,
                "tail_strength_score": 82,
            }],
        }
        build_realtime_tail_premium_monitor.return_value = {
            "trade_date": "20260729",
            "stocks": [{
                "ts_code": "600102.SH",
                "close": 8.6,
                "high": 8.9,
                "tail_return_after_1430": 0.4,
                "tail_strength_score": 78,
                "tail_auction_return": 0.15,
            }],
        }

        result = build_realtime_info(now=datetime(2026, 7, 29, 14, 10))

        intraday = result["intraday"]["stocks"][0]
        overnight = result["overnight"]["stocks"][0]
        self.assertEqual(intraday["current_price"], 11.2)
        self.assertEqual(intraday["day_high"], 11.8)
        self.assertFalse(intraday["tail_after_1430_available"])
        self.assertIsNone(intraday["tail_return_after_1430"])
        self.assertIsNone(intraday["tail_strength_score"])
        self.assertEqual(overnight["current_price"], 8.6)
        self.assertEqual(overnight["day_high"], 8.9)
        self.assertFalse(overnight["tail_after_1430_available"])
        self.assertIsNone(overnight["tail_return_after_1430"])
        self.assertIsNone(overnight["tail_strength_score"])
        self.assertTrue(overnight["tail_auction_available"])
        self.assertEqual(overnight["tail_auction_return"], 0.15)

    @patch("realtime_info_service.build_realtime_tail_premium_monitor")
    @patch("realtime_info_service._cached_minute_bars", create=True)
    @patch("realtime_info_service.rank_sector_potential", create=True)
    @patch("realtime_info_service.load_recent_daily", create=True)
    @patch("realtime_info_service.load_market_snapshot")
    @patch("realtime_info_service.sync_cached_market_data")
    @patch("realtime_info_service.get_trade_dates", return_value=["20260729"])
    def test_realtime_info_rebuilds_intraday_confluence_from_today_market(
        self,
        _get_trade_dates,
        sync_cached_market_data,
        load_market_snapshot,
        load_recent_daily,
        rank_sector_potential,
        cached_minute_bars,
        build_realtime_tail_premium_monitor,
    ):
        sync_cached_market_data.return_value = {"data_trade_date": "20260729", "cache_updated": True}
        market = pd.DataFrame([
            {
                "trade_date": "20260729",
                "ts_code": "600201.SH",
                "name": "今日共振",
                "industry": "机器人",
                "close": 18.6,
                "high": 19.1,
                "pct_chg": 4.2,
                "turnover_rate": 5.2,
                "volume_ratio": 2.8,
                "amount": 620_000_000,
            }
        ])
        load_market_snapshot.return_value = market
        load_recent_daily.return_value = pd.DataFrame([{"ts_code": "600201.SH", "trade_date": "20260728", "close": 17.85}])
        rank_sector_potential.return_value = pd.DataFrame([{"industry_name": "机器人", "potential_score": 88.0}])
        build_realtime_tail_premium_monitor.return_value = {"trade_date": "20260729", "stocks": []}

        def fake_bars(ts_code, start_datetime, end_datetime, freq="60min"):
            if freq == "60min":
                return build_60min_bars(ts_code, water_macd_kdj_cross_closes())
            return build_tail_1min_bars(
                ts_code,
                [18.0, 18.0, 18.0, 18.0, 18.0, 18.0, 18.08, 18.12, 18.18],
                [1000, 1000, 1000, 1000, 1000, 1000, 2500, 3000, 3600],
            )

        cached_minute_bars.side_effect = fake_bars

        result = build_realtime_info(now=datetime(2026, 7, 29, 14, 50))

        rows = result["intraday"]["stocks"]
        self.assertEqual(result["data_as_of"], "2026-07-27 14:33:00")
        self.assertEqual(
            result["intraday"]["data_as_of"],
            "2026-07-27 14:33:00",
        )
        self.assertEqual([row["ts_code"] for row in rows], ["600201.SH"])
        self.assertEqual(rows[0]["industry"], "机器人")
        self.assertTrue(rows[0]["macd_golden_cross_60m"])
        self.assertTrue(rows[0]["kdj_golden_cross_60m"])
        self.assertEqual(rows[0]["current_price"], 18.6)
        self.assertEqual(rows[0]["day_high"], 19.1)
        called_windows = [(*call.args, call.kwargs.get("freq")) for call in cached_minute_bars.call_args_list]
        self.assertIn(("600201.SH", "2026-05-20 09:30:00", "2026-07-29 14:49:00", "60min"), called_windows)
        self.assertIn(("600201.SH", "2026-07-29 14:25:00", "2026-07-29 14:49:00", "1min"), called_windows)

    @patch("realtime_info_service.build_realtime_tail_premium_monitor")
    @patch("realtime_info_service._cached_minute_bars")
    @patch("realtime_info_service.rank_sector_potential")
    @patch("realtime_info_service.load_recent_daily")
    @patch("realtime_info_service.load_market_snapshot")
    @patch("realtime_info_service.sync_cached_market_data")
    @patch("realtime_info_service.get_trade_dates", return_value=["20260729"])
    def test_realtime_intraday_uses_estimated_volume_ratio_when_snapshot_value_is_missing(
        self,
        _get_trade_dates,
        sync_cached_market_data,
        load_market_snapshot,
        load_recent_daily,
        rank_sector_potential,
        cached_minute_bars,
        build_realtime_tail_premium_monitor,
    ):
        sync_cached_market_data.return_value = {
            "data_trade_date": "20260729",
            "cache_updated": True,
        }
        load_market_snapshot.return_value = pd.DataFrame([{
            "trade_date": "20260729",
            "ts_code": "600201.SH",
            "name": "量比回退",
            "industry": "机器人",
            "close": 18.6,
            "high": 19.1,
            "pct_chg": 4.2,
            "turnover_rate": 5.2,
            "volume_ratio": None,
            "vol": 225_000,
            "amount": 620_000_000,
        }])
        load_recent_daily.return_value = pd.DataFrame([
            {"ts_code": "600201.SH", "trade_date": date, "close": 17.85, "vol": 100_000}
            for date in ["20260722", "20260723", "20260724", "20260725", "20260728"]
        ])
        rank_sector_potential.return_value = pd.DataFrame([{
            "industry_name": "机器人",
            "potential_score": 88.0,
        }])
        cached_minute_bars.return_value = build_60min_bars(
            "600201.SH",
            water_macd_kdj_cross_closes(),
        )
        build_realtime_tail_premium_monitor.return_value = {
            "trade_date": "20260729",
            "stocks": [],
        }

        result = build_realtime_info(now=datetime(2026, 7, 29, 14, 30))

        self.assertEqual(
            [row["ts_code"] for row in result["intraday"]["stocks"]],
            ["600201.SH"],
        )
        self.assertAlmostEqual(
            result["intraday"]["stocks"][0]["volume_ratio"],
            2.571429,
        )

    @patch("realtime_info_service.build_realtime_tail_premium_monitor")
    @patch("realtime_info_service._cached_minute_bars", create=True)
    @patch("realtime_info_service.rank_sector_potential", create=True)
    @patch("realtime_info_service.load_recent_daily", create=True)
    @patch("realtime_info_service.load_market_snapshot")
    @patch("realtime_info_service.sync_cached_market_data")
    @patch("realtime_info_service.get_trade_dates", return_value=["20260729", "20260728"])
    def test_realtime_info_uses_previous_snapshot_when_today_daily_is_unavailable(
        self,
        _get_trade_dates,
        sync_cached_market_data,
        load_market_snapshot,
        load_recent_daily,
        rank_sector_potential,
        cached_minute_bars,
        build_realtime_tail_premium_monitor,
    ):
        sync_cached_market_data.return_value = {
            "data_trade_date": "20260728",
            "cache_updated": True,
            "cache_warnings": ["daily 20260729: daily 返回为空"],
        }
        previous_market = pd.DataFrame([
            {
                "trade_date": "20260728",
                "ts_code": "600201.SH",
                "name": "今日共振",
                "industry": "机器人",
                "close": 17.85,
                "high": 18.0,
                "low": 17.5,
                "pct_chg": 1.2,
                "turnover_rate": 5.2,
                "volume_ratio": 2.8,
                "amount": 620_000_000,
            }
        ])
        load_market_snapshot.side_effect = [pd.DataFrame(), previous_market]
        load_recent_daily.return_value = pd.DataFrame([{"ts_code": "600201.SH", "trade_date": "20260728", "close": 17.85}])
        rank_sector_potential.return_value = pd.DataFrame([{"industry_name": "机器人", "potential_score": 88.0}])
        build_realtime_tail_premium_monitor.return_value = {"trade_date": "20260729", "stocks": []}

        def fake_bars(ts_code, start_datetime, end_datetime, freq="60min"):
            if freq == "60min":
                bars = build_60min_bars(ts_code, water_macd_kdj_cross_closes())
                today = pd.DataFrame([
                    {"ts_code": ts_code, "trade_time": "2026-07-29 09:30:00", "open": 17.9, "close": 18.0, "high": 18.1, "low": 17.85, "vol": 10000, "amount": 18000000},
                    {"ts_code": ts_code, "trade_time": "2026-07-29 10:30:00", "open": 18.0, "close": 18.18, "high": 18.42, "low": 17.95, "vol": 13000, "amount": 23600000},
                ])
                return pd.concat([bars, today], ignore_index=True)
            return build_tail_1min_bars(
                ts_code,
                [18.0, 18.0, 18.0, 18.0, 18.0, 18.0, 18.08, 18.12, 18.18],
                [1000, 1000, 1000, 1000, 1000, 1000, 2500, 3000, 3600],
            )

        cached_minute_bars.side_effect = fake_bars

        result = build_realtime_info(now=datetime(2026, 7, 29, 14, 50))

        row = result["intraday"]["stocks"][0]
        self.assertEqual(result["trade_date"], "20260729")
        self.assertEqual(result["intraday"]["base_trade_date"], "20260728")
        self.assertEqual(result["intraday"]["data_source"], "previous_snapshot")
        self.assertEqual(row["ts_code"], "600201.SH")
        self.assertEqual(row["current_price"], 18.18)
        self.assertEqual(row["day_high"], 18.42)
        load_market_snapshot.assert_any_call("20260729")
        load_market_snapshot.assert_any_call("20260728")
        load_recent_daily.assert_called_once_with("20260728", 100)

    @patch("realtime_info_service._cached_minute_bars", create=True)
    def test_realtime_intraday_bar_loader_skips_tail_minute_before_1430(self, cached_minute_bars):
        market = pd.DataFrame([
            {
                "ts_code": "600201.SH",
                "name": "盘中候选",
                "industry": "机器人",
                "turnover_rate": 5.2,
                "volume_ratio": 2.8,
                "amount": 620_000_000,
                "pct_chg": 4.2,
            }
        ])
        sectors = pd.DataFrame([{"industry_name": "机器人", "potential_score": 88.0}])
        cached_minute_bars.return_value = build_60min_bars("600201.SH", water_macd_kdj_cross_closes())

        result = _load_realtime_intraday_signal_bars(
            market,
            sectors,
            "20260729",
            datetime(2026, 7, 29, 14, 10),
        )

        self.assertEqual(list(result.keys()), ["600201.SH"])
        self.assertIn("60m", result["600201.SH"])
        self.assertNotIn("tail_1m", result["600201.SH"])
        cached_minute_bars.assert_called_once_with(
            "600201.SH",
            "2026-05-20 09:30:00",
            "2026-07-29 14:09:00",
            freq="60min",
        )

    @patch("realtime_info_service.build_realtime_tail_premium_monitor")
    @patch("realtime_info_service._cached_minute_bars", create=True)
    @patch("realtime_info_service.rank_sector_potential", create=True)
    @patch("realtime_info_service.load_recent_daily", create=True)
    @patch("realtime_info_service.load_market_snapshot")
    @patch("realtime_info_service.sync_cached_market_data")
    @patch("realtime_info_service.get_trade_dates", return_value=["20260729"])
    def test_realtime_intraday_reuses_same_minute_result_cache(
        self,
        _get_trade_dates,
        sync_cached_market_data,
        load_market_snapshot,
        load_recent_daily,
        rank_sector_potential,
        cached_minute_bars,
        build_realtime_tail_premium_monitor,
    ):
        sync_cached_market_data.return_value = {"data_trade_date": "20260729", "cache_updated": True}
        market = pd.DataFrame([
            {
                "trade_date": "20260729",
                "ts_code": "600201.SH",
                "name": "今日共振",
                "industry": "机器人",
                "close": 18.6,
                "high": 19.1,
                "pct_chg": 4.2,
                "turnover_rate": 5.2,
                "volume_ratio": 2.8,
                "amount": 620_000_000,
            }
        ])
        load_market_snapshot.return_value = market
        load_recent_daily.return_value = pd.DataFrame([{"ts_code": "600201.SH", "trade_date": "20260728", "close": 17.85}])
        rank_sector_potential.return_value = pd.DataFrame([{"industry_name": "机器人", "potential_score": 88.0}])
        cached_minute_bars.return_value = build_60min_bars("600201.SH", water_macd_kdj_cross_closes())
        build_realtime_tail_premium_monitor.return_value = {"trade_date": "20260729", "stocks": []}

        first = build_realtime_info(now=datetime(2026, 7, 29, 14, 50, 5))
        second = build_realtime_info(now=datetime(2026, 7, 29, 14, 50, 45))

        self.assertEqual(first["intraday"]["stocks"], second["intraday"]["stocks"])
        self.assertTrue(second["intraday"]["result_cache_hit"])
        self.assertEqual(cached_minute_bars.call_count, 2)

    @patch("realtime_info_service.build_realtime_tail_premium_monitor")
    @patch("realtime_info_service._cached_minute_bars", create=True)
    @patch("realtime_info_service.rank_sector_potential", create=True)
    @patch("realtime_info_service.load_recent_daily", create=True)
    @patch("realtime_info_service.load_market_snapshot")
    @patch("realtime_info_service.sync_cached_market_data")
    @patch("realtime_info_service.get_trade_dates", return_value=["20260729"])
    def test_realtime_intraday_tail_minutes_update_price_after_1430(
        self,
        _get_trade_dates,
        sync_cached_market_data,
        load_market_snapshot,
        load_recent_daily,
        rank_sector_potential,
        cached_minute_bars,
        build_realtime_tail_premium_monitor,
    ):
        sync_cached_market_data.return_value = {"data_trade_date": "20260729", "cache_updated": True}
        load_market_snapshot.return_value = pd.DataFrame([{
            "trade_date": "20260729",
            "ts_code": "600201.SH",
            "name": "尾盘更新",
            "industry": "机器人",
            "close": 18.0,
            "high": 18.2,
            "pct_chg": 2.0,
            "turnover_rate": 5.2,
            "volume_ratio": 2.8,
            "amount": 620_000_000,
        }])
        load_recent_daily.return_value = pd.DataFrame([{"ts_code": "600201.SH", "trade_date": "20260728", "close": 17.85}])
        rank_sector_potential.return_value = pd.DataFrame([{"industry_name": "机器人", "potential_score": 88.0}])
        build_realtime_tail_premium_monitor.return_value = {"trade_date": "20260729", "stocks": []}

        tail = pd.DataFrame([
            {"ts_code": "600201.SH", "trade_time": "2026-07-29 14:25:00", "open": 18.0, "high": 18.0, "low": 18.0, "close": 18.0, "vol": 1000, "amount": 18_000},
            {"ts_code": "600201.SH", "trade_time": "2026-07-29 14:26:00", "open": 18.0, "high": 18.0, "low": 18.0, "close": 18.0, "vol": 1000, "amount": 18_000},
            {"ts_code": "600201.SH", "trade_time": "2026-07-29 14:27:00", "open": 18.0, "high": 18.0, "low": 18.0, "close": 18.0, "vol": 1000, "amount": 18_000},
            {"ts_code": "600201.SH", "trade_time": "2026-07-29 14:28:00", "open": 18.0, "high": 18.0, "low": 18.0, "close": 18.0, "vol": 1000, "amount": 18_000},
            {"ts_code": "600201.SH", "trade_time": "2026-07-29 14:29:00", "open": 18.0, "high": 18.0, "low": 18.0, "close": 18.0, "vol": 1000, "amount": 18_000},
            {"ts_code": "600201.SH", "trade_time": "2026-07-29 14:30:00", "open": 18.0, "high": 18.0, "low": 18.0, "close": 18.0, "vol": 1000, "amount": 18_000},
            {"ts_code": "600201.SH", "trade_time": "2026-07-29 14:31:00", "open": 18.1, "high": 18.2, "low": 18.08, "close": 18.15, "vol": 1600, "amount": 29_040},
            {"ts_code": "600201.SH", "trade_time": "2026-07-29 14:49:00", "open": 18.55, "high": 18.72, "low": 18.5, "close": 18.68, "vol": 4200, "amount": 78_456},
        ])

        def fake_bars(ts_code, start_datetime, end_datetime, freq="60min"):
            if freq == "60min":
                bars = build_60min_bars(ts_code, water_macd_kdj_cross_closes())
                today = pd.DataFrame([
                    {"ts_code": ts_code, "trade_time": "2026-07-29 10:30:00", "open": 18.0, "close": 18.1, "high": 18.2, "low": 17.95, "vol": 10000, "amount": 181000},
                ])
                return pd.concat([bars, today], ignore_index=True)
            return tail

        cached_minute_bars.side_effect = fake_bars

        result = build_realtime_info(now=datetime(2026, 7, 29, 14, 50))

        row = result["intraday"]["stocks"][0]
        self.assertTrue(row["tail_after_1430_available"])
        self.assertEqual(row["current_price"], 18.68)
        self.assertEqual(row["day_high"], 18.72)
        self.assertGreater(row["tail_return_after_1430"], 3.0)

    @patch("realtime_info_service.build_realtime_tail_premium_monitor")
    @patch("realtime_info_service._cached_minute_bars", create=True)
    @patch("realtime_info_service.rank_sector_potential", create=True)
    @patch("realtime_info_service.load_recent_daily", create=True)
    @patch("realtime_info_service.load_market_snapshot")
    @patch("realtime_info_service.sync_cached_market_data")
    @patch("realtime_info_service.get_trade_dates", return_value=["20260729"])
    def test_realtime_intraday_marks_stale_minute_bars_unavailable(
        self,
        _get_trade_dates,
        sync_cached_market_data,
        load_market_snapshot,
        load_recent_daily,
        rank_sector_potential,
        cached_minute_bars,
        build_realtime_tail_premium_monitor,
    ):
        sync_cached_market_data.return_value = {"data_trade_date": "20260729", "cache_updated": True}
        load_market_snapshot.return_value = pd.DataFrame([{
            "trade_date": "20260729",
            "ts_code": "600201.SH",
            "name": "旧分时",
            "industry": "机器人",
            "close": 18.0,
            "high": 18.2,
            "pct_chg": 2.0,
            "turnover_rate": 5.2,
            "volume_ratio": 2.8,
            "amount": 620_000_000,
        }])
        load_recent_daily.return_value = pd.DataFrame([{"ts_code": "600201.SH", "trade_date": "20260728", "close": 17.85}])
        rank_sector_potential.return_value = pd.DataFrame([{"industry_name": "机器人", "potential_score": 88.0}])
        cached_minute_bars.return_value = build_60min_bars("600201.SH", water_macd_kdj_cross_closes())
        build_realtime_tail_premium_monitor.return_value = {"trade_date": "20260729", "stocks": []}

        result = build_realtime_info(now=datetime(2026, 7, 29, 14, 50))

        row = result["intraday"]["stocks"][0]
        self.assertFalse(row["minute_data_current"])
        self.assertFalse(row["tail_after_1430_available"])
        self.assertIsNone(row["tail_return_after_1430"])
        self.assertEqual(row["main_force_reason"], "当日分时未返回")

    @patch("realtime_info_service.build_realtime_tail_premium_monitor")
    @patch("realtime_info_service._cached_minute_bars", create=True)
    @patch("realtime_info_service.rank_sector_potential", create=True)
    @patch("realtime_info_service.load_recent_daily", create=True)
    @patch("realtime_info_service.load_market_snapshot")
    @patch("realtime_info_service.sync_cached_market_data")
    @patch("realtime_info_service.get_trade_dates", return_value=["20260729", "20260728"])
    def test_realtime_intraday_uses_latest_1459_minutes_after_close_when_today_daily_unavailable(
        self,
        _get_trade_dates,
        sync_cached_market_data,
        load_market_snapshot,
        load_recent_daily,
        rank_sector_potential,
        cached_minute_bars,
        build_realtime_tail_premium_monitor,
    ):
        sync_cached_market_data.return_value = {
            "data_trade_date": "20260728",
            "cache_updated": True,
            "cache_warnings": ["daily 20260729: daily 返回为空"],
        }
        base_market = pd.DataFrame([{
            "trade_date": "20260728",
            "ts_code": "600201.SH",
            "name": "收盘可用",
            "industry": "机器人",
            "close": 17.85,
            "high": 18.0,
            "pct_chg": 1.2,
            "turnover_rate": 5.2,
            "volume_ratio": 2.8,
            "amount": 620_000_000,
        }])
        load_market_snapshot.side_effect = [pd.DataFrame(), base_market]
        load_recent_daily.return_value = pd.DataFrame([{"ts_code": "600201.SH", "trade_date": "20260728", "close": 17.85}])
        rank_sector_potential.return_value = pd.DataFrame([{"industry_name": "机器人", "potential_score": 88.0}])
        build_realtime_tail_premium_monitor.return_value = {"trade_date": "20260728", "stocks": []}
        stale_60m = build_60min_bars("600201.SH", water_macd_kdj_cross_closes())

        def fake_bars(ts_code, start_datetime, end_datetime, freq="60min"):
            if freq == "60min" and end_datetime.endswith("15:00:00"):
                return stale_60m
            if freq == "60min":
                today = pd.DataFrame([
                    {"ts_code": ts_code, "trade_time": "2026-07-29 14:30:00", "open": 17.9, "close": 18.0, "high": 18.1, "low": 17.85, "vol": 10000, "amount": 180000},
                    {"ts_code": ts_code, "trade_time": "2026-07-29 14:59:00", "open": 18.0, "close": 18.18, "high": 18.42, "low": 17.95, "vol": 13000, "amount": 236000},
                ])
                return pd.concat([stale_60m, today], ignore_index=True)
            if end_datetime.endswith("15:00:00"):
                return pd.DataFrame()
            return pd.DataFrame([
                {"ts_code": ts_code, "trade_time": "2026-07-29 14:25:00", "open": 18.0, "high": 18.0, "low": 18.0, "close": 18.0, "vol": 1000, "amount": 18_000},
                {"ts_code": ts_code, "trade_time": "2026-07-29 14:26:00", "open": 18.0, "high": 18.0, "low": 18.0, "close": 18.0, "vol": 1000, "amount": 18_000},
                {"ts_code": ts_code, "trade_time": "2026-07-29 14:27:00", "open": 18.0, "high": 18.0, "low": 18.0, "close": 18.0, "vol": 1000, "amount": 18_000},
                {"ts_code": ts_code, "trade_time": "2026-07-29 14:28:00", "open": 18.0, "high": 18.0, "low": 18.0, "close": 18.0, "vol": 1000, "amount": 18_000},
                {"ts_code": ts_code, "trade_time": "2026-07-29 14:29:00", "open": 18.0, "high": 18.0, "low": 18.0, "close": 18.0, "vol": 1000, "amount": 18_000},
                {"ts_code": ts_code, "trade_time": "2026-07-29 14:30:00", "open": 18.0, "high": 18.0, "low": 18.0, "close": 18.0, "vol": 1000, "amount": 18_000},
                {"ts_code": ts_code, "trade_time": "2026-07-29 14:59:00", "open": 18.55, "high": 18.72, "low": 18.5, "close": 18.68, "vol": 4200, "amount": 78_456},
            ])

        cached_minute_bars.side_effect = fake_bars

        result = build_realtime_info(now=datetime(2026, 7, 29, 15, 10))

        row = result["intraday"]["stocks"][0]
        self.assertEqual(result["trade_date"], "20260729")
        self.assertEqual(result["intraday"]["trade_date"], "20260729")
        self.assertEqual(result["intraday"]["base_trade_date"], "20260728")
        self.assertTrue(row["minute_data_current"])
        self.assertTrue(row["tail_after_1430_available"])
        self.assertEqual(row["current_price"], 18.68)

    @patch("realtime_info_service.build_realtime_tail_premium_monitor")
    @patch("realtime_info_service._cached_minute_bars", create=True)
    @patch("realtime_info_service.rank_sector_potential", create=True)
    @patch("realtime_info_service.load_recent_daily", create=True)
    @patch("realtime_info_service.load_market_snapshot")
    @patch("realtime_info_service.sync_cached_market_data")
    @patch("realtime_info_service.get_trade_dates", return_value=["20260729"])
    def test_realtime_intraday_falls_back_to_1459_when_1500_has_no_current_minutes(
        self,
        _get_trade_dates,
        sync_cached_market_data,
        load_market_snapshot,
        load_recent_daily,
        rank_sector_potential,
        cached_minute_bars,
        build_realtime_tail_premium_monitor,
    ):
        sync_cached_market_data.return_value = {"data_trade_date": "20260729", "cache_updated": True}
        load_market_snapshot.return_value = pd.DataFrame([{
            "trade_date": "20260729",
            "ts_code": "600201.SH",
            "name": "1459可用",
            "industry": "机器人",
            "close": 18.0,
            "high": 18.2,
            "pct_chg": 2.0,
            "turnover_rate": 5.2,
            "volume_ratio": 2.8,
            "amount": 620_000_000,
        }])
        load_recent_daily.return_value = pd.DataFrame([{"ts_code": "600201.SH", "trade_date": "20260728", "close": 17.85}])
        rank_sector_potential.return_value = pd.DataFrame([{"industry_name": "机器人", "potential_score": 88.0}])
        build_realtime_tail_premium_monitor.return_value = {"trade_date": "20260729", "stocks": []}
        stale_60m = build_60min_bars("600201.SH", water_macd_kdj_cross_closes())
        fresh_tail = pd.DataFrame([
            {"ts_code": "600201.SH", "trade_time": "2026-07-29 14:25:00", "open": 18.0, "high": 18.0, "low": 18.0, "close": 18.0, "vol": 1000, "amount": 18_000},
            {"ts_code": "600201.SH", "trade_time": "2026-07-29 14:30:00", "open": 18.0, "high": 18.05, "low": 17.98, "close": 18.0, "vol": 1000, "amount": 18_000},
            {"ts_code": "600201.SH", "trade_time": "2026-07-29 14:59:00", "open": 18.5, "high": 18.72, "low": 18.45, "close": 18.68, "vol": 4200, "amount": 78_456},
        ])

        def fake_bars(ts_code, start_datetime, end_datetime, freq="60min"):
            if freq == "60min" and end_datetime.endswith("15:00:00"):
                return stale_60m
            if freq == "60min" and end_datetime.endswith("14:59:00"):
                today = pd.DataFrame([
                    {"ts_code": ts_code, "trade_time": "2026-07-29 14:30:00", "open": 18.0, "close": 18.18, "high": 18.42, "low": 17.95, "vol": 13000, "amount": 236000},
                ])
                return pd.concat([stale_60m, today], ignore_index=True)
            if freq == "1min" and end_datetime.endswith("15:00:00"):
                return pd.DataFrame()
            return fresh_tail

        cached_minute_bars.side_effect = fake_bars

        result = build_realtime_info(now=datetime(2026, 7, 29, 15, 10))

        row = result["intraday"]["stocks"][0]
        self.assertTrue(row["minute_data_current"])
        self.assertTrue(row["tail_after_1430_available"])
        self.assertEqual(row["current_price"], 18.68)
        self.assertEqual(row["day_high"], 18.72)
        called_windows = [
            (*call.args, call.kwargs.get("freq"))
            for call in cached_minute_bars.call_args_list
        ]
        self.assertIn(("600201.SH", "2026-05-20 09:30:00", "2026-07-29 14:59:00", "60min"), called_windows)
        self.assertIn(("600201.SH", "2026-07-29 14:25:00", "2026-07-29 14:59:00", "1min"), called_windows)

    @patch("realtime_info_service.build_realtime_tail_premium_monitor")
    @patch("realtime_info_service._cached_minute_bars", create=True)
    @patch("realtime_info_service.rank_sector_potential", create=True)
    @patch("realtime_info_service.load_recent_daily", create=True)
    @patch("realtime_info_service.load_market_snapshot")
    @patch("realtime_info_service.sync_cached_market_data")
    @patch("realtime_info_service.get_trade_dates", return_value=["20260729", "20260728"])
    def test_realtime_intraday_keeps_base_signals_when_latest_60m_is_unavailable(
        self,
        _get_trade_dates,
        sync_cached_market_data,
        load_market_snapshot,
        load_recent_daily,
        rank_sector_potential,
        cached_minute_bars,
        build_realtime_tail_premium_monitor,
    ):
        sync_cached_market_data.return_value = {"data_trade_date": "20260728", "cache_updated": True}
        base_market = pd.DataFrame([{
            "trade_date": "20260728",
            "ts_code": "600201.SH",
            "name": "基础信号",
            "industry": "机器人",
            "close": 17.85,
            "high": 18.0,
            "pct_chg": 1.2,
            "turnover_rate": 5.2,
            "volume_ratio": 2.8,
            "amount": 620_000_000,
        }])
        load_market_snapshot.side_effect = [pd.DataFrame(), base_market]
        load_recent_daily.return_value = pd.DataFrame([{"ts_code": "600201.SH", "trade_date": "20260728", "close": 17.85}])
        rank_sector_potential.return_value = pd.DataFrame([{"industry_name": "机器人", "potential_score": 88.0}])
        build_realtime_tail_premium_monitor.return_value = {"trade_date": "20260728", "stocks": []}

        def fake_bars(ts_code, start_datetime, end_datetime, freq="60min"):
            if freq == "60min" and end_datetime.startswith("2026-07-28"):
                return build_60min_bars(ts_code, water_macd_kdj_cross_closes())
            return pd.DataFrame()

        cached_minute_bars.side_effect = fake_bars

        result = build_realtime_info(now=datetime(2026, 7, 29, 15, 10))

        row = result["intraday"]["stocks"][0]
        self.assertEqual(result["intraday"]["trade_date"], "20260729")
        self.assertEqual(result["intraday"]["base_trade_date"], "20260728")
        self.assertEqual(result["intraday"]["data_source"], "previous_snapshot")
        self.assertFalse(row["minute_data_current"])
        self.assertEqual(row["next_day_bias"], "数据不足")
        self.assertIn("已尝试15:00和14:59", row["next_day_bias_reason"])


if __name__ == "__main__":
    unittest.main()
