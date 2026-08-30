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
    _attach_historical_resilience_fields,
    _attach_realtime_chip_fields,
    _attach_bottom_consolidation_fields,
    _attach_market_relative_fields,
    _build_market_relative_benchmark,
    _build_bottom_filter_debug,
    _group_realtime_stage_rows,
    _database_realtime_result_key,
    _enrich_rows_with_market,
    _fill_missing_realtime_volume_ratio,
    _include_bottom_candidate_sectors,
    _market_price_map,
    _apply_minute_snapshots_to_market,
    _load_realtime_market_inputs,
    _load_realtime_intraday_signal_bars,
    _market_relative_candidate_mask,
    _refresh_market_relative_fields,
    _minute_price_snapshot,
    _minute_result_with_1459_fallback,
    _snapshot_supports_realtime_filters,
    _trading_session_progress,
)
from strategy import _macd_kdj_60m_signal, _select_intraday_signal_stocks
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
        self.chip_queries = patch(
            "chip_peak_service._query_tushare",
            return_value=pd.DataFrame(),
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
        self.chip_queries.start()
        self.addCleanup(self.snapshot_fallback.stop)
        self.addCleanup(self.eastmoney_minutes.stop)
        self.addCleanup(self.sina_minutes.stop)
        self.addCleanup(self.database_minutes.stop)
        self.addCleanup(self.database_minute_saves.stop)
        self.addCleanup(self.database_results.stop)
        self.addCleanup(self.database_result_saves.stop)
        self.addCleanup(self.database_prune.stop)
        self.addCleanup(self.complete_dates.stop)
        self.addCleanup(self.chip_queries.stop)

    def test_bottom_consolidation_rejects_flat_base_without_catalyst(self):
        closes = [11.5 - index * 0.04 for index in range(40)] + [
            9.8 + (index % 4) * 0.05 for index in range(20)
        ]
        history = pd.DataFrame([
            {
                "ts_code": "600001.SH",
                "trade_date": f"2026{index + 1:04d}",
                "close": close,
                "high": close * 1.02,
                "low": close * 0.98,
            }
            for index, close in enumerate(closes)
        ])
        market = pd.DataFrame([{
            "ts_code": "600001.SH",
            "close": 9.95,
        }])

        result = _attach_bottom_consolidation_fields(market, history, "20260731")

        row = result.iloc[0]
        self.assertFalse(row["bottom_consolidation"])
        self.assertEqual(row["resonance_type"], "强势共振")
        self.assertLessEqual(row["bottom_box_amplitude_20d"], 15.0)

    def test_bottom_consolidation_accepts_limit_up_pullback_within_20_days(self):
        closes = [10.0, 10.1, 11.05, 10.7, 10.4, 10.2, 10.0] + [
            9.85 + (index % 3) * 0.04 for index in range(12)
        ]
        history = pd.DataFrame([
            {
                "ts_code": "600010.SH",
                "trade_date": f"202607{index + 1:02d}",
                "close": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "pct_chg": 9.8 if index == 2 else 0.0,
                "vol": 1_000_000,
            }
            for index, close in enumerate(closes)
        ])
        market = pd.DataFrame([{
            "ts_code": "600010.SH", "close": 9.92,
            "pct_chg": 0.8, "vol": 1_100_000,
        }])

        row = _attach_bottom_consolidation_fields(
            market, history, "20260731",
        ).iloc[0]

        self.assertTrue(row["bottom_consolidation"])
        self.assertEqual(row["resonance_type"], "涨停回落筑底")
        self.assertEqual(row["bottom_window_days"], 20)
        self.assertEqual(row["bottom_limit_up_date"], "20260703")

    def test_bottom_consolidation_ignores_zero_low_without_aborting_scan(self):
        history = pd.DataFrame([
            {
                "ts_code": "600012.SH",
                "trade_date": f"202607{index + 1:02d}",
                "close": 10.0,
                "high": 10.1,
                "low": 0.0 if index == 18 else 9.9,
                "pct_chg": 9.8 if index == 2 else 0.0,
                "vol": 1_000_000,
            }
            for index in range(19)
        ])
        market = pd.DataFrame([{
            "ts_code": "600012.SH",
            "close": 9.2,
            "high": 9.3,
            "low": 9.1,
            "pct_chg": 0.2,
            "vol": 900_000,
        }])

        row = _attach_bottom_consolidation_fields(
            market, history, "20260731",
        ).iloc[0]

        self.assertFalse(row["bottom_consolidation"])
        self.assertEqual(row["resonance_type"], "强势共振")

    def test_bottom_consolidation_accepts_one_to_three_day_volume_breakout(self):
        closes = [10.0 + (index % 3) * 0.03 for index in range(17)] + [
            10.15, 10.35,
        ]
        volumes = [1_000_000] * 17 + [1_700_000, 1_900_000]
        history = pd.DataFrame([
            {
                "ts_code": "600011.SH",
                "trade_date": f"202607{index + 1:02d}",
                "close": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "pct_chg": 0.2,
                "vol": volume,
            }
            for index, (close, volume) in enumerate(zip(closes, volumes))
        ])
        market = pd.DataFrame([{
            "ts_code": "600011.SH", "close": 10.55,
            "pct_chg": 1.93, "vol": 2_000_000,
        }])

        row = _attach_bottom_consolidation_fields(
            market, history, "20260731",
        ).iloc[0]

        self.assertTrue(row["bottom_consolidation"])
        self.assertEqual(row["resonance_type"], "底部放量启动")
        self.assertEqual(row["bottom_breakout_days"], 3)
        self.assertGreaterEqual(row["bottom_volume_expansion"], 1.5)

    def test_bottom_consolidation_rejects_continuing_decline(self):
        closes = [14.0 - index * 0.1 for index in range(60)]
        history = pd.DataFrame([
            {
                "ts_code": "600002.SH",
                "trade_date": f"2026{index + 1:04d}",
                "close": close,
                "high": close * 1.01,
                "low": close * 0.99,
            }
            for index, close in enumerate(closes)
        ])
        market = pd.DataFrame([{"ts_code": "600002.SH", "close": 8.0}])

        row = _attach_bottom_consolidation_fields(
            market, history, "20260731",
        ).iloc[0]

        self.assertFalse(row["bottom_consolidation"])
        self.assertEqual(row["resonance_type"], "强势共振")

    def test_bottom_consolidation_rejects_high_position(self):
        closes = [9.0 + index * 0.05 for index in range(40)] + [
            11.5 + (index % 4) * 0.05 for index in range(20)
        ]
        history = pd.DataFrame([
            {
                "ts_code": "600003.SH",
                "trade_date": f"2026{index + 1:04d}",
                "close": close,
                "high": close * 1.02,
                "low": close * 0.98,
            }
            for index, close in enumerate(closes)
        ])
        market = pd.DataFrame([{"ts_code": "600003.SH", "close": 11.7}])

        row = _attach_bottom_consolidation_fields(
            market, history, "20260731",
        ).iloc[0]

        self.assertFalse(row["bottom_consolidation"])

    def test_bottom_candidate_outside_hot_sectors_gets_minute_confirmation(self):
        market = pd.DataFrame([
            {
                "ts_code": "600101.SH", "name": "强势样本", "industry": "机器人",
                "pct_chg": 3.0, "turnover_rate": 5.0, "volume_ratio": 1.5,
                "amount": 200_000_000, "bottom_consolidation": False,
            },
            {
                "ts_code": "600102.SH", "name": "底部启动", "industry": "食品",
                "pct_chg": 0.1, "turnover_rate": 3.0, "volume_ratio": 0.7,
                "amount": 100_000_000, "bottom_consolidation": True,
                "resonance_type": "涨停回落筑底",
            },
        ])
        sectors = pd.DataFrame([{"industry_name": "机器人"}])
        requested = []

        def minute_loader(ts_code, start, end, freq, trade_date):
            requested.append(ts_code)
            return MinuteLoadResult(pd.DataFrame(), "fixture", [])

        _load_realtime_intraday_signal_bars(
            market,
            sectors,
            "20260731",
            datetime(2026, 7, 31, 14, 20),
            minute_loader=minute_loader,
        )

        self.assertIn("600102.SH", requested)

    def test_bottom_candidate_sector_is_added_without_replacing_hot_sector(self):
        sectors = pd.DataFrame([{"industry_name": "机器人", "rank": 1}])
        market = pd.DataFrame([
            {"industry": "机器人", "bottom_consolidation": False},
            {"industry": "食品", "bottom_consolidation": True},
        ])

        result = _include_bottom_candidate_sectors(sectors, market)

        self.assertEqual(result.iloc[0]["industry_name"], "机器人")
        self.assertEqual(set(result["industry_name"]), {"机器人", "食品"})

    def test_bottom_candidate_can_confirm_below_regular_pct_threshold(self):
        market = pd.DataFrame([{
            "ts_code": "600103.SH", "name": "底部确认", "pct_chg": 0.1,
            "turnover_rate": 3.0, "volume_ratio": 1.6,
            "amount": 100_000_000, "bottom_consolidation": True,
            "resonance_type": "底部放量启动",
        }])
        bars = {
            "600103.SH": {
                "60m": build_60min_bars(
                    "600103.SH", water_macd_kdj_cross_closes(),
                )
            }
        }

        with patch("strategy.load_macd_settings", return_value={
            "fast_period": 5, "slow_period": 34,
            "signal_period": 5, "version": 1,
        }):
            result = _select_intraday_signal_stocks(market, bars)

        self.assertEqual([row["ts_code"] for row in result], ["600103.SH"])

    def test_bottom_candidate_accepts_below_zero_macd_histogram_repair(self):
        closes = [12 - index * 0.07 for index in range(39)] + [9.345]
        row = pd.Series({
            "ts_code": "600104.SH", "name": "零轴下修复",
            "pct_chg": 0.3, "turnover_rate": 3.0, "volume_ratio": 0.7,
            "amount": 100_000_000, "bottom_consolidation": True,
            "resonance_type": "涨停回落筑底",
        })
        bars = {"60m": build_60min_bars("600104.SH", closes)}

        with patch("strategy.load_macd_settings", return_value={
            "fast_period": 5, "slow_period": 34,
            "signal_period": 5, "version": 1,
        }):
            signal = _macd_kdj_60m_signal(row, bars)

        self.assertIsNotNone(signal)
        self.assertLess(signal["macd_dif_60m"], 0)
        self.assertTrue(signal["bottom_turning_60m"])

    def test_bottom_filter_debug_counts_each_pipeline_boundary(self):
        market = pd.DataFrame([
            {"ts_code": "600201.SH", "bottom_consolidation": True},
            {"ts_code": "600202.SH", "bottom_consolidation": True},
            {"ts_code": "600203.SH", "bottom_consolidation": False},
        ])
        bars = {"600201.SH": {"60m": pd.DataFrame([{"close": 10}])}}
        preliminary = [{
            "ts_code": "600201.SH",
            "signal": {"bottom_consolidation": True},
        }]
        rows = []

        result = _build_bottom_filter_debug(market, bars, preliminary, rows)

        self.assertEqual(result["daily_candidate_count"], 2)
        self.assertEqual(result["minute_loaded_count"], 1)
        self.assertEqual(result["minute_missing_count"], 1)
        self.assertEqual(result["technical_confirmed_count"], 1)
        self.assertEqual(result["technical_rejected_count"], 0)
        self.assertEqual(result["final_output_count"], 0)

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
        self.assertIn("market-relative-v1", memory_key)
        self.assertIn("market-relative-v1", database_key)

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

    def test_market_relative_benchmark_uses_mainboard_non_st_equal_weight(self):
        market = pd.DataFrame([
            {"ts_code": "600001.SH", "name": "主板一", "pct_chg": 1.0},
            {"ts_code": "000001.SZ", "name": "主板二", "pct_chg": 3.0},
            {"ts_code": "300001.SZ", "name": "创业板", "pct_chg": 20.0},
            {"ts_code": "688001.SH", "name": "科创板", "pct_chg": 15.0},
            {"ts_code": "920001.BJ", "name": "北交所", "pct_chg": 10.0},
            {"ts_code": "600002.SH", "name": "ST风险", "pct_chg": -9.0},
        ])

        benchmark = _build_market_relative_benchmark(market)

        self.assertAlmostEqual(benchmark["market_pct_chg"], 2.0)
        self.assertEqual(benchmark["market_state"], "up")
        self.assertEqual(benchmark["market_state_label"], "大盘上涨")
        self.assertEqual(benchmark["sample_count"], 2)

    def test_attach_market_relative_fields_adds_strength_label_reason_and_score(self):
        market = pd.DataFrame([
            {
                "ts_code": "600001.SH",
                "name": "强势股",
                "pct_chg": 3.2,
                "turnover_rate": 5.0,
                "volume_ratio": 2.0,
            },
            {
                "ts_code": "600002.SH",
                "name": "普通股",
                "pct_chg": 1.2,
                "turnover_rate": 1.0,
                "volume_ratio": 1.0,
            },
        ])
        benchmark = {
            "market_pct_chg": 1.0,
            "market_state": "up",
            "market_state_label": "大盘上涨",
            "sample_count": 2000,
        }

        result, returned = _attach_market_relative_fields(market, benchmark)

        self.assertEqual(returned, benchmark)
        strong = result[result["ts_code"] == "600001.SH"].iloc[0]
        self.assertAlmostEqual(strong["market_pct_chg"], 1.0)
        self.assertAlmostEqual(strong["relative_strength"], 2.2)
        self.assertEqual(strong["market_resonance_label"], "强于大盘")
        self.assertIn("大盘 1.00%", strong["market_resonance_reason"])
        self.assertIn("个股 3.20%", strong["market_resonance_reason"])
        self.assertGreater(strong["realtime_relative_strength_score"], 0)

    def test_historical_resilience_score_uses_weighted_relative_daily_history(self):
        dates = [f"202607{day:02d}" for day in range(1, 21)]
        rows = []
        for offset, trade_date in enumerate(dates, start=1):
            market_pct = -1.0 if offset % 2 == 0 else 0.5
            rows.extend([
                {
                    "ts_code": "600003.SH",
                    "name": "基准一",
                    "trade_date": trade_date,
                    "pct_chg": market_pct,
                },
                {
                    "ts_code": "000001.SZ",
                    "name": "基准二",
                    "trade_date": trade_date,
                    "pct_chg": market_pct,
                },
                {
                    "ts_code": "600001.SH",
                    "name": "抗跌强",
                    "trade_date": trade_date,
                    "pct_chg": market_pct + (2.0 if offset % 2 == 0 else 1.0),
                },
                {
                    "ts_code": "600002.SH",
                    "name": "抗跌弱",
                    "trade_date": trade_date,
                    "pct_chg": market_pct - 1.0,
                },
            ])
        market = pd.DataFrame([
            {"ts_code": "600001.SH", "name": "抗跌强", "pct_chg": 1.0},
            {"ts_code": "600002.SH", "name": "抗跌弱", "pct_chg": 1.0},
        ])

        result = _attach_historical_resilience_fields(
            market,
            pd.DataFrame(rows),
            "20260721",
        ).set_index("ts_code")

        strong = result.loc["600001.SH"]
        weak = result.loc["600002.SH"]
        self.assertGreater(strong["historical_resilience_score"], 80)
        self.assertLess(weak["historical_resilience_score"], 45)
        self.assertGreater(
            strong["historical_resilience_score"],
            weak["historical_resilience_score"],
        )
        self.assertEqual(strong["historical_resilience_label"], "强抗跌")
        self.assertEqual(strong["historical_resilience_sample_count"], 20)
        self.assertIn("近20日", strong["historical_resilience_reason"])
        self.assertIn("下跌日跑赢", strong["historical_resilience_reason"])

    def test_historical_resilience_score_marks_insufficient_history(self):
        market = pd.DataFrame([{
            "ts_code": "600001.SH",
            "name": "样本少",
            "pct_chg": 1.0,
        }])
        history = pd.DataFrame([
            {
                "ts_code": "600001.SH",
                "name": "样本少",
                "trade_date": "20260720",
                "pct_chg": 1.0,
            },
            {
                "ts_code": "600002.SH",
                "name": "基准",
                "trade_date": "20260720",
                "pct_chg": -1.0,
            },
        ])

        result = _attach_historical_resilience_fields(
            market,
            history,
            "20260721",
        )

        self.assertTrue(pd.isna(result.iloc[0]["historical_resilience_score"]))
        self.assertEqual(result.iloc[0]["historical_resilience_label"], "历史不足")
        self.assertEqual(
            result.iloc[0]["historical_resilience_reason"],
            "近20日有效日线不足",
        )

    def test_refresh_market_relative_fields_recomputes_after_pct_overlay(self):
        signal = {
            "ts_code": "600001.SH",
            "pct_chg": -0.09,
            "market_pct_chg": -0.99,
            "market_resonance_state": "down",
            "market_resonance_state_label": "大盘下跌",
            "market_resonance_label": "逆势抗跌",
            "relative_strength": 11.08,
            "market_resonance_reason": "大盘 -0.99%，个股 10.09%，相对强 11.08pct",
            "realtime_relative_strength_score": 999,
            "volume_ratio": 1.5,
            "turnover_rate": 4.8,
        }

        refreshed = _refresh_market_relative_fields(signal)

        self.assertAlmostEqual(refreshed["relative_strength"], 0.9)
        self.assertIn("个股 -0.09%", refreshed["market_resonance_reason"])
        self.assertIn("相对强 0.90pct", refreshed["market_resonance_reason"])
        self.assertNotEqual(
            refreshed["realtime_relative_strength_score"],
            signal["realtime_relative_strength_score"],
        )

    def test_market_enrichment_updates_pct_with_current_snapshot(self):
        market = pd.DataFrame([{
            "ts_code": "300364.SZ",
            "close": 24.34,
            "high": 25.60,
            "pct_chg": -1.97,
        }])
        stale_candidate = {
            "ts_code": "300364.SZ",
            "name": "中文在线",
            "close": 24.34,
            "current_price": 24.34,
            "pct_chg": 17.64,
        }

        [row] = _enrich_rows_with_market(
            [stale_candidate],
            _market_price_map(market),
            datetime(2026, 8, 3, 16, 31),
        )

        self.assertEqual(row["current_price"], 24.34)
        self.assertEqual(row["day_high"], 25.60)
        self.assertEqual(row["pct_chg"], -1.97)

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

    def test_persistent_minute_result_fetches_only_missing_database_tail(self):
        import realtime_info_service

        cached = pd.DataFrame([
            {
                "ts_code": "600201.SH",
                "trade_time": "2026-07-30 14:25:00",
                "close": 10.0,
            },
            {
                "ts_code": "600201.SH",
                "trade_time": "2026-07-30 14:42:00",
                "close": 10.1,
            },
        ])
        refreshed = pd.DataFrame([
            {
                "ts_code": "600201.SH",
                "trade_time": "2026-07-30 14:43:00",
                "close": 10.2,
            },
        ])
        with (
            patch(
                "realtime_info_service.load_minute_cache",
                return_value=cached,
                create=True,
            ),
            patch(
                "realtime_info_service.minute_cache_is_fresh",
                return_value=False,
                create=True,
            ),
            patch(
                "realtime_info_service._minute_result_with_1459_fallback",
                return_value=MinuteLoadResult(
                    refreshed,
                    "eastmoney_fallback",
                    [],
                ),
            ) as provider,
            patch("realtime_info_service.save_minute_cache"),
        ):
            result = realtime_info_service._persistent_minute_result(
                "600201.SH",
                "2026-07-30 14:25:00",
                "2026-07-30 14:50:00",
                "1min",
                "20260730",
                datetime(2026, 7, 30, 14, 51),
            )

        provider.assert_called_once()
        self.assertEqual(provider.call_args.args[1], "2026-07-30 14:43:00")
        self.assertEqual(result.bars.iloc[0]["close"], 10.0)
        self.assertEqual(result.bars.iloc[-1]["close"], 10.2)

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

        with patch(
            "realtime_info_service._build_market_relative_benchmark",
            return_value={
                "market_pct_chg": None,
                "market_state": "fallback",
                "market_state_label": "大盘不可用",
                "sample_count": 0,
            },
        ):
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

        with patch(
            "realtime_info_service._build_market_relative_benchmark",
            return_value={
                "market_pct_chg": None,
                "market_state": "fallback",
                "market_state_label": "大盘不可用",
                "sample_count": 0,
            },
        ):
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

        with patch(
            "realtime_info_service._build_market_relative_benchmark",
            return_value={
                "market_pct_chg": None,
                "market_state": "fallback",
                "market_state_label": "大盘不可用",
                "sample_count": 0,
            },
        ):
            result = _load_realtime_intraday_signal_bars(
                market,
                sectors,
                "20260729",
                datetime(2026, 7, 29, 14, 50),
                minute_loader=minute_loader,
            )

        self.assertEqual(requested_codes, ["600202.SH"])
        self.assertEqual(list(result), ["600202.SH"])

    def test_market_relative_filter_requires_more_than_market_when_market_up(self):
        candidates = pd.DataFrame([
            {"ts_code": "600001.SH", "pct_chg": 2.2},
            {"ts_code": "600002.SH", "pct_chg": 1.7},
        ])
        candidates, benchmark = _attach_market_relative_fields(
            candidates,
            {
                "market_pct_chg": 1.0,
                "market_state": "up",
                "market_state_label": "大盘上涨",
                "sample_count": 2000,
            },
        )

        mask = _market_relative_candidate_mask(candidates, benchmark)

        self.assertEqual(mask.tolist(), [True, False])

    def test_market_relative_filter_accepts_small_drop_when_market_down(self):
        candidates = pd.DataFrame([
            {"ts_code": "600001.SH", "pct_chg": -0.2},
            {"ts_code": "600002.SH", "pct_chg": -1.1},
            {"ts_code": "600003.SH", "pct_chg": 0.1},
        ])
        candidates, benchmark = _attach_market_relative_fields(
            candidates,
            {
                "market_pct_chg": -2.0,
                "market_state": "down",
                "market_state_label": "大盘下跌",
                "sample_count": 2000,
            },
        )

        mask = _market_relative_candidate_mask(candidates, benchmark)

        self.assertEqual(mask.tolist(), [True, False, True])

    def test_market_relative_filter_falls_back_to_old_positive_rule_without_benchmark(self):
        candidates = pd.DataFrame([
            {"ts_code": "600001.SH", "pct_chg": 0.2},
            {"ts_code": "600002.SH", "pct_chg": 0.19},
            {"ts_code": "600003.SH", "pct_chg": -0.2},
        ])
        candidates, benchmark = _attach_market_relative_fields(
            candidates,
            {
                "market_pct_chg": None,
                "market_state": "fallback",
                "market_state_label": "大盘不可用",
                "sample_count": 0,
            },
        )

        mask = _market_relative_candidate_mask(candidates, benchmark)

        self.assertEqual(mask.tolist(), [True, False, False])

    def test_signal_minutes_use_market_relative_filter_for_down_market(self):
        requested_codes = []
        market = pd.DataFrame([
            {
                "ts_code": "600001.SH",
                "name": "抗跌股",
                "industry": "机器人",
                "turnover_rate": 3.0,
                "volume_ratio": 1.4,
                "amount": 800_000,
                "pct_chg": -0.2,
            },
            {
                "ts_code": "600002.SH",
                "name": "弱势股",
                "industry": "机器人",
                "turnover_rate": 3.0,
                "volume_ratio": 1.4,
                "amount": 790_000,
                "pct_chg": -1.3,
            },
            {
                "ts_code": "600003.SH",
                "name": "市场样本",
                "industry": "其他",
                "turnover_rate": 3.0,
                "volume_ratio": 1.4,
                "amount": 780_000,
                "pct_chg": -4.0,
            },
        ] + [
            {
                "ts_code": f"6001{index:02d}.SH",
                "name": f"市场样本{index}",
                "industry": "其他",
                "turnover_rate": 3.0,
                "volume_ratio": 1.4,
                "amount": 780_000,
                "pct_chg": -3.0,
            }
            for index in range(20)
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

        self.assertEqual(requested_codes, ["600001.SH"])
        self.assertEqual(list(result), ["600001.SH"])

    def test_signal_minutes_skip_st_named_candidates(self):
        requested_codes = []
        market = pd.DataFrame([{
            "ts_code": "600203.SH",
            "name": "ST风险",
            "industry": "机器人",
            "turnover_rate": 3.0,
            "volume_ratio": 1.4,
            "amount": 300_000,
            "pct_chg": 3.0,
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

        self.assertEqual(requested_codes, [])
        self.assertEqual(result, {})

    def test_60m_water_bullish_without_recent_cross_is_weak_intraday_signal(self):
        closes = [
            10.0, 10.0358, 10.0717, 10.1075, 10.1434, 10.1792,
            10.2151, 10.2509, 10.2867, 10.3226, 10.3584, 10.3943,
            10.4301, 10.4659, 10.5018, 10.5376, 10.5735, 10.6093,
            10.6452, 10.681, 10.7168, 10.7527, 10.7885, 10.8244,
            10.8602, 10.896, 10.9319, 10.9677, 11.0036, 11.0394,
            11.0753, 11.1111, 11.1469, 11.1828, 11.2186, 11.2545,
            11.2903, 11.3261, 11.362, 11.3978, 11.4337, 11.4695,
            11.3509, 11.2277, 11.2064, 11.2415, 11.2761, 11.1785,
            11.201, 11.1253, 11.1561, 11.1914, 11.1546, 11.0982,
            11.0827, 11.0018, 11.0787, 11.1499, 11.1911, 11.2468,
            11.3263, 11.3883, 11.3801, 11.3343, 11.3571, 11.3624,
            11.3531,
        ]
        row = pd.Series({
            "ts_code": "600777.SH",
            "name": "弱共振",
            "pct_chg": 3.2,
            "turnover_rate": 5.0,
            "volume_ratio": 1.4,
            "amount": 300_000_000,
        })

        signal = _macd_kdj_60m_signal(
            row,
            build_60min_bars("600777.SH", closes),
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal["intraday_signal_tier"], "weak")
        self.assertTrue(signal["macd_bullish_60m"])
        self.assertTrue(signal["macd_above_zero_60m"])
        self.assertFalse(signal["macd_histogram_up_60m"])
        self.assertIn("60分MACD水上多头观察", signal["intraday_signal_reason"])

    def test_snapshot_supports_relaxed_realtime_filter_candidates(self):
        market = pd.DataFrame([{
            "ts_code": "600202.SH",
            "pct_chg": 0.2,
            "turnover_rate": 1.0,
            "volume_ratio": 1.0,
            "amount": 100_000,
        }])

        with patch(
            "realtime_info_service._build_market_relative_benchmark",
            return_value={
                "market_pct_chg": None,
                "market_state": "fallback",
                "market_state_label": "大盘不可用",
                "sample_count": 0,
            },
        ):
            self.assertTrue(_snapshot_supports_realtime_filters(market))

    def test_snapshot_supports_market_relative_resilient_down_market_candidate(self):
        market = pd.DataFrame([
            {
                "ts_code": "600001.SH",
                "name": "抗跌股",
                "pct_chg": -0.2,
                "turnover_rate": 3.0,
                "volume_ratio": 1.2,
                "amount": 200_000,
            },
            {
                "ts_code": "600002.SH",
                "name": "市场样本",
                "pct_chg": -4.0,
                "turnover_rate": 3.0,
                "volume_ratio": 1.2,
                "amount": 200_000,
            },
        ] + [
            {
                "ts_code": f"6001{index:02d}.SH",
                "name": f"市场样本{index}",
                "pct_chg": -3.0,
                "turnover_rate": 3.0,
                "volume_ratio": 1.2,
                "amount": 200_000,
            }
            for index in range(20)
        ])

        self.assertTrue(_snapshot_supports_realtime_filters(market))

    def test_signal_minutes_fall_back_to_old_pct_rule_when_benchmark_raises(self):
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

        with patch(
            "realtime_info_service._build_market_relative_benchmark",
            side_effect=RuntimeError("benchmark failed"),
        ):
            result = _load_realtime_intraday_signal_bars(
                market,
                sectors,
                "20260729",
                datetime(2026, 7, 29, 14, 50),
                minute_loader=minute_loader,
            )

        self.assertEqual(requested_codes, ["600202.SH"])
        self.assertEqual(list(result), ["600202.SH"])

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

        with (
            patch(
                "realtime_info_service.macd_parameter_key",
                return_value="macd-test",
            ),
            patch(
                "strategy.load_macd_settings",
                return_value={
                    "fast_period": 5,
                    "slow_period": 34,
                    "signal_period": 5,
                    "version": 1,
                },
            ),
        ):
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

    @patch("realtime_info_service._load_tail_minute_bars_for_pick")
    def test_intraday_confluence_outputs_market_relative_fields(self, tail_loader):
        import realtime_info_service

        market = pd.DataFrame([
            {
                "trade_date": "20260731",
                "ts_code": "600301.SH",
                "name": "强势共振",
                "industry": "机器人",
                "close": 12.6,
                "high": 12.8,
                "low": 12.1,
                "pct_chg": 3.2,
                "turnover_rate": 4.8,
                "volume_ratio": 1.5,
                "amount": 180_000_000,
            },
            {
                "trade_date": "20260731",
                "ts_code": "600302.SH",
                "name": "市场样本",
                "industry": "其他",
                "close": 10.1,
                "high": 10.2,
                "low": 9.9,
                "pct_chg": -1.2,
                "turnover_rate": 3.0,
                "volume_ratio": 1.1,
                "amount": 120_000_000,
            },
        ])
        tail_loader.return_value = MinuteLoadResult(
            pd.DataFrame(), "not_available", []
        )

        def minute_loader(ts_code, start, end, freq, trade_date):
            return MinuteLoadResult(
                build_60min_bars(ts_code, water_macd_kdj_cross_closes()),
                "fixture",
                [],
            )

        with (
            patch(
                "realtime_info_service.macd_parameter_key",
                return_value="macd-test",
            ),
            patch(
                "strategy.load_macd_settings",
                return_value={
                    "fast_period": 5,
                    "slow_period": 34,
                    "signal_period": 5,
                    "version": 1,
                },
            ),
        ):
            result = realtime_info_service._build_realtime_intraday_section(
                market,
                pd.DataFrame(),
                "20260731",
                datetime(2026, 7, 31, 14, 20),
                limit=10,
                minute_loader=minute_loader,
                force_refresh=True,
            )

        row = result["stocks"][0]
        self.assertEqual(row["ts_code"], "600301.SH")
        self.assertIn("market_pct_chg", row)
        self.assertIn("relative_strength", row)
        self.assertIn("market_resonance_label", row)
        self.assertIn("market_resonance_reason", row)
        self.assertIn("realtime_relative_strength_score", row)
        self.assertIn(
            row["market_resonance_label"],
            {"强于大盘", "震荡走强", "逆势抗跌"},
        )

    @patch("realtime_info_service._load_tail_minute_bars_for_pick")
    def test_intraday_confluence_outputs_resilient_stock_when_market_down(self, tail_loader):
        import realtime_info_service

        market = pd.DataFrame([
            {
                "trade_date": "20260731",
                "ts_code": "600301.SH",
                "name": "逆势抗跌",
                "industry": "机器人",
                "close": 9.98,
                "high": 10.2,
                "low": 9.8,
                "pct_chg": -0.2,
                "turnover_rate": 4.8,
                "volume_ratio": 1.5,
                "amount": 180_000_000,
            },
        ] + [
            {
                "trade_date": "20260731",
                "ts_code": f"6004{index:02d}.SH",
                "name": f"市场样本{index}",
                "industry": "其他",
                "close": 9.7,
                "high": 10.0,
                "low": 9.5,
                "pct_chg": -3.0,
                "turnover_rate": 3.0,
                "volume_ratio": 1.1,
                "amount": 120_000_000,
            }
            for index in range(20)
        ])
        tail_loader.return_value = MinuteLoadResult(
            pd.DataFrame(), "not_available", []
        )

        def minute_loader(ts_code, start, end, freq, trade_date):
            return MinuteLoadResult(
                build_60min_bars(ts_code, water_macd_kdj_cross_closes()),
                "fixture",
                [],
            )

        with (
            patch(
                "realtime_info_service.macd_parameter_key",
                return_value="macd-test",
            ),
            patch(
                "strategy.load_macd_settings",
                return_value={
                    "fast_period": 5,
                    "slow_period": 34,
                    "signal_period": 5,
                    "version": 1,
                },
            ),
        ):
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
        self.assertEqual(
            result["stocks"][0]["market_resonance_label"],
            "逆势抗跌",
        )

    def test_intraday_cache_key_includes_market_relative_rule_version(self):
        import realtime_info_service

        with (
            patch("realtime_info_service.macd_parameter_key", return_value="macd-test"),
            patch("realtime_info_service._REALTIME_INTRADAY_RESULT_CACHE", {}) as cache,
        ):
            realtime_info_service._build_realtime_intraday_section(
                pd.DataFrame(),
                pd.DataFrame(),
                "20260731",
                datetime(2026, 7, 31, 14, 20),
                limit=10,
                force_refresh=True,
            )

        [cache_key] = list(cache.keys())
        self.assertIn("market-relative-v1", cache_key)

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

    def test_database_result_namespace_changes_with_bottom_rule_version(self):
        with patch(
            "realtime_info_service._REALTIME_BOTTOM_CONSOLIDATION_RULE_VERSION",
            "bottom-test-v1",
        ):
            old_key = _database_realtime_result_key(10)
        with patch(
            "realtime_info_service._REALTIME_BOTTOM_CONSOLIDATION_RULE_VERSION",
            "bottom-test-v2",
        ):
            new_key = _database_realtime_result_key(10)

        self.assertNotEqual(old_key, new_key)

    def test_last_successful_result_is_not_reused_across_bottom_rule_versions(self):
        successful = {
            "trade_date": "20260730",
            "data_trade_date": "20260730",
            "data_as_of": "2026-07-30 14:40:00",
            "intraday": {"stocks": [{"ts_code": "old-rule"}]},
            "overnight": {"stocks": []},
        }
        unavailable = {
            "trade_date": "20260730",
            "data_trade_date": "20260730",
            "data_as_of": "2026-07-30 14:41:00",
            "intraday": {"stocks": []},
            "overnight": {"stocks": []},
        }
        with (
            patch(
                "realtime_info_service._build_realtime_info_uncached",
                side_effect=[successful, unavailable],
            ),
            patch(
                "realtime_info_service._REALTIME_BOTTOM_CONSOLIDATION_RULE_VERSION",
                "bottom-test-v1",
            ),
        ):
            first = build_realtime_info(
                now=datetime(2026, 7, 30, 14, 40),
                limit=10,
                force_refresh=True,
            )

        with (
            patch(
                "realtime_info_service._build_realtime_info_uncached",
                return_value=unavailable,
            ),
            patch(
                "realtime_info_service._REALTIME_BOTTOM_CONSOLIDATION_RULE_VERSION",
                "bottom-test-v2",
            ),
        ):
            second = build_realtime_info(
                now=datetime(2026, 7, 30, 14, 41),
                limit=10,
                force_refresh=True,
            )

        self.assertEqual(first["intraday"]["stocks"][0]["ts_code"], "old-rule")
        self.assertEqual(second["data_status"], "unavailable")
        self.assertEqual(second["intraday"]["stocks"], [])

    def test_empty_force_refresh_falls_back_to_legacy_database_result(self):
        legacy_payload = {
            "trade_date": "20260825",
            "data_trade_date": "20260825",
            "data_as_of": "2026-08-25 15:00:00",
            "data_updated_at": "2026-08-25 16:14:47",
            "intraday": {"stocks": [{"ts_code": "legacy-result"}]},
            "overnight": {"stocks": []},
        }

        def load_cached(_scope, key):
            if "bottom-consolidation" in key:
                return None
            return {
                "payload": legacy_payload,
                "updated_at": "2026-08-25 16:16:19",
            }

        with (
            patch(
                "realtime_info_service._build_realtime_info_uncached",
                return_value={
                    "trade_date": "20260825",
                    "intraday": {"stocks": []},
                    "overnight": {"stocks": []},
                },
            ),
            patch(
                "realtime_info_service.load_result_cache",
                side_effect=load_cached,
            ),
        ):
            result = build_realtime_info(
                now=datetime(2026, 8, 25, 16, 56, 53),
                limit=20,
                force_refresh=True,
            )

        self.assertEqual(result["data_status"], "stale")
        self.assertEqual(result["data_status_label"], "备用缓存")
        self.assertEqual(
            result["intraday"]["stocks"][0]["ts_code"],
            "legacy-result",
        )
        self.assertTrue(result["legacy_rule_cache"])

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
        load_recent_daily.return_value = pd.DataFrame([
            {
                "ts_code": ts_code,
                "trade_date": f"2026{index + 1:04d}",
                "close": 10.0,
            }
            for ts_code in ("600101.SH", "600102.SH")
            for index in range(120)
        ])
        build_realtime_intraday_section.return_value = {
            "trade_date": "20260729",
            "stocks": [{"ts_code": "600101.SH", "name": "实时共振"}],
        }
        build_realtime_tail_premium_monitor.return_value = {
            "trade_date": "20260729",
            "stocks": [{"ts_code": "600102.SH", "name": "隔夜候选", "pct_chg": 4.0}],
        }

        result = build_realtime_info(now=datetime(2026, 7, 29, 14, 36))

        self.assertEqual(result["trade_date"], "20260729")
        self.assertTrue(result["data_current"])
        self.assertEqual(result["intraday"]["stocks"][0]["current_price"], 12.34)
        self.assertEqual(result["intraday"]["stocks"][0]["day_high"], 12.8)
        self.assertEqual(result["overnight"]["stocks"][0]["current_price"], 8.88)
        self.assertEqual(result["overnight"]["stocks"][0]["day_high"], 9.2)
        self.assertFalse(any(
            key.startswith("chip_")
            for key in result["overnight"]["stocks"][0]
        ))
        sync_cached_market_data.assert_called_once_with(force_current=True)
        load_recent_daily.assert_called_once_with("20260729", 120)
        build_realtime_intraday_section.assert_called_once()
        build_realtime_tail_premium_monitor.assert_called_once()
        kwargs = build_realtime_tail_premium_monitor.call_args.kwargs
        self.assertEqual(kwargs["trade_date_override"], "20260729")
        self.assertEqual(
            kwargs["history_override"].groupby("ts_code").size().to_dict(),
            {"600101.SH": 100, "600102.SH": 100},
        )
        self.assertEqual(
            kwargs["source_metadata"]["data_source"],
            "current_snapshot",
        )
        self.assertTrue(callable(kwargs["minute_loader"]))
        self.assertEqual(
            kwargs["market_override"].iloc[0]["close"], 12.34
        )

    def test_realtime_info_filters_unbuyable_overnight_output_after_enrichment(self):
        with (
            patch("realtime_info_service.get_trade_dates", return_value=["20260731"]),
            patch(
                "realtime_info_service._load_realtime_market_inputs",
                return_value=(
                    pd.DataFrame([
                        {"ts_code": "301082.SZ", "close": 10.62, "high": 10.62, "pct_chg": 20.0},
                        {"ts_code": "920510.BJ", "close": 22.48, "high": 23.0, "pct_chg": 6.18},
                        {"ts_code": "600396.SH", "close": 15.51, "high": 15.51, "pct_chg": 10.0},
                        {"ts_code": "600988.SH", "close": 28.0, "high": 28.5, "pct_chg": 6.2},
                    ]),
                    pd.DataFrame(),
                    "20260731",
                    "current_snapshot",
                    True,
                    [],
                ),
            ),
            patch(
                "realtime_info_service._build_realtime_intraday_section",
                return_value={
                    "trade_date": "20260731",
                    "candidate_count": 0,
                    "stocks": [],
                },
            ),
            patch(
                "realtime_info_service.build_realtime_tail_premium_monitor",
                return_value={
                    "trade_date": "20260731",
                    "candidate_count": 4,
                    "failed_count": 1,
                    "stocks": [
                        {"ts_code": "301082.SZ", "name": "久盛电气", "pct_chg": 5.0},
                        {"ts_code": "920510.BJ", "name": "丰光精密", "pct_chg": 6.18},
                        {"ts_code": "600396.SH", "name": "华电辽能", "pct_chg": 6.0, "minute_data_warnings": ["x"]},
                        {"ts_code": "600988.SH", "name": "赤峰黄金", "pct_chg": 6.2},
                    ],
                },
            ),
        ):
            result = build_realtime_info(
                now=datetime(2026, 7, 31, 15, 1),
                limit=20,
                force_refresh=True,
            )

        overnight = result["overnight"]
        self.assertEqual(
            [row["ts_code"] for row in overnight["stocks"]],
            ["600988.SH"],
        )
        self.assertEqual(overnight["candidate_count"], 1)
        self.assertEqual(overnight["failed_count"], 0)

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
                "pct_chg": 4.0,
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
        load_recent_daily.assert_called_once_with("20260728", 120)

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

        with patch(
            "realtime_info_service._build_market_relative_benchmark",
            return_value={
                "market_pct_chg": None,
                "market_state": "fallback",
                "market_state_label": "大盘不可用",
                "sample_count": 0,
            },
        ):
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

    def test_minute_snapshot_pct_uses_pre_close_not_stale_close(self):
        market = pd.DataFrame([{
            "ts_code": "600733.SH",
            "close": 5.74,
            "pre_close": 5.96,
            "pct_chg": -3.69,
        }])
        bars_by_code = {
            "600733.SH": {
                "60m": pd.DataFrame([{
                    "ts_code": "600733.SH",
                    "trade_time": "2026-07-31 15:00:00",
                    "open": 6.0,
                    "high": 6.08,
                    "low": 5.71,
                    "close": 6.02,
                    "vol": 1000,
                    "amount": 6020,
                }])
            }
        }

        result = _apply_minute_snapshots_to_market(
            market,
            bars_by_code,
            "20260731",
        )

        self.assertAlmostEqual(result.iloc[0]["pct_chg"], 1.006711, places=5)

    def test_minute_snapshot_pct_uses_prior_snapshot_close_for_today_minutes(self):
        market = pd.DataFrame([{
            "trade_date": "20260730",
            "ts_code": "600733.SH",
            "close": 5.96,
            "pre_close": 5.74,
            "pct_chg": 3.83,
        }])
        bars_by_code = {
            "600733.SH": {
                "60m": pd.DataFrame([{
                    "ts_code": "600733.SH",
                    "trade_time": "2026-07-31 15:00:00",
                    "open": 6.0,
                    "high": 6.08,
                    "low": 5.71,
                    "close": 6.02,
                    "vol": 1000,
                    "amount": 6020,
                }])
            }
        }

        result = _apply_minute_snapshots_to_market(
            market,
            bars_by_code,
            "20260731",
            base_trade_date="20260730",
        )

        self.assertAlmostEqual(result.iloc[0]["pct_chg"], 1.006711, places=5)

    def test_tail_snapshot_keeps_derived_previous_close_after_price_overlay(self):
        market = pd.DataFrame([{
            "trade_date": "20260731",
            "ts_code": "600733.SH",
            "close": 6.02,
            "pct_chg": 1.006711,
        }])
        bars_by_code = {
            "600733.SH": {
                "60m": pd.DataFrame([{
                    "ts_code": "600733.SH",
                    "trade_time": "2026-07-31 14:30:00",
                    "open": 5.96,
                    "high": 6.08,
                    "low": 5.71,
                    "close": 6.02,
                    "vol": 1000,
                    "amount": 6020,
                }])
            }
        }

        overlaid = _apply_minute_snapshots_to_market(
            market,
            bars_by_code,
            "20260731",
        )
        tail_snapshot = _minute_price_snapshot(
            "600733.SH",
            {
                "tail_1m": pd.DataFrame([{
                    "ts_code": "600733.SH",
                    "trade_time": "2026-07-31 14:59:00",
                    "open": 6.01,
                    "high": 6.03,
                    "low": 6.0,
                    "close": 6.02,
                    "vol": 1000,
                    "amount": 6020,
                }])
            },
            "20260731",
            overlaid.iloc[0].get("previous_close_for_pct"),
        )

        self.assertAlmostEqual(overlaid.iloc[0]["previous_close_for_pct"], 5.96, places=5)
        self.assertAlmostEqual(tail_snapshot["pct_chg"], 1.006711, places=5)

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

    def test_bottom_consolidation_marks_shrinking_stable_base_as_observation(self):
        closes = [10.8, 10.5, 10.25, 10.08, 10.0, 10.04, 9.99, 10.03, 10.01,
                  10.04, 10.02, 10.05, 10.04, 10.06, 10.05, 10.07, 10.06, 10.08, 10.07]
        volumes = [1_600_000] * 9 + [1_300_000, 1_200_000, 1_100_000, 1_000_000,
                   900_000, 850_000, 800_000, 700_000, 650_000, 600_000]
        history = pd.DataFrame([
            {
                "ts_code": "600020.SH", "trade_date": f"202607{index + 1:02d}",
                "close": close, "high": close * 1.01, "low": close * 0.99,
                "pct_chg": 0.1, "vol": volume,
            }
            for index, (close, volume) in enumerate(zip(closes, volumes))
        ])
        market = pd.DataFrame([{
            "ts_code": "600020.SH", "close": 10.09, "high": 10.12, "low": 10.03,
            "pct_chg": 0.2, "vol": 580_000, "volume_ratio": 0.65,
        }])

        row = _attach_bottom_consolidation_fields(history=history, market=market, trade_date="20260731").iloc[0]

        self.assertTrue(row["bottom_consolidation"])
        self.assertEqual(row["resonance_stage"], "observation")
        self.assertEqual(row["resonance_type"], "缩量企稳观察")

    def test_realtime_stage_groups_apply_limit_independently(self):
        rows = [
            {"ts_code": "obs-low", "resonance_stage": "observation", "bottom_setup_score": 60},
            {"ts_code": "obs-high", "resonance_stage": "observation", "bottom_setup_score": 90},
            {"ts_code": "trigger-low", "resonance_stage": "trigger", "bottom_breakout_strength": 1.1},
            {"ts_code": "trigger-high", "resonance_stage": "trigger", "bottom_breakout_strength": 3.2},
            {"ts_code": "launch-low", "resonance_stage": "launch", "intraday_signal_score": 50},
            {"ts_code": "launch-high", "resonance_stage": "launch", "intraday_signal_score": 80},
        ]

        grouped = _group_realtime_stage_rows(rows, limit=1)

        self.assertEqual([row["ts_code"] for row in grouped["observation_stocks"]], ["obs-high"])
        self.assertEqual([row["ts_code"] for row in grouped["trigger_stocks"]], ["trigger-high"])
        self.assertEqual([row["ts_code"] for row in grouped["launch_stocks"]], ["launch-high"])

    def test_stage_groups_prioritize_buildable_chip_signal_before_original_score(self):
        rows = [
            {
                "ts_code": "600001.SH",
                "resonance_stage": "observation",
                "bottom_setup_score": 99,
                "chip_build_position": False,
                "chip_washout_score": 60,
            },
            {
                "ts_code": "600002.SH",
                "resonance_stage": "observation",
                "bottom_setup_score": 70,
                "chip_build_position": True,
                "chip_washout_score": 82,
            },
        ]

        result = _group_realtime_stage_rows(rows, 20)

        self.assertEqual(
            result["observation_stocks"][0]["ts_code"],
            "600002.SH",
        )

    @patch("realtime_info_service.attach_chip_peak_fields")
    def test_chip_enrichment_only_sends_supported_stage_rows(self, attach):
        source = [
            {"ts_code": "600001.SH", "resonance_stage": "observation"},
            {"ts_code": "600002.SH", "resonance_stage": "trigger"},
            {"ts_code": "600003.SH", "resonance_stage": "launch"},
            {"ts_code": "600004.SH", "resonance_stage": "regular"},
        ]
        attach.return_value = (
            [
                {**row, "chip_washout_score": 80.0}
                for row in source[:3]
            ],
            [],
        )

        result, warnings = _attach_realtime_chip_fields(
            source,
            pd.DataFrame(),
            "20260730",
        )

        sent_rows = attach.call_args.args[0]
        self.assertEqual(
            [row["ts_code"] for row in sent_rows],
            ["600001.SH", "600002.SH", "600003.SH"],
        )
        self.assertEqual([row["ts_code"] for row in result], [row["ts_code"] for row in source])
        self.assertNotIn("chip_washout_score", result[3])
        self.assertEqual(warnings, [])

    def test_database_cache_key_includes_chip_peak_rule_version(self):
        self.assertIn(
            "chip-peak-washout-v1",
            _database_realtime_result_key(20),
        )

    def test_stable_base_becomes_first_bullish_trigger_on_breakout(self):
        closes = [10.8, 10.5, 10.25, 10.08, 10.0, 10.04, 9.99, 10.03, 10.01,
                  10.04, 10.02, 10.05, 10.04, 10.06, 10.05, 10.07, 10.06, 10.08, 10.07]
        volumes = [1_600_000] * 9 + [1_300_000, 1_200_000, 1_100_000, 1_000_000,
                   900_000, 850_000, 800_000, 700_000, 650_000, 600_000]
        history = pd.DataFrame([
            {
                "ts_code": "600021.SH", "trade_date": f"202607{index + 1:02d}",
                "close": close, "high": close * 1.01, "low": close * 0.99,
                "pct_chg": 0.1, "vol": volume,
            }
            for index, (close, volume) in enumerate(zip(closes, volumes))
        ])
        market = pd.DataFrame([{
            "ts_code": "600021.SH", "close": 10.30, "high": 10.35, "low": 10.05,
            "pct_chg": 2.28, "vol": 720_000, "volume_ratio": 1.25,
        }])

        row = _attach_bottom_consolidation_fields(market, history, "20260731").iloc[0]

        self.assertEqual(row["resonance_stage"], "trigger")
        self.assertEqual(row["resonance_type"], "底部首阳触发")
        self.assertGreater(row["bottom_breakout_strength"], 0)


if __name__ == "__main__":
    unittest.main()
