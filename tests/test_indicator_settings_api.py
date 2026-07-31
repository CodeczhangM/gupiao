import unittest
from unittest.mock import patch

from fastapi import HTTPException


class IndicatorSettingsApiTests(unittest.TestCase):
    def test_get_returns_periods_version_key_and_update_time(self):
        import app

        current = {
            "fast_period": 5,
            "slow_period": 34,
            "signal_period": 5,
            "version": 4,
            "updated_at": "2026-07-31 10:00:00",
        }
        with patch(
            "app.load_macd_settings",
            return_value=current,
        ):
            result = app.get_macd_indicator_settings()

        self.assertEqual(result["fast_period"], 5)
        self.assertEqual(result["macd_parameter_key"], "macd-5-34-5-v4")

    def test_put_saves_and_returns_recalculation_state(self):
        import app
        from indicator_settings_models import MacdSettingsUpdate

        expected = {
            "settings": {
                "fast_period": 6,
                "slow_period": 35,
                "signal_period": 6,
                "version": 2,
                "macd_parameter_key": "macd-6-35-6-v2",
            },
            "free_review_build": {"status": "pending"},
        }
        with patch(
            "app.save_macd_settings_and_recalculate",
            return_value=expected,
        ) as save:
            result = app.put_macd_indicator_settings(
                MacdSettingsUpdate(
                    fast_period=6,
                    slow_period=35,
                    signal_period=6,
                )
            )

        save.assert_called_once_with(6, 35, 6)
        self.assertEqual(result, expected)

    def test_put_maps_database_failure_to_502(self):
        import app
        from indicator_settings_models import MacdSettingsUpdate

        with patch(
            "app.save_macd_settings_and_recalculate",
            side_effect=RuntimeError("database offline"),
        ):
            with self.assertRaises(HTTPException) as raised:
                app.put_macd_indicator_settings(
                    MacdSettingsUpdate(
                        fast_period=5,
                        slow_period=34,
                        signal_period=5,
                    )
                )

        self.assertEqual(raised.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
