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


if __name__ == "__main__":
    unittest.main()
