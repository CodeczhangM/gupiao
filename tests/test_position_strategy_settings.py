import json
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch


def fake_connection():
    cursor = MagicMock()
    cursor_context = MagicMock()
    cursor_context.__enter__.return_value = cursor
    connection = MagicMock()
    connection.cursor.return_value = cursor_context
    connection_context = MagicMock()
    connection_context.__enter__.return_value = connection
    return cursor, connection_context


class PositionStrategySettingsTests(unittest.TestCase):
    def test_defaults_contain_every_strategy_group(self):
        from position_strategy_settings import DEFAULT_POSITION_STRATEGY_SETTINGS

        self.assertEqual(
            set(DEFAULT_POSITION_STRATEGY_SETTINGS),
            {"pressure", "breakout", "distance", "risk_reward", "network"},
        )
        self.assertEqual(DEFAULT_POSITION_STRATEGY_SETTINGS["distance"], {
            "critical_pct": 1.5, "waiting_pct": 3.0, "observe_pct": 5.0,
        })
        self.assertEqual(DEFAULT_POSITION_STRATEGY_SETTINGS["network"]["stage_budget_seconds"], 15)

    def test_validation_rejects_unordered_distance_thresholds(self):
        from position_strategy_settings import validate_position_strategy_settings

        with self.assertRaisesRegex(ValueError, "距离阈值"):
            validate_position_strategy_settings({
                "distance": {"critical_pct": 3, "waiting_pct": 2, "observe_pct": 5}
            })

    def test_validation_deep_merges_partial_settings_without_mutating_defaults(self):
        from position_strategy_settings import (
            DEFAULT_POSITION_STRATEGY_SETTINGS,
            validate_position_strategy_settings,
        )

        result = validate_position_strategy_settings({"breakout": {"confirm_pct": 0.8}})
        result["pressure"]["history_days"] = 999

        self.assertEqual(result["breakout"]["confirm_pct"], 0.8)
        self.assertEqual(DEFAULT_POSITION_STRATEGY_SETTINGS["pressure"]["history_days"], 60)

    def test_load_decodes_json_version_and_timestamp(self):
        import position_strategy_settings as module

        cursor, connection = fake_connection()
        cursor.fetchone.return_value = {
            "settings_json": json.dumps({"distance": {"critical_pct": 1.2}}),
            "version": 4,
            "updated_at": datetime(2026, 8, 31, 15, 0),
        }
        with (
            patch.object(module, "init_position_strategy_settings"),
            patch.object(module, "get_connection", return_value=connection),
            patch.object(module, "_settings_cache", None),
        ):
            result = module.load_position_strategy_settings(force=True)

        self.assertEqual(result["distance"]["critical_pct"], 1.2)
        self.assertEqual(result["distance"]["waiting_pct"], 3.0)
        self.assertEqual(result["version"], 4)
        self.assertEqual(result["updated_at"], "2026-08-31 15:00:00")

    def test_update_locks_row_and_increments_version(self):
        import position_strategy_settings as module

        cursor, connection = fake_connection()
        cursor.fetchone.return_value = {
            "settings_json": json.dumps(module.DEFAULT_POSITION_STRATEGY_SETTINGS),
            "version": 7,
        }
        with (
            patch.object(module, "init_position_strategy_settings"),
            patch.object(module, "get_connection", return_value=connection),
            patch.object(module, "_settings_cache", None),
        ):
            result = module.update_position_strategy_settings({
                "breakout": {"confirm_pct": 0.7}
            })

        sql = "\n".join(call.args[0] for call in cursor.execute.call_args_list)
        self.assertIn("FOR UPDATE", sql)
        self.assertEqual(result["version"], 8)
        self.assertEqual(result["breakout"]["confirm_pct"], 0.7)

    def test_parameter_key_uses_version(self):
        from position_strategy_settings import position_strategy_parameter_key

        self.assertEqual(
            position_strategy_parameter_key({"version": 12}),
            "position-strategy-v12",
        )


if __name__ == "__main__":
    unittest.main()
