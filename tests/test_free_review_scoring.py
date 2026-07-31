import unittest

import pandas as pd


def market_fixture():
    return pd.DataFrame([
        {
            "ts_code": "600001.SH", "name": "正常股份",
            "industry": "制造", "list_status": "L",
            "list_date": "20200101", "close": 20, "open": 19.5,
            "high": 20.5, "low": 19.2, "pct_chg": 2,
            "vol": 2000, "amount": 400000,
            "turnover_rate": 4, "turnover_rate_f": 5,
            "volume_ratio": 1.8, "pe": 18, "pe_ttm": 20,
            "pb": 2, "ps": 2.5, "ps_ttm": 2.4, "dv_ttm": 1.2,
            "total_mv": 3000000, "circ_mv": 2000000,
        },
        {
            "ts_code": "600002.SH", "name": "*ST风险",
            "industry": "制造", "list_status": "L",
            "list_date": "20200101", "close": 5, "vol": 100,
            "amount": 500, "pe_ttm": -5,
        },
        {
            "ts_code": "300001.SZ", "name": "新股",
            "industry": "科技", "list_status": "L",
            "list_date": "20260701", "close": 10, "vol": 100,
            "amount": 1000, "pe_ttm": 30,
        },
        {
            "ts_code": "830001.BJ", "name": "北交正常",
            "industry": "科技", "list_status": "L",
            "list_date": "20200101", "close": 8, "vol": 0,
            "amount": 0, "pe_ttm": -2,
        },
    ])


def history_fixture():
    rows = []
    dates = pd.bdate_range("2026-03-12", periods=100)
    for code, base in (("600001.SH", 10.0),):
        for index, day in enumerate(dates):
            close = base + index * 0.1
            rows.append({
                "ts_code": code,
                "trade_date": day.strftime("%Y%m%d"),
                "open": close - 0.1,
                "high": close + 0.3,
                "low": close - 0.3,
                "close": close,
                "vol": 1000 + index * 10,
                "amount": close * (1000 + index * 10),
                "pct_chg": 1,
            })
    return pd.DataFrame(rows)


def financial_fixture():
    rows = []
    periods = [
        "20240930", "20241231", "20250331", "20250630",
        "20250930", "20251231", "20260331", "20260630",
    ]
    for index, period in enumerate(periods):
        rows.append({
            "ts_code": "600001.SH",
            "end_date": period,
            "ann_date": "20260715" if period == "20260630" else period,
            "roe": 8 + index,
            "roe_dt": 7 + index,
            "roa": 4 + index * 0.2,
            "roic": 6 + index * 0.3,
            "grossprofit_margin": 25 + index,
            "netprofit_margin": 8 + index * 0.5,
            "current_ratio": 1.5,
            "debt_to_assets": 45,
            "ocf_to_or": 12,
            "cfps": 1.2,
            "tr_yoy": 10 + index,
            "netprofit_yoy": 12 + index,
            "dt_netprofit_yoy": 11 + index,
            "ocf_yoy": 9 + index,
            "basic_eps_yoy": 8 + index,
        })
    return pd.DataFrame(rows)


class FreeReviewScoringTests(unittest.TestCase):
    def test_eligible_universe_excludes_risk_suspended_and_new_rows(self):
        import free_review_scoring

        result = free_review_scoring.eligible_universe(
            market_fixture(),
            "20260730",
        )

        self.assertEqual(result["ts_code"].tolist(), ["600001.SH"])

    def test_build_snapshot_emits_detailed_metrics_and_bounded_scores(self):
        import free_review_scoring

        result = free_review_scoring.build_review_snapshot(
            market_fixture(),
            history_fixture(),
            financial_fixture(),
            "20260730",
        )

        self.assertEqual(len(result), 1)
        row = result.iloc[0]
        required = {
            "ma5", "ma10", "ma20", "ma30", "ma60",
            "ret_5", "ret_10", "ret_20", "ret_60",
            "drawdown_20", "drawdown_60", "position_60",
            "vol_ratio_ma5", "vol_ratio_ma10", "vol_ratio_ma20",
            "macd_dif", "macd_dea", "macd_hist",
            "kdj_k", "kdj_d", "kdj_j",
            "rsi6", "rsi12", "rsi24",
            "boll_position", "boll_width", "atr_pct",
            "trend_score", "volume_price_score", "momentum_score",
            "valuation_score", "financial_quality_score",
            "financial_growth_score", "risk_penalty", "total_score",
            "data_completeness", "financial_improvement_count",
            "score_reasons", "risk_flags", "missing_fields",
            "macd_fast_period", "macd_slow_period",
            "macd_signal_period", "macd_parameter_key",
        }
        self.assertTrue(required.issubset(result.columns))
        self.assertAlmostEqual(row["ma5"], 19.7, places=4)
        self.assertGreater(row["ret_20"], 0)
        self.assertEqual(
            (
                row["macd_fast_period"],
                row["macd_slow_period"],
                row["macd_signal_period"],
            ),
            (5, 34, 5),
        )
        self.assertEqual(row["financial_end_date"], "20260630")
        self.assertEqual(row["financial_improvement_count"], 7)
        self.assertGreaterEqual(row["total_score"], 0)
        self.assertLessEqual(row["total_score"], 100)
        self.assertLessEqual(row["risk_penalty"], 20)
        self.assertEqual(
            row["score_version"],
            free_review_scoring.SCORE_VERSION,
        )

    def test_loss_stock_remains_eligible_but_gets_zero_valuation_score(self):
        import free_review_scoring

        market = market_fixture().iloc[[0]].copy()
        market.loc[:, "pe_ttm"] = -10
        result = free_review_scoring.build_review_snapshot(
            market,
            history_fixture(),
            financial_fixture(),
            "20260730",
        )

        self.assertEqual(result.iloc[0]["profit_state"], "loss")
        self.assertEqual(result.iloc[0]["valuation_score"], 0)


if __name__ == "__main__":
    unittest.main()
