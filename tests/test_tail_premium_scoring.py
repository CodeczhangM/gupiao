import unittest

import pandas as pd

from tail_premium_scoring import (
    build_daily_factor_frame,
    eligible_tail_universe,
    normalize_amount_yuan,
    rank_tail_premium_candidates,
    score_tail_premium_row,
)


def _history(
    ts_code="600001.SH",
    *,
    closes=None,
    amount=100_000,
    amount_unit="thousand_yuan",
):
    values = closes or [10 + index * 0.02 for index in range(65)]
    return pd.DataFrame([
        {
            "ts_code": ts_code,
            "trade_date": (
                pd.Timestamp("2026-04-27") + pd.offsets.BDay(index)
            ).strftime("%Y%m%d"),
            "open": close * 0.99,
            "high": close * 1.02,
            "low": close * 0.98,
            "close": close,
            "pre_close": values[index - 1] if index else close,
            "vol": 1_000_000 + index * 1_000,
            "amount": amount,
            "amount_unit": amount_unit,
            "pct_chg": (
                (close / values[index - 1] - 1) * 100 if index else 0
            ),
        }
        for index, close in enumerate(values)
    ])


def _market_row(ts_code="600001.SH", **overrides):
    row = {
        "ts_code": ts_code,
        "name": "示例股份",
        "industry": "食品",
        "open": 11.2,
        "high": 11.8,
        "low": 11.0,
        "close": 11.7,
        "pre_close": 11.0,
        "vol": 2_000_000,
        "amount": 120_000_000,
        "amount_unit": "yuan",
        "pct_chg": 6.36,
        "turnover_rate": 8.0,
        "volume_ratio": 2.0,
    }
    row.update(overrides)
    return row


class TailPremiumScoringTests(unittest.TestCase):
    def test_amount_normalization_uses_explicit_unit(self):
        self.assertEqual(
            normalize_amount_yuan(50_000, unit="thousand_yuan"),
            50_000_000,
        )
        self.assertEqual(
            normalize_amount_yuan(50_000_000, unit="yuan"),
            50_000_000,
        )

    def test_eligibility_excludes_risk_liquidity_and_confirmed_downtrend(self):
        rising = _history()
        rising.loc[rising.index[-10], "pct_chg"] = 9.8
        illiquid = _history("600002.SH", amount=20_000)
        illiquid.loc[illiquid.index[-10], "pct_chg"] = 9.8
        falling_values = [20 - index * 0.1 for index in range(65)]
        falling = _history("600003.SH", closes=falling_values)
        falling.loc[falling.index[-10], "pct_chg"] = 9.8
        st_history = _history("600004.SH")
        st_history.loc[st_history.index[-10], "pct_chg"] = 9.8
        market = pd.DataFrame([
            _market_row(),
            _market_row("600002.SH", name="低流动"),
            _market_row(
                "600003.SH",
                name="下降股",
                close=falling_values[-1] - 0.5,
            ),
            _market_row("600004.SH", name="*ST 风险"),
        ])

        factors = build_daily_factor_frame(
            market,
            pd.concat(
                [rising, illiquid, falling, st_history],
                ignore_index=True,
            ),
            "20260731",
            macd_settings={
                "fast_period": 5,
                "slow_period": 34,
                "signal_period": 5,
                "version": 1,
            },
        )
        eligible = eligible_tail_universe(factors)

        self.assertEqual(eligible["ts_code"].tolist(), ["600001.SH"])
        reasons = factors.set_index("ts_code")["exclusion_reasons"].to_dict()
        self.assertIn("近5日日均成交额低于5000万元", reasons["600002.SH"])
        self.assertIn("MA60下降且股价位于MA60下方", reasons["600003.SH"])
        self.assertIn("风险股票", reasons["600004.SH"])

    def test_tail_arbitrage_requires_recent_limit_but_rejects_hot_or_locked_boards(self):
        valid_history = _history("600010.SH")
        valid_history.loc[valid_history.index[-8], "pct_chg"] = 9.8
        no_limit_history = _history("600011.SH")
        hot_history = _history("600012.SH")
        hot_history.loc[hot_history.index[-8], "pct_chg"] = 9.8
        sealed_history = _history("600013.SH")
        sealed_history.loc[sealed_history.index[-8], "pct_chg"] = 9.8
        continuous_history = _history("600014.SH")
        continuous_history.loc[continuous_history.index[-8], "pct_chg"] = 9.8
        market = pd.DataFrame([
            _market_row("600010.SH", pct_chg=5.2),
            _market_row("600011.SH", pct_chg=5.2),
            _market_row("600012.SH", pct_chg=7.01),
            _market_row("600013.SH", pct_chg=6.5, limit_flag="涨停"),
            _market_row("600014.SH", pct_chg=6.5, continuous_limit_days=2),
        ])

        factors = build_daily_factor_frame(
            market,
            pd.concat(
                [
                    valid_history,
                    no_limit_history,
                    hot_history,
                    sealed_history,
                    continuous_history,
                ],
                ignore_index=True,
            ),
            "20260731",
            macd_settings={
                "fast_period": 5,
                "slow_period": 34,
                "signal_period": 5,
                "version": 1,
            },
        )
        eligible = eligible_tail_universe(factors)

        self.assertEqual(eligible["ts_code"].tolist(), ["600010.SH"])
        reasons = factors.set_index("ts_code")["exclusion_reasons"].to_dict()
        self.assertIn("近20日无涨停基因", reasons["600011.SH"])
        self.assertIn("当日涨幅超过7%", reasons["600012.SH"])
        self.assertIn("当日封板买入受限", reasons["600013.SH"])
        self.assertIn("连续涨停后隔日兑现风险高", reasons["600014.SH"])

    def test_short_history_remains_but_cannot_receive_complete_trend_score(self):
        market = pd.DataFrame([_market_row()])
        short_history = _history().tail(30).copy()
        short_history.loc[short_history.index[-8], "pct_chg"] = 9.8
        factors = build_daily_factor_frame(
            market,
            short_history,
            "20260731",
            macd_settings={
                "fast_period": 5,
                "slow_period": 34,
                "signal_period": 5,
                "version": 1,
            },
        )

        self.assertTrue(factors.iloc[0]["eligible_tail_premium"])
        self.assertEqual(factors.iloc[0]["history_quality"], "insufficient")
        self.assertIn(
            "历史不足60日",
            factors.iloc[0]["data_quality_warnings"],
        )

    def test_tail_score_boundaries_and_opening_auction_definition(self):
        base = {
            "tail_return_after_1430": 0.5,
            "opening_auction_return": 0.1,
            "tail_close_position": 0.75,
            "tail_volume_ratio": 1.2,
            "pct_chg": 3,
            "open": 10.01,
            "close": 10.3,
            "high": 10.4,
            "low": 9.9,
            "pre_close": 10,
            "turnover_rate": 8,
            "volume_ratio": 2,
            "return20": 20,
            "high_position_60": 0.7,
        }

        scored = score_tail_premium_row(base)

        self.assertEqual(scored["tail_raw_score"], 50)
        self.assertAlmostEqual(scored["tail_score"], 26.92, places=2)
        self.assertAlmostEqual(scored["opening_auction_return"], 0.1)

    def test_weighted_score_is_clipped_and_risks_are_explained(self):
        risky = {
            "tail_return_after_1430": -0.2,
            "opening_auction_return": -0.2,
            "tail_close_position": 0.3,
            "tail_volume_ratio": 5.5,
            "pct_chg": 1.0,
            "open": 11.5,
            "close": 11.0,
            "high": 12.0,
            "low": 10.8,
            "pre_close": 10.9,
            "turnover_rate": 30,
            "volume_ratio": 5.5,
            "return20": 60,
            "high_position_60": 0.98,
            "price_volume_stagnation": True,
        }

        scored = score_tail_premium_row(risky)

        self.assertGreater(scored["risk_score"], 0)
        self.assertGreaterEqual(scored["premium_score"], 0)
        self.assertLessEqual(scored["premium_score"], 100)
        self.assertIn("高位巨量阴线", scored["risk_items"])
        self.assertIn("高位放量滞涨", scored["risk_items"])
        self.assertIn("长上影线", scored["risk_items"])
        self.assertIn("高位高换手", scored["risk_items"])

    def test_ranking_is_deterministic_and_defaults_to_top20(self):
        rows = []
        for index in range(25):
            rows.append({
                "ts_code": f"600{index:03d}.SH",
                "premium_score": 80,
                "tail_score": 20,
                "sector_score": 10,
                "amount_yuan": 100_000_000 + index,
            })

        result = rank_tail_premium_candidates(pd.DataFrame(rows))

        self.assertEqual(len(result), 20)
        self.assertEqual(result.iloc[0]["ts_code"], "600024.SH")


if __name__ == "__main__":
    unittest.main()
