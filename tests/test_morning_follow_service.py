import unittest
from datetime import datetime
from unittest.mock import patch

import pandas as pd

from morning_follow_service import (
    _EASTMONEY_MORNING_CACHE,
    _MORNING_FOLLOW_RESULT_CACHE,
    _SINA_MORNING_CACHE,
    _daily_follow_candidates,
    _eastmoney_morning_bars,
    _eastmoney_secid,
    _fill_effective_volume_ratio,
    _follow_sort_key,
    _load_follow_inputs,
    _morning_confirmation,
    _morning_bars_for_candidate,
    _morning_follow_phase,
    _parse_eastmoney_trends,
    _parse_sina_klines,
    _select_candidate_trade_date,
    _setup_row,
    build_morning_follow_monitor,
)


def morning_bars(closes, opens=None, volumes=None):
    opens = opens or closes
    volumes = volumes or [1000] * len(closes)
    return pd.DataFrame([
        {
            "ts_code": "600101.SH",
            "trade_time": f"2026-07-30 09:{30 + index:02d}:00",
            "open": opens[index],
            "high": max(opens[index], close),
            "low": min(opens[index], close),
            "close": close,
            "vol": volumes[index],
        }
        for index, close in enumerate(closes)
    ])


class MorningFollowServiceTests(unittest.TestCase):
    def setUp(self):
        _EASTMONEY_MORNING_CACHE.clear()
        _MORNING_FOLLOW_RESULT_CACHE.clear()
        _SINA_MORNING_CACHE.clear()

    def test_result_cache_key_changes_with_macd_configuration(self):
        import morning_follow_service

        metadata = {
            "candidate_trade_date": "20260729",
            "confirmation_trade_date": "20260730",
        }
        with patch(
            "morning_follow_service.macd_parameter_key",
            side_effect=["macd-5-34-5-v1", "macd-6-35-6-v2"],
        ):
            first = morning_follow_service._morning_result_cache_key(
                10, 30, metadata, "早盘确认"
            )
            second = morning_follow_service._morning_result_cache_key(
                10, 30, metadata, "早盘确认"
            )

        self.assertNotEqual(first, second)

    def test_eastmoney_secid_supports_sh_and_sz_only(self):
        self.assertEqual(_eastmoney_secid("600298.SH"), "1.600298")
        self.assertEqual(_eastmoney_secid("300910.SZ"), "0.300910")
        self.assertIsNone(_eastmoney_secid("830001.BJ"))
        self.assertIsNone(_eastmoney_secid("bad-code"))

    def test_parse_eastmoney_trends_filters_date_window_and_future_rows(self):
        payload = {
            "data": {
                "trends": [
                    (
                        "2026-07-29 09:31,9.90,9.91,9.92,9.89,"
                        "100,99000.00,9.90"
                    ),
                    (
                        "2026-07-30 09:30,10.10,10.10,10.10,10.10,"
                        "100,101000.00,10.10"
                    ),
                    (
                        "2026-07-30 09:31,10.10,10.12,10.13,10.09,"
                        "200,202000.00,10.11"
                    ),
                    (
                        "2026-07-30 09:37,10.20,10.21,10.22,10.19,"
                        "150,153000.00,10.16"
                    ),
                ],
            },
        }

        result = _parse_eastmoney_trends(
            payload,
            "600101.SH",
            "20260730",
            datetime(2026, 7, 30, 9, 36),
        )

        self.assertEqual(
            result["trade_time"].dt.strftime("%H:%M").tolist(),
            ["09:30", "09:31"],
        )
        self.assertEqual(
            result["ts_code"].tolist(),
            ["600101.SH", "600101.SH"],
        )
        self.assertEqual(result.iloc[1]["close"], 10.12)
        self.assertEqual(result.iloc[1]["vol"], 200)

    def test_parse_eastmoney_trends_rejects_invalid_payload_rows(self):
        payload = {
            "data": {
                "trends": [
                    "bad-row",
                    (
                        "2026-07-30 09:31,broken,10.12,10.13,10.09,"
                        "200,202000.00,10.11"
                    ),
                ],
            },
        }

        result = _parse_eastmoney_trends(
            payload,
            "600101.SH",
            "20260730",
            datetime(2026, 7, 30, 9, 36),
        )

        self.assertTrue(result.empty)

    def test_parse_sina_klines_filters_confirmation_window(self):
        text = (
            "/* redirect guard */\nvar x=(["
            '{"day":"2026-07-29 09:31:00","open":"9.90",'
            '"high":"9.92","low":"9.89","close":"9.91",'
            '"volume":"1000","amount":"9900"},'
            '{"day":"2026-07-30 09:30:00","open":"10.10",'
            '"high":"10.10","low":"10.10","close":"10.10",'
            '"volume":"1000","amount":"10100"},'
            '{"day":"2026-07-30 09:31:00","open":"10.10",'
            '"high":"10.13","low":"10.09","close":"10.12",'
            '"volume":"2000","amount":"20200"},'
            '{"day":"2026-07-30 09:37:00","open":"10.20",'
            '"high":"10.22","low":"10.19","close":"10.21",'
            '"volume":"1500","amount":"15300"}'
            "]);"
        )

        result = _parse_sina_klines(
            text,
            "600101.SH",
            "20260730",
            datetime(2026, 7, 30, 9, 36),
        )

        self.assertEqual(
            result["trade_time"].dt.strftime("%H:%M").tolist(),
            ["09:30", "09:31"],
        )
        self.assertEqual(result.iloc[1]["close"], 10.12)
        self.assertEqual(result.iloc[1]["vol"], 2000)

    @patch("morning_follow_service._eastmoney_morning_bars")
    @patch("morning_follow_service._cached_minute_bars")
    def test_morning_loader_keeps_usable_tushare_as_primary(
        self,
        cached_minutes,
        eastmoney_minutes,
    ):
        cached_minutes.return_value = morning_bars(
            [10.1, 10.12, 10.14, 10.16, 10.18, 10.2]
        )

        bars, source, reason = _morning_bars_for_candidate(
            "600101.SH",
            "20260730",
            datetime(2026, 7, 30, 9, 36),
        )

        self.assertEqual(source, "tushare")
        self.assertIsNone(reason)
        self.assertEqual(len(bars), 6)
        eastmoney_minutes.assert_not_called()

    @patch("morning_follow_service._eastmoney_morning_bars")
    @patch("morning_follow_service._cached_minute_bars")
    def test_morning_loader_falls_back_when_tushare_is_empty(
        self,
        cached_minutes,
        eastmoney_minutes,
    ):
        cached_minutes.return_value = pd.DataFrame()
        eastmoney_minutes.return_value = (
            morning_bars([10.1, 10.12, 10.14, 10.16, 10.18, 10.2]),
            None,
        )

        bars, source, reason = _morning_bars_for_candidate(
            "600101.SH",
            "20260730",
            datetime(2026, 7, 30, 9, 36),
        )

        self.assertEqual(source, "eastmoney_fallback")
        self.assertIsNone(reason)
        self.assertEqual(len(bars), 6)

    @patch("morning_follow_service._eastmoney_morning_bars")
    @patch("morning_follow_service._cached_minute_bars")
    def test_morning_loader_falls_back_when_tushare_raises(
        self,
        cached_minutes,
        eastmoney_minutes,
    ):
        cached_minutes.side_effect = RuntimeError("Tushare暂不可用")
        eastmoney_minutes.return_value = (
            morning_bars([10.1, 10.12, 10.14, 10.16, 10.18, 10.2]),
            None,
        )

        bars, source, reason = _morning_bars_for_candidate(
            "600101.SH",
            "20260730",
            datetime(2026, 7, 30, 9, 36),
        )

        self.assertEqual(source, "eastmoney_fallback")
        self.assertIsNone(reason)
        self.assertEqual(len(bars), 6)

    @patch("morning_follow_service._eastmoney_morning_bars")
    @patch("morning_follow_service._cached_minute_bars")
    def test_morning_loader_falls_back_when_tushare_only_has_old_date(
        self,
        cached_minutes,
        eastmoney_minutes,
    ):
        old = morning_bars([10.1] * 6)
        old["trade_time"] = old["trade_time"].str.replace(
            "2026-07-30",
            "2026-07-29",
        )
        cached_minutes.return_value = old
        eastmoney_minutes.return_value = (
            morning_bars([10.1, 10.12, 10.14, 10.16, 10.18, 10.2]),
            None,
        )

        bars, source, reason = _morning_bars_for_candidate(
            "600101.SH",
            "20260730",
            datetime(2026, 7, 30, 9, 36),
        )

        self.assertEqual(source, "eastmoney_fallback")
        self.assertIsNone(reason)
        self.assertEqual(len(bars), 6)

    @patch("morning_follow_service._sina_morning_bars")
    @patch("morning_follow_service._eastmoney_morning_bars")
    @patch("morning_follow_service._cached_minute_bars")
    def test_morning_loader_reports_both_sources_unavailable(
        self,
        cached_minutes,
        eastmoney_minutes,
        sina_minutes,
    ):
        cached_minutes.return_value = pd.DataFrame()
        eastmoney_minutes.return_value = (
            pd.DataFrame(),
            "东方财富备用源请求超时",
        )
        sina_minutes.return_value = (
            pd.DataFrame(),
            "新浪财经备用源请求超时",
        )

        bars, source, reason = _morning_bars_for_candidate(
            "600101.SH",
            "20260730",
            datetime(2026, 7, 30, 9, 36),
        )

        self.assertTrue(bars.empty)
        self.assertEqual(source, "unavailable")
        self.assertEqual(
            reason,
            (
                "Tushare当日分钟为空；东方财富备用源请求超时；"
                "新浪财经备用源请求超时"
            ),
        )

    @patch("morning_follow_service._sina_morning_bars")
    @patch("morning_follow_service._eastmoney_morning_bars")
    @patch("morning_follow_service._cached_minute_bars")
    def test_morning_loader_uses_sina_when_eastmoney_fails(
        self,
        cached_minutes,
        eastmoney_minutes,
        sina_minutes,
    ):
        cached_minutes.return_value = pd.DataFrame()
        eastmoney_minutes.return_value = (
            pd.DataFrame(),
            "东方财富备用源连接失败",
        )
        sina_minutes.return_value = (
            morning_bars([10.1, 10.12, 10.14, 10.16, 10.18, 10.2]),
            None,
        )

        bars, source, reason = _morning_bars_for_candidate(
            "600101.SH",
            "20260730",
            datetime(2026, 7, 30, 9, 36),
        )

        self.assertEqual(source, "sina_fallback")
        self.assertIsNone(reason)
        self.assertEqual(len(bars), 6)

    @patch("morning_follow_service.subprocess.run")
    def test_eastmoney_morning_bars_uses_safe_curl_and_short_cache(
        self,
        run,
    ):
        run.return_value.stdout = (
            '{"data":{"trends":['
            '"2026-07-30 09:30,10.10,10.10,10.10,10.10,'
            '100,101000.00,10.10",'
            '"2026-07-30 09:31,10.10,10.12,10.13,10.09,'
            '200,202000.00,10.11"'
            ']}}'
        )

        first, first_error = _eastmoney_morning_bars(
            "600101.SH",
            "20260730",
            datetime(2026, 7, 30, 9, 36),
        )
        second, second_error = _eastmoney_morning_bars(
            "600101.SH",
            "20260730",
            datetime(2026, 7, 30, 9, 36, 10),
        )

        self.assertEqual(len(first), 2)
        self.assertEqual(len(second), 2)
        self.assertIsNone(first_error)
        self.assertIsNone(second_error)
        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(
            command[:4],
            ["curl", "-fsSL", "--max-time", "6"],
        )
        self.assertIn("--retry", command)
        self.assertEqual(command[command.index("--retry") + 1], "2")
        self.assertIn("--retry-all-errors", command)
        self.assertIn("--retry-delay", command)
        self.assertTrue(
            any("secid=1.600101" in part for part in command)
        )
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertTrue(run.call_args.kwargs["check"])
        self.assertEqual(run.call_args.kwargs["timeout"], 15)

    def test_follow_sort_orders_cautious_between_strict_and_observation(self):
        rows = [
            {
                "ts_code": "600103.SH",
                "follow_status": "宽松观察",
                "follow_setup_score": 90,
                "tail_condition_pass_count": 2,
            },
            {
                "ts_code": "600102.SH",
                "follow_status": "谨慎跟进",
                "follow_setup_score": 75,
                "tail_condition_pass_count": 2,
            },
            {
                "ts_code": "600101.SH",
                "follow_status": "可以跟进",
                "follow_setup_score": 70,
                "tail_condition_pass_count": 3,
            },
        ]

        ordered = sorted(rows, key=_follow_sort_key)

        self.assertEqual(
            [row["ts_code"] for row in ordered],
            ["600101.SH", "600102.SH", "600103.SH"],
        )

    def test_candidate_date_switches_to_today_at_1430(self):
        dates = ["20260729", "20260728"]

        self.assertEqual(
            _select_candidate_trade_date(dates, datetime(2026, 7, 29, 14, 29)),
            "20260728",
        )
        self.assertEqual(
            _select_candidate_trade_date(dates, datetime(2026, 7, 29, 14, 30)),
            "20260729",
        )

    def test_missing_volume_ratio_uses_latest_five_prior_days(self):
        market = pd.DataFrame([{
            "ts_code": "600101.SH",
            "vol": 150_000,
            "volume_ratio": None,
        }])
        history = pd.DataFrame([
            {"ts_code": "600101.SH", "trade_date": date, "vol": 100_000}
            for date in ["20260722", "20260723", "20260724", "20260725", "20260728"]
        ])

        result = _fill_effective_volume_ratio(market, history, "20260729")

        self.assertAlmostEqual(result.iloc[0]["estimated_volume_ratio"], 1.5)
        self.assertAlmostEqual(result.iloc[0]["effective_volume_ratio"], 1.5)

    def test_daily_filter_rejects_st_star_market_and_unqualified_leader(self):
        market = pd.DataFrame([
            {
                "ts_code": "600101.SH", "name": "合格股份", "close": 10,
                "pct_chg": 3, "turnover_rate": 6, "volume_ratio": 1.8,
                "vol": 180_000, "amount": 300_000,
            },
            {
                "ts_code": "688001.SH", "name": "科创股份", "close": 10,
                "pct_chg": 3, "turnover_rate": 6, "volume_ratio": 1.8,
                "vol": 180_000, "amount": 300_000,
            },
            {
                "ts_code": "600102.SH", "name": "ST样本", "close": 10,
                "pct_chg": 3, "turnover_rate": 6, "volume_ratio": 1.8,
                "vol": 180_000, "amount": 300_000,
            },
            {
                "ts_code": "600103.SH", "name": "弱势龙头", "close": 10,
                "pct_chg": 0.5, "turnover_rate": 6, "volume_ratio": 1.8,
                "vol": 180_000, "amount": 300_000,
            },
        ])

        result = _daily_follow_candidates(
            market,
            pd.DataFrame(),
            "20260729",
            leader_codes={"600103.SH"},
            max_fetch=30,
        )

        self.assertEqual(result["ts_code"].tolist(), ["600101.SH"])
        self.assertFalse(result.iloc[0]["morning_follow_sector_leader"])

    def test_daily_filter_reports_total_before_max_fetch_limit(self):
        market = pd.DataFrame([
            {
                "ts_code": code,
                "name": name,
                "close": 10,
                "pct_chg": 3,
                "turnover_rate": 6,
                "volume_ratio": ratio,
                "vol": 180_000,
                "amount": 300_000,
            }
            for code, name, ratio in (
                ("600101.SH", "候选一", 1.8),
                ("600102.SH", "候选二", 1.7),
            )
        ])

        result = _daily_follow_candidates(
            market,
            pd.DataFrame(),
            "20260729",
            leader_codes=set(),
            max_fetch=1,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result.attrs["hard_filter_count"], 2)

    def test_morning_confirmation_waits_before_935(self):
        result = _morning_confirmation(
            {"close": 10.0, "previous_tail_support": 9.95},
            morning_bars([10.1, 10.12, 10.13]),
            datetime(2026, 7, 30, 9, 33),
            "20260730",
        )

        self.assertEqual(result["follow_status"], "等待9:35确认")

    def test_morning_confirmation_accepts_supported_price_above_vwap(self):
        result = _morning_confirmation(
            {"close": 10.0, "previous_tail_support": 9.95},
            morning_bars([10.1, 10.12, 10.14, 10.16, 10.18, 10.2]),
            datetime(2026, 7, 30, 9, 36),
            "20260730",
        )

        self.assertEqual(result["follow_status"], "可以跟进")
        self.assertTrue(result["morning_above_open"])
        self.assertTrue(result["morning_above_vwap"])

    def test_morning_confirmation_rejects_gap_above_three_percent(self):
        result = _morning_confirmation(
            {"close": 10.0, "previous_tail_support": 9.95},
            morning_bars([10.31, 10.32, 10.33, 10.34, 10.35, 10.36]),
            datetime(2026, 7, 30, 9, 36),
            "20260730",
        )

        self.assertEqual(result["follow_status"], "放弃")

    def test_morning_confirmation_never_accepts_missing_minutes(self):
        result = _morning_confirmation(
            {"close": 10.0, "previous_tail_support": 9.95},
            pd.DataFrame(),
            datetime(2026, 7, 30, 9, 36),
            "20260730",
        )

        self.assertEqual(result["follow_status"], "数据未就绪")

    def test_morning_confirmation_keeps_borderline_case_waiting(self):
        result = _morning_confirmation(
            {"close": 10.0, "previous_tail_support": 9.95},
            morning_bars(
                [10.1, 10.08, 10.09, 10.1, 10.11, 10.09],
                opens=[10.08] * 6,
            ),
            datetime(2026, 7, 30, 9, 36),
            "20260730",
        )

        self.assertEqual(result["follow_status"], "等待确认")

    def test_relaxed_setup_waits_as_relaxed_observation_before_confirmation_day(
        self,
    ):
        result = _morning_confirmation(
            {
                "close": 10.0,
                "previous_tail_support": 9.95,
                "setup_tier": "relaxed",
            },
            pd.DataFrame(),
            datetime(2026, 7, 29, 15, 10),
            "20260730",
        )

        self.assertEqual(result["follow_status"], "宽松观察")
        self.assertIn("轻仓", result["morning_entry_plan"])
        self.assertIn("不可追高", result["morning_entry_plan"])

    def test_relaxed_setup_upgrades_to_cautious_follow_after_935(self):
        result = _morning_confirmation(
            {
                "close": 10.0,
                "previous_tail_support": 9.95,
                "setup_tier": "relaxed",
            },
            morning_bars([10.1, 10.12, 10.14, 10.16, 10.18, 10.2]),
            datetime(2026, 7, 30, 9, 36),
            "20260730",
        )

        self.assertEqual(result["follow_status"], "谨慎跟进")
        self.assertIn(
            "尾盘条件未完全确认",
            result["morning_entry_plan"],
        )
        self.assertIn("下一交易日", result["t1_exit_plan"])

    def test_morning_phase_only_refreshes_in_two_live_windows(self):
        self.assertEqual(
            _morning_follow_phase(
                datetime(2026, 7, 29, 14, 45), "20260729", "20260730"
            ),
            ("观察池构建中", True),
        )
        self.assertEqual(
            _morning_follow_phase(
                datetime(2026, 7, 30, 9, 40), "20260729", "20260730"
            ),
            ("早盘确认", True),
        )
        self.assertEqual(
            _morning_follow_phase(
                datetime(2026, 7, 30, 10, 1), "20260729", "20260730"
            ),
            ("确认结束", False),
        )

    @patch("morning_follow_service.load_recent_daily", return_value=pd.DataFrame())
    @patch("morning_follow_service.load_market_snapshot")
    @patch("morning_follow_service.sync_cached_market_data")
    @patch("morning_follow_service.get_trade_dates")
    def test_follow_inputs_sync_today_snapshot_after_close_when_missing(
        self,
        trade_dates,
        sync_market,
        load_snapshot,
        _load_history,
    ):
        trade_dates.return_value = [
            "20260730",
            "20260729",
            "20260728",
        ]
        sync_market.return_value = {"cache_updated": True}
        load_snapshot.side_effect = [
            pd.DataFrame(),
            pd.DataFrame([{"ts_code": "600101.SH"}]),
        ]

        _market, _history, metadata = _load_follow_inputs(
            datetime(2026, 7, 29, 15, 10)
        )

        sync_market.assert_called_once_with(force_current=True)
        self.assertEqual(load_snapshot.call_count, 2)
        self.assertEqual(metadata["candidate_trade_date"], "20260729")
        self.assertEqual(metadata["confirmation_trade_date"], "20260730")

    @patch("morning_follow_service.load_recent_daily", return_value=pd.DataFrame())
    @patch(
        "morning_follow_service.load_market_snapshot",
        return_value=pd.DataFrame([{"ts_code": "600101.SH"}]),
    )
    @patch("morning_follow_service.sync_cached_market_data")
    @patch("morning_follow_service.get_trade_dates")
    def test_follow_inputs_reuses_existing_today_snapshot_after_close(
        self,
        trade_dates,
        sync_market,
        _load_snapshot,
        _load_history,
    ):
        trade_dates.return_value = [
            "20260730",
            "20260729",
            "20260728",
        ]

        _load_follow_inputs(datetime(2026, 7, 29, 15, 10))

        sync_market.assert_not_called()

    @patch("morning_follow_service.load_recent_daily", return_value=pd.DataFrame())
    @patch(
        "morning_follow_service.load_eastmoney_market_snapshot",
        create=True,
    )
    @patch(
        "morning_follow_service.load_market_snapshot",
        return_value=pd.DataFrame(),
    )
    @patch("morning_follow_service.sync_cached_market_data")
    @patch("morning_follow_service.get_trade_dates")
    def test_follow_inputs_use_eastmoney_when_today_snapshot_stays_empty(
        self,
        trade_dates,
        sync_market,
        _load_snapshot,
        eastmoney_snapshot,
        _load_history,
    ):
        trade_dates.return_value = [
            "20260731",
            "20260730",
            "20260729",
        ]
        sync_market.return_value = {"cache_updated": False}
        eastmoney_snapshot.return_value = (
            pd.DataFrame(
                [
                    {
                        "ts_code": "600101.SH",
                        "trade_date": "20260730",
                        "name": "合格股份",
                        "close": 10.2,
                        "pct_chg": 3.1,
                        "turnover_rate": 5.2,
                        "volume_ratio": 1.8,
                        "amount": 500_000,
                        "vol": 50_000,
                    }
                ]
            ),
            None,
        )

        market, _history, metadata = _load_follow_inputs(
            datetime(2026, 7, 30, 14, 45)
        )

        self.assertEqual(market.iloc[0]["ts_code"], "600101.SH")
        eastmoney_snapshot.assert_called_once_with("20260730")
        self.assertEqual(metadata["candidate_trade_date"], "20260730")
        self.assertEqual(metadata["data_source"], "eastmoney_snapshot_fallback")
        self.assertEqual(metadata["data_status"], "live")

    @patch("morning_follow_service.load_recent_daily", return_value=pd.DataFrame())
    @patch(
        "morning_follow_service.load_eastmoney_market_snapshot",
        return_value=(pd.DataFrame(), "东方财富快照未返回有效数据"),
    )
    @patch("morning_follow_service.load_market_snapshot")
    @patch("morning_follow_service.sync_cached_market_data")
    @patch("morning_follow_service.get_trade_dates")
    def test_follow_inputs_use_previous_complete_snapshot_as_stale_fallback(
        self,
        trade_dates,
        sync_market,
        load_snapshot,
        _eastmoney_snapshot,
        load_history,
    ):
        trade_dates.return_value = [
            "20260731",
            "20260730",
            "20260729",
            "20260728",
        ]
        sync_market.return_value = {"cache_updated": False}
        previous_market = pd.DataFrame(
            [
                {
                    "ts_code": "600101.SH",
                    "trade_date": "20260729",
                    "name": "合格股份",
                    "close": 10.2,
                    "pct_chg": 3.1,
                    "turnover_rate": 5.2,
                    "volume_ratio": 1.8,
                    "amount": 500_000,
                    "vol": 50_000,
                }
            ]
        )
        load_snapshot.side_effect = [
            pd.DataFrame(),
            pd.DataFrame(),
            previous_market,
        ]

        market, _history, metadata = _load_follow_inputs(
            datetime(2026, 7, 30, 14, 45)
        )

        self.assertEqual(market.iloc[0]["trade_date"], "20260729")
        load_history.assert_called_once_with("20260729", 100)
        self.assertEqual(
            metadata["requested_candidate_trade_date"],
            "20260730",
        )
        self.assertEqual(metadata["candidate_trade_date"], "20260729")
        self.assertEqual(metadata["confirmation_trade_date"], "20260730")
        self.assertEqual(metadata["data_trade_date"], "20260729")
        self.assertEqual(metadata["data_source"], "previous_snapshot")
        self.assertEqual(metadata["data_status"], "stale")
        self.assertEqual(metadata["data_status_label"], "备用缓存")
        self.assertFalse(metadata["data_current"])

    @patch("morning_follow_service._macd_kdj_60m_signal")
    def test_setup_row_requires_tail_rules_and_never_uses_opening_auction(
        self,
        signal_builder,
    ):
        signal_builder.return_value = {
            "tail_strength_score": 88,
            "tail_return_after_1430": 0.6,
            "tail_volume_ratio": 1.8,
            "tail_close_position": 0.9,
            "macd_above_zero_60m": True,
            "macd_recent_golden_cross_60m": True,
            "kdj_bullish_60m": True,
        }
        stock = {
            "ts_code": "600101.SH",
            "name": "合格股份",
            "industry": "机器人",
            "close": 10,
            "pct_chg": 3,
            "turnover_rate": 6,
            "effective_volume_ratio": 1.8,
            "amount": 300_000,
            "open": 9.2,
        }
        tail = pd.DataFrame([{"low": 9.95}, {"low": 10.0}])

        result = _setup_row(
            stock,
            {"60m": pd.DataFrame([{"close": 10}]), "tail_1m": tail},
            {
                "sector_macd_status": "板块60分MACD水上走强",
                "sector_macd_above_zero": True,
                "sector_macd_trending_up": True,
                "sector_60m_excluded": False,
            },
            leader_codes={"600101.SH"},
        )

        self.assertIsNotNone(result)
        self.assertGreaterEqual(result["follow_setup_score"], 70)
        self.assertEqual(result["previous_tail_support"], 9.95)
        self.assertNotIn("opening_auction_return", result)
        self.assertNotIn("集合竞价", result["follow_reason"])
        self.assertEqual(result["setup_tier"], "strict")
        self.assertEqual(result["setup_tier_label"], "严格候选")
        self.assertEqual(result["tail_condition_pass_count"], 3)
        self.assertTrue(result["tail_conditions_all_pass"])
        self.assertEqual(result["tail_condition_notes"], [])
        self.assertEqual(result["follow_status"], "明日观察")

    @patch("morning_follow_service._macd_kdj_60m_signal")
    def test_setup_row_relaxes_failed_tail_but_keeps_hard_gates(
        self,
        signal_builder,
    ):
        valid_signal = {
            "tail_strength_score": 88,
            "tail_return_after_1430": 0.6,
            "tail_volume_ratio": 1.8,
            "tail_close_position": 0.9,
            "macd_above_zero_60m": True,
            "macd_recent_golden_cross_60m": True,
            "kdj_bullish_60m": True,
        }
        stock = {
            "ts_code": "600101.SH",
            "name": "合格股份",
            "industry": "机器人",
            "close": 10,
            "pct_chg": 3,
            "turnover_rate": 6,
            "effective_volume_ratio": 1.8,
            "amount": 300_000,
        }
        bars = {
            "60m": pd.DataFrame([{"close": 10}]),
            "tail_1m": pd.DataFrame([{"low": 9.95}]),
        }
        sector = {
            "sector_macd_status": "板块60分MACD水上走强",
            "sector_macd_above_zero": True,
            "sector_macd_trending_up": True,
            "sector_60m_excluded": False,
        }

        for field, invalid_value, expected_note in (
            (
                "tail_return_after_1430",
                0.11,
                "尾盘涨幅0.11%，低于0.15%",
            ),
            (
                "tail_return_after_1430",
                1.30,
                "尾盘涨幅1.30%，高于1.20%",
            ),
            (
                "tail_volume_ratio",
                0.85,
                "尾盘量能0.85倍，低于1.20倍",
            ),
            (
                "tail_volume_ratio",
                3.10,
                "尾盘量能3.10倍，高于3.00倍",
            ),
            (
                "tail_close_position",
                0.57,
                "尾盘收盘位置57%，低于75%",
            ),
        ):
            with self.subTest(field=field):
                signal_builder.return_value = {
                    **valid_signal,
                    field: invalid_value,
                }
                result = _setup_row(
                    stock,
                    bars,
                    sector,
                    leader_codes=set(),
                )
                self.assertIsNotNone(result)
                self.assertEqual(result["setup_tier"], "relaxed")
                self.assertEqual(result["setup_tier_label"], "宽松观察")
                self.assertEqual(result["tail_condition_pass_count"], 2)
                self.assertIn(
                    expected_note,
                    result["tail_condition_notes"],
                )
                self.assertEqual(result["follow_status"], "宽松观察")

        signal_builder.return_value = valid_signal
        self.assertIsNone(
            _setup_row(stock, bars, {}, leader_codes={"600101.SH"})
        )
        self.assertIsNone(
            _setup_row(
                stock,
                bars,
                {**sector, "sector_60m_excluded": True},
                leader_codes={"600101.SH"},
            )
        )
        self.assertIsNone(
            _setup_row(
                stock,
                {
                    **bars,
                    "tail_1m": pd.DataFrame([{"close": 10.0}]),
                },
                sector,
                leader_codes=set(),
            )
        )
        signal_builder.return_value = None
        self.assertIsNone(
            _setup_row(stock, bars, sector, leader_codes=set())
        )

        signal_builder.return_value = {
            **valid_signal,
            "macd_above_zero_60m": False,
            "macd_recent_golden_cross_60m": True,
            "kdj_bullish_60m": False,
        }
        weak_sector = {
            **sector,
            "sector_macd_above_zero": False,
            "sector_macd_trending_up": False,
        }
        result = _setup_row(
            stock,
            bars,
            weak_sector,
            leader_codes=set(),
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["tail_condition_notes"], [])
        self.assertEqual(result["setup_tier"], "relaxed")
        self.assertIn(
            "低于严格候选70分",
            result["setup_tier_reason"],
        )

    @patch("morning_follow_service._morning_bars_for_candidate")
    @patch("morning_follow_service._load_setup_bars")
    @patch("morning_follow_service._sector_60m_signal_from_bars")
    @patch("morning_follow_service._leader_codes_from_sector_potential")
    @patch("morning_follow_service._load_follow_inputs")
    def test_monitor_builds_previous_day_pool_then_confirms_current_morning(
        self,
        load_inputs,
        leader_codes,
        sector_signal,
        load_setup_bars,
        morning_bars_for_candidate,
    ):
        market = pd.DataFrame([{
            "ts_code": "600101.SH",
            "name": "合格股份",
            "industry": "机器人",
            "close": 10.0,
            "pct_chg": 3.0,
            "turnover_rate": 6.0,
            "volume_ratio": 1.8,
            "vol": 180_000,
            "amount": 300_000,
        }])
        load_inputs.return_value = (
            market,
            pd.DataFrame(),
            {
                "candidate_trade_date": "20260729",
                "confirmation_trade_date": "20260730",
            },
        )
        leader_codes.return_value = {"600101.SH": {"leader_score": 90}}
        sector_signal.return_value = {
            "机器人": {
                "sector_macd_status": "板块60分MACD水上走强",
                "sector_macd_above_zero": True,
                "sector_macd_trending_up": True,
                "sector_60m_excluded": False,
            },
        }
        load_setup_bars.return_value = (
            {
                "600101.SH": {
                    "60m": pd.DataFrame([{"close": 10.0}]),
                    "tail_1m": pd.DataFrame([
                        {"low": 9.95},
                        {"low": 10.0},
                    ]),
                },
            },
            pd.DataFrame([{
                "ts_code": "600201.SH",
                "industry": "机器人",
            }]),
            {"600201.SH": pd.DataFrame([{"close": 100.0}])},
            [],
        )
        morning_bars_for_candidate.return_value = (
            morning_bars([10.1, 10.12, 10.14, 10.16, 10.18, 10.2]),
            "eastmoney_fallback",
            None,
        )

        with patch("morning_follow_service._macd_kdj_60m_signal") as stock_signal:
            stock_signal.return_value = {
                "tail_strength_score": 88,
                "tail_return_after_1430": 0.6,
                "tail_volume_ratio": 1.8,
                "tail_close_position": 0.9,
                "macd_above_zero_60m": True,
                "macd_recent_golden_cross_60m": True,
                "kdj_bullish_60m": True,
            }
            result = build_morning_follow_monitor(
                limit=10,
                now=datetime(2026, 7, 30, 9, 36),
            )
            _MORNING_FOLLOW_RESULT_CACHE.clear()
            morning_bars_for_candidate.return_value = (
                pd.DataFrame(),
                "unavailable",
                "Tushare当日分钟为空；东方财富备用源请求超时",
            )
            unavailable = build_morning_follow_monitor(
                limit=10,
                now=datetime(2026, 7, 30, 9, 36),
            )

        self.assertEqual(result["candidate_trade_date"], "20260729")
        self.assertEqual(result["confirmation_trade_date"], "20260730")
        self.assertEqual(result["stocks"][0]["follow_status"], "可以跟进")
        self.assertEqual(
            result["stocks"][0]["morning_minute_source"],
            "eastmoney_fallback",
        )
        stock = unavailable["stocks"][0]
        self.assertEqual(stock["follow_status"], "数据未就绪")
        self.assertEqual(stock["morning_minute_source"], "unavailable")
        self.assertEqual(
            stock["follow_reason"],
            "Tushare当日分钟为空；东方财富备用源请求超时",
        )


if __name__ == "__main__":
    unittest.main()
