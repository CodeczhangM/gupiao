from __future__ import annotations

import re
from typing import Any

import pandas as pd


SH_PREFIXES = ("600", "601", "603", "605", "688", "689")
SZ_PREFIXES = ("000", "001", "002", "003", "300", "301")
RULE_VERSION = "cycle-entry-v1"
STATUS_LABELS = {
    "watch": "继续观察",
    "low_buy": "低吸提示",
    "confirmed": "确认介入",
    "data_delayed": "数据延迟",
}


def normalize_cycle_watch_code(raw: str) -> str:
    value = str(raw or "").strip().upper()
    match = re.fullmatch(r"(\d{6})(?:\.(SH|SZ))?", value)
    if not match:
        raise ValueError("股票代码必须为六位数字，可选带 .SH 或 .SZ 后缀")
    digits, supplied = match.groups()
    expected = (
        "SH" if digits.startswith(SH_PREFIXES)
        else "SZ" if digits.startswith(SZ_PREFIXES)
        else None
    )
    if expected is None:
        raise ValueError("暂只支持沪深 A 股代码")
    if supplied and supplied != expected:
        raise ValueError(f"股票代码 {digits} 的市场后缀应为 .{expected}")
    return f"{digits}.{expected}"


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if pd.notna(number) else default


def _delayed_result(ts_code: str, reason: str, realtime: dict[str, Any]) -> dict[str, Any]:
    return {
        "ts_code": ts_code,
        "status": "data_delayed",
        "status_label": STATUS_LABELS["data_delayed"],
        "opportunity_score": 0.0,
        "current_price": realtime.get("close"),
        "pct_chg": realtime.get("pct_chg"),
        "support_price": None,
        "matched_conditions": [],
        "missing_conditions": [reason],
        "risk_items": [],
        "invalidation_reason": reason,
        "factors": {"rule_version": RULE_VERSION},
    }


def evaluate_cycle_entry(
    ts_code: str,
    daily: pd.DataFrame,
    bars_60m: pd.DataFrame,
    realtime: dict,
    planned_low_price: float | None = None,
    planned_high_price: float | None = None,
) -> dict[str, Any]:
    code = normalize_cycle_watch_code(ts_code)
    required = {"close", "high", "low", "vol"}
    if daily is None or len(daily) < 20 or not required.issubset(daily.columns):
        return _delayed_result(code, "有效日线不足20个交易日", realtime or {})

    frame = daily.copy()
    if "trade_date" in frame:
        frame = frame.sort_values("trade_date", kind="mergesort")
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=list(required)).tail(30)
    if len(frame) < 20:
        return _delayed_result(code, "有效日线不足20个交易日", realtime or {})

    close = frame["close"].astype(float).copy()
    setup_price = float(close.iloc[-1])
    current_price = _number((realtime or {}).get("close"), setup_price)
    ma5 = float(close.tail(5).mean())
    ma10 = float(close.tail(10).mean())
    ma20 = float(close.tail(20).mean())
    recent_high = float(frame["high"].tail(8).max())
    pullback_pct = (recent_high - setup_price) / recent_high * 100 if recent_high > 0 else 0.0

    planned_support = None
    if planned_low_price and planned_high_price:
        planned_support = min(
            max(current_price, float(planned_low_price)),
            float(planned_high_price),
        )
    supports = [ma10, ma20, float(frame["low"].tail(5).min())]
    if planned_support:
        supports.append(planned_support)
    support_price = min(supports, key=lambda value: abs(setup_price - value))
    support_distance = abs(setup_price / support_price - 1) * 100 if support_price > 0 else 999.0

    volume = frame["vol"].astype(float)
    baseline = float(volume.iloc[-8:-3].mean())
    volume_contraction = float(volume.tail(3).mean() / baseline) if baseline > 0 else 999.0
    previous_lows = frame["low"].iloc[-6:-3]
    recent_lows = frame["low"].tail(3)
    stopped_falling = bool(
        not previous_lows.empty
        and not recent_lows.empty
        and float(recent_lows.min()) >= float(previous_lows.min()) * 0.99
    )
    low20 = float(frame["low"].tail(20).min())
    high20 = float(frame["high"].tail(20).max())
    position20 = (
        (setup_price - low20) / (high20 - low20)
        if high20 > low20 else 0.5
    )

    structure_ok = current_price >= ma20 * 0.98
    pullback_ok = 3.0 <= pullback_pct <= 10.0
    support_ok = support_distance <= 2.0
    contraction_ok = volume_contraction <= 0.8
    position_ok = position20 <= 0.70
    daily_score = (
        (15 if structure_ok else 0)
        + (15 if pullback_ok else 0)
        + (15 if support_ok else 0)
        + (10 if contraction_ok else 0)
        + (10 if stopped_falling else 0)
        + (5 if position_ok else 0)
    )

    matched: list[str] = []
    missing: list[str] = []
    if structure_ok:
        matched.append("中期结构未破坏")
    else:
        missing.append("等待重新站稳MA20")
    if pullback_ok and contraction_ok:
        matched.append("回撤缩量")
    else:
        missing.append("等待3%至10%缩量回撤")
    if support_ok:
        matched.append("接近支撑位")
    else:
        missing.append("等待价格接近支撑")
    if stopped_falling:
        matched.append("短线止跌")
    else:
        missing.append("等待低点企稳")

    confirmations: list[str] = []
    bars = bars_60m.copy() if isinstance(bars_60m, pd.DataFrame) else pd.DataFrame()
    if not bars.empty and "close" in bars:
        bars_close = pd.to_numeric(bars["close"], errors="coerce").dropna()
        if len(bars_close) >= 3:
            latest_delta = float(bars_close.iloc[-1] - bars_close.iloc[-2])
            previous_delta = float(bars_close.iloc[-2] - bars_close.iloc[-3])
            if latest_delta > 0 and latest_delta > previous_delta:
                confirmations.append("60分钟MACD柱修复")
    intraday_vwap = _number((realtime or {}).get("intraday_vwap"), 0.0)
    if intraday_vwap > 0 and current_price > intraday_vwap:
        confirmations.append("站上日内均价")
    if current_price > ma5:
        confirmations.append("站上MA5")
    if current_price > float(close.iloc[-4:-1].max()):
        confirmations.append("突破近3日最高收盘")
    volume_ratio = _number((realtime or {}).get("volume_ratio"), 0.0)
    if volume_ratio >= 1.0 and _number((realtime or {}).get("pct_chg"), 0.0) > 0:
        confirmations.append("上涨放量")
    confirmation_count = len(confirmations)
    confirmation_ready = bool(
        confirmation_count >= 2
        and ("60分钟MACD柱修复" in confirmations or volume_ratio >= 1.0)
    )
    matched.extend(confirmations)
    if not confirmation_ready:
        missing.append("等待60分钟确认")

    risk_items: list[str] = []
    hard_risk = current_price < ma20 * 0.98 and volume_ratio >= 1.5
    if hard_risk:
        risk_items.append("放量跌破MA20")
    if position20 >= 0.85:
        risk_items.append("20日位置偏高")
    pct_chg = _number((realtime or {}).get("pct_chg"), 0.0)
    if pct_chg >= 8.5:
        risk_items.append("接近涨停不追高")

    opportunity_score = min(100.0, float(daily_score + confirmation_count * 6))
    if hard_risk:
        status = "watch"
    elif (
        daily_score >= 65
        and confirmation_ready
    ):
        status = "confirmed"
    elif daily_score >= 65:
        status = "low_buy"
    else:
        status = "watch"
    invalidation_reason = "；".join(risk_items) if risk_items else "放量跌破MA20则失效"
    return {
        "ts_code": code,
        "status": status,
        "status_label": STATUS_LABELS[status],
        "opportunity_score": round(opportunity_score, 2),
        "current_price": round(current_price, 4),
        "pct_chg": round(pct_chg, 4),
        "support_price": round(support_price, 4),
        "matched_conditions": matched,
        "missing_conditions": missing,
        "risk_items": risk_items,
        "invalidation_reason": invalidation_reason,
        "factors": {
            "rule_version": RULE_VERSION,
            "daily_score": daily_score,
            "confirmation_count": confirmation_count,
            "ma5": round(ma5, 6),
            "ma10": round(ma10, 6),
            "ma20": round(ma20, 6),
            "pullback_pct": round(pullback_pct, 6),
            "volume_contraction": round(volume_contraction, 6),
            "position20": round(position20, 6),
            "support_distance_pct": round(support_distance, 6),
            "relative_strength": _number((realtime or {}).get("relative_strength"), 0.0),
        },
    }
