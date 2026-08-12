import unittest
from unittest.mock import patch

import pandas as pd


def market_rows(trade_date="20260812"):
    rows = []
    for industry, code_suffix, base_amount, pct_values in [
        ("机器人", "R", 300000.0, [6.2, 4.5, 2.1, 1.5, 0.8, -0.4, 3.2, 5.1]),
        ("半导体", "S", 260000.0, [2.2, 1.8, 0.5, -0.2, 3.4, 1.1, -0.5, 0.2]),
        ("医药", "M", 180000.0, [-1.0, -0.8, 0.2, 0.5, -0.4, 0.1, -0.2, 0.0]),
    ]:
        for index, pct_chg in enumerate(pct_values):
            code = f"{index:06d}.{code_suffix}"
            rows.append({
                "trade_date": trade_date,
                "ts_code": code,
                "name": f"{industry}{index}",
                "industry": industry,
                "close": 10 + index,
                "pct_chg": pct_chg,
                "amount": base_amount + index * 10000,
                "turnover_rate": 2.0 + index * 0.3,
                "volume_ratio": 1.1 + index * 0.2,
                "total_mv": 800000 + index * 50000,
                "list_status": "L",
                "list_date": "20200101",
            })
    return pd.DataFrame(rows)


def history_rows():
    dates = [
        "20260716", "20260717", "20260720", "20260721", "20260722",
        "20260723", "20260724", "20260727", "20260728", "20260729",
        "20260730", "20260731", "20260803", "20260804", "20260805",
        "20260806", "20260807", "20260810", "20260811", "20260812",
    ]
    current = market_rows()
    rows = []
    for row_index, stock in current.iterrows():
        industry = stock["industry"]
        for offset, trade_date in enumerate(dates):
            trend = {"机器人": 0.45, "半导体": 0.18, "医药": -0.08}[industry]
            close = 8 + offset * trend + (row_index % 3) * 0.1
            if trade_date == "20260811":
                pct = {"机器人": 1.2, "半导体": -1.5, "医药": -0.6}[industry]
                amount = float(stock["amount"]) * {
                    "机器人": 0.72,
                    "半导体": 0.75,
                    "医药": 0.9,
                }[industry]
            elif trade_date == "20260812":
                pct = float(stock["pct_chg"])
                amount = float(stock["amount"])
            else:
                pct = trend
                amount = float(stock["amount"]) * 0.65
            rows.append({
                "trade_date": trade_date,
                "ts_code": stock["ts_code"],
                "close": close,
                "high": close * 1.03,
                "low": close * 0.97,
                "pct_chg": pct,
                "amount": amount,
                "vol": amount / 10,
            })
    return pd.DataFrame(rows)


def moneyflow_for(date):
    if date == "20260811":
        return pd.DataFrame([
            {"trade_date": date, "name": "机器人", "net_amount": 800000000, "net_amount_rate": 3.1},
            {"trade_date": date, "name": "半导体", "net_amount": -600000000, "net_amount_rate": -2.3},
            {"trade_date": date, "name": "医药", "net_amount": -300000000, "net_amount_rate": -1.1},
        ])
    return pd.DataFrame([
        {"trade_date": date, "name": "机器人", "net_amount": 1300000000, "net_amount_rate": 4.8},
        {"trade_date": date, "name": "半导体", "net_amount": 450000000, "net_amount_rate": 1.7},
        {"trade_date": date, "name": "医药", "net_amount": -260000000, "net_amount_rate": -0.9},
    ])


class SectorRotationServiceTests(unittest.TestCase):
    def test_scores_and_sorts_continuation_and_rotation_lists(self):
        import sector_rotation_service

        with patch(
            "sector_rotation_service.get_complete_dates",
            return_value=["20260812", "20260811", "20260810"],
        ):
            with patch(
                "sector_rotation_service.load_market_snapshot",
                return_value=market_rows(),
            ):
                with patch(
                    "sector_rotation_service.load_recent_daily",
                    return_value=history_rows(),
                ):
                    with patch(
                        "sector_rotation_service.load_moneyflow",
                        side_effect=lambda date: moneyflow_for(date),
                    ):
                        result = (
                            sector_rotation_service
                            .build_tomorrow_sector_rotation(
                                limit=5,
                                stocks_per_sector=3,
                            )
                        )

        self.assertEqual(result["trade_date"], "20260812")
        self.assertEqual(
            result["lookback_trade_dates"],
            ["20260811", "20260812"],
        )
        self.assertEqual(
            result["moneyflow_trade_dates"],
            ["20260811", "20260812"],
        )
        self.assertEqual(
            result["continuation_inflow"][0]["industry_name"],
            "机器人",
        )
        self.assertEqual(
            result["rotation_rebound"][0]["industry_name"],
            "半导体",
        )
        self.assertGreater(
            result["continuation_inflow"][0]["continuation_score"],
            result["continuation_inflow"][-1]["continuation_score"],
        )
        self.assertGreater(
            result["rotation_rebound"][0]["rotation_score"],
            result["rotation_rebound"][-1]["rotation_score"],
        )
        self.assertTrue(result["continuation_inflow"][0]["attack_leaders"])
        self.assertTrue(result["rotation_rebound"][0]["catchup_candidates"])
        attack_scores = [
            row["attack_score"]
            for row in result["continuation_inflow"][0]["attack_leaders"]
        ]
        self.assertEqual(attack_scores, sorted(attack_scores, reverse=True))
        catchup_scores = [
            row["catchup_score"]
            for row in result["rotation_rebound"][0]["catchup_candidates"]
        ]
        self.assertEqual(catchup_scores, sorted(catchup_scores, reverse=True))

    def test_missing_one_moneyflow_day_degrades_confidence(self):
        import sector_rotation_service

        def partial_moneyflow(date):
            if date == "20260811":
                return pd.DataFrame()
            return moneyflow_for(date)

        with patch(
            "sector_rotation_service.get_complete_dates",
            return_value=["20260812", "20260811", "20260810"],
        ):
            with patch(
                "sector_rotation_service.load_market_snapshot",
                return_value=market_rows(),
            ):
                with patch(
                    "sector_rotation_service.load_recent_daily",
                    return_value=history_rows(),
                ):
                    with patch(
                        "sector_rotation_service.load_moneyflow",
                        side_effect=partial_moneyflow,
                    ):
                        result = (
                            sector_rotation_service
                            .build_tomorrow_sector_rotation()
                        )

        self.assertTrue(result["warnings"])
        self.assertIn("20260811", result["warnings"][0])
        confidences = {
            row["confidence"]
            for row in (
                result["continuation_inflow"]
                + result["rotation_rebound"]
            )
        }
        self.assertNotIn("高", confidences)

    def test_no_moneyflow_returns_empty_lists_with_warning(self):
        import sector_rotation_service

        with patch(
            "sector_rotation_service.get_complete_dates",
            return_value=["20260812", "20260811"],
        ):
            with patch(
                "sector_rotation_service.load_market_snapshot",
                return_value=market_rows(),
            ):
                with patch(
                    "sector_rotation_service.load_recent_daily",
                    return_value=history_rows(),
                ):
                    with patch(
                        "sector_rotation_service.load_moneyflow",
                        return_value=pd.DataFrame(),
                    ):
                        result = (
                            sector_rotation_service
                            .build_tomorrow_sector_rotation()
                        )

        self.assertEqual(result["continuation_inflow"], [])
        self.assertEqual(result["rotation_rebound"], [])
        self.assertIn("暂无足够资金流数据", " ".join(result["warnings"]))

    def test_less_than_two_complete_dates_returns_empty_lists(self):
        import sector_rotation_service

        with patch(
            "sector_rotation_service.get_complete_dates",
            return_value=["20260812"],
        ):
            result = sector_rotation_service.build_tomorrow_sector_rotation()

        self.assertEqual(result["continuation_inflow"], [])
        self.assertEqual(result["rotation_rebound"], [])
        self.assertIn("完整交易日不足", " ".join(result["warnings"]))


if __name__ == "__main__":
    unittest.main()
