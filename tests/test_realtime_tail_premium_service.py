import unittest
from datetime import datetime

import pandas as pd

from realtime_market_source import MinuteLoadResult
from realtime_tail_premium_service import (
    build_realtime_tail_premium_monitor,
    _raw_tail_prefilter_market,
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
        ("09:30:00", 11.05, 11.12, 10.98, 11.10, 1000),
        ("10:30:00", 11.10, 11.42, 11.08, 11.40, 1500),
        ("14:45:00", 11.40, 11.60, 11.35, 11.55, 1800),
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
        self.assertAlmostEqual(row["pct_chg"], 5.0)
        self.assertEqual(row["data_as_of"], "2026-07-31 14:45:00")
        self.assertEqual(result["data_as_of"], "2026-07-31 14:45:00")
        self.assertIn(
            ("2026-07-31 09:30:00", "2026-07-31 14:45:00", "1min"),
            requested,
        )

    def test_stale_waiting_refreshes_only_displayed_candidates(self):
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
        self.assertEqual(len(requested_codes), 1)

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


if __name__ == "__main__":
    unittest.main()
