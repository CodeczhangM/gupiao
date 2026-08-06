import json
import unittest
from datetime import datetime
from unittest.mock import patch

from market_news_summary_service import (
    _fetch_eastmoney_news,
    build_market_news_summary,
    normalize_news_items,
)


class MarketNewsSummaryServiceTests(unittest.TestCase):
    def test_normalize_news_items_deduplicates_and_sorts(self):
        raw = [
            {
                "title": "美股科技股反弹",
                "summary": "纳指上涨",
                "source": "东方财富",
                "showTime": "2026-08-05 07:30:00",
                "url": "https://example.com/us",
            },
            {
                "title": "美股科技股反弹",
                "summary": "重复",
                "source": "东方财富",
                "showTime": "2026-08-05 07:31:00",
            },
            {
                "title": "A股政策利好发酵",
                "digest": "关注半导体",
                "source": "证券时报",
                "showTime": "2026-08-05 08:30:00",
            },
        ]

        result = normalize_news_items(raw, market="a_share", category="要闻")

        self.assertEqual([item["title"] for item in result], [
            "A股政策利好发酵",
            "美股科技股反弹",
        ])
        self.assertEqual(result[0]["published_at"], "2026-08-05 08:30:00")
        self.assertEqual(result[0]["market"], "a_share")
        self.assertEqual(result[0]["sentiment_tag"], "positive")
        self.assertEqual(result[0]["sentiment_label"], "利好")

    def test_market_strength_words_are_positive(self):
        result = normalize_news_items(
            [
                {
                    "title": "沪指大涨 半导体板块爆发",
                    "summary": "贵金属板块领涨",
                    "showTime": "2026-08-05 10:00:00",
                }
            ],
            market="a_share",
            category="A股",
        )

        self.assertEqual(result[0]["sentiment_tag"], "positive")

    def test_rules_summary_marks_positive_and_negative_bullets(self):
        news = [
            {
                "title": "A股半导体板块利好催化",
                "showTime": "2026-08-05 09:00:00",
                "market": "a_share",
            },
            {
                "title": "美国禁止部分材料出口带来风险",
                "showTime": "2026-08-05 07:00:00",
                "market": "us",
            },
        ]

        with patch("market_news_summary_service._fetch_eastmoney_news", side_effect=[news[:1], news[1:]]):
            result = build_market_news_summary(
                now=datetime(2026, 8, 5, 9, 30),
                force_refresh=True,
                use_ai=False,
            )

        self.assertIn("[利好] A股半导体板块利好催化", result["summary_text"])
        self.assertIn("[利空] 美国禁止部分材料出口带来风险", result["summary_text"])
        self.assertEqual(result["news"][0]["sentiment_tag"], "positive")
        self.assertEqual(result["news"][1]["sentiment_tag"], "negative")
        self.assertEqual(result["sections"][0]["bullets"][0]["sentiment_label"], "利好")

    @patch("market_news_summary_service._run_trae_summary", return_value=None)
    @patch("market_news_summary_service._fetch_eastmoney_news")
    def test_build_summary_uses_rules_when_trae_unavailable(self, fetch, _trae):
        fetch.side_effect = [
            [
                {
                    "title": "A股半导体板块迎政策催化",
                    "digest": "产业链活跃",
                    "showTime": "2026-08-05 09:00:00",
                    "source": "东方财富",
                }
            ],
            [
                {
                    "title": "隔夜美股纳指收涨 AI 龙头走强",
                    "digest": "科技风险偏好回暖",
                    "showTime": "2026-08-05 07:00:00",
                    "source": "东方财富",
                }
            ],
        ]

        result = build_market_news_summary(
            now=datetime(2026, 8, 5, 9, 30),
            limit=3,
            force_refresh=True,
            use_ai=True,
        )

        self.assertFalse(result["ai_used"])
        self.assertEqual(result["ai_provider"], "rules")
        self.assertIn("A股半导体板块迎政策催化", result["summary_text"])
        self.assertIn("AI", result["focus_sectors"])
        self.assertEqual(len(result["sections"]), 3)

    @patch("market_news_summary_service._run_trae_summary", return_value="Trae 简报")
    @patch("market_news_summary_service._fetch_eastmoney_news")
    def test_build_summary_uses_trae_when_available(self, fetch, _trae):
        fetch.side_effect = [
            [{"title": "A股机器人活跃", "showTime": "2026-08-05 09:00:00"}],
            [{"title": "美股芯片股上涨", "showTime": "2026-08-05 07:00:00"}],
        ]

        result = build_market_news_summary(
            now=datetime(2026, 8, 5, 9, 30),
            force_refresh=True,
            use_ai=True,
        )

        self.assertTrue(result["ai_used"])
        self.assertEqual(result["ai_provider"], "trae")
        self.assertEqual(result["summary_text"], "Trae 简报")

    @patch("market_news_summary_service._run_curl")
    def test_us_news_source_filters_global_market_items(self, curl):
        curl.return_value = json.dumps({
            "data": {
                "list": [
                    {"title": "隔夜美股纳指收涨 AI 龙头走强", "showTime": "2026-08-05 07:00:00"},
                    {"title": "本地生活服务消费升温", "showTime": "2026-08-05 06:00:00"},
                    {"title": "美国将限制部分关键材料出口", "showTime": "2026-08-05 05:00:00"},
                ]
            }
        })

        result = _fetch_eastmoney_news("us", 3)

        self.assertEqual(
            [item["title"] for item in result],
            [
                "隔夜美股纳指收涨 AI 龙头走强",
                "美国将限制部分关键材料出口",
            ],
        )
        self.assertTrue(all(item["market"] == "us" for item in result))

    @patch("market_news_summary_service._fetch_eastmoney_news", side_effect=RuntimeError("down"))
    def test_source_failure_returns_structured_fallback(self, _fetch):
        result = build_market_news_summary(
            now=datetime(2026, 8, 5, 9, 30),
            force_refresh=True,
        )

        self.assertEqual(result["news"], [])
        self.assertTrue(result["warnings"])
        self.assertIn("暂无可用消息面", result["summary_text"])


if __name__ == "__main__":
    unittest.main()
