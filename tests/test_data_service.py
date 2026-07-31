import unittest
from unittest.mock import patch

import pandas as pd
import requests

import data_service


class _FakeTushareApi:
    def __init__(self, error):
        self._DataApi__timeout = 30
        self.error = error
        self.calls = []

    def __getattr__(self, api_name):
        def call(**kwargs):
            self.calls.append({
                "api_name": api_name,
                "timeout": self._DataApi__timeout,
                "kwargs": kwargs,
            })
            raise self.error

        return call


class TushareQueryPolicyTests(unittest.TestCase):
    def setUp(self):
        data_service._query_cache.clear()

    def tearDown(self):
        data_service._query_cache.clear()

    def test_stk_mins_uses_short_timeout_without_sleep_retries(self):
        fake = _FakeTushareApi(
            requests.exceptions.ReadTimeout("stk_mins timeout")
        )

        with (
            patch.object(data_service, "token", "token"),
            patch.object(data_service, "pro", fake),
            patch.object(data_service.time, "sleep") as sleep,
        ):
            with self.assertRaises(requests.exceptions.ReadTimeout):
                data_service._query_tushare(
                    "stk_mins",
                    ts_code="600298.SH",
                    freq="60min",
                    start_date="2026-07-31 09:30:00",
                    end_date="2026-07-31 14:30:00",
                )

        self.assertEqual(
            [(call["api_name"], call["timeout"]) for call in fake.calls],
            [("stk_mins", 6)],
        )
        sleep.assert_not_called()
        self.assertEqual(fake._DataApi__timeout, 30)

    def test_non_minute_queries_keep_global_retry_policy(self):
        fake = _FakeTushareApi(
            requests.exceptions.ReadTimeout("daily timeout")
        )

        with (
            patch.object(data_service, "token", "token"),
            patch.object(data_service, "pro", fake),
            patch.object(data_service.time, "sleep") as sleep,
        ):
            with self.assertRaises(requests.exceptions.ReadTimeout):
                data_service._query_tushare("daily", trade_date="20260731")

        self.assertEqual(
            [(call["api_name"], call["timeout"]) for call in fake.calls],
            [("daily", 30), ("daily", 30), ("daily", 30), ("daily", 30)],
        )
        self.assertEqual(
            [call.args[0] for call in sleep.call_args_list],
            [3, 6, 10],
        )

    def test_successful_query_is_cached_after_custom_timeout_restores(self):
        class SuccessfulApi:
            def __init__(self):
                self._DataApi__timeout = 30
                self.calls = []

            def __getattr__(self, api_name):
                def call(**kwargs):
                    self.calls.append((api_name, self._DataApi__timeout))
                    return pd.DataFrame([{"ts_code": kwargs["ts_code"]}])

                return call

        fake = SuccessfulApi()

        with (
            patch.object(data_service, "token", "token"),
            patch.object(data_service, "pro", fake),
        ):
            first = data_service._query_tushare(
                "stk_mins",
                ts_code="600298.SH",
                freq="60min",
            )
            second = data_service._query_tushare(
                "stk_mins",
                ts_code="600298.SH",
                freq="60min",
            )

        self.assertEqual(fake.calls, [("stk_mins", 6)])
        self.assertEqual(fake._DataApi__timeout, 30)
        self.assertEqual(first["ts_code"].tolist(), ["600298.SH"])
        self.assertEqual(second["ts_code"].tolist(), ["600298.SH"])


if __name__ == "__main__":
    unittest.main()
