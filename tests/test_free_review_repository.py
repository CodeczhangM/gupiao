import json
import unittest
from datetime import datetime
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


class FreeReviewRepositoryTests(unittest.TestCase):
    def test_init_creates_versioned_snapshot_and_build_tables(self):
        import free_review_repository

        cursor, connection = fake_connection()
        with (
            patch.object(free_review_repository, "_schema_ready", False),
            patch(
                "free_review_repository.get_connection",
                return_value=connection,
            ),
        ):
            free_review_repository.init_free_review_schema()

        sql = "\n".join(
            call.args[0] for call in cursor.execute.call_args_list
        )
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS review_stock_snapshot",
            sql,
        )
        self.assertIn(
            "PRIMARY KEY (trade_date, ts_code, score_version)",
            sql,
        )
        self.assertIn(
            "INDEX idx_review_score",
            sql,
        )
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS review_snapshot_build",
            sql,
        )

    def test_replace_snapshot_deletes_only_version_then_inserts_rows(self):
        import free_review_repository

        cursor, connection = fake_connection()
        frame = pd.DataFrame([{
            "trade_date": "20260730",
            "ts_code": "600001.SH",
            "score_version": "free-review-v1",
            "name": "正常股份",
            "industry": "制造",
            "total_score": 78.5,
            "score_reasons": ["趋势向上"],
            "risk_flags": [],
            "missing_fields": ["roic"],
        }])
        with (
            patch(
                "free_review_repository.init_free_review_schema"
            ),
            patch(
                "free_review_repository.get_connection",
                return_value=connection,
            ),
        ):
            free_review_repository.replace_review_snapshot(
                "20260730",
                "free-review-v1",
                frame,
            )

        delete_sql, delete_params = cursor.execute.call_args.args
        self.assertIn("DELETE FROM review_stock_snapshot", delete_sql)
        self.assertEqual(
            delete_params,
            ("20260730", "free-review-v1"),
        )
        insert_sql, rows = cursor.executemany.call_args.args
        self.assertIn("INSERT INTO review_stock_snapshot", insert_sql)
        serialized = next(
            value for value in rows[0]
            if isinstance(value, str) and value.startswith("[")
        )
        self.assertEqual(json.loads(serialized), ["趋势向上"])

    def test_build_status_round_trips_timestamps(self):
        import free_review_repository

        cursor, connection = fake_connection()
        cursor.fetchone.return_value = {
            "trade_date": "20260730",
            "score_version": "free-review-v1",
            "status": "running",
            "stage": "financial",
            "started_at": datetime(2026, 7, 30, 18, 0),
            "completed_at": None,
            "updated_at": datetime(2026, 7, 30, 18, 1),
        }
        with (
            patch(
                "free_review_repository.init_free_review_schema"
            ),
            patch(
                "free_review_repository.get_connection",
                return_value=connection,
            ),
        ):
            result = free_review_repository.load_build_status(
                "20260730",
            )

        self.assertEqual(result["stage"], "financial")
        self.assertEqual(result["started_at"], "2026-07-30 18:00:00")

    def test_query_compiler_uses_whitelisted_fields_and_parameters(self):
        from free_review_models import FreeReviewQuery
        from free_review_repository import compile_review_where

        request = FreeReviewQuery(
            trade_date="20260730",
            keyword="制造",
            industries=["电子", "机械"],
            markets=["主板"],
            profit_state="profit",
            volume_state="active",
            growth_state="growth",
            ranges={
                "total_score": {"min": 65, "max": 90},
                "pe_ttm": {"min": 0, "max": 35},
            },
            sort_by="volume_ratio",
            sort_direction="asc",
            page=2,
            page_size=100,
        )
        sql, params = compile_review_where(request, "20260730")

        self.assertIn("trade_date=%s", sql)
        self.assertIn("(ts_code LIKE %s OR name LIKE %s", sql)
        self.assertIn("`industry` IN (%s,%s)", sql)
        self.assertIn("`total_score` >= %s", sql)
        self.assertIn("`pe_ttm` <= %s", sql)
        self.assertIn("volume_state=%s", sql)
        self.assertIn("growth_state=%s", sql)
        self.assertNotIn("制造", sql)
        self.assertIn("%制造%", params)

    def test_query_compiler_uses_current_macd_score_version(self):
        import free_review_repository
        from free_review_models import FreeReviewQuery

        with patch(
            "free_review_repository.current_score_version",
            return_value="free-review-v1-macd-5-34-5-v9",
        ):
            _sql, params = free_review_repository.compile_review_where(
                FreeReviewQuery(trade_date="20260730"),
                "20260730",
            )

        self.assertEqual(
            params[:2],
            ("20260730", "free-review-v1-macd-5-34-5-v9"),
        )

    def test_meta_reports_old_snapshot_as_stale_after_macd_change(self):
        import free_review_repository

        cursor, connection = fake_connection()
        cursor.fetchone.side_effect = [
            {"trade_date": None},
            {
                "trade_date": "20260730",
                "score_version": "free-review-v1-macd-5-34-5-v1",
                "generated_at": datetime(2026, 7, 30, 18, 0),
                "stock_count": 321,
            },
        ]
        with (
            patch(
                "free_review_repository.init_free_review_schema"
            ),
            patch(
                "free_review_repository.get_connection",
                return_value=connection,
            ),
            patch(
                "free_review_repository.current_score_version",
                return_value="free-review-v1-macd-6-35-6-v2",
            ),
            patch(
                "free_review_repository.macd_provenance",
                return_value={
                    "macd_fast_period": 6,
                    "macd_slow_period": 35,
                    "macd_signal_period": 6,
                    "macd_parameter_key": "macd-6-35-6-v2",
                },
            ),
        ):
            result = free_review_repository.load_review_meta()

        self.assertFalse(result["ready"])
        self.assertEqual(result["stock_count"], 0)
        self.assertEqual(result["stale_stock_count"], 321)
        self.assertEqual(
            result["stale_score_version"],
            "free-review-v1-macd-5-34-5-v1",
        )

    def test_query_model_rejects_unknown_filter_sort_and_page_size(self):
        from pydantic import ValidationError

        from free_review_models import FreeReviewQuery

        with self.assertRaises(ValidationError):
            FreeReviewQuery(ranges={"drop_table": {"min": 1}})
        with self.assertRaises(ValidationError):
            FreeReviewQuery(sort_by="drop_table")
        with self.assertRaises(ValidationError):
            FreeReviewQuery(page_size=5000)

    def test_query_model_accepts_financial_event_ranges_and_sort(self):
        from free_review_models import FreeReviewQuery

        query = FreeReviewQuery(
            ranges={
                "financial_event_hit": {"min": 1},
                "deducted_netprofit": {"min": 50_000_000},
                "deducted_netprofit_growth": {"min": 50},
                "announcement_return_5d": {"min": 0},
            },
            sort_by="financial_event_score",
        )

        self.assertEqual(query.sort_by, "financial_event_score")
        self.assertEqual(query.ranges["financial_event_hit"].min, 1)

    def test_replace_review_snapshot_persists_financial_event_columns(self):
        import free_review_repository

        cursor, connection = fake_connection()
        frame = pd.DataFrame([{
            "ts_code": "600001.SH",
            "name": "财报强股",
            "industry": "制造",
            "deducted_netprofit": 80_000_000,
            "deducted_netprofit_growth": 60.0,
            "financial_growth_basis": "single_quarter_qoq",
            "deducted_netprofit_threshold_hit": 1,
            "financial_growth_threshold_hit": 1,
            "financial_event_hit": 1,
            "financial_statement_end_date": "20260630",
            "financial_statement_ann_date": "20260715",
            "announcement_return_3d": 6.0,
            "announcement_return_5d": 8.0,
            "announcement_return_10d": 12.0,
            "announcement_max_return_10d": 16.0,
            "financial_event_score": 82.0,
            "sector_financial_event_score": 75.0,
        }])
        with (
            patch.object(free_review_repository, "_schema_ready", False),
            patch(
                "free_review_repository.get_connection",
                return_value=connection,
            ),
        ):
            free_review_repository.replace_review_snapshot(
                "20260730",
                "free-review-v1-macd-5-34-5",
                frame,
            )

        sql = "\n".join(
            call.args[0] for call in cursor.execute.call_args_list
        )
        insert_sql = cursor.executemany.call_args.args[0]
        self.assertIn("financial_event_score", sql)
        self.assertIn("financial_event_hit", insert_sql)

    def test_paginated_query_decodes_json_and_returns_total(self):
        import free_review_repository
        from free_review_models import FreeReviewQuery

        cursor, connection = fake_connection()
        cursor.fetchone.return_value = {"total": 1}
        cursor.fetchall.return_value = [{
            "trade_date": "20260730",
            "ts_code": "600001.SH",
            "score_reasons": '["趋势向上"]',
            "risk_flags": "[]",
            "missing_fields": '["roic"]',
            "generated_at": datetime(2026, 7, 30, 18, 0),
        }]
        with (
            patch(
                "free_review_repository.init_free_review_schema"
            ),
            patch(
                "free_review_repository.get_connection",
                return_value=connection,
            ),
        ):
            result = free_review_repository.query_review_snapshot(
                FreeReviewQuery(trade_date="20260730")
            )

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["score_reasons"], ["趋势向上"])
        self.assertEqual(
            result["items"][0]["generated_at"],
            "2026-07-30 18:00:00",
        )
        select_sql, select_params = cursor.execute.call_args.args
        self.assertIn("ORDER BY `total_score` DESC", select_sql)
        self.assertEqual(select_params[-2:], (50, 0))

    def test_sector_aggregation_contains_required_metrics(self):
        import free_review_repository

        cursor, connection = fake_connection()
        cursor.fetchall.return_value = [{
            "industry": "电子",
            "stock_count": 12,
            "avg_pct_chg": 1.2,
            "up_ratio": 0.75,
            "median_volume_ratio": 1.4,
            "avg_turnover_rate": 3.2,
            "avg_pe_ttm": 28.0,
            "avg_total_score": 71.5,
        }]
        with (
            patch(
                "free_review_repository.init_free_review_schema"
            ),
            patch(
                "free_review_repository.get_connection",
                return_value=connection,
            ),
        ):
            result = free_review_repository.load_review_sectors(
                "20260730"
            )

        expected = {
            "industry", "stock_count", "avg_pct_chg", "up_ratio",
            "median_volume_ratio", "avg_turnover_rate",
            "avg_pe_ttm", "avg_total_score",
        }
        self.assertTrue(expected.issubset(result[0]))


if __name__ == "__main__":
    unittest.main()
