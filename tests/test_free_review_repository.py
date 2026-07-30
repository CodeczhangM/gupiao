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


if __name__ == "__main__":
    unittest.main()
