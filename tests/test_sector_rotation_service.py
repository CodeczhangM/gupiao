import unittest
import json
from unittest.mock import patch

import pandas as pd


TRADE_DATES = [
    value.strftime("%Y%m%d")
    for value in pd.bdate_range(end="2026-08-12", periods=120)
]
CURRENT = TRADE_DATES[-1]
PREV1 = TRADE_DATES[-2]
PREV2 = TRADE_DATES[-3]

# 周线形态参数：(起点, 日斜率, 日加速度)
# 机器人加速上行 -> 金叉/零轴上方/红柱放大；医药加速下行 -> 死叉/零轴下方/绿柱放大。
CLOSE_PARAMS = {
    "机器人": (8.0, 0.20, 0.005),
    "半导体": (20.0, 0.05, 0.0),
    "医药": (40.0, -0.08, -0.004),
}


def market_rows(trade_date=CURRENT):
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
    current = market_rows()
    rows = []
    for row_index, stock in current.iterrows():
        industry = stock["industry"]
        base, slope, accel = CLOSE_PARAMS[industry]
        for offset, trade_date in enumerate(TRADE_DATES):
            close = (
                base
                + slope * offset
                + accel * offset * offset
                + (row_index % 3) * 0.1
            )
            if trade_date == PREV1:
                pct = {"机器人": 1.2, "半导体": -1.5, "医药": -0.6}[industry]
                amount = float(stock["amount"]) * {
                    "机器人": 0.72,
                    "半导体": 0.75,
                    "医药": 0.9,
                }[industry]
            elif trade_date == CURRENT:
                pct = float(stock["pct_chg"])
                amount = float(stock["amount"])
            else:
                pct = {"机器人": 0.45, "半导体": 0.18, "医药": -0.08}[industry]
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
    if date == PREV1:
        flows = {
            "机器人": (800000000, 3.1),
            "半导体": (-600000000, -2.3),
            "医药": (-300000000, -1.1),
        }
    else:
        flows = {
            "机器人": (1300000000, 4.8),
            "半导体": (450000000, 1.7),
            "医药": (-260000000, -0.9),
        }
    return pd.DataFrame([
        {
            "trade_date": date,
            "name": name,
            "net_amount": net_amount,
            "net_amount_rate": net_amount_rate,
        }
        for name, (net_amount, net_amount_rate) in flows.items()
    ])


def build_with_patches(**overrides):
    import sector_rotation_service

    complete_dates = overrides.get("complete_dates", list(reversed(TRADE_DATES)))
    market = overrides["market"] if "market" in overrides else market_rows()
    history = overrides["history"] if "history" in overrides else history_rows()
    moneyflow = overrides.get("moneyflow", moneyflow_for)
    with patch(
        "sector_rotation_service.get_complete_dates",
        return_value=complete_dates,
    ):
        with patch(
            "sector_rotation_service.load_market_snapshot",
            return_value=market,
        ):
            with patch(
                "sector_rotation_service.load_recent_daily",
                return_value=history,
            ):
                with patch(
                    "sector_rotation_service.load_moneyflow",
                    side_effect=moneyflow,
                ):
                    return (
                        sector_rotation_service
                        .build_tomorrow_sector_rotation(
                            limit=overrides.get("limit", 5),
                            stocks_per_sector=overrides.get(
                                "stocks_per_sector", 3
                            ),
                        )
                    )


class SectorRotationServiceTests(unittest.TestCase):
    def test_groups_ratings_and_capital_flow_windows(self):
        result = build_with_patches()
        groups = result["groups"]

        self.assertEqual(result["trade_date"], CURRENT)
        self.assertEqual(
            result["lookback_trade_dates"],
            [PREV2, PREV1, CURRENT],
        )
        self.assertEqual(
            result["moneyflow_trade_dates"],
            [PREV2, PREV1, CURRENT],
        )
        self.assertEqual(
            result["macd_basis"]["macd_parameter_key"],
            "macd-4-10-5-v1",
        )

        priority = groups["priority_focus"]
        self.assertTrue(priority)
        robot = next(
            row for row in priority if row["industry_name"] == "机器人"
        )
        self.assertEqual(robot["trend_rating"], "strong")
        self.assertEqual(robot["capital_flow"]["trend"], "inflow")
        self.assertEqual(
            robot["capital_flow"]["net_amount_3d"],
            [1300000000.0, 800000000.0, 1300000000.0],
        )
        self.assertTrue(robot["macd"]["ready"])
        self.assertEqual(robot["macd"]["status"], "golden_cross")
        self.assertEqual(robot["macd"]["zero_axis"], "above")
        self.assertEqual(robot["macd"]["histogram_trend"], "red_expand")
        self.assertIn("金叉", robot["macd"]["status_text"])
        self.assertTrue(robot["attack_leaders"])
        self.assertTrue(robot["catchup_candidates"])
        attack_scores = [
            row["attack_score"] for row in robot["attack_leaders"]
        ]
        self.assertEqual(attack_scores, sorted(attack_scores, reverse=True))

        caution = groups["caution_avoid"]
        self.assertTrue(caution)
        pharma = next(
            row for row in caution if row["industry_name"] == "医药"
        )
        self.assertEqual(pharma["trend_rating"], "avoid")
        self.assertEqual(pharma["capital_flow"]["trend"], "outflow")
        self.assertEqual(pharma["macd"]["status"], "death_cross")
        self.assertEqual(pharma["macd"]["zero_axis"], "below")
        self.assertEqual(pharma["macd"]["histogram_trend"], "green_expand")
        self.assertTrue(pharma["catchup_candidates"])
        catchup_scores = [
            row["catchup_score"]
            for row in pharma["catchup_candidates"]
        ]
        self.assertEqual(catchup_scores, sorted(catchup_scores, reverse=True))

        # 同组内 rank 连续且从 1 开始
        for rows in groups.values():
            self.assertEqual(
                [row["rank"] for row in rows],
                list(range(1, len(rows) + 1)),
            )
        # 全部板块都被分到某个组
        grouped_names = {
            row["industry_name"]
            for rows in groups.values()
            for row in rows
        }
        self.assertIn("半导体", grouped_names)
        self.assertTrue(robot["trend_desc"])
        self.assertIn("近3日主力资金持续流入", robot["trend_desc"])

    def test_payload_is_strict_json_serializable_with_missing_numeric_values(self):
        market = market_rows()
        market.loc[0, "volume_ratio"] = float("nan")
        market.loc[1, "turnover_rate"] = float("nan")
        history = history_rows()
        history.loc[0, "high"] = float("nan")

        result = build_with_patches(market=market, history=history)
        json.dumps(result, ensure_ascii=False, allow_nan=False)

    def test_missing_one_moneyflow_day_degrades_to_watch(self):
        def partial_moneyflow(date):
            if date == PREV1:
                return pd.DataFrame()
            return moneyflow_for(date)

        result = build_with_patches(moneyflow=partial_moneyflow)

        self.assertTrue(result["warnings"])
        self.assertIn(PREV1, result["warnings"][0])
        rows = [
            row
            for rows in result["groups"].values()
            for row in rows
        ]
        self.assertTrue(rows)
        self.assertTrue(
            all(row["trend_rating"] == "watch" for row in rows)
        )
        self.assertTrue(
            all(row["capital_flow"]["trend"] == "unknown" for row in rows)
        )
        confidences = {row["confidence"] for row in rows}
        self.assertNotIn("高", confidences)

    def test_no_moneyflow_returns_empty_groups_with_warning(self):
        result = build_with_patches(moneyflow=lambda date: pd.DataFrame())

        self.assertTrue(
            all(
                not rows for rows in result["groups"].values()
            )
        )
        self.assertIn("暂无足够资金流数据", " ".join(result["warnings"]))

    def test_less_than_two_complete_dates_returns_empty_groups(self):
        result = build_with_patches(complete_dates=[CURRENT])

        self.assertTrue(
            all(
                not rows for rows in result["groups"].values()
            )
        )
        self.assertIn("完整交易日不足", " ".join(result["warnings"]))


if __name__ == "__main__":
    unittest.main()
