"""Trade-review service: reconstruct a trade's K-line path and ask AI for a disciplined review."""

from __future__ import annotations

from datetime import datetime, timedelta
import re
from typing import Any

import pandas as pd

from ai_agent import analyze_prompt
from data_service import get_stock_daily_history_range
from stock_detail_service import _json_safe, build_technical_snapshot


_TS_CODE_PATTERN = re.compile(r"\d{6}\.(SH|SZ)")
_DATE_PATTERN = re.compile(r"\d{8}")
_VALID_POSITION_STATUS = {"holding", "sold"}


def _value(payload: dict[str, Any], snake_name: str, default=None):
    camel_name = "".join(
        [snake_name.split("_")[0], *[part.capitalize() for part in snake_name.split("_")[1:]]]
    )
    return payload.get(snake_name, payload.get(camel_name, default))


def _date(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not _DATE_PATTERN.fullmatch(text):
        raise ValueError(f"{field} 必须是 YYYYMMDD 格式")
    return text


def _positive_number(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是正数") from exc
    if number <= 0:
        raise ValueError(f"{field} 必须大于 0")
    return number


def _normalize_request(payload: dict[str, Any]) -> dict[str, Any]:
    ts_code = str(_value(payload, "ts_code", "")).upper().strip()
    if not _TS_CODE_PATTERN.fullmatch(ts_code):
        raise ValueError("股票代码格式应为 600000.SH 或 000001.SZ")

    buy_date = _date(_value(payload, "buy_date"), "买入日期")
    position_status = str(_value(payload, "position_status", "holding")).lower().strip()
    if position_status not in _VALID_POSITION_STATUS:
        raise ValueError("持仓状态只能是 holding 或 sold")

    sell_date = _value(payload, "sell_date")
    if position_status == "sold":
        sell_date = _date(sell_date, "卖出日期")
        if sell_date < buy_date:
            raise ValueError("卖出日期不能早于买入日期")
    elif sell_date:
        sell_date = _date(sell_date, "截止日期")
        if sell_date < buy_date:
            raise ValueError("截止日期不能早于买入日期")
    else:
        sell_date = datetime.now().strftime("%Y%m%d")

    sell_price = _value(payload, "sell_price")
    if position_status == "sold":
        sell_price = _positive_number(sell_price, "卖出价格")
    elif sell_price not in (None, ""):
        sell_price = _positive_number(sell_price, "参考卖出价格")
    else:
        sell_price = None

    return {
        "ts_code": ts_code,
        "buy_date": buy_date,
        "buy_price": _positive_number(_value(payload, "buy_price"), "买入价格"),
        "sell_date": sell_date,
        "sell_price": sell_price,
        "position_status": position_status,
        "loss_status": str(_value(payload, "loss_status", "")).strip(),
        "holding_note": str(_value(payload, "holding_note", "")).strip(),
    }


def _pick_trade_window(history: pd.DataFrame, buy_date: str, end_date: str) -> pd.DataFrame:
    trade_window = history[
        history["trade_date"].astype(str).between(buy_date, end_date)
    ].copy()
    if trade_window.empty:
        raise LookupError("买入日至截止日没有可用日线数据，请检查股票代码和日期")
    return trade_window.reset_index(drop=True)


def _candle_for_date(candles: list[dict[str, Any]], trade_date: str) -> dict[str, Any]:
    return next((item for item in candles if item.get("trade_date") == trade_date), {})


def _trade_metrics(window: pd.DataFrame, buy_price: float, sell_price: float | None) -> dict[str, Any]:
    close = pd.to_numeric(window["close"], errors="coerce").dropna()
    current_price = float(sell_price or close.iloc[-1])
    path_return = close / buy_price - 1
    return {
        "entry_price": buy_price,
        "exit_or_current_price": current_price,
        "return_pct": (current_price / buy_price - 1) * 100,
        "max_gain_pct": path_return.max() * 100,
        "max_drawdown_pct": path_return.min() * 100,
        "trade_days": int(len(window)),
        "period_high": float(pd.to_numeric(window["high"], errors="coerce").max()),
        "period_low": float(pd.to_numeric(window["low"], errors="coerce").min()),
    }


def _compact_kline(candles: list[dict[str, Any]], buy_date: str, exit_date: str) -> list[dict[str, Any]]:
    selected = [item for item in candles if buy_date <= str(item.get("trade_date", "")) <= exit_date]
    keys = ("trade_date", "open", "high", "low", "close", "vol", "pct_chg", "ma5", "ma20", "ma60", "dif", "dea", "histogram", "rsi6")
    return [{key: candle.get(key) for key in keys} for candle in selected[-180:]]


def build_trade_review_prompt(review: dict[str, Any]) -> str:
    trade = review["trade"]
    metrics = review["metrics"]
    return f"""
你是一名严格、克制的A股交易复盘教练。仅根据给定交易信息、买入前后技术指标和交易期日K线复盘；不补造新闻、主力资金或基本面消息。

交易信息：{_json_safe(trade)}
阶段统计：{_json_safe(metrics)}
买入日技术快照：{_json_safe(review['entry_snapshot'])}
卖出/当前日技术快照：{_json_safe(review['exit_snapshot'])}
买入至卖出/当前的日K与指标：{_json_safe(review['trade_kline'])}

请使用中文固定输出：
1. 交易结果概览：收益、最大浮盈、最大回撤与持有周期。
2. 买入复盘：买入时趋势、量能、均线、MACD 等是否具备确认条件；缺少了什么。
3. 持仓复盘：期间出现哪些减仓、止损或继续持有信号；结合用户的持仓/亏损描述指出纪律问题。
4. 卖出复盘：已卖出时判断卖点是否合理；持仓时说明当前应观察的确认条件，不要求立即交易。
5. 下次改进清单：给出 3 条可执行、条件化的规则。

不保证收益，不执行交易。内容仅用于研究和交易复盘，不构成投资建议。
""".strip()


def review_trade(payload: dict[str, Any]) -> dict[str, Any]:
    trade = _normalize_request(payload)
    buy_start = (datetime.strptime(trade["buy_date"], "%Y%m%d") - timedelta(days=180)).strftime("%Y%m%d")
    history = get_stock_daily_history_range(trade["ts_code"], buy_start, trade["sell_date"])
    if history.empty:
        raise LookupError("未获取到股票日线数据")

    snapshot = build_technical_snapshot(history)
    trade_window = _pick_trade_window(history, trade["buy_date"], trade["sell_date"])
    entry_date = str(trade_window.iloc[0]["trade_date"])
    exit_date = str(trade_window.iloc[-1]["trade_date"])
    candles = snapshot["candles"]
    review = {
        "trade": {**trade, "entry_trade_date": entry_date, "exit_trade_date": exit_date},
        "metrics": _trade_metrics(trade_window, trade["buy_price"], trade["sell_price"]),
        "entry_snapshot": _candle_for_date(candles, entry_date),
        "exit_snapshot": _candle_for_date(candles, exit_date),
        "trade_kline": _compact_kline(candles, entry_date, exit_date),
    }
    review["ai_summary"] = analyze_prompt(build_trade_review_prompt(review))
    return _json_safe(review)
