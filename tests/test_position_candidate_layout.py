from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PositionCandidateLayoutTests(unittest.TestCase):
    def test_realtime_page_renders_one_unified_position_candidate_table(self):
        html = (ROOT / "quantClient" / "index.html").read_text(encoding="utf-8")
        main = (ROOT / "quantClient" / "main.js").read_text(encoding="utf-8")
        candidate_html, overnight_html = html.split("<h3>盘末隔夜溢价 TOP20</h3>", 1)

        self.assertIn("<h3>近期观察与建仓</h3>", candidate_html)
        self.assertIn("realtimePositionCandidateRows", candidate_html)
        self.assertIn("position_candidates", main)
        self.assertIn("建仓等级", candidate_html)
        self.assertIn("综合分", candidate_html)
        self.assertIn("板块/热点", candidate_html)
        self.assertIn("量价/主力", candidate_html)
        self.assertIn("MACD", candidate_html)
        self.assertIn("筹码峰", candidate_html)
        self.assertIn("position_risk_items", candidate_html)
        self.assertNotIn("v-for=\"stageTable in realtimeStageTables\"", candidate_html)
        self.assertNotIn("<h3>缩量企稳观察</h3>", candidate_html)
        self.assertNotIn("<h3>底部首阳触发</h3>", candidate_html)
        self.assertNotIn("<h3>底部放量启动</h3>", candidate_html)
        self.assertIn("realtimeOvernightRows", overnight_html)


if __name__ == "__main__":
    unittest.main()
