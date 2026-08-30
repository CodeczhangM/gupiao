import unittest
from unittest.mock import patch

from fastapi import HTTPException

import app
from cycle_watch_models import (
    CycleWatchCheckRequest,
    CycleWatchCreateRequest,
    CycleWatchReadAlertsRequest,
    CycleWatchUpdateRequest,
)


class CycleWatchApiTests(unittest.TestCase):
    @patch("app.get_cycle_watchlist", return_value={"stocks": []})
    def test_list_returns_service_payload(self, service):
        self.assertEqual(app.list_cycle_watch()["stocks"], [])
        service.assert_called_once_with()

    @patch("app.add_cycle_watch", return_value={"ts_code": "600000.SH"})
    def test_create_passes_validated_body(self, service):
        request = CycleWatchCreateRequest(ts_code="600000", note="银行周期")
        result = app.create_cycle_watch(request)

        self.assertEqual(result["ts_code"], "600000.SH")
        self.assertEqual(service.call_args.args[0]["note"], "银行周期")

    @patch("app.edit_cycle_watch", return_value={"ts_code": "600000.SH", "enabled": False})
    def test_patch_and_delete_map_path_code(self, edit):
        updated = app.patch_cycle_watch(
            "600000.SH", CycleWatchUpdateRequest(enabled=False),
        )
        self.assertFalse(updated["enabled"])
        edit.assert_called_once()

        with patch("app.remove_cycle_watch") as remove:
            response = app.delete_cycle_watch("600000.SH")
        self.assertEqual(response.status_code, 204)
        remove.assert_called_once_with("600000.SH")

    @patch("app.check_cycle_watchlist", return_value={"success_count": 1})
    def test_check_supports_all_or_one_stock(self, service):
        result = app.check_cycle_watch(
            CycleWatchCheckRequest(ts_code="000001", schedule_slot="manual")
        )
        self.assertEqual(result["success_count"], 1)
        service.assert_called_once_with(ts_code="000001", schedule_slot="manual")

    @patch("app.get_cycle_watch_history", return_value=[{"id": 1}])
    def test_history_clamps_limit(self, service):
        self.assertEqual(app.cycle_watch_history("600000.SH", 999), {"history": [{"id": 1}]})
        service.assert_called_once_with("600000.SH", 200)

    @patch("app.read_cycle_watch_alerts", return_value={"updated_count": 2})
    def test_read_alerts_returns_updated_count(self, service):
        result = app.mark_cycle_watch_alerts_read(
            CycleWatchReadAlertsRequest(trade_date="20260828")
        )
        self.assertEqual(result["updated_count"], 2)

    @patch("app.add_cycle_watch", side_effect=ValueError("代码错误"))
    def test_validation_error_maps_to_422(self, _service):
        with self.assertRaises(HTTPException) as raised:
            app.create_cycle_watch(CycleWatchCreateRequest(ts_code="600000"))
        self.assertEqual(raised.exception.status_code, 422)

    @patch("app.edit_cycle_watch", side_effect=LookupError("不存在"))
    def test_missing_stock_maps_to_404(self, _service):
        with self.assertRaises(HTTPException) as raised:
            app.patch_cycle_watch("600000.SH", CycleWatchUpdateRequest(enabled=False))
        self.assertEqual(raised.exception.status_code, 404)

    @patch("app.get_cycle_watchlist", side_effect=RuntimeError("database unavailable"))
    def test_upstream_failure_maps_to_502(self, _service):
        with self.assertRaises(HTTPException) as raised:
            app.list_cycle_watch()
        self.assertEqual(raised.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
