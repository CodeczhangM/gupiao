import csv
import io
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd


class FreeReviewServiceTests(unittest.TestCase):
    def test_build_materializes_latest_complete_trade_date(self):
        import free_review_service

        snapshot = pd.DataFrame([{
            "trade_date": "20260730",
            "ts_code": "600001.SH",
            "score_version": "free-review-v1",
            "financial_end_date": "20260630",
        }])
        with (
            patch(
                "free_review_service.get_complete_dates",
                return_value=["20260730"],
            ),
            patch(
                "free_review_service.load_market_snapshot",
                return_value=pd.DataFrame([{"ts_code": "600001.SH"}]),
            ),
            patch(
                "free_review_service.load_recent_daily",
                return_value=pd.DataFrame([{"ts_code": "600001.SH"}]),
            ),
            patch(
                "free_review_service.sync_financial_indicators",
                return_value={"failed_periods": []},
            ),
            patch(
                "free_review_service.load_financial_as_of",
                return_value=pd.DataFrame([{"ts_code": "600001.SH"}]),
            ),
            patch(
                "free_review_service.build_review_snapshot",
                return_value=snapshot,
            ),
            patch(
                "free_review_service.replace_review_snapshot"
            ) as replace,
            patch(
                "free_review_service.save_build_status"
            ) as save_status,
        ):
            result = free_review_service.build_free_review_snapshot(
                "20260730",
                force=True,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["total_count"], 1)
        replace.assert_called_once()
        stages = [
            call.args[0]["stage"]
            for call in save_status.call_args_list
        ]
        self.assertEqual(
            stages,
            ["cache", "financial", "scoring", "persisting", "complete"],
        )

    def test_start_returns_existing_running_build_without_new_thread(self):
        import free_review_service

        running = {
            "trade_date": "20260730",
            "score_version": "free-review-v1",
            "status": "running",
            "stage": "financial",
        }
        with (
            patch(
                "free_review_service.get_complete_dates",
                return_value=["20260730"],
            ),
            patch(
                "free_review_service.load_build_status",
                return_value=running,
            ),
            patch("free_review_service.threading.Thread") as thread,
        ):
            result = free_review_service.start_free_review_build()

        self.assertEqual(result, running)
        thread.assert_not_called()

    def test_csv_export_uses_query_filters_and_utf8_bom(self):
        import free_review_service
        from free_review_models import FreeReviewQuery

        rows = [{
            "trade_date": "20260730",
            "ts_code": "600001.SH",
            "name": "正常股份",
            "industry": "制造",
            "total_score": 78.5,
            "score_reasons": ["趋势向上", "量价活跃"],
            "risk_flags": [],
        }]
        request = FreeReviewQuery(
            trade_date="20260730",
            ranges={"total_score": {"min": 70}},
        )
        with patch(
            "free_review_service.load_review_export_rows",
            return_value=("20260730", rows),
        ) as load_rows:
            filename, content = free_review_service.export_free_review_csv(
                request
            )

        self.assertEqual(filename, "free-review-20260730.csv")
        self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
        decoded = content.decode("utf-8-sig")
        parsed = list(csv.reader(io.StringIO(decoded)))
        self.assertIn("正常股份", parsed[1])
        self.assertIn("趋势向上；量价活跃", parsed[1])
        load_rows.assert_called_once_with(request, limit=10000)


if __name__ == "__main__":
    unittest.main()
