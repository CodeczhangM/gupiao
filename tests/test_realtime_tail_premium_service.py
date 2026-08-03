import unittest
from datetime import datetime

import pandas as pd

from realtime_market_source import MinuteLoadResult
from realtime_tail_premium_service import (
    build_realtime_tail_premium_monitor,
    _filter_waiting_realtime_candidates,
    _raw_tail_prefilter_market,
    _refresh_waiting_market_with_current_minutes,
)


def _history(ts_code, name, industry, *, falling=False):
    closes = [
        (20 - index * 0.1) if falling else (10 + index * 0.03)
        for index in range(70)
    ]
    return pd.DataFrame([
        {
            "ts_code": ts_code,
            "name": name,
            "industry": industry,
            "trade_date": (
                pd.Timestamp("2026-04-20") + pd.offsets.BDay(index)
            ).strftime("%Y%m%d"),
            "open": close * 0.99,
            "high": close * 1.02,
            "low": close * 0.98,
            "close": close,
            "pre_close": closes[index - 1] if index else close,
            "vol": 1_000_000,
            "amount": 100_000,
            "amount_unit": "thousand_yuan",
            "pct_chg": (
                (close / closes[index - 1] - 1) * 100 if index else 0
            ),
        }
        for index, close in enumerate(closes)
    ])


def _tail_bars(ts_code):
    times = [
        "14:25:00", "14:26:00", "14:27:00", "14:28:00",
        "14:29:00", "14:30:00", "14:31:00", "14:40:00",
        "14:49:00",
    ]
    closes = [11, 11, 11, 11, 11, 11, 11.03, 11.06, 11.10]
    volumes = [1000, 1000, 1000, 1000, 1000, 1000, 1500, 1600, 1700]
    return pd.DataFrame([
        {
            "ts_code": ts_code,
            "trade_time": f"2026-07-31 {clock}",
            "open": close - 0.01,
            "high": close + 0.02,
            "low": close - 0.02,
            "close": close,
            "vol": volume,
            "amount": close * volume,
        }
        for clock, close, volume in zip(times, closes, volumes)
    ])


def _morning_bars(ts_code):
    rows = [
        ("09:30:00", 11.05, 11.12, 10.98, 11.10, 6_000_000),
        ("10:30:00", 11.10, 11.42, 11.08, 11.40, 6_000_000),
        ("14:45:00", 11.40, 11.60, 11.35, 11.55, 6_000_000),
    ]
    return pd.DataFrame([
        {
            "ts_code": ts_code,
            "trade_time": f"2026-07-31 {clock}",
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "vol": volume,
            "amount": close * volume,
        }
        for clock, open_, high, low, close, volume in rows
    ])


def _morning_bars_with_last_close(ts_code, close, *, volume=18_000_000):
    rows = [
        ("09:30:00", close * 0.98, close * 1.01, close * 0.97, close * 0.99, volume * 0.4),
        ("14:45:00", close * 0.99, close * 1.01, close * 0.98, close, volume * 0.6),
    ]
    return pd.DataFrame([
        {
            "ts_code": ts_code,
            "trade_time": f"2026-07-31 {clock}",
            "open": open_,
            "high": high,
            "low": low,
            "close": last_close,
            "vol": volume,
            "amount": last_close * volume,
        }
        for clock, open_, high, low, last_close, volume in rows
    ])


class RealtimeTailPremiumServiceTests(unittest.TestCase):
    def setUp(self):
        rows = [
            {
                "ts_code": "600001.SH",
                "name": "优选股份",
                "industry": "食品",
                "open": 11.05,
                "high": 11.2,
                "low": 10.9,
                "close": 11.1,
                "pre_close": 11,
                "vol": 2_000_000,
                "amount": 120_000_000,
                "amount_unit": "yuan",
                "pct_chg": 0.91,
                "turnover_rate": 8,
                "volume_ratio": 1.8,
            },
            {
                "ts_code": "600002.SH",
                "name": "*ST 风险",
                "industry": "食品",
                "open": 11,
                "high": 11.1,
                "low": 10.9,
                "close": 11,
                "pre_close": 11,
                "vol": 1_000_000,
                "amount": 100_000_000,
                "amount_unit": "yuan",
                "pct_chg": 0,
                "turnover_rate": 5,
                "volume_ratio": 1.2,
            },
        ]
        self.market = pd.DataFrame(rows)
        self.history = pd.concat([
            _history("600001.SH", "优选股份", "食品"),
            _history("600002.SH", "*ST 风险", "食品"),
        ])

    def _loader(self, ts_code, start, end, freq, trade_date):
        if freq == "1min":
            bars = _tail_bars(ts_code)
        else:
            bars = pd.DataFrame()
        return MinuteLoadResult(bars, "fixture", [])

    def test_monitor_builds_explainable_live_tail_candidate(self):
        frequencies = []

        def loader(ts_code, start, end, freq, trade_date):
            frequencies.append(freq)
            return self._loader(ts_code, start, end, freq, trade_date)

        result = build_realtime_tail_premium_monitor(
            limit=20,
            now=datetime(2026, 7, 31, 14, 50),
            market_override=self.market,
            history_override=self.history,
            trade_date_override="20260731",
            minute_loader=loader,
            sector_potential_override=pd.DataFrame([{
                "industry": "食品",
                "avg_pct_chg": 2.5,
                "up_ratio": 0.8,
                "limit_up_count": 2,
                "sector_rank": 1,
            }]),
        )

        self.assertEqual(result["selection_state"], "live_tail_window")
        self.assertEqual(result["candidate_count"], 1)
        row = result["stocks"][0]
        self.assertEqual(row["ts_code"], "600001.SH")
        self.assertAlmostEqual(row["opening_auction_return"], 0.454545, places=5)
        self.assertIn("premium_score", row)
        self.assertIn("tail_score", row)
        self.assertIn("risk_items", row)
        self.assertEqual(result["data_as_of"], "2026-07-31 14:49:00")
        self.assertEqual(set(frequencies), {"1min"})

    def test_before_1450_is_observation_only(self):
        calls = []

        def loader(ts_code, start, end, freq, trade_date):
            calls.append((start, end, freq))
            return self._loader(ts_code, start, end, freq, trade_date)

        result = build_realtime_tail_premium_monitor(
            limit=20,
            now=datetime(2026, 7, 31, 14, 45),
            market_override=self.market,
            history_override=self.history,
            trade_date_override="20260731",
            minute_loader=loader,
            sector_potential_override=pd.DataFrame(),
        )

        self.assertEqual(result["selection_state"], "waiting_tail_window")
        self.assertTrue(result["stocks"])
        self.assertEqual(
            result["stocks"][0]["buyable_tail_signal"],
            "等待14:50",
        )
        self.assertEqual(calls, [])

    def test_before_1450_uses_current_day_minutes_for_display_price(self):
        requested = []

        def loader(ts_code, start, end, freq, trade_date):
            requested.append((start, end, freq))
            return MinuteLoadResult(_morning_bars(ts_code), "fixture", [])

        result = build_realtime_tail_premium_monitor(
            limit=20,
            now=datetime(2026, 7, 31, 14, 45),
            market_override=self.market,
            history_override=self.history,
            trade_date_override="20260731",
            minute_loader=loader,
            source_metadata={
                "data_current": False,
                "data_source": "previous_snapshot",
            },
            sector_potential_override=pd.DataFrame(),
        )

        row = result["stocks"][0]
        self.assertEqual(row["ts_code"], "600001.SH")
        self.assertEqual(row["buyable_tail_signal"], "等待14:50")
        self.assertAlmostEqual(row["close"], 11.55)
        self.assertAlmostEqual(row["pct_chg"], 5.0, places=5)
        self.assertEqual(row["data_as_of"], "2026-07-31 14:45:00")
        self.assertEqual(result["data_as_of"], "2026-07-31 14:45:00")
        self.assertIn(
            ("2026-07-31 09:30:00", "2026-07-31 14:45:00", "1min"),
            requested,
        )

    def test_before_1450_pct_uses_pre_close_not_stale_close(self):
        market = pd.DataFrame([{
            "ts_code": "600733.SH",
            "name": "北汽蓝谷",
            "close": 5.74,
            "pre_close": 5.96,
            "pct_chg": -3.69,
            "vol": 100_000,
            "amount": 50_000_000,
            "amount_unit": "yuan",
        }])

        def loader(ts_code, start, end, freq, trade_date):
            rows = [
                ("09:30:00", 5.96, 6.08, 5.71, 6.00, 6_000_000),
                ("14:45:00", 6.00, 6.08, 5.90, 6.02, 6_000_000),
            ]
            return MinuteLoadResult(
                pd.DataFrame([{
                    "ts_code": ts_code,
                    "trade_time": f"2026-07-31 {time_text}",
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "vol": vol,
                    "amount": close * vol,
                } for time_text, open_, high, low, close, vol in rows]),
                "fixture",
                [],
            )

        result, _latest_times, _warnings = _refresh_waiting_market_with_current_minutes(
            market,
            "20260731",
            datetime(2026, 7, 31, 14, 45),
            loader,
        )

        row = result.iloc[0]
        self.assertEqual(row["ts_code"], "600733.SH")
        self.assertAlmostEqual(row["close"], 6.02)
        self.assertAlmostEqual(row["pct_chg"], 1.006711, places=5)

    def test_before_1450_pct_derives_previous_close_when_pre_close_missing(self):
        from realtime_tail_premium_service import _resolve_previous_close_for_pct

        # pre_close/previous_close 缺失时，应用 close 和 pct_chg 反推昨收，
        # 而不是直接回退到当日 close（会导致 pct_chg 被错误计算为 0）。
        market = pd.DataFrame([{
            "ts_code": "600733.SH",
            "name": "北汽蓝谷",
            "close": 6.02,
            "pre_close": None,
            "previous_close": None,
            "pct_chg": 1.006711,
            "vol": 100_000,
            "amount": 50_000_000,
            "amount_unit": "yuan",
        }])

        def loader(ts_code, start, end, freq, trade_date):
            return MinuteLoadResult(
                pd.DataFrame([{
                    "ts_code": ts_code,
                    "trade_time": "2026-07-31 14:45:00",
                    "open": 6.00,
                    "high": 6.08,
                    "low": 5.90,
                    "close": 6.02,
                    "vol": 6_000_000,
                    "amount": 36_120_000,
                }]),
                "fixture",
                [],
            )

        # 反推得到的昨收应接近 5.96，而不是当日 close 6.02
        self.assertAlmostEqual(
            _resolve_previous_close_for_pct(market.iloc[0].to_dict()),
            5.96,
            places=4,
        )

        result, _latest_times, _warnings = _refresh_waiting_market_with_current_minutes(
            market,
            "20260731",
            datetime(2026, 7, 31, 14, 45),
            loader,
        )

        row = result.iloc[0]
        self.assertAlmostEqual(row["close"], 6.02)
        # 涨幅应基于反推的昨收 5.96 计算，而不是被零化为接近 0
        self.assertAlmostEqual(row["pct_chg"], 1.006711, places=4)

    def test_stale_waiting_refreshes_candidate_pool_before_final_screening(self):
        requested_codes = []
        extra = self.market.iloc[[0]].copy()
        extra["ts_code"] = "600003.SH"
        extra["name"] = "备选股份"
        market = pd.concat([self.market, extra], ignore_index=True)
        history = pd.concat([
            self.history,
            _history("600003.SH", "备选股份", "食品"),
        ], ignore_index=True)

        def loader(ts_code, start, end, freq, trade_date):
            requested_codes.append(ts_code)
            return MinuteLoadResult(_morning_bars(ts_code), "fixture", [])

        result = build_realtime_tail_premium_monitor(
            limit=1,
            now=datetime(2026, 7, 31, 14, 45),
            market_override=market,
            history_override=history,
            trade_date_override="20260731",
            minute_loader=loader,
            source_metadata={
                "data_current": False,
                "data_source": "previous_snapshot",
            },
            sector_potential_override=pd.DataFrame(),
        )

        self.assertEqual(result["candidate_count"], 1)
        self.assertGreater(len(requested_codes), 1)

    def test_stale_waiting_filters_after_current_minute_metrics(self):
        weak = self.market.iloc[[0]].copy()
        weak["ts_code"] = "600010.SH"
        weak["name"] = "昨日强势"
        weak["close"] = 12.0
        weak["pre_close"] = 12.0
        weak["pct_chg"] = 8.0
        weak["volume_ratio"] = 3.5
        weak["turnover_rate"] = 8.0
        strong = self.market.iloc[[0]].copy()
        strong["ts_code"] = "600011.SH"
        strong["name"] = "今日转强"
        strong["close"] = 10.0
        strong["pre_close"] = 10.0
        strong["pct_chg"] = 1.0
        strong["volume_ratio"] = 2.0
        strong["turnover_rate"] = 6.0
        market = pd.concat([weak, strong], ignore_index=True)
        history = pd.concat([
            _history("600010.SH", "昨日强势", "食品"),
            _history("600011.SH", "今日转强", "食品"),
        ], ignore_index=True)

        def loader(ts_code, start, end, freq, trade_date):
            close = 11.7 if ts_code == "600010.SH" else 10.35
            return MinuteLoadResult(
                _morning_bars_with_last_close(ts_code, close),
                "fixture",
                [],
            )

        result = build_realtime_tail_premium_monitor(
            limit=2,
            now=datetime(2026, 7, 31, 14, 45),
            market_override=market,
            history_override=history,
            trade_date_override="20260731",
            minute_loader=loader,
            source_metadata={
                "data_current": False,
                "data_source": "previous_snapshot",
            },
            sector_potential_override=pd.DataFrame(),
        )

        codes = [row["ts_code"] for row in result["stocks"]]
        self.assertIn("600011.SH", codes)
        self.assertNotIn("600010.SH", codes)
        self.assertGreater(result["stocks"][0]["pct_chg"], 0)

    def test_stale_waiting_candidates_start_from_current_performance_not_prior_strength(self):
        prior_strong = self.market.iloc[[0]].copy()
        prior_strong["ts_code"] = "600020.SH"
        prior_strong["name"] = "昨日涨停"
        prior_strong["close"] = 12.0
        prior_strong["pre_close"] = 12.0
        prior_strong["pct_chg"] = 9.8
        prior_strong["volume_ratio"] = 4.0
        prior_strong["turnover_rate"] = 8.0
        prior_strong["amount"] = 900_000_000
        today_strong = self.market.iloc[[0]].copy()
        today_strong["ts_code"] = "600021.SH"
        today_strong["name"] = "今日走强"
        today_strong["close"] = 10.0
        today_strong["pre_close"] = 10.0
        today_strong["pct_chg"] = 0.1
        today_strong["volume_ratio"] = 0.9
        today_strong["turnover_rate"] = 3.0
        today_strong["amount"] = 850_000_000
        market = pd.concat([prior_strong, today_strong], ignore_index=True)
        history = pd.concat([
            _history("600020.SH", "昨日涨停", "食品"),
            _history("600021.SH", "今日走强", "食品"),
        ], ignore_index=True)

        def loader(ts_code, start, end, freq, trade_date):
            if ts_code == "600020.SH":
                close, volume = 11.88, 200_000
            else:
                close, volume = 10.45, 100_000_000
            return MinuteLoadResult(
                _morning_bars_with_last_close(ts_code, close, volume=volume),
                "fixture",
                [],
            )

        result = build_realtime_tail_premium_monitor(
            limit=2,
            max_fetch=2,
            now=datetime(2026, 7, 31, 14, 45),
            market_override=market,
            history_override=history,
            trade_date_override="20260731",
            minute_loader=loader,
            source_metadata={
                "data_current": False,
                "data_source": "previous_snapshot",
            },
            sector_potential_override=pd.DataFrame(),
        )

        codes = [row["ts_code"] for row in result["stocks"]]
        self.assertEqual(codes, ["600021.SH"])
        self.assertGreaterEqual(result["stocks"][0]["amount"], 50_000_000)
        self.assertGreaterEqual(result["stocks"][0]["volume_ratio"], 1.2)

    def test_waiting_realtime_filter_accepts_relaxed_positive_candidate(self):
        factors = pd.DataFrame([
            {
                "ts_code": "600030.SH",
                "pct_chg": 0.0,
                "volume_ratio": 1.0,
                "amount": 30_000_000,
                "data_as_of": "2026-07-31 14:18:00",
            },
            {
                "ts_code": "600031.SH",
                "pct_chg": -0.01,
                "volume_ratio": 3.0,
                "amount": 90_000_000,
                "data_as_of": "2026-07-31 14:18:00",
            },
        ])

        result = _filter_waiting_realtime_candidates(factors)

        self.assertEqual(result["ts_code"].tolist(), ["600030.SH"])

    def test_raw_prefilter_limits_factor_universe_to_strong_liquid_stocks(self):
        market = pd.DataFrame([
            {
                "ts_code": f"600{index:03d}.SH",
                "pct_chg": 0.1,
                "volume_ratio": 1.0,
                "turnover_rate": 1.0,
                "amount": 10_000_000,
            }
            for index in range(200)
        ])
        market.loc[123, ["pct_chg", "volume_ratio", "turnover_rate", "amount"]] = [
            8.5,
            4.0,
            9.0,
            900_000_000,
        ]

        result = _raw_tail_prefilter_market(market, max_fetch=20)

        self.assertEqual(len(result), 20)
        self.assertIn("600123.SH", set(result["ts_code"]))

    def test_raw_prefilter_excludes_star_market_candidates(self):
        market = pd.DataFrame([
            {
                "ts_code": "688766.SH",
                "pct_chg": 9.0,
                "volume_ratio": 5.0,
                "turnover_rate": 12.0,
                "amount": 900_000_000,
            },
            {
                "ts_code": "689001.SH",
                "pct_chg": 8.5,
                "volume_ratio": 4.5,
                "turnover_rate": 10.0,
                "amount": 800_000_000,
            },
            {
                "ts_code": "600667.SH",
                "pct_chg": 4.0,
                "volume_ratio": 1.5,
                "turnover_rate": 5.0,
                "amount": 300_000_000,
            },
        ])

        result = _raw_tail_prefilter_market(market, max_fetch=20)

        self.assertEqual(result["ts_code"].tolist(), ["600667.SH"])


if __name__ == "__main__":
    unittest.main()
