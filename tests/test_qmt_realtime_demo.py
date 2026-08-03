import contextlib
import io
import json
import unittest

import qmt_realtime_demo


class FakeXtData:
    __name__ = "fake_xtdata"

    def __init__(self):
        self.periods = []

    def get_full_tick(self, symbols):
        return {symbol: {"lastPrice": 10.0} for symbol in symbols}

    def get_market_data_ex(self, field_list, stock_list, period, **kwargs):
        self.periods.append(period)
        return {stock_list[0]: [{"period": period, "close": 10.0}]}


class QmtRealtimeDemoTests(unittest.TestCase):
    def test_run_probe_reports_missing_xtquant_without_crashing(self):
        report = qmt_realtime_demo.run_probe(
            ["600000.SH"],
            xtdata=None,
            seconds=0,
        )

        self.assertFalse(report["xtquant_installed"])
        self.assertEqual(report["symbols"], ["600000.SH"])
        self.assertEqual(report["checks"][0]["name"], "import_xtquant")
        self.assertFalse(report["checks"][0]["ok"])
        self.assertIn("environment", report)
        self.assertIn("python_executable", report["environment"])
        self.assertIn("platform", report["environment"])
        self.assertIn("hints", report["environment"])

    def test_sample_preview_handles_mapping_and_sequence_shapes(self):
        preview = qmt_realtime_demo.preview_data({
            "600000.SH": [{"time": 1, "price": 10}, {"time": 2, "price": 10.1}],
        })

        self.assertEqual(preview["type"], "dict")
        self.assertIn("600000.SH", preview["sample"])

    def test_run_probe_checks_level1_and_level2_periods(self):
        fake_xtdata = FakeXtData()
        report = qmt_realtime_demo.run_probe(
            ["600000.SH"],
            xtdata=fake_xtdata,
            seconds=0,
        )

        self.assertTrue(report["xtquant_installed"])
        self.assertIn("full_tick_realtime_snapshot", [check["name"] for check in report["checks"]])
        self.assertEqual(
            fake_xtdata.periods,
            ["1m", "tick", "l2transaction", "l2order", "l2orderqueue"],
        )

    def test_main_prints_clean_json_even_when_import_prints_banner(self):
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            exit_code = qmt_realtime_demo.main(["--symbols", "600000.SH", "--seconds", "0"])

        self.assertEqual(exit_code, 0)
        self.assertTrue(stdout.getvalue().lstrip().startswith("{"))
        parsed = json.loads(stdout.getvalue())
        self.assertIn("checks", parsed)


if __name__ == "__main__":
    unittest.main()
