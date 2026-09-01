import unittest
import importlib
from unittest.mock import MagicMock, patch

from fastapi import HTTPException


def load_app():
    with (
        patch("tushare.set_token"),
        patch("tushare.pro_api", return_value=MagicMock()),
    ):
        return importlib.import_module("app")


class PositionStrategySettingsApiTests(unittest.TestCase):
    def test_get_returns_settings_and_parameter_key(self):
        app = load_app()

        current = {
            "pressure": {"history_days": 60},
            "version": 4,
            "updated_at": "2026-08-31 15:00:00",
        }
        with patch("app.load_position_strategy_settings", return_value=current):
            result = app.get_position_strategy_settings()

        self.assertEqual(result["pressure"]["history_days"], 60)
        self.assertEqual(result["position_strategy_parameter_key"], "position-strategy-v4")

    def test_put_forwards_nested_partial_payload(self):
        app = load_app()
        from position_strategy_settings_models import PositionStrategySettingsUpdate

        expected = {
            "settings": {"version": 2},
            "position_strategy_parameter_key": "position-strategy-v2",
        }
        request = PositionStrategySettingsUpdate.model_validate({
            "breakout": {"confirm_pct": 0.7},
            "network": {"stage_budget_seconds": 12},
        })
        with patch("app.save_position_strategy_settings", return_value=expected) as save:
            result = app.put_position_strategy_settings(request)

        save.assert_called_once_with({
            "breakout": {"confirm_pct": 0.7},
            "network": {"stage_budget_seconds": 12},
        })
        self.assertEqual(result, expected)

    def test_put_maps_validation_failure_to_422(self):
        app = load_app()
        from position_strategy_settings_models import PositionStrategySettingsUpdate

        request = PositionStrategySettingsUpdate.model_validate({
            "distance": {"critical_pct": 3, "waiting_pct": 2, "observe_pct": 5}
        })
        with patch("app.save_position_strategy_settings", side_effect=ValueError("距离阈值错误")):
            with self.assertRaises(HTTPException) as raised:
                app.put_position_strategy_settings(request)

        self.assertEqual(raised.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
