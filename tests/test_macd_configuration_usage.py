import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MACD_SOURCES = (
    "strategy.py",
    "free_review_scoring.py",
    "overnight_monitor_service.py",
    "stock_detail_service.py",
)


class MacdConfigurationUsageTests(unittest.TestCase):
    def test_production_macd_calculations_have_no_legacy_period_hardcodes(self):
        legacy_pattern = re.compile(
            r"\.ewm\(\s*span\s*=\s*(?:12|26|9)\b"
        )
        violations = []
        for relative_path in MACD_SOURCES:
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            for match in legacy_pattern.finditer(source):
                line = source.count("\n", 0, match.start()) + 1
                violations.append(f"{relative_path}:{line}")
        self.assertEqual(
            violations,
            [],
            "仍有旧 MACD 12/26/9 硬编码: " + ", ".join(violations),
        )


if __name__ == "__main__":
    unittest.main()
