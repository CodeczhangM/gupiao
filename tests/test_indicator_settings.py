import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
from pandas.testing import assert_series_equal


def fake_connection():
    cursor = MagicMock()
    cursor_context = MagicMock()
    cursor_context.__enter__.return_value = cursor
    connection = MagicMock()
    connection.cursor.return_value = cursor_context
    connection_context = MagicMock()
    connection_context.__enter__.return_value = connection
    return cursor, connection_context


class IndicatorSettingsTests(unittest.TestCase):
    def test_init_creates_global_macd_setting_with_defaults(self):
        import indicator_settings

        cursor, connection = fake_connection()
        with (
            patch.object(indicator_settings, "_schema_ready", False),
            patch(
                "indicator_settings.get_connection",
                return_value=connection,
            ),
        ):
            indicator_settings.init_indicator_settings()

        sql = "\n".join(
            call.args[0] for call in cursor.execute.call_args_list
        )
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS indicator_settings",
            sql,
        )
        self.assertIn("fast_period", sql)
        self.assertIn("slow_period", sql)
        self.assertIn("signal_period", sql)
        insert_call = cursor.execute.call_args_list[-1]
        self.assertEqual(insert_call.args[1], (5, 34, 5))

    def test_validation_requires_integer_ranges_and_fast_below_slow(self):
        from indicator_settings import validate_macd_settings

        self.assertEqual(
            validate_macd_settings(5, 34, 5),
            {
                "fast_period": 5,
                "slow_period": 34,
                "signal_period": 5,
            },
        )
        for values in (
            (1, 34, 5),
            (5, 121, 5),
            (5, 34, 1),
            (34, 34, 5),
            (35, 34, 5),
            (5.5, 34, 5),
            (True, 34, 5),
        ):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    validate_macd_settings(*values)

    def test_load_serializes_version_and_timestamp(self):
        import indicator_settings

        cursor, connection = fake_connection()
        cursor.fetchone.return_value = {
            "fast_period": 5,
            "slow_period": 34,
            "signal_period": 5,
            "version": 3,
            "updated_at": datetime(2026, 7, 31, 9, 30),
        }
        with (
            patch("indicator_settings.init_indicator_settings"),
            patch(
                "indicator_settings.get_connection",
                return_value=connection,
            ),
            patch.object(indicator_settings, "_settings_cache", None),
        ):
            result = indicator_settings.load_macd_settings(force=True)

        self.assertEqual(result["version"], 3)
        self.assertEqual(result["updated_at"], "2026-07-31 09:30:00")

    def test_update_locks_row_and_increments_version(self):
        import indicator_settings

        cursor, connection = fake_connection()
        cursor.fetchone.return_value = {"version": 7}
        with (
            patch("indicator_settings.init_indicator_settings"),
            patch(
                "indicator_settings.get_connection",
                return_value=connection,
            ),
            patch.object(indicator_settings, "_settings_cache", None),
        ):
            result = indicator_settings.update_macd_settings(6, 35, 6)

        sql = "\n".join(
            call.args[0] for call in cursor.execute.call_args_list
        )
        self.assertIn("FOR UPDATE", sql)
        self.assertIn("version=%s", sql)
        update_params = cursor.execute.call_args_list[-1].args[1]
        self.assertEqual(update_params, (6, 35, 6, 8))
        self.assertEqual(result["version"], 8)

    def test_calculator_matches_pandas_ewm_definition(self):
        from indicator_settings import calculate_macd

        close = pd.Series(range(1, 61), dtype="float64")
        settings = {
            "fast_period": 5,
            "slow_period": 34,
            "signal_period": 5,
        }
        dif, dea, histogram = calculate_macd(close, settings)
        expected_dif = (
            close.ewm(span=5, adjust=False, min_periods=5).mean()
            - close.ewm(span=34, adjust=False, min_periods=34).mean()
        )
        expected_dea = expected_dif.ewm(
            span=5,
            adjust=False,
            min_periods=5,
        ).mean()

        assert_series_equal(dif, expected_dif)
        assert_series_equal(dea, expected_dea)
        assert_series_equal(histogram, (expected_dif - expected_dea) * 2)

    def test_parameter_key_contains_periods_and_version(self):
        from indicator_settings import macd_parameter_key

        self.assertEqual(
            macd_parameter_key({
                "fast_period": 5,
                "slow_period": 34,
                "signal_period": 5,
                "version": 4,
            }),
            "macd-5-34-5-v4",
        )


if __name__ == "__main__":
    unittest.main()
