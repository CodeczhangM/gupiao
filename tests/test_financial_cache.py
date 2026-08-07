import unittest
from unittest.mock import MagicMock, patch

import pandas as pd


def fake_connection():
    cursor = MagicMock()
    cursor_context = MagicMock()
    cursor_context.__enter__.return_value = cursor
    connection = MagicMock()
    connection.cursor.return_value = cursor_context
    connection_context = MagicMock()
    connection_context.__enter__.return_value = connection
    return cursor, connection_context


class FinancialCacheTests(unittest.TestCase):
    def test_quarter_periods_returns_latest_eight_calendar_quarters(self):
        import financial_cache

        self.assertEqual(
            financial_cache.quarter_periods("20260730", 8),
            [
                "20260630", "20260331", "20251231", "20250930",
                "20250630", "20250331", "20241231", "20240930",
            ],
        )

    def test_init_creates_financial_and_sync_tables_with_keys(self):
        import financial_cache

        cursor, connection = fake_connection()
        with (
            patch.object(financial_cache, "_schema_ready", False),
            patch(
                "financial_cache.get_connection",
                return_value=connection,
            ),
        ):
            financial_cache.init_financial_cache()

        sql = "\n".join(
            call.args[0] for call in cursor.execute.call_args_list
        )
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS financial_indicator_cache",
            sql,
        )
        self.assertIn(
            "PRIMARY KEY (ts_code, end_date, ann_date)",
            sql,
        )
        self.assertIn(
            "INDEX idx_financial_period (end_date, ann_date)",
            sql,
        )
        self.assertIn(
            "INDEX idx_financial_code_announcement (ts_code, ann_date)",
            sql,
        )
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS financial_cache_sync",
            sql,
        )
        self.assertIn("PRIMARY KEY (source_name, end_date)", sql)

    def test_financial_fields_include_profit_dedt(self):
        import financial_cache

        self.assertIn("profit_dedt", financial_cache.FINANCIAL_NUMERIC_FIELDS)
        self.assertIn("profit_dedt", financial_cache.FINANCIAL_FIELDS)

    def test_init_migrates_profit_dedt_column(self):
        import financial_cache

        cursor, connection = fake_connection()
        with (
            patch.object(financial_cache, "_schema_ready", False),
            patch(
                "financial_cache.get_connection",
                return_value=connection,
            ),
        ):
            financial_cache.init_financial_cache()

        sql = "\n".join(
            call.args[0] for call in cursor.execute.call_args_list
        )
        self.assertIn("profit_dedt DOUBLE", sql)
        self.assertIn("ALTER TABLE financial_indicator_cache", sql)
        self.assertIn("ADD COLUMN profit_dedt DOUBLE NULL", sql)

    def test_sync_fetches_only_missing_periods(self):
        import financial_cache

        rows = pd.DataFrame([{
            "ts_code": "600001.SH",
            "ann_date": "20260715",
            "end_date": "20260630",
            "roe": 12.5,
        }])
        loader = MagicMock(return_value=rows)
        with (
            patch(
                "financial_cache._complete_periods",
                return_value={"20260331"},
            ),
            patch(
                "financial_cache._upsert_financial_rows",
                return_value=1,
            ) as save_rows,
            patch("financial_cache._save_sync_state"),
            patch(
                "financial_cache.quarter_periods",
                return_value=["20260630", "20260331"],
            ),
        ):
            result = financial_cache.sync_financial_indicators(
                loader,
                "20260730",
                quarters=2,
            )

        loader.assert_called_once()
        self.assertEqual(
            loader.call_args.args[0],
            "fina_indicator_vip",
        )
        self.assertEqual(
            loader.call_args.kwargs["period"],
            "20260630",
        )
        save_rows.assert_called_once()
        self.assertEqual(result["synced_periods"], 1)
        self.assertEqual(result["cached_periods"], 1)

    def test_complete_periods_requires_profit_dedt_coverage(self):
        import financial_cache

        cursor, connection = fake_connection()
        cursor.fetchall.return_value = [
            {"end_date": "20260630", "profit_dedt_count": 0},
            {"end_date": "20260331", "profit_dedt_count": 12},
        ]
        with (
            patch.object(financial_cache, "_schema_ready", True),
            patch(
                "financial_cache.get_connection",
                return_value=connection,
            ),
        ):
            result = financial_cache._complete_periods([
                "20260630",
                "20260331",
            ])

        self.assertEqual(result, {"20260331"})

    def test_point_in_time_selection_excludes_future_announcements(self):
        import financial_cache

        raw = pd.DataFrame([
            {
                "ts_code": "600001.SH",
                "end_date": "20260331",
                "ann_date": "20260420",
                "update_flag": "0",
                "roe": 10,
            },
            {
                "ts_code": "600001.SH",
                "end_date": "20260331",
                "ann_date": "20260425",
                "update_flag": "1",
                "roe": 11,
            },
            {
                "ts_code": "600001.SH",
                "end_date": "20260630",
                "ann_date": "20260820",
                "update_flag": "1",
                "roe": 20,
            },
        ])
        with patch(
            "financial_cache._load_financial_period_rows",
            return_value=raw,
        ):
            result = financial_cache.load_financial_as_of(
                "20260730",
                periods=8,
            )

        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["roe"], 11)
        self.assertEqual(result.iloc[0]["ann_date"], "20260425")


if __name__ == "__main__":
    unittest.main()
