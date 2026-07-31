"""Pure helpers for stock-detail technical analysis and AI prompt preparation."""

from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd

from data_service import get_stock_daily_history
from database import get_latest_report, get_report
from indicator_settings import (
    calculate_macd,
    load_macd_settings,
    macd_provenance,
)


_CANDLE_COLUMNS = (
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "vol",
    "pct_chg",
    "turnover_rate",
)
_TS_CODE_PATTERN = re.compile(r"\d{6}\.(SH|SZ)")


def _json_safe(value: Any) -> Any:
    """Convert pandas/numpy scalar values and non-finite numbers to JSON values."""
    if value is None or value is pd.NA:
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, bool):
        return value
    if hasattr(value, "item"):
        try:
            value = value.item()
        except ValueError:
            pass
    if isinstance(value, float):
        return round(value, 6) if math.isfinite(value) else None
    return value


def _latest(series: pd.Series) -> float | None:
    if series.empty or pd.isna(series.iloc[-1]):
        return None
    return _json_safe(float(series.iloc[-1]))


def _rsi(close: pd.Series, period: int) -> pd.Series:
    """Return RSI from simple rolling average gains and losses."""
    close = pd.to_numeric(close, errors="coerce")
    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    average_gain = gains.rolling(period, min_periods=period).mean()
    average_loss = losses.rolling(period, min_periods=period).mean()
    rs = average_gain / average_loss
    result = 100 - 100 / (1 + rs)
    return result.mask((average_loss == 0) & (average_gain > 0), 100).mask(
        (average_loss == 0) & (average_gain == 0), 50
    )


def _as_history_frame(history: Any) -> pd.DataFrame:
    if isinstance(history, pd.DataFrame):
        frame = history.copy()
    else:
        frame = pd.DataFrame(history or [])
    for column in _CANDLE_COLUMNS:
        if column not in frame:
            frame[column] = None
    frame = frame.loc[:, _CANDLE_COLUMNS]
    frame["trade_date"] = frame["trade_date"].astype("string")
    frame = frame.sort_values("trade_date", na_position="last").reset_index(drop=True)
    for column in ("open", "high", "low", "close", "vol", "pct_chg", "turnover_rate"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def build_technical_snapshot(history: Any) -> dict[str, Any]:
    """Build JSON-safe candles and latest technical readings from normalized OHLCV data."""
    frame = _as_history_frame(history)
    close, high, low, volume = (frame[name] for name in ("close", "high", "low", "vol"))

    ma = {f"ma{period}": _latest(close.rolling(period, min_periods=period).mean()) for period in (5, 10, 20, 60)}

    macd_settings = load_macd_settings()
    dif, dea, macd_histogram = calculate_macd(close, macd_settings)

    low9 = low.rolling(9, min_periods=9).min()
    high9 = high.rolling(9, min_periods=9).max()
    rsv = ((close - low9) / (high9 - low9) * 100).where(high9 != low9, 50)
    k = rsv.ewm(com=2, adjust=False, min_periods=1).mean()
    d = k.ewm(com=2, adjust=False, min_periods=1).mean()
    j = 3 * k - 2 * d

    boll_middle = close.rolling(20, min_periods=20).mean()
    boll_std = close.rolling(20, min_periods=20).std(ddof=0)
    volume_ma5 = volume.rolling(5, min_periods=5).mean()
    support20 = low.rolling(20, min_periods=20).min()
    resistance20 = high.rolling(20, min_periods=20).max()
    support60 = low.rolling(60, min_periods=60).min()
    resistance60 = high.rolling(60, min_periods=60).max()
    rsi = {period: _rsi(close, period) for period in (6, 12, 24)}

    # Charts need indicator values for every candle, not only the final reading.
    frame = frame.assign(
        ma5=close.rolling(5, min_periods=5).mean(),
        ma10=close.rolling(10, min_periods=10).mean(),
        ma20=close.rolling(20, min_periods=20).mean(),
        ma60=close.rolling(60, min_periods=60).mean(),
        dif=dif,
        dea=dea,
        histogram=macd_histogram,
        k=k,
        d=d,
        j=j,
        rsi6=rsi[6],
        rsi12=rsi[12],
        rsi24=rsi[24],
        boll_upper=boll_middle + 2 * boll_std,
        boll_middle=boll_middle,
        boll_lower=boll_middle - 2 * boll_std,
        volume_ma5=volume_ma5,
        support_20=support20,
        resistance_20=resistance20,
        support_60=support60,
        resistance_60=resistance60,
    )
    latest_volume = _latest(volume)
    latest_volume_ma5 = _latest(volume_ma5)
    volume_ratio = (
        latest_volume / latest_volume_ma5
        if latest_volume is not None and latest_volume_ma5 not in (None, 0)
        else None
    )

    latest = {
        "trade_date": _json_safe(frame["trade_date"].iloc[-1]) if not frame.empty else None,
        "ohlcv": {
            name: _latest(frame[name])
            for name in ("open", "high", "low", "close", "vol", "pct_chg", "turnover_rate")
        },
        "moving_averages": ma,
        "macd": {
            "dif": _latest(dif),
            "dea": _latest(dea),
            "histogram": _latest(macd_histogram),
            **macd_provenance(macd_settings),
        },
        "kdj": {"k": _latest(k), "d": _latest(d), "j": _latest(j)},
        "rsi": {f"rsi{period}": _latest(rsi[period]) for period in (6, 12, 24)},
        "bollinger": {
            "upper": _latest(boll_middle + 2 * boll_std),
            "middle": _latest(boll_middle),
            "lower": _latest(boll_middle - 2 * boll_std),
        },
        "volume": {
            "ma5": latest_volume_ma5,
            "ratio_to_ma5": _json_safe(volume_ratio),
            "turnover_rate": _latest(frame["turnover_rate"]),
        },
        "support_resistance": {
            "support20": _latest(support20),
            "resistance20": _latest(resistance20),
            "support60": _latest(support60),
            "resistance60": _latest(resistance60),
        },
    }
    candles = [_json_safe(record) for record in frame.to_dict(orient="records")]
    return {"candles": candles, "latest": _json_safe(latest), "history_complete": len(frame) >= 120}


def find_strategy_signals(report: dict[str, Any] | None, ts_code: str) -> list[dict[str, Any]]:
    """Find a stock in current pool reports or the older strong/dip report format."""
    if not isinstance(report, dict) or not ts_code:
        return []
    sources: list[tuple[str, Any]] = []
    pools = report.get("pools")
    if isinstance(pools, dict):
        sources.extend((str(strategy), stocks) for strategy, stocks in pools.items())
    sources.extend((
        ("reversal", report.get("dip")),
        ("breakout", report.get("strong")),
        ("first_limit", report.get("first_limit")),
    ))
    found, seen = [], set()
    for strategy, stocks in sources:
        if not isinstance(stocks, list):
            continue
        for stock in stocks:
            if not isinstance(stock, dict) or str(stock.get("ts_code") or "") != str(ts_code):
                continue
            key = (strategy, str(stock.get("ts_code")))
            if key not in seen:
                found.append({"strategy": strategy, **_json_safe(stock)})
                seen.add(key)
    return found


def _report_stock(signals: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the report row associated with the first matching strategy signal."""
    return signals[0] if signals else {}


def _apply_report_turnover(history: Any, report_stock: dict[str, Any]) -> Any:
    """Fill the latest history row from the selected report when daily data lacks turnover."""
    turnover_rate = report_stock.get("turnover_rate")
    if turnover_rate is None or not isinstance(history, pd.DataFrame) or history.empty:
        return history
    frame = history.copy()
    if "turnover_rate" not in frame:
        frame["turnover_rate"] = None
    turnover = pd.to_numeric(frame["turnover_rate"], errors="coerce")
    if turnover.notna().any():
        return frame
    frame.loc[frame.index[-1], "turnover_rate"] = turnover_rate
    return frame


def get_stock_technical_detail(
    ts_code: str, trade_date: str, report_id: int | None = None
) -> dict[str, Any]:
    """Assemble one report-linked stock technical detail response without calling an AI."""
    if not _TS_CODE_PATTERN.fullmatch(ts_code or ""):
        raise ValueError("invalid ts_code")

    report = get_report(report_id) if report_id is not None else get_latest_report()
    if not report:
        raise LookupError("report not found")

    signals = find_strategy_signals(report, ts_code)
    stock = _report_stock(signals)
    history = _apply_report_turnover(
        get_stock_daily_history(ts_code, trade_date, n=120), stock
    )
    snapshot = build_technical_snapshot(history)
    detail = {
        "report_id": report.get("id"),
        "trade_date": trade_date,
        "identity": {
            "ts_code": ts_code,
            "name": stock.get("name"),
            "industry": stock.get("industry"),
        },
        **snapshot,
        "strategy_signals": signals,
    }
    detail["prompt"] = build_ai_prompt(detail)
    return detail


def build_ai_prompt(detail: dict[str, Any]) -> str:
    """Prepare a Chinese analysis request only; this function never invokes an AI provider."""
    detail = detail or {}
    identity = detail.get("identity") or {}
    ts_code = identity.get("ts_code") or detail.get("ts_code") or "未知代码"
    name = identity.get("name") or detail.get("name") or "未知名称"
    trade_date = detail.get("trade_date") or detail.get("latest", {}).get("trade_date") or "未知日期"
    latest = detail.get("latest") or detail.get("technical", {}).get("latest") or {}
    candles = detail.get("candles") or detail.get("technical", {}).get("candles") or []
    signals = detail.get("strategy_signals")
    if signals is None:
        signals = detail.get("signals") or []
    ohlcv = latest.get("ohlcv") or (candles[-1] if candles else {})
    metrics = {key: value for key, value in latest.items() if key not in {"trade_date", "ohlcv"}}

    return f"""
你是一名审慎的A股技术分析助手。请只基于以下已提供数据分析，不补造新闻、资金或基本面信息。

股票身份：{name}（{ts_code}）
交易日期：{trade_date}
最新 OHLCV 摘要：{_json_safe(ohlcv)}
K线数量：{len(candles)}
全部技术指标：{_json_safe(metrics)}
策略信号：{_json_safe(signals)}

请用中文按固定格式输出：
1. 趋势与关键指标解读（MA、MACD、KDJ、RSI、布林带、量能、支撑/压力）。
2. 条件化建仓方案：只有出现何种确认信号才考虑建仓，并给出分批思路。
3. 条件化减仓方案：何种走弱、跌破或过热情形应减仓。
4. 条件化止损方案：明确失效条件与止损纪律，不把技术分析表述为确定预测。
5. 风险提示和数据不足项。

不执行交易、不保证收益。以上内容仅供研究参考，不构成投资建议。
""".strip()
