import unittest
from unittest.mock import patch

from fastapi import HTTPException

import app
from free_review_models import FreeReviewQuery


class FreeReviewApiTests(unittest.TestCase):
    @patch(
        "app.start_free_review_build",
        return_value={"status": "pending", "stage": "queued"},
    )
    def test_build_forwards_force(self, service):
        result = app.free_review_build(force=True)

        self.assertEqual(result["status"], "pending")
        service.assert_called_once_with(force=True)

    @patch(
        "app.load_free_review_build_status",
        return_value={"status": "running", "stage": "financial"},
    )
    def test_status_returns_service_payload(self, service):
        result = app.free_review_build_status(trade_date="20260730")

        self.assertEqual(result["stage"], "financial")
        service.assert_called_once_with("20260730")

    @patch(
        "app.query_free_review",
        return_value={"total": 1, "items": [{"ts_code": "600001.SH"}]},
    )
    def test_query_forwards_validated_model(self, service):
        request = FreeReviewQuery(ranges={"total_score": {"min": 70}})
        result = app.free_review_query(request)

        self.assertEqual(result["total"], 1)
        service.assert_called_once_with(request)

    @patch(
        "app.query_free_review",
        side_effect=LookupError("自由复盘选股快照尚未生成"),
    )
    def test_not_ready_maps_to_404(self, _service):
        with self.assertRaises(HTTPException) as raised:
            app.free_review_query(FreeReviewQuery())

        self.assertEqual(raised.exception.status_code, 404)

    @patch(
        "app.query_free_review",
        side_effect=ValueError("不支持的筛选字段"),
    )
    def test_invalid_query_maps_to_422(self, _service):
        with self.assertRaises(HTTPException) as raised:
            app.free_review_query(FreeReviewQuery())

        self.assertEqual(raised.exception.status_code, 422)

    @patch(
        "app.export_free_review_csv",
        return_value=("free-review-20260730.csv", b"\xef\xbb\xbfdata"),
    )
    def test_export_sets_csv_headers(self, _service):
        response = app.free_review_export(FreeReviewQuery())

        self.assertIn("text/csv", response.media_type)
        self.assertIn(
            "free-review-20260730.csv",
            response.headers["content-disposition"],
        )
        self.assertEqual(response.body, b"\xef\xbb\xbfdata")

    @patch(
        "app.start_free_review_build",
        side_effect=RuntimeError("Tushare 权限不足"),
    )
    def test_build_failure_maps_to_502(self, _service):
        with self.assertRaises(HTTPException) as raised:
            app.free_review_build(force=False)

        self.assertEqual(raised.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
