import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd

import realtime_cache


def fake_connection():
    cursor = MagicMock()
    cursor_context = MagicMock()
    cursor_context.__enter__.return_value = cursor
    connection = MagicMock()
    connection.cursor.return_value = cursor_context
    connection_context = MagicMock()
    connection_context.__enter__.return_value = connection
    return cursor, connection_context


class RealtimeCacheTests(unittest.TestCase):
    def test_init_creates_minute_and_result_tables(self):
        cursor, connection = fake_connection()

        with patch(
            "realtime_cache.get_connection",
            return_value=connection,
        ):
            realtime_cache.init_realtime_cache()

        sql = "\n".join(
            call.args[0]
            for call in cursor.execute.call_args_list
        )
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS realtime_minute_cache",
            sql,
        )
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS realtime_result_cache",
            sql,
        )

    def test_result_cache_decodes_json_and_timestamp(self):
        cursor, connection = fake_connection()
        cursor.fetchone.return_value = {
            "payload_json": (
                '{"stocks":[{"ts_code":"600001.SH"}]}'
            ),
            "updated_at": datetime(2026, 7, 30, 14, 40),
        }

        with patch(
            "realtime_cache.get_connection",
            return_value=connection,
        ):
            result = realtime_cache.load_result_cache(
                "realtime_info",
                "limit=10",
            )

        self.assertEqual(
            result["payload"]["stocks"][0]["ts_code"],
            "600001.SH",
        )
        self.assertEqual(
            result["updated_at"],
            "2026-07-30 14:40:00",
        )

    def test_result_cache_saves_trade_date_and_json_payload(self):
        cursor, connection = fake_connection()
        payload = {
            "trade_date": "20260730",
            "data_as_of": "2026-07-30 14:39:00",
            "data_status": "live",
            "stocks": [{"ts_code": "600001.SH"}],
        }

        with patch(
            "realtime_cache.get_connection",
            return_value=connection,
        ):
            realtime_cache.save_result_cache(
                "intraday_monitor",
                "default",
                payload,
            )

        sql, params = cursor.execute.call_args.args
        self.assertIn(
            "INSERT INTO realtime_result_cache",
            sql,
        )
        self.assertEqual(params["trade_date"], "20260730")
        self.assertIn('"ts_code": "600001.SH"', params["payload_json"])

    def test_minute_cache_upserts_normalized_rows(self):
        frame = pd.DataFrame(
            [
                {
                    "ts_code": "600001.SH",
                    "trade_time": "2026-07-30 14:39:00",
                    "open": 10,
                    "high": 10.2,
                    "low": 9.9,
                    "close": 10.1,
                    "vol": 1000,
                    "amount": 10100,
                }
            ]
        )
        cursor, connection = fake_connection()

        with patch(
            "realtime_cache.get_connection",
            return_value=connection,
        ):
            realtime_cache.save_minute_cache(
                frame,
                "1min",
                "eastmoney_fallback",
                "20260730",
            )

        self.assertEqual(cursor.executemany.call_count, 1)
        _sql, rows = cursor.executemany.call_args.args
        self.assertEqual(rows[0][0], "600001.SH")
        self.assertEqual(rows[0][-2], "20260730")

    def test_minute_cache_loads_ordered_window(self):
        cursor, connection = fake_connection()
        cursor.fetchall.return_value = [
            {
                "ts_code": "600001.SH",
                "trade_time": datetime(2026, 7, 30, 14, 39),
                "open": 10,
                "high": 10.2,
                "low": 9.9,
                "close": 10.1,
                "vol": 1000,
                "amount": 10100,
            }
        ]

        with patch(
            "realtime_cache.get_connection",
            return_value=connection,
        ):
            result = realtime_cache.load_minute_cache(
                "600001.SH",
                "2026-07-30 09:30:00",
                "2026-07-30 14:40:00",
                "1min",
            )

        self.assertEqual(result.iloc[0]["close"], 10.1)
        self.assertEqual(
            result.iloc[0]["trade_time"],
            pd.Timestamp("2026-07-30 14:39:00"),
        )

    def test_current_minute_cache_requires_requested_end_within_90_seconds(
        self,
    ):
        frame = pd.DataFrame(
            [
                {"trade_time": "2026-07-30 09:30:00"},
                {"trade_time": "2026-07-30 14:39:00"},
            ]
        )

        self.assertTrue(
            realtime_cache.minute_cache_is_fresh(
                frame,
                "2026-07-30 09:30:00",
                "2026-07-30 14:40:00",
                datetime(2026, 7, 30, 14, 40),
                "1min",
            )
        )
        self.assertFalse(
            realtime_cache.minute_cache_is_fresh(
                frame,
                "2026-07-30 09:30:00",
                "2026-07-30 14:42:00",
                datetime(2026, 7, 30, 14, 42),
                "1min",
            )
        )

    def test_prune_keeps_exactly_supplied_five_trade_dates(self):
        cursor, connection = fake_connection()
        keep = [
            "20260730",
            "20260729",
            "20260728",
            "20260727",
            "20260724",
        ]

        with patch(
            "realtime_cache.get_connection",
            return_value=connection,
        ):
            realtime_cache.prune_realtime_cache(keep)

        delete_calls = [
            call
            for call in cursor.execute.call_args_list
            if call.args[0].lstrip().startswith("DELETE")
        ]
        self.assertEqual(len(delete_calls), 2)
        self.assertIn(
            "DELETE FROM realtime_minute_cache",
            delete_calls[0].args[0],
        )
        self.assertIn(
            "DELETE FROM realtime_result_cache",
            delete_calls[1].args[0],
        )
        self.assertEqual(delete_calls[0].args[1], tuple(keep))


if __name__ == "__main__":
    unittest.main()
