import unittest
from datetime import datetime
import math
from unittest.mock import patch

import pandas as pd

from realtime_market_source import MinuteLoadResult
from overnight_monitor_service import (
    _FAILED_MINUTE_BAR_CACHE,
    _MINUTE_BAR_CACHE,
    _OVERNIGHT_RESULT_CACHE,
    build_overnight_monitor,
    _json_safe,
    _overnight_labels,
    _cached_minute_bars,
    _opening_auction_signal,
    _sector_representative_universe,
    _mask_unavailable_tail_fields,
    _realtime_end_datetime,
    _sector_60m_signal_from_bars,
)
from tests.test_advantage_stock_scoring import (
    build_60min_bars,
    build_tail_1min_bars,
    water_macd_kdj_continuation_closes,
)


def market_fixture():
    rows = []
    for index, spec in enumerate([
        ("600101.SH", "尾盘抢筹", 5.2, 5.5, 1.8, 260_000),
        ("600102.SH", "涨停过热", 9.9, 4.5, 2.8, 420_000),
        ("600103.SH", "尾盘回落", 4.8, 6.0, 2.2, 300_000),
        ("600104.SH", "量比不足", 4.2, 5.0, 1.1, 280_000),
    ]):
        rows.append({
            "ts_code": spec[0],
            "name": spec[1],
            "industry": "酒店餐饮",
            "close": 10 + index,
            "pct_chg": spec[2],
            "turnover_rate": spec[3],
            "volume_ratio": spec[4],
            "amount": spec[5],
            "vol": 1000,
            "total_mv": 1_000_000,
        })
    return pd.DataFrame(rows)


class OvernightMonitorServiceTests(unittest.TestCase):
    def setUp(self):
        _MINUTE_BAR_CACHE.clear()
        _FAILED_MINUTE_BAR_CACHE.clear()
        _OVERNIGHT_RESULT_CACHE.clear()

    @patch("overnight_monitor_service.time.sleep", return_value=None)
    @patch("overnight_monitor_service.get_stock_minute_bars", side_effect=RuntimeError("您请求速度过快"))
    def test_failed_minute_request_is_throttled_briefly(self, get_stock_minute_bars, _sleep):
        with self.assertRaisesRegex(RuntimeError, "请求速度过快"):
            _cached_minute_bars("600001.SH", "2026-07-29 09:30:00", "2026-07-29 14:35:00", "1min")
        with self.assertRaisesRegex(RuntimeError, "请求速度过快"):
            _cached_minute_bars("600001.SH", "2026-07-29 09:30:00", "2026-07-29 14:35:00", "1min")

        self.assertEqual(get_stock_minute_bars.call_count, 1)

    def test_realtime_end_datetime_uses_one_minute_lag_during_trading(self):
        self.assertEqual(
            _realtime_end_datetime("20260729", now=datetime(2026, 7, 29, 14, 36, 20)),
            "2026-07-29 14:35:00",
        )

    def test_realtime_end_datetime_uses_close_after_market(self):
        self.assertEqual(
            _realtime_end_datetime("20260729", now=datetime(2026, 7, 29, 15, 5, 0)),
            "2026-07-29 15:00:00",
        )

    @patch("overnight_monitor_service._load_overnight_inputs")
    def test_runtime_overrides_bypass_default_input_loader(self, default_loader):
        def fake_loader(ts_code, start_datetime, end_datetime, freq, trade_date):
            bars = (
                build_60min_bars(
                    ts_code, water_macd_kdj_continuation_closes()
                )
                if freq == "60min"
                else build_tail_1min_bars(
                    ts_code,
                    [10, 10, 10, 10, 10, 10.1, 10.2],
                    [1000, 1000, 1000, 1000, 1000, 2400, 3200],
                )
            )
            return MinuteLoadResult(bars, "eastmoney_fallback", [])

        result = build_overnight_monitor(
            limit=10,
            max_fetch=10,
            now=datetime(2026, 7, 30, 14, 50),
            market_override=market_fixture(),
            history_override=pd.DataFrame(),
            trade_date_override="20260730",
            minute_loader=fake_loader,
            source_metadata={
                "latest_trade_date": "20260730",
                "data_current": True,
                "data_source": "eastmoney_snapshot_fallback",
            },
        )

        default_loader.assert_not_called()
        self.assertEqual(
            result["data_source"], "eastmoney_snapshot_fallback"
        )
        self.assertIn(
            "eastmoney_fallback", result["minute_data_sources"]
        )

    def test_preloaded_candidate_60m_bars_are_not_loaded_twice(self):
        calls = {}

        def fake_loader(ts_code, start_datetime, end_datetime, freq, trade_date):
            key = (ts_code, freq)
            calls[key] = calls.get(key, 0) + 1
            bars = (
                build_60min_bars(
                    ts_code, water_macd_kdj_continuation_closes()
                )
                if freq == "60min"
                else build_tail_1min_bars(
                    ts_code,
                    [10, 10, 10, 10, 10, 10.1, 10.2],
                    [1000, 1000, 1000, 1000, 1000, 2400, 3200],
                )
            )
            return MinuteLoadResult(bars, "tushare", [])

        build_overnight_monitor(
            limit=10,
            max_fetch=10,
            now=datetime(2026, 7, 30, 14, 50),
            market_override=market_fixture(),
            history_override=pd.DataFrame(),
            trade_date_override="20260730",
            minute_loader=fake_loader,
        )

        sixty_minute_counts = [
            count for (code, freq), count in calls.items() if freq == "60min"
        ]
        self.assertTrue(sixty_minute_counts)
        self.assertTrue(all(count == 1 for count in sixty_minute_counts))

    def test_sector_representatives_use_full_market_for_candidate_industries(self):
        market = pd.DataFrame([
            {"ts_code": "600101.SH", "industry": "机器人", "amount": 100_000, "volume_ratio": 1.5, "pct_chg": 3},
            {"ts_code": "600201.SH", "industry": "机器人", "amount": 900_000, "volume_ratio": 2.6, "pct_chg": 4},
            {"ts_code": "600202.SH", "industry": "机器人", "amount": 800_000, "volume_ratio": 2.4, "pct_chg": 2},
            {"ts_code": "600301.SH", "industry": "银行", "amount": 1_000_000, "volume_ratio": 3.0, "pct_chg": 5},
        ])
        candidates = pd.DataFrame([{"ts_code": "600101.SH", "industry": "机器人"}])

        result = _sector_representative_universe(market, candidates, per_sector=2)

        self.assertEqual(set(result["industry"]), {"机器人"})
        self.assertIn("600201.SH", set(result["ts_code"]))
        self.assertIn("600202.SH", set(result["ts_code"]))
        self.assertIn("600101.SH", set(result["ts_code"]))

    def test_tail_fields_are_hidden_until_tail_phase_but_opening_auction_is_available_after_open(self):
        signal = {
            "tail_strength_score": 88,
            "tail_return_after_1430": 0.5,
            "tail_auction_return": 0.2,
            "tail_volume_ratio": 1.8,
            "tail_close_position": 0.9,
        }

        before_tail = _mask_unavailable_tail_fields(signal, "2026-07-29 14:29:00")
        before_auction = _mask_unavailable_tail_fields(signal, "2026-07-29 14:40:00")

        self.assertIsNone(before_tail["tail_return_after_1430"])
        self.assertFalse(before_tail["tail_after_1430_available"])
        self.assertEqual(before_tail["tail_auction_return"], 0.2)
        self.assertEqual(before_auction["tail_auction_return"], 0.2)
        self.assertTrue(before_auction["tail_after_1430_available"])
        self.assertTrue(before_auction["tail_auction_available"])

    def test_opening_auction_signal_uses_open_against_previous_close_after_930(self):
        stock = {"open": 10.2, "pre_close": 10.0}

        signal = _opening_auction_signal(stock, "2026-07-29 09:31:00")

        self.assertTrue(signal["tail_auction_available"])
        self.assertEqual(signal["opening_auction_return"], 2.0)
        self.assertEqual(signal["tail_auction_return"], 2.0)

    @patch("overnight_monitor_service.get_trade_dates", side_effect=RuntimeError("offline"))
    @patch("overnight_monitor_service.get_stock_minute_bars")
    @patch("overnight_monitor_service.get_cached_scan_inputs")
    def test_overnight_monitor_prefers_buyable_tail_accumulation_not_limit_up(
        self,
        get_cached_scan_inputs,
        get_stock_minute_bars,
        _get_trade_dates,
    ):
        get_cached_scan_inputs.return_value = (market_fixture(), pd.DataFrame(), {"data_trade_date": "20260728"})

        def fake_bars(ts_code, start_datetime, end_datetime, freq="60min"):
            if freq == "60min":
                return build_60min_bars(ts_code, water_macd_kdj_continuation_closes())
            if ts_code == "600103.SH":
                return build_tail_1min_bars(
                    ts_code,
                    [10, 10, 10, 10, 10, 10, 9.98, 9.96, 9.94, 9.92, 9.9, 9.88, 9.84],
                    [3000, 3000, 3000, 3000, 3000, 3000, 3200, 3400, 3800, 4200, 4600, 5000, 7000],
                )
            return build_tail_1min_bars(
                ts_code,
                [10, 10, 10, 10, 10, 10.02, 10.05, 10.08, 10.12, 10.16, 10.2, 10.24, 10.32],
                [3000, 3000, 3000, 3000, 3000, 4200, 4300, 4500, 4600, 4800, 5200, 5400, 8000],
            )

        get_stock_minute_bars.side_effect = fake_bars

        result = build_overnight_monitor(limit=10, now=datetime(2026, 7, 28, 14, 50))

        codes = [row["ts_code"] for row in result["stocks"]]
        self.assertEqual(codes[0], "600101.SH")
        self.assertNotIn("600102.SH", codes)
        self.assertNotIn("600104.SH", codes)
        self.assertEqual(result["stocks"][0]["overnight_bias"], "尾盘透支风险")
        self.assertEqual(result["stocks"][0]["buyable_tail_signal"], "观察")
        self.assertTrue(result["stocks"][0]["tail_auction_available"])
        self.assertEqual(result["refresh_interval_seconds"], 30)
        self.assertTrue(result["auto_refresh_enabled"])

    @patch("overnight_monitor_service.get_trade_dates", side_effect=RuntimeError("offline"))
    @patch("overnight_monitor_service.get_stock_minute_bars")
    @patch("overnight_monitor_service.get_cached_scan_inputs")
    def test_overnight_monitor_keeps_partial_results_when_tushare_rate_limits(
        self,
        get_cached_scan_inputs,
        get_stock_minute_bars,
        _get_trade_dates,
    ):
        get_cached_scan_inputs.return_value = (market_fixture(), pd.DataFrame(), {"data_trade_date": "20260728"})

        def fake_bars(ts_code, start_datetime, end_datetime, freq="60min"):
            if ts_code == "600103.SH":
                raise RuntimeError("您请求速度过快")
            if freq == "60min":
                return build_60min_bars(ts_code, water_macd_kdj_continuation_closes())
            return build_tail_1min_bars(
                ts_code,
                [10, 10, 10, 10, 10, 10.02, 10.05, 10.08, 10.12, 10.16, 10.2, 10.24, 10.32],
                [3000, 3000, 3000, 3000, 3000, 4200, 4300, 4500, 4600, 4800, 5200, 5400, 8000],
            )

        get_stock_minute_bars.side_effect = fake_bars

        result = build_overnight_monitor(limit=10, max_fetch=10, now=datetime(2026, 7, 28, 14, 50))

        self.assertIn("600101.SH", [row["ts_code"] for row in result["stocks"]])
        self.assertEqual(result["failed_count"], 1)
        self.assertIn("600103.SH", result["warnings"][0])
        self.assertIn("请求速度过快", result["warnings"][0])

    @patch("overnight_monitor_service.get_trade_dates", side_effect=RuntimeError("offline"))
    @patch("overnight_monitor_service.rank_sector_potential")
    @patch("overnight_monitor_service.get_stock_minute_bars")
    @patch("overnight_monitor_service.get_cached_scan_inputs")
    def test_overnight_monitor_adds_sector_leaders_to_candidate_pool(
        self,
        get_cached_scan_inputs,
        get_stock_minute_bars,
        rank_sector_potential,
        _get_trade_dates,
    ):
        market = pd.concat([
            market_fixture(),
            pd.DataFrame([{
                "ts_code": "600105.SH",
                "name": "板块龙头",
                "industry": "机器人",
                "close": 15,
                "pct_chg": 1.2,
                "turnover_rate": 1.2,
                "volume_ratio": 1.0,
                "amount": 90_000,
                "vol": 1000,
                "total_mv": 1_000_000,
            }]),
        ], ignore_index=True)
        get_cached_scan_inputs.return_value = (market, pd.DataFrame(), {"data_trade_date": "20260728"})
        rank_sector_potential.return_value = pd.DataFrame([{
            "industry_name": "机器人",
            "leader_stocks": [{"ts_code": "600105.SH", "name": "板块龙头", "leader_score": 88}],
        }])

        def fake_bars(ts_code, start_datetime, end_datetime, freq="60min"):
            if freq == "60min":
                return build_60min_bars(ts_code, water_macd_kdj_continuation_closes())
            return build_tail_1min_bars(
                ts_code,
                [10, 10, 10, 10, 10, 10.02, 10.05, 10.08, 10.12, 10.16, 10.2, 10.24, 10.32],
                [3000, 3000, 3000, 3000, 3000, 4200, 4300, 4500, 4600, 4800, 5200, 5400, 8000],
            )

        get_stock_minute_bars.side_effect = fake_bars

        result = build_overnight_monitor(limit=10, max_fetch=2, now=datetime(2026, 7, 28, 14, 50))

        by_code = {row["ts_code"]: row for row in result["stocks"]}
        self.assertLessEqual(len(result["stocks"]), 10)
        self.assertIn("600105.SH", by_code)
        self.assertEqual(by_code["600105.SH"]["overnight_pool_source"], "龙头")
        self.assertTrue(by_code["600105.SH"]["overnight_sector_leader"])

    @patch("overnight_monitor_service.rank_sector_potential", return_value=pd.DataFrame())
    @patch("overnight_monitor_service.get_stock_minute_bars")
    @patch("overnight_monitor_service.load_recent_daily")
    @patch("overnight_monitor_service.load_market_snapshot")
    @patch("overnight_monitor_service.sync_cached_market_data")
    @patch("overnight_monitor_service.get_trade_dates", return_value=["20260729"])
    def test_overnight_monitor_uses_current_trade_date_snapshot_during_trading(
        self,
        _get_trade_dates,
        sync_cached_market_data,
        load_market_snapshot,
        load_recent_daily,
        get_stock_minute_bars,
        _rank_sector_potential,
    ):
        today_market = market_fixture().copy()
        today_market["trade_date"] = "20260729"
        today_market.loc[today_market["ts_code"] == "600101.SH", "pct_chg"] = 4.2
        sync_cached_market_data.return_value = {"data_trade_date": "20260729", "cache_updated": True, "cache_warnings": []}
        load_market_snapshot.return_value = today_market
        load_recent_daily.return_value = pd.DataFrame()

        def fake_bars(ts_code, start_datetime, end_datetime, freq="60min"):
            self.assertIn("2026-07-29", end_datetime)
            if freq == "60min":
                return build_60min_bars(ts_code, water_macd_kdj_continuation_closes())
            return build_tail_1min_bars(
                ts_code,
                [10, 10, 10, 10, 10, 10.02, 10.05, 10.08, 10.12, 10.16, 10.2, 10.24, 10.32],
                [3000, 3000, 3000, 3000, 3000, 4200, 4300, 4500, 4600, 4800, 5200, 5400, 8000],
            )

        get_stock_minute_bars.side_effect = fake_bars

        result = build_overnight_monitor(limit=10, now=datetime(2026, 7, 29, 14, 36))

        self.assertEqual(result["trade_date"], "20260729")
        self.assertEqual(result["latest_trade_date"], "20260729")
        self.assertTrue(result["data_current"])
        sync_cached_market_data.assert_called_once_with(force_current=True)
        load_market_snapshot.assert_called_once_with("20260729")

    @patch("overnight_monitor_service.rank_sector_potential", return_value=pd.DataFrame())
    @patch("overnight_monitor_service.get_stock_minute_bars")
    @patch("overnight_monitor_service.load_recent_daily", return_value=pd.DataFrame())
    @patch("overnight_monitor_service.load_market_snapshot")
    @patch("overnight_monitor_service.sync_cached_market_data", return_value={"data_trade_date": "20260729"})
    @patch("overnight_monitor_service.get_trade_dates", return_value=["20260729"])
    def test_overnight_monitor_reuses_recent_result_snapshot(
        self,
        _get_trade_dates,
        sync_cached_market_data,
        load_market_snapshot,
        _load_recent_daily,
        get_stock_minute_bars,
        _rank_sector_potential,
    ):
        today_market = market_fixture().copy()
        today_market["trade_date"] = "20260729"
        load_market_snapshot.return_value = today_market

        def fake_bars(ts_code, start_datetime, end_datetime, freq="60min"):
            if freq == "60min":
                return build_60min_bars(ts_code, water_macd_kdj_continuation_closes())
            return build_tail_1min_bars(
                ts_code,
                [10, 10, 10, 10, 10, 10.02, 10.05, 10.08, 10.12, 10.16, 10.2, 10.24, 10.32],
                [3000, 3000, 3000, 3000, 3000, 4200, 4300, 4500, 4600, 4800, 5200, 5400, 8000],
            )

        get_stock_minute_bars.side_effect = fake_bars

        first = build_overnight_monitor(limit=10, now=datetime(2026, 7, 29, 14, 36))
        second = build_overnight_monitor(limit=10, now=datetime(2026, 7, 29, 14, 36, 20))

        self.assertEqual(second["stocks"], first["stocks"])
        self.assertTrue(second["result_cache_hit"])
        sync_cached_market_data.assert_called_once()

    def test_json_safe_converts_non_finite_numbers_to_null(self):
        payload = _json_safe({
            "score": float("nan"),
            "items": [{"ratio": float("inf")}, {"ok": 1.25}],
            "numpy_value": pd.Series([math.nan]).iloc[0],
        })

        self.assertIsNone(payload["score"])
        self.assertIsNone(payload["items"][0]["ratio"])
        self.assertIsNone(payload["numpy_value"])
        self.assertEqual(payload["items"][1]["ok"], 1.25)

    def test_overnight_labels_downgrades_overextended_tail_pull(self):
        signal = {
            "tail_strength_score": 100,
            "tail_return_after_1430": 1.69,
            "tail_auction_return": 0.0,
            "tail_close_position": 1.0,
            "macd_above_zero_60m": True,
            "kdj_bullish_60m": True,
            "next_day_bias": "高开偏强",
        }

        overnight_bias, buyable_tail_signal, reason, score = _overnight_labels(signal, pct_chg=5.23, volume_ratio=4.55)

        self.assertEqual(overnight_bias, "尾盘透支风险")
        self.assertEqual(buyable_tail_signal, "观察")
        self.assertIn("尾盘拉升偏急", reason)
        self.assertLess(score, 75)

    def test_overnight_labels_keeps_confirmed_moderate_tail_accumulation_buyable(self):
        signal = {
            "tail_strength_score": 100,
            "tail_return_after_1430": 0.82,
            "tail_auction_return": 0.18,
            "tail_close_position": 0.92,
            "macd_above_zero_60m": True,
            "kdj_bullish_60m": True,
            "next_day_bias": "高开偏强",
        }

        overnight_bias, buyable_tail_signal, reason, score = _overnight_labels(signal, pct_chg=4.6, volume_ratio=2.6)

        self.assertEqual(overnight_bias, "隔夜高开优先")
        self.assertEqual(buyable_tail_signal, "尾盘可买")
        self.assertIn("集合竞价继续确认", reason)
        self.assertGreaterEqual(score, 82)

    def test_sector_macd_water_golden_cross_boosts_moderate_tail_signal(self):
        signal = {
            "tail_strength_score": 85,
            "tail_return_after_1430": 0.82,
            "tail_auction_return": 0.18,
            "tail_close_position": 0.92,
            "macd_above_zero_60m": True,
            "kdj_bullish_60m": True,
            "next_day_bias": "高开偏强",
            "sector_macd_bonus": 24,
            "sector_macd_status": "板块60分MACD水上金叉",
        }

        overnight_bias, buyable_tail_signal, reason, score = _overnight_labels(signal, pct_chg=4.6, volume_ratio=2.6)

        self.assertEqual(overnight_bias, "隔夜高开优先")
        self.assertEqual(buyable_tail_signal, "尾盘可买")
        self.assertIn("板块60分MACD水上金叉", reason)
        self.assertGreaterEqual(score, 82)

    def test_sector_60m_signal_builds_industry_trend_bonus(self):
        market = pd.DataFrame([
            {"ts_code": "600201.SH", "industry": "机器人"},
            {"ts_code": "600202.SH", "industry": "机器人"},
        ])
        bars_by_code = {
            "600201.SH": build_60min_bars("600201.SH", water_macd_kdj_continuation_closes()),
            "600202.SH": build_60min_bars("600202.SH", [close * 1.01 for close in water_macd_kdj_continuation_closes()]),
        }

        result = _sector_60m_signal_from_bars(market, bars_by_code)

        self.assertIn("机器人", result)
        self.assertTrue(result["机器人"]["sector_macd_trending_up"])
        self.assertGreater(result["机器人"]["sector_macd_bonus"], 0)

    def test_sector_60m_signal_excludes_downward_macd_or_kdj(self):
        market = pd.DataFrame([
            {"ts_code": "600301.SH", "industry": "消费电子"},
            {"ts_code": "600302.SH", "industry": "消费电子"},
        ])
        closes = [12 + index * 0.08 for index in range(30)] + [14.4, 14.2, 14.0, 13.8, 13.55, 13.25, 12.95, 12.7, 12.45, 12.2]
        bars_by_code = {
            "600301.SH": build_60min_bars("600301.SH", closes),
            "600302.SH": build_60min_bars("600302.SH", [close * 1.02 for close in closes]),
        }

        result = _sector_60m_signal_from_bars(market, bars_by_code)

        self.assertIn("消费电子", result)
        self.assertTrue(result["消费电子"]["sector_60m_excluded"])
        self.assertTrue(result["消费电子"]["sector_macd_down_60m"] or result["消费电子"]["sector_kdj_down_60m"])


if __name__ == "__main__":
    unittest.main()
