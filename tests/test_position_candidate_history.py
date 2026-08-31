import unittest

import pandas as pd

from position_candidate_history import (
    event_decay,
    extract_limit_gene,
    extract_pullback_confirmation,
    extract_resonance_events,
    merge_key_levels,
)


def _bars(*, limit_days=(), events=()):
    dates = pd.bdate_range(end="2026-08-31", periods=26)
    rows = []
    for index, date in enumerate(dates):
        days_ago = len(dates) - 1 - index
        pct_chg = 10.0 if days_ago in set(limit_days) else 0.5
        row = {
            "ts_code": "600001.SH",
            "trade_date": date.strftime("%Y%m%d"),
            "open": 10.0,
            "high": 11.2 if pct_chg >= 9.5 else 10.2,
            "low": 9.9,
            "close": 11.0 if pct_chg >= 9.5 else 10.1,
            "pct_chg": pct_chg,
            "vol": 1_000_000,
            "amount": 10_000_000,
        }
        rows.append(row)
    for event_type, days_ago, strength in events:
        row = rows[-1 - days_ago]
        row[f"{event_type}_event"] = True
        row[f"{event_type}_strength"] = strength
    return pd.DataFrame(rows)


def _level(price, source, strength=5):
    return {"price": price, "source": source, "strength": strength}


def _gene():
    return {
        "latest_limit_up_date": "20260820",
        "latest_limit_up_body_low": 9.8,
        "latest_limit_up_start_price": 10.0,
        "latest_limit_up_close": 11.0,
    }


def _pullback_bars(*, intraday_breach=False, close_break=False, high_volume=False):
    rows = [
        {"trade_date": "20260820", "open": 10, "high": 11.1, "low": 9.8, "close": 11, "vol": 100},
        {"trade_date": "20260821", "open": 10.8, "high": 11.0, "low": 10.3, "close": 10.6, "vol": 80},
        {"trade_date": "20260824", "open": 10.5, "high": 10.7,
         "low": 9.5 if close_break else 9.7 if intraday_breach else 9.92,
         "close": 9.5 if close_break else 10.08, "vol": 180 if high_volume else 60},
        {"trade_date": "20260825", "open": 10.1, "high": 10.5, "low": 10.0, "close": 10.4, "vol": 70},
    ]
    return pd.DataFrame(rows)


class PositionCandidateHistoryTests(unittest.TestCase):
    def test_limit_gene_accepts_day_one_and_day_ten(self):
        self.assertTrue(extract_limit_gene(_bars(limit_days={1}), "20260831")["limit_gene_eligible"])
        result = extract_limit_gene(_bars(limit_days={10}), "20260831")
        self.assertTrue(result["limit_gene_eligible"])
        self.assertEqual(result["latest_limit_up_days_ago"], 10)

    def test_limit_gene_rejects_today_and_day_eleven(self):
        self.assertFalse(extract_limit_gene(_bars(limit_days={0}), "20260831")["limit_gene_eligible"])
        self.assertFalse(extract_limit_gene(_bars(limit_days={11}), "20260831")["limit_gene_eligible"])

    def test_limit_gene_returns_latest_candle_evidence_and_count(self):
        result = extract_limit_gene(_bars(limit_days={2, 7}), "20260831")
        self.assertEqual(result["latest_limit_up_days_ago"], 2)
        self.assertEqual(result["limit_up_count_10d"], 2)
        self.assertEqual(result["latest_limit_up_close"], 11.0)
        self.assertEqual(result["latest_limit_up_body_low"], 10.0)
        self.assertEqual(result["latest_limit_up_start_price"], 10.0)

    def test_resonance_decay_boundaries(self):
        self.assertEqual(
            [event_decay(day) for day in (1, 3, 4, 7, 8, 12, 13, 20, 21)],
            [1.0, 1.0, 0.8, 0.8, 0.6, 0.6, 0.4, 0.4, 0.0],
        )

    def test_resonance_keeps_best_decayed_event_per_type(self):
        result = extract_resonance_events(
            _bars(events=[("daily_macd", 2, 10), ("daily_macd", 6, 20)]),
            "20260831",
        )
        events = [event for event in result["resonance_events"] if event["type"] == "daily_macd"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["days_ago"], 6)
        self.assertEqual(events[0]["decay"], 0.8)
        self.assertEqual(result["latest_resonance_date"], events[0]["date"])

    def test_resonance_does_not_fabricate_missing_events(self):
        result = extract_resonance_events(_bars(), "20260831")
        self.assertEqual(result["resonance_events"], [])
        self.assertEqual(result["historical_resonance_score"], 0.0)

    def test_invalid_dates_are_ignored_without_string_timestamp_comparison(self):
        bars = _bars(limit_days={1}, events=[("volume_breakout", 2, 8)])
        bars.loc[0, "trade_date"] = "invalid"
        self.assertTrue(extract_limit_gene(bars, "20260831")["limit_gene_eligible"])
        self.assertTrue(extract_resonance_events(bars, "20260831")["resonance_events"])

    def test_nearby_level_sources_merge(self):
        zones = merge_key_levels([
            _level(10.00, "MA10"),
            _level(10.12, "筹码峰"),
            _level(9.20, "启动价"),
        ])
        self.assertEqual(zones[0]["lower"], 10.0)
        self.assertEqual(zones[0]["upper"], 10.12)
        self.assertEqual(zones[0]["sources"], ["MA10", "筹码峰"])

    def test_level_merge_keeps_at_most_three_strongest_zones(self):
        zones = merge_key_levels([_level(8 + index, f"L{index}", index) for index in range(5)])
        self.assertEqual(len(zones), 3)
        self.assertEqual([zone["strength"] for zone in zones], [4.0, 3.0, 2.0])

    def test_pullback_intraday_breach_can_reclaim_support(self):
        result = extract_pullback_confirmation(
            _pullback_bars(intraday_breach=True),
            _gene(),
            {"current_price": 10.3, "volume_ratio": 1.0},
        )
        self.assertEqual(result["pullback_state"], "盘中跌破但收回")
        self.assertTrue(result["support_held"])
        self.assertFalse(result["breakout_confirmed"])

    def test_confirmed_breakout_requires_half_percent_and_volume(self):
        result = extract_pullback_confirmation(
            _pullback_bars(),
            _gene(),
            {"current_price": 10.56, "volume_ratio": 1.4},
        )
        self.assertGreaterEqual(result["breakout_pct"], 0.5)
        self.assertTrue(result["price_volume_confirmation"])
        self.assertTrue(result["breakout_confirmed"])

    def test_volume_break_below_support_is_vetoed(self):
        result = extract_pullback_confirmation(
            _pullback_bars(close_break=True, high_volume=True),
            _gene(),
            {"current_price": 9.5, "volume_ratio": 2.0},
        )
        self.assertEqual(result["pullback_state"], "有效跌破关键位")
        self.assertTrue(result["support_volume_break_veto"])


if __name__ == "__main__":
    unittest.main()
