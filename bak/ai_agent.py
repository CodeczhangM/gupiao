import os


DEFAULT_MODEL = "deepseek-r1:7b"


def _build_prompt(strong_text: str, dip_text: str, trade_date: str) -> str:
    return f"""
你是A股短线分析师，今天分析的是 {trade_date} 的市场数据。

请基于给定数据做辅助分析，不要编造未提供的数据。输出必须简洁、专业，并提醒市场风险。

=== 一、强势推荐股 ===
{strong_text}

=== 二、抄底候选（板块 + 个股） ===
{dip_text}

请分别针对两类数据给出分析：

【强势股分析】
1. 哪几只最值得关注？理由是什么（结合涨幅、换手率、成交量）？
2. 主要风险点？
3. 短线操作建议（买入时机、止损位）

【抄底分析】
1. 哪个板块最具反弹潜力？为什么？
2. 板块内哪只个股最值得抄底？
3. 抄底风险提示（是否存在继续下跌风险）
4. 建议的介入策略（分批建仓 or 等企稳信号）

请用中文回答，每条建议控制在2~3句话。最后补充一句：以上为量化筛选后的辅助分析，不构成投资建议。
""".strip()


def analyze_stocks(strong_text: str, dip_text: str, trade_date: str) -> str:
    try:
        import ollama
    except ImportError as exc:
        raise RuntimeError("缺少 ollama 依赖，请先运行：pip install -r requirements.txt") from exc

    model = os.getenv("OLLAMA_MODEL", DEFAULT_MODEL)
    prompt = _build_prompt(strong_text, dip_text, trade_date)

    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        raise RuntimeError(
            f"Ollama 调用失败，请确认已启动 Ollama，并已拉取模型：ollama pull {model}"
        ) from exc

    return response["message"]["content"]
