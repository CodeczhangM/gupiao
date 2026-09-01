import unittest
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch

from cycle_watch_repository import (
    delete_watch_stock,
    init_cycle_watch_schema,
    list_cycle_history,
    mark_cycle_alerts_read,
    save_cycle_evaluation,
    upsert_watch_stock,
)


class FakeCursor:
    def __init__(self, fetchone_values=None, fetchall_value=None, lastrowid=41, rowcount=0):
        self.statements = []
        self.fetchone_values = list(fetchone_values or [])
        self.fetchall_value = list(fetchall_value or [])
        self.lastrowid = lastrowid
        self.rowcount = rowcount

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.statements.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.fetchone_values.pop(0) if self.fetchone_values else None

    def fetchall(self):
        return self.fetchall_value


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def connection_factory(cursor):
    @contextmanager
    def fake_connection():
        yield FakeConnection(cursor)
    return fake_connection


class CycleWatchRepositoryTests(unittest.TestCase):
    def test_schema_contains_watchlist_evaluations_and_idempotent_slot(self):
        cursor = FakeCursor()
        with patch("cycle_watch_repository.get_connection", connection_factory(cursor)):
            init_cycle_watch_schema()

        sql = " ".join(statement for statement, _params in cursor.statements)
        self.assertIn("CREATE TABLE IF NOT EXISTS cycle_watchlist", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS cycle_watch_evaluations", sql)
        self.assertIn("UNIQUE KEY uq_cycle_eval_slot (ts_code, trade_date, schedule_slot)", sql)

    def test_upsert_restores_existing_stock_and_returns_decoded_row(self):
        row = {
            "ts_code": "600000.SH", "name": "浦发银行", "note": "银行周期",
            "planned_low_price": 9.8, "planned_high_price": 10.2,
            "enabled": 1, "created_at": datetime(2026, 8, 28, 10, 0),
            "updated_at": datetime(2026, 8, 28, 10, 1), "last_checked_at": None,
            "latest_evaluation_id": None,
        }
        cursor = FakeCursor(fetchone_values=[row])
        with (
            patch("cycle_watch_repository.init_cycle_watch_schema"),
            patch("cycle_watch_repository.get_connection", connection_factory(cursor)),
        ):
            saved = upsert_watch_stock({
                "ts_code": "600000.SH", "name": "浦发银行", "note": "银行周期",
                "planned_low_price": 9.8, "planned_high_price": 10.2,
            })

        insert_sql = cursor.statements[0][0]
        self.assertIn("ON DUPLICATE KEY UPDATE", insert_sql)
        self.assertIn("enabled = 1", insert_sql)
        self.assertTrue(saved["enabled"])
        self.assertEqual(saved["created_at"], "2026-08-28 10:00:00")

    def test_save_evaluation_uses_slot_idempotency_and_updates_latest_pointer(self):
        cursor = FakeCursor(lastrowid=77)
        evaluation = {
            "ts_code": "600000.SH", "trade_date": "20260828",
            "checked_at": datetime(2026, 8, 28, 10, 35), "data_as_of": "2026-08-28 10:34:00",
            "status": "low_buy", "status_label": "低吸提示", "opportunity_score": 68,
            "current_price": 10.0, "pct_chg": -0.2, "support_price": 9.9,
            "matched_conditions": ["回撤缩量"], "missing_conditions": ["等待60分钟确认"],
            "risk_items": [], "invalidation_reason": "跌破MA20失效",
            "factors": {"rule_version": "cycle-entry-v1"}, "is_new_alert": True,
        }
        with (
            patch("cycle_watch_repository.init_cycle_watch_schema"),
            patch("cycle_watch_repository.get_connection", connection_factory(cursor)),
        ):
            saved = save_cycle_evaluation(evaluation, "1035")

        sql = " ".join(statement for statement, _params in cursor.statements)
        self.assertIn("ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)", sql)
        self.assertIn("latest_evaluation_id", sql)
        self.assertEqual(saved["id"], 77)

    def test_save_evaluation_only_passes_sql_parameters_to_driver(self):
        class RejectContainerParametersCursor(FakeCursor):
            def execute(self, sql, params=None):
                if isinstance(params, dict):
                    unsupported = {
                        key: value
                        for key, value in params.items()
                        if isinstance(value, (dict, list))
                    }
                    if unsupported:
                        raise TypeError(
                            f"container values can not be used as parameters: {sorted(unsupported)}"
                        )
                super().execute(sql, params)

        cursor = RejectContainerParametersCursor(lastrowid=78)
        evaluation = {
            "ts_code": "600000.SH", "trade_date": "20260828",
            "checked_at": datetime(2026, 8, 28, 10, 35), "data_as_of": "2026-08-28 10:34:00",
            "status": "low_buy", "status_label": "低吸提示", "opportunity_score": 68,
            "current_price": 10.0, "pct_chg": -0.2, "support_price": 9.9,
            "matched_conditions": ["回撤缩量"], "missing_conditions": ["等待60分钟确认"],
            "risk_items": [], "invalidation_reason": "跌破MA20失效",
            "factors": {"rule_version": "cycle-entry-v1"}, "is_new_alert": True,
        }
        with (
            patch("cycle_watch_repository.init_cycle_watch_schema"),
            patch("cycle_watch_repository.get_connection", connection_factory(cursor)),
        ):
            saved = save_cycle_evaluation(evaluation, "1035")

        insert_params = cursor.statements[0][1]
        self.assertNotIn("matched_conditions", insert_params)
        self.assertNotIn("missing_conditions", insert_params)
        self.assertNotIn("risk_items", insert_params)
        self.assertNotIn("factors", insert_params)
        self.assertEqual(saved["id"], 78)

    def test_delete_watch_stock_does_not_delete_history(self):
        cursor = FakeCursor(rowcount=1)
        with (
            patch("cycle_watch_repository.init_cycle_watch_schema"),
            patch("cycle_watch_repository.get_connection", connection_factory(cursor)),
        ):
            deleted = delete_watch_stock("600000.SH")

        sql = " ".join(statement for statement, _params in cursor.statements)
        self.assertTrue(deleted)
        self.assertIn("DELETE FROM cycle_watchlist", sql)
        self.assertNotIn("DELETE FROM cycle_watch_evaluations", sql)

    def test_history_decodes_json_and_alert_read_returns_count(self):
        history_cursor = FakeCursor(fetchall_value=[{
            "id": 1, "ts_code": "600000.SH", "trade_date": "20260828",
            "checked_at": datetime(2026, 8, 28, 10, 35), "data_as_of": None,
            "status": "low_buy", "status_label": "低吸提示", "opportunity_score": 68,
            "current_price": 10, "pct_chg": 0, "support_price": 9.9,
            "matched_conditions_json": '["回撤缩量"]',
            "missing_conditions_json": '[]', "risk_items_json": '[]',
            "invalidation_reason": "", "factors_json": '{"daily_score": 68}',
            "is_new_alert": 1, "alert_read": 0, "schedule_slot": "1035",
        }])
        with (
            patch("cycle_watch_repository.init_cycle_watch_schema"),
            patch("cycle_watch_repository.get_connection", connection_factory(history_cursor)),
        ):
            rows = list_cycle_history("600000.SH", 20)
        self.assertEqual(rows[0]["matched_conditions"], ["回撤缩量"])
        self.assertEqual(rows[0]["factors"]["daily_score"], 68)

        read_cursor = FakeCursor(rowcount=2)
        with (
            patch("cycle_watch_repository.init_cycle_watch_schema"),
            patch("cycle_watch_repository.get_connection", connection_factory(read_cursor)),
        ):
            self.assertEqual(mark_cycle_alerts_read("20260828"), 2)


if __name__ == "__main__":
    unittest.main()
