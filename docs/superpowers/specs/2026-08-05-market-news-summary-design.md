# 当日美股与 A 股消息面简报设计

## 目标

新增独立“消息面”模块，简洁汇总当天 A 股、美股及外围市场新闻，辅助看盘前判断情绪、板块催化和潜在风险。该模块不参与选股打分，不阻塞实时信息、隔夜溢价等交易链路。

## 范围

- 新增 Python 后端服务和 API：`/api/market-news-summary`。
- Java 网关新增转发：`/api/quant/market-news-summary`。
- 前端新增“消息面”入口和简洁展示卡片。
- 数据源优先使用东方财富公开资讯接口；数据源不可用时返回结构化降级结果。
- Trae AI 作为可选增强：存在命令且调用成功时使用 AI 生成自然语言简报；否则使用规则摘要。

## 数据流

1. 后端按 `market=all|a_share|us` 和 `limit` 拉取新闻。
2. 新闻标准化为 `title/source/published_at/url/market/category`。
3. 按标题去重、按发布时间倒序截断。
4. 规则摘要生成三段：
   - A 股消息
   - 美股/外围
   - 对今日 A 股影响
5. 如果配置允许且 Trae 命令可用，把标准化新闻输入 Trae，替换或补充 `summary_text`。
6. 响应包含原始新闻列表、摘要、数据源、更新时间、AI 使用状态和警告。

## 输出结构

```json
{
  "trade_date": "20260805",
  "updated_at": "2026-08-05 09:00:00",
  "summary_text": "今日消息面简报...",
  "sentiment": "中性",
  "focus_sectors": ["AI", "半导体"],
  "ai_provider": "trae|rules",
  "ai_used": false,
  "data_sources": ["eastmoney"],
  "warnings": [],
  "sections": [
    {"name": "A股", "bullets": ["..."]},
    {"name": "美股/外围", "bullets": ["..."]},
    {"name": "对今日A股影响", "bullets": ["..."]}
  ],
  "news": []
}
```

## 失败与性能

- 东方财富请求短超时，单源失败不抛 500，写入 `warnings`。
- 服务进程内缓存 5 分钟，刷新按钮可传 `force_refresh=true` 绕过缓存。
- Trae AI 默认短超时；失败自动回退规则摘要。
- 没有新闻时返回空列表和“暂无可用消息面”的摘要。

## 测试

- 服务测试：新闻去重、规则摘要、数据源失败降级、Trae 不可用降级。
- Python API 测试：接口返回结构稳定。
- Java 网关测试：参数正确转发。
- 前端静态测试：新增 Tab、请求路径和核心字段渲染存在。
