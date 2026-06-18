import unittest
from unittest.mock import patch

import pandas as pd

import data_service
import main
from strategy import pick_stocks


def build_history(
    ts_code="600001.SH",
    start_close=20.0,
    prior_close=10.0,
    latest_close=12.0,
    prior_volume=100.0,
    latest_volume=150.0,
    days=61,
    latest_pct_chg=2.0,
):
    rows = []
    for index in range(days):
        is_first = index == 0
        is_latest = index == days - 1
        close = start_close if is_first else latest_close if is_latest else prior_close
        rows.append({
            "ts_code": ts_code,
            "trade_date": f"2026{index + 1:04d}",
            "pct_chg": latest_pct_chg if is_latest else 0.0,
            "close": close,
            "high": close,
            "low": close,
            "vol": latest_volume if is_latest else prior_volume,
        })
    return pd.DataFrame(rows)


def build_market(
    ts_code="600001.SH",
    industry="热点行业",
    close=12.0,
    pct_chg=2.0,
    vol=150.0,
    turnover_rate=9.0,
    volume_ratio=2.5,
    amount=300_000_000,
):
    return pd.DataFrame([{
        "ts_code": ts_code,
        "name": "测试股份",
        "industry": industry,
        "close": close,
        "high": close,
        "low": close,
        "pct_chg": pct_chg,
        "turnover_rate": turnover_rate,
        "volume_ratio": volume_ratio,
        "vol": vol,
        "amount": amount,
        "total_mv": 1_000_000,
    }])


class AdvantageStockScoringTests(unittest.TestCase):
    def test_scores_oversold_reversal_conditions_to_100(self):
        result = pick_stocks(build_market(), build_history(start_close=20.0, latest_close=12.0))

        self.assertEqual(len(result), 1)
        row = result.iloc[0]
        expected_flags = [
            "in_bottom_area",
            "ret60_oversold",
            "volume_price_rise",
            "turnover_active",
            "volume_ratio_active",
            "close_above_ma20",
            "macd_golden_cross",
            "hot_theme",
        ]
        for field in expected_flags:
            self.assertTrue(bool(row[field]), field)

        expected_scores = {
            "in_bottom_area_score": 25,
            "ret60_oversold_score": 20,
            "volume_price_rise_score": 15,
            "turnover_active_score": 10,
            "volume_ratio_active_score": 10,
            "close_above_ma20_score": 10,
            "macd_golden_cross_score": 5,
            "hot_theme_score": 5,
        }
        for field, expected in expected_scores.items():
            self.assertEqual(row[field], expected, field)
        self.assertEqual(row["strength20_score"], 10)
        self.assertEqual(row["score"], 110)
        self.assertAlmostEqual(row["ret60"], -40.0)
        self.assertAlmostEqual(row["strength20"], 1.0)
        self.assertAlmostEqual(row["volume_expand_rate"], 1.5)
        self.assertAlmostEqual(row["recent_low_60"], 10.0)
        self.assertAlmostEqual(row["amount_yuan"], 300_000_000)

    def test_thresholds_are_strict_for_reversal_signals(self):
        history = build_history(
            start_close=12.6,
            latest_close=10.0,
            latest_volume=150.0,
            latest_pct_chg=2.0,
        )
        market = build_market(
            close=10.0,
            vol=150.0,
            turnover_rate=8.0,
            volume_ratio=2.0,
            amount=300_000_000,
        )

        row = pick_stocks(market, history).iloc[0]

        self.assertTrue(bool(row["in_bottom_area"]))
        self.assertFalse(bool(row["ret60_oversold"]))
        self.assertTrue(bool(row["volume_price_rise"]))
        self.assertFalse(bool(row["close_above_ma20"]))
        self.assertFalse(bool(row["turnover_active"]))
        self.assertFalse(bool(row["volume_ratio_active"]))

    def test_bottom_boundary_at_120_percent_qualifies(self):
        row = pick_stocks(
            build_market(close=12.0),
            build_history(start_close=20.0, latest_close=12.0),
        ).iloc[0]

        self.assertTrue(bool(row["in_bottom_area"]))
        self.assertEqual(row["in_bottom_area_score"], 25)

    def test_hot_theme_only_marks_top_five_industries(self):
        industries = [
            ("行业一", 8.0),
            ("行业二", 7.0),
            ("行业三", 6.0),
            ("行业四", 5.0),
            ("行业五", 4.0),
            ("冷门行业", -5.0),
        ]
        market = pd.concat(
            [
                build_market(
                    ts_code=f"60000{index + 1}.SH",
                    industry=industry,
                    pct_chg=pct_chg,
                )
                for index, (industry, pct_chg) in enumerate(industries)
            ],
            ignore_index=True,
        )
        history = build_history(ts_code="600006.SH", start_close=20.0, latest_close=12.0)

        row = pick_stocks(market, history).iloc[0]

        self.assertFalse(bool(row["hot_theme"]))
        self.assertEqual(row["hot_theme_score"], 0)

    def test_missing_industry_scores_zero_for_hot_theme(self):
        market = build_market().drop(columns=["industry"])

        row = pick_stocks(market, build_history(start_close=20.0, latest_close=12.0)).iloc[0]

        self.assertFalse(bool(row["hot_theme"]))
        self.assertEqual(row["hot_theme_score"], 0)

    def test_history_shorter_than_61_days_is_omitted(self):
        result = pick_stocks(build_market(), build_history(days=60))

        self.assertTrue(result.empty)

    def test_zero_prior_volume_does_not_score_volume_conditions(self):
        row = pick_stocks(
            build_market(),
            build_history(start_close=20.0, latest_close=12.0, prior_volume=0.0, latest_volume=150.0),
        ).iloc[0]

        self.assertEqual(row["volume_expand_rate"], 0)
        self.assertFalse(bool(row["volume_price_rise"]))

    def test_hard_filters_require_oversold_turnover_and_amount(self):
        no_ret60 = pick_stocks(
            build_market(),
            build_history(start_close=14.0, latest_close=12.0),
        )
        low_turnover = pick_stocks(
            build_market(turnover_rate=3.0),
            build_history(start_close=20.0, latest_close=12.0),
        )
        low_amount = pick_stocks(
            build_market(amount=200_000_000),
            build_history(start_close=20.0, latest_close=12.0),
        )

        self.assertTrue(no_ret60.empty)
        self.assertTrue(low_turnover.empty)
        self.assertTrue(low_amount.empty)

    def test_amount_can_be_estimated_when_missing(self):
        market = build_market(amount=None, close=12.0, vol=20_000_000).drop(columns=["amount"])

        row = pick_stocks(market, build_history(start_close=20.0, latest_close=12.0)).iloc[0]

        self.assertAlmostEqual(row["amount_yuan"], 24_000_000_000)

    def test_underperforming_industry_gets_relative_strength_penalty(self):
        target = build_market(ts_code="600001.SH", industry="同行业", pct_chg=2.0)
        stronger_peer = build_market(ts_code="600002.SH", industry="同行业", pct_chg=8.0)
        market = pd.concat([target, stronger_peer], ignore_index=True)

        row = pick_stocks(
            market,
            build_history(ts_code="600001.SH", start_close=20.0, latest_close=12.0),
        ).iloc[0]

        self.assertAlmostEqual(row["sector_avg_pct_chg"], 5.0)
        self.assertAlmostEqual(row["rs_industry"], -3.0)
        self.assertEqual(row["rs_industry_penalty"], -10)
        self.assertEqual(row["relative_strength_rank"], 2)
        self.assertEqual(row["score"], 100)

    def test_matching_or_beating_industry_has_no_relative_strength_penalty(self):
        target = build_market(ts_code="600001.SH", industry="同行业", pct_chg=8.0)
        weaker_peer = build_market(ts_code="600002.SH", industry="同行业", pct_chg=2.0)
        market = pd.concat([target, weaker_peer], ignore_index=True)

        row = pick_stocks(
            market,
            build_history(ts_code="600001.SH", start_close=20.0, latest_close=12.0),
        ).iloc[0]

        self.assertAlmostEqual(row["sector_avg_pct_chg"], 5.0)
        self.assertAlmostEqual(row["rs_industry"], 3.0)
        self.assertEqual(row["rs_industry_penalty"], 0)
        self.assertEqual(row["relative_strength_rank"], 1)
        self.assertEqual(row["score"], 110)

    def test_strength20_adds_continuous_bonus_by_20_day_position(self):
        history = build_history(start_close=20.0, latest_close=12.0)
        recent_mask = history.index >= len(history) - 20
        history.loc[recent_mask, "low"] = 10.0
        history.loc[recent_mask, "high"] = 14.0
        history.loc[history.index[-1], ["close", "low", "high"]] = [12.0, 12.0, 12.0]

        row = pick_stocks(build_market(), history).iloc[0]

        self.assertAlmostEqual(row["low20"], 10.0)
        self.assertAlmostEqual(row["high20"], 14.0)
        self.assertAlmostEqual(row["strength20"], 0.5)
        self.assertEqual(row["strength20_score"], 5)
        self.assertEqual(row["score"], 105)

    def test_strength20_is_zero_when_20_day_range_is_flat(self):
        row = pick_stocks(
            build_market(close=10.0),
            build_history(start_close=20.0, prior_close=10.0, latest_close=10.0),
        ).iloc[0]

        self.assertAlmostEqual(row["strength20"], 0.0)
        self.assertEqual(row["strength20_score"], 0)


class MainWiringTests(unittest.TestCase):
    @patch("main.analyze_stocks", return_value="分析完成")
    @patch("main.format_sectors_for_ai", return_value="板块文本")
    @patch("main.format_for_ai", return_value="股票文本")
    @patch("main.pick_dip_sectors", return_value=(pd.DataFrame(), pd.DataFrame()))
    @patch("main.get_sector_data", return_value=None)
    @patch("main.pick_dip_stocks", return_value=pd.DataFrame())
    @patch("main.pick_stocks", return_value=pd.DataFrame())
    @patch("main.get_recent_daily_data")
    @patch("main.get_market_data")
    def test_main_reuses_60_day_history_for_strong_and_dip(
        self,
        get_market_data,
        get_recent_daily_data,
        pick_stocks_mock,
        pick_dip_stocks_mock,
        _get_sector_data,
        _pick_dip_sectors,
        _format_for_ai,
        _format_sectors_for_ai,
        _analyze_stocks,
    ):
        market = build_market()
        history = build_history()
        get_market_data.return_value = (market, "20260615")
        get_recent_daily_data.return_value = history

        main.main()

        get_recent_daily_data.assert_called_once_with("20260615", n=61)
        self.assertIs(pick_stocks_mock.call_args.args[1], history)
        self.assertIs(pick_dip_stocks_mock.call_args.args[1], history)


class HistoricalDataContractTests(unittest.TestCase):
    @patch("data_service.get_trade_dates", return_value=["20260615"])
    @patch("data_service._query_tushare")
    def test_recent_daily_data_requests_low_price(
        self,
        query_tushare,
        _get_trade_dates,
    ):
        query_tushare.return_value = pd.DataFrame([{
            "ts_code": "600001.SH",
            "trade_date": "20260615",
            "close": 10,
            "high": 11,
            "low": 9,
            "vol": 100,
            "pct_chg": 1,
        }])

        data_service.get_recent_daily_data("20260615", n=1)

        fields = query_tushare.call_args.kwargs["fields"].split(",")
        self.assertIn("low", fields)


if __name__ == "__main__":
    unittest.main()
