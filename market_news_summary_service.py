from __future__ import annotations

from datetime import datetime
import json
import os
import shlex
import subprocess
import time
from typing import Any


NEWS_CACHE_TTL_SECONDS = 300
_NEWS_CACHE: dict[tuple, tuple[float, dict[str, Any]]] = {}

EASTMONEY_NEWS_SOURCES = {
    "a_share": [
        (
            "A股",
            "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?"
            "client=web&biz=web_news_col&column=345&order=1&needInteractData=0"
            "&page_index=1&page_size={limit}&req_trace=market_news_a",
        ),
    ],
    "us": [
        (
            "美股/外围",
            "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?"
            "client=web&biz=web_news_col&column=344&order=1&needInteractData=0"
            "&page_index=1&page_size={limit}&req_trace=market_news_global",
        ),
        (
            "美股/外围",
            "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?"
            "client=web&biz=web_news_col&column=348&order=1&needInteractData=0"
            "&page_index=1&page_size={limit}&req_trace=market_news_global_extra",
        ),
    ],
}

FOCUS_KEYWORDS = {
    "AI": ("AI", "人工智能", "算力", "大模型"),
    "半导体": ("半导体", "芯片", "存储", "光刻"),
    "机器人": ("机器人", "减速器", "具身智能"),
    "新能源": ("新能源", "锂电", "光伏", "储能"),
    "券商": ("券商", "证券", "并购重组"),
    "医药": ("医药", "创新药", "医疗"),
    "军工": ("军工", "卫星", "低空"),
}

POSITIVE_WORDS = (
    "利好",
    "上涨",
    "大涨",
    "暴涨",
    "领涨",
    "走强",
    "反弹",
    "收涨",
    "催化",
    "突破",
    "爆发",
)
NEGATIVE_WORDS = (
    "利空",
    "下跌",
    "走弱",
    "回落",
    "收跌",
    "风险",
    "承压",
    "限制",
    "禁止",
    "管制",
    "反制",
    "消极",
)

GLOBAL_MARKET_KEYWORDS = (
    "美股",
    "纳指",
    "道指",
    "标普",
    "英伟达",
    "特斯拉",
    "苹果",
    "微软",
    "谷歌",
    "中概",
    "美元",
    "美债",
    "美联储",
    "特朗普",
    "美国",
    "海外",
    "外围",
)

SENTIMENT_LABELS = {
    "positive": "利好",
    "negative": "利空",
    "neutral": "中性",
}


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _extract_payload_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "result", "list", "news"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _extract_payload_items(value)
            if nested:
                return nested
    return []


def _item_time(item: dict[str, Any]) -> str:
    for key in (
        "showTime",
        "show_time",
        "publishTime",
        "publish_time",
        "date",
        "time",
    ):
        value = _safe_text(item.get(key))
        if value:
            return value[:19]
    return ""


def _classify_item_sentiment(title: str, summary: str = "") -> str:
    text = f"{title} {summary}"
    positive = sum(text.count(word) for word in POSITIVE_WORDS)
    negative = sum(text.count(word) for word in NEGATIVE_WORDS)
    if positive > negative:
        return "positive"
    if negative > positive:
        return "negative"
    return "neutral"


def _with_item_sentiment(item: dict[str, Any]) -> dict[str, Any]:
    tag = item.get("sentiment_tag") or _classify_item_sentiment(
        _safe_text(item.get("title")),
        _safe_text(item.get("summary") or item.get("digest")),
    )
    return {
        **item,
        "sentiment_tag": tag,
        "sentiment_label": SENTIMENT_LABELS.get(tag, "中性"),
    }


def normalize_news_items(
    raw_items: list[dict[str, Any]],
    *,
    market: str,
    category: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_items:
        title = _safe_text(
            item.get("title")
            or item.get("news_title")
            or item.get("Title")
            or item.get("name")
        )
        if not title or title in seen:
            continue
        seen.add(title)
        rows.append(_with_item_sentiment({
            "title": title,
            "summary": _safe_text(
                item.get("summary")
                or item.get("digest")
                or item.get("abstract")
                or item.get("content")
            ),
            "source": _safe_text(item.get("source") or item.get("mediaName")),
            "published_at": _item_time(item),
            "url": _safe_text(item.get("url") or item.get("art_url")),
            "market": market,
            "category": category,
        }))
    return sorted(
        rows,
        key=lambda row: row.get("published_at") or "",
        reverse=True,
    )


def _run_curl(url: str) -> str:
    result = subprocess.run(
        ["curl", "-fsSL", "--max-time", "5", "--retry", "1", url],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout


def _fetch_eastmoney_news(market: str, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for category, template in EASTMONEY_NEWS_SOURCES.get(market, []):
        payload = json.loads(_run_curl(template.format(limit=max(1, limit))))
        rows = normalize_news_items(
            _extract_payload_items(payload),
            market=market,
            category=category,
        )
        if market == "us":
            rows = [item for item in rows if _looks_global_market_news(item)]
        items.extend(rows)
    return list({item.get("title", ""): item for item in items}.values())


def _looks_global_market_news(item: dict[str, Any]) -> bool:
    text = f"{item.get('title', '')} {item.get('summary', '')}"
    return any(keyword in text for keyword in GLOBAL_MARKET_KEYWORDS)


def _focus_sectors(news: list[dict[str, Any]]) -> list[str]:
    text = " ".join(
        f"{item.get('title', '')} {item.get('summary', '')}"
        for item in news
    )
    sectors = [
        label
        for label, keywords in FOCUS_KEYWORDS.items()
        if any(keyword in text for keyword in keywords)
    ]
    return sectors[:6]


def _sentiment(news: list[dict[str, Any]]) -> str:
    text = " ".join(item.get("title", "") for item in news)
    positive = sum(text.count(word) for word in POSITIVE_WORDS)
    negative = sum(text.count(word) for word in NEGATIVE_WORDS)
    if positive >= negative + 2:
        return "偏利好"
    if negative >= positive + 2:
        return "偏利空"
    return "中性"


def _bullet_titles(news: list[dict[str, Any]], market: str, limit: int = 3) -> list[str]:
    rows = [item for item in news if item.get("market") == market]
    return [item["title"] for item in rows[:limit]]


def _bullet_items(news: list[dict[str, Any]], market: str, limit: int = 3) -> list[dict[str, str]]:
    rows = [_with_item_sentiment(item) for item in news if item.get("market") == market]
    return [
        {
            "text": item["title"],
            "sentiment_tag": item["sentiment_tag"],
            "sentiment_label": item["sentiment_label"],
        }
        for item in rows[:limit]
    ]


def _neutral_bullet(text: str) -> dict[str, str]:
    return {"text": text, "sentiment_tag": "neutral", "sentiment_label": "中性"}


def _section_text(bullet: dict[str, str]) -> str:
    return f"[{bullet['sentiment_label']}] {bullet['text']}"


def _rules_summary(news: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]], str, list[str]]:
    a_bullets = _bullet_items(news, "a_share")
    us_bullets = _bullet_items(news, "us")
    sectors = _focus_sectors(news)
    sentiment = _sentiment(news)
    impact = [
        _neutral_bullet(f"整体情绪：{sentiment}"),
        _neutral_bullet("重点关注：" + ("、".join(sectors) if sectors else "暂无特别集中的板块线索")),
    ]
    if not news:
        sections = [
            {"name": "A股", "bullets": [_neutral_bullet("暂无可用消息面")]},
            {"name": "美股/外围", "bullets": [_neutral_bullet("暂无可用消息面")]},
            {"name": "对今日A股影响", "bullets": [_neutral_bullet("等待更多数据确认")]},
        ]
        return "今日消息面简报：暂无可用消息面。", sections, "中性", []
    sections = [
        {"name": "A股", "bullets": a_bullets or [_neutral_bullet("暂无 A 股要闻")]},
        {"name": "美股/外围", "bullets": us_bullets or [_neutral_bullet("暂无美股/外围要闻")]},
        {"name": "对今日A股影响", "bullets": impact},
    ]
    lines = ["今日消息面简报"]
    for section in sections:
        lines.append("")
        lines.append(f"{section['name']}：")
        lines.extend(
            f"{index}. {_section_text(bullet)}"
            for index, bullet in enumerate(section["bullets"], 1)
        )
    return "\n".join(lines), sections, sentiment, sectors


def _run_trae_summary(news: list[dict[str, Any]], timeout: int = 12) -> str | None:
    command = _safe_text(os.getenv("TRAE_AI_CMD") or "trae")
    if not command:
        return None
    prompt = (
        "请用简洁中文总结以下当天A股与美股消息面，格式包含："
        "A股、美股/外围、对今日A股影响。不要超过180字。\n"
        + "\n".join(
            f"- [{item.get('market')}] {item.get('title')}"
            for item in news[:20]
        )
    )
    try:
        result = subprocess.run(
            shlex.split(command),
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
    except Exception:
        return None
    text = result.stdout.strip()
    return text or None


def clear_market_news_summary_cache() -> None:
    _NEWS_CACHE.clear()


def build_market_news_summary(
    *,
    market: str = "all",
    limit: int = 8,
    force_refresh: bool = False,
    use_ai: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now()
    safe_market = market if market in {"all", "a_share", "us"} else "all"
    safe_limit = max(1, min(int(limit), 20))
    cache_key = (safe_market, safe_limit, bool(use_ai), current.strftime("%Y%m%d%H%M"))
    cached = _NEWS_CACHE.get(cache_key)
    if cached and not force_refresh and time.monotonic() - cached[0] <= NEWS_CACHE_TTL_SECONDS:
        return {**cached[1], "result_cache_hit": True}

    markets = ["a_share", "us"] if safe_market == "all" else [safe_market]
    news: list[dict[str, Any]] = []
    warnings: list[str] = []
    for target in markets:
        try:
            fetched = _fetch_eastmoney_news(target, safe_limit)
            normalized = (
                fetched
                if all(item.get("market") for item in fetched)
                else normalize_news_items(
                    fetched,
                    market=target,
                    category="A股" if target == "a_share" else "美股/外围",
                )
            )
            news.extend(_with_item_sentiment(item) for item in normalized)
        except Exception as exc:
            warnings.append(f"东方财富{target}资讯失败: {str(exc)[:120]}")
    deduped_news = list({item.get("title", ""): item for item in news}.values())
    news = sorted(
        deduped_news,
        key=lambda row: row.get("published_at") or "",
        reverse=True,
    )[: safe_limit * len(markets)]
    summary_text, sections, sentiment, sectors = _rules_summary(news)
    ai_used = False
    ai_provider = "rules"
    if use_ai and news:
        ai_text = _run_trae_summary(news)
        if ai_text:
            summary_text = ai_text
            ai_used = True
            ai_provider = "trae"
    result = {
        "trade_date": current.strftime("%Y%m%d"),
        "updated_at": current.isoformat(sep=" ", timespec="seconds"),
        "summary_text": summary_text,
        "sentiment": sentiment,
        "focus_sectors": sectors,
        "ai_provider": ai_provider,
        "ai_used": ai_used,
        "data_sources": ["eastmoney"] if news else [],
        "warnings": list(dict.fromkeys(warnings)),
        "sections": sections,
        "news": news,
        "result_cache_hit": False,
    }
    _NEWS_CACHE[cache_key] = (time.monotonic(), result)
    return result
