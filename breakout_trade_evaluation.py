from __future__ import annotations

import math
from typing import Any


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _quality_label(score: float | None, touched: bool) -> str:
    if not touched or score is None:
        return "未触发"
    if score >= 80:
        return "强突破"
    if score >= 65:
        return "正常突破"
    if score >= 50:
        return "弱突破"
    return "疑似假突破"


def evaluate_breakout(
    daily_bar: dict[str, Any],
    plan: dict[str, Any],
    context: dict[str, Any] | None,
    settings: dict[str, Any],
) -> dict[str, Any]:
    context = context or {}
    high = _number(daily_bar.get("high"), 0) or 0
    low = _number(daily_bar.get("low"), high) or high
    close = _number(daily_bar.get("close"), 0) or 0
    open_price = _number(daily_bar.get("open"), close) or close
    volume = _number(daily_bar.get("vol"))
    pressure_low = _number(plan.get("pressure_low"), math.inf) or math.inf
    pressure_high = _number(plan.get("pressure_high"), math.inf) or math.inf
    trigger = _number(plan.get("breakout_trigger"), math.inf) or math.inf
    confirm = _number(plan.get("breakout_confirm"), math.inf) or math.inf
    config = settings["breakout"]
    candle_range = max(0.0, high - low)
    close_position = (close - low) / candle_range if candle_range > 0 else 0.5
    upper_shadow = (high - max(open_price, close)) / candle_range if candle_range > 0 else 0.0
    average_volume = _number(context.get("avg_volume_5"))
    volume_ratio = (
        volume / average_volume
        if volume is not None and average_volume is not None and average_volume > 0
        else _number(context.get("volume_ratio"))
    )
    volume_confirmed = bool(
        volume_ratio is not None
        and volume_ratio >= float(config["volume_confirm_ratio"])
    )
    price_confirmed = close >= confirm
    position_confirmed = close_position >= float(config["close_position_min"])
    touched = high >= pressure_low

    if not touched:
        state = "NOT_TRIGGERED"
    elif high >= trigger and close < pressure_high:
        state = "FAILED"
    elif price_confirmed and volume_confirmed and position_confirmed:
        state = (
            "OVEREXTENDED"
            if close >= confirm * (1 + float(settings["distance"]["waiting_pct"]) / 100)
            else "CONFIRMED"
        )
    elif close >= trigger or high >= trigger:
        state = "TRIGGERED"
    else:
        state = "TOUCHING"

    quality_points = 0.0
    available_weight = 0.0
    evidence: list[str] = []
    if touched:
        available_weight += 25
        if close >= confirm:
            quality_points += 25
            evidence.append("收盘达到确认价")
        elif close >= trigger:
            quality_points += 16
            evidence.append("收盘越过触发价")
        elif high >= trigger:
            quality_points += 8
            evidence.append("盘中触发突破")

        if volume_ratio is not None:
            available_weight += 20
            if volume_ratio >= float(config["volume_confirm_ratio"]):
                quality_points += 20
                evidence.append(f"量比{volume_ratio:.2f}确认")
            elif volume_ratio >= 1.0:
                quality_points += 6

        available_weight += 15
        quality_points += 15 if position_confirmed else 5 if close_position >= 0.5 else 0
        evidence.append(f"收盘位置{close_position:.0%}")

        available_weight += 10
        if upper_shadow < float(config["long_upper_shadow_ratio"]):
            quality_points += 10
        evidence.append(f"上影占振幅{upper_shadow:.0%}")

        hold_ratio = _number(context.get("breakout_hold_ratio"))
        if hold_ratio is not None:
            available_weight += 10
            quality_points += 10 * max(0.0, min(1.0, hold_ratio))
            evidence.append(f"突破保持{hold_ratio:.0%}")
        tail_stable = context.get("tail_stable_above_pressure")
        if tail_stable is not None:
            available_weight += 10
            quality_points += 10 if bool(tail_stable) else 0
            evidence.append("尾盘站稳" if tail_stable else "尾盘未站稳")
        above_vwap = context.get("above_vwap")
        if above_vwap is not None:
            available_weight += 5
            quality_points += 5 if bool(above_vwap) else 0
            evidence.append("收盘位于VWAP上方" if above_vwap else "收盘跌破VWAP")
        sector_score = _number(context.get("sector_hot_score"))
        if sector_score is not None:
            available_weight += 5
            quality_points += 5 * max(0.0, min(1.0, sector_score / 18))
            evidence.append("板块同步确认")
    quality_score = (
        round(quality_points / available_weight * 100, 2)
        if touched and available_weight > 0 else None
    )

    risk_score = 0.0
    risks: list[str] = []
    if high >= trigger and close < pressure_high:
        risk_score += 50
        risks.append("盘中突破后收盘跌回压力区")
    if touched and close_position < 0.5:
        risk_score += 20
        risks.append("突破日收盘位置低于50%")
    if high >= trigger and not volume_confirmed:
        risk_score += 15
        risks.append("突破未明显放量")
    if upper_shadow >= float(config["long_upper_shadow_ratio"]):
        risk_score += 15
        risks.append("突破日长上影明显")
    if int(_number(context.get("failed_pressure_attacks"), 0) or 0) >= 3:
        risk_score += 10
        risks.append("同一压力区连续冲击失败")
    if (_number(context.get("tail_return_after_1430"), 0) or 0) <= -0.8:
        risk_score += 15
        risks.append("尾盘明显回落")
    if context.get("above_vwap") is False:
        risk_score += 10
        risks.append("收盘跌破VWAP")
    if context.get("next_day_back_inside_pressure") is True:
        risk_score += 25
        risks.append("突破后次日快速跌回压力区")
    turnover = _number(context.get("turnover_rate"))
    if turnover is not None and turnover > 18:
        risk_score += 15
        risks.append("高位换手异常放大")
    risk_score = min(100.0, risk_score)
    risk_label = "HIGH" if risk_score >= 50 else "MEDIUM" if risk_score >= 25 else "LOW"
    return {
        "breakout_state": state,
        "breakout_quality_score": quality_score,
        "breakout_quality_label": _quality_label(quality_score, touched),
        "breakout_quality_available_weight": round(available_weight, 2),
        "breakout_evidence": evidence,
        "false_breakout_risk_score": round(risk_score, 2),
        "false_breakout_risk": risk_label,
        "false_breakout_evidence": risks,
        "close_position": round(close_position, 4),
        "upper_shadow_ratio": round(upper_shadow, 4),
        "breakout_volume_ratio": None if volume_ratio is None else round(volume_ratio, 4),
        "price_volume_confirmation": volume_confirmed,
        "breakout_confirmed": state == "CONFIRMED",
    }


def calculate_risk_reward(
    plan: dict[str, Any],
    breakout_state: str,
    current_price: float | None,
    settings: dict[str, Any],
) -> dict[str, Any]:
    confirm = _number(plan.get("breakout_confirm"))
    stop = _number(plan.get("invalid_price"))
    target = _number(plan.get("target_price"))
    current = _number(current_price)
    if breakout_state in {"CONFIRMED", "OVEREXTENDED", "TRIGGERED", "FAILED"}:
        entry = max(value for value in (current, confirm) if value is not None) if current is not None or confirm is not None else None
    else:
        entry = confirm
    evidence = {
        "entry_price": entry, "stop_price": stop, "target_price": target,
        "risk": None, "reward": None,
    }
    if entry is None or stop is None or target is None:
        return {
            "entry_price": entry, "stop_price": stop, "target_price": target,
            "risk_reward_ratio": None, "risk_reward_score": None,
            "risk_reward_label": "数据不足", "risk_reward_evidence": evidence,
        }
    risk = entry - stop
    reward = target - entry
    evidence.update({"risk": round(risk, 4), "reward": round(reward, 4)})
    if risk <= 0 or reward <= 0:
        return {
            "entry_price": entry, "stop_price": stop, "target_price": target,
            "risk_reward_ratio": None, "risk_reward_score": None,
            "risk_reward_label": "结构无效", "risk_reward_evidence": evidence,
        }
    ratio = reward / risk
    config = settings["risk_reward"]
    if ratio < float(config["minimum_ratio"]):
        score, label = min(39.0, ratio / float(config["minimum_ratio"]) * 39), "不建议"
    elif ratio < float(config["good_ratio"]):
        score, label = 40 + (ratio - float(config["minimum_ratio"])) / (float(config["good_ratio"]) - float(config["minimum_ratio"])) * 19, "一般"
    elif ratio < float(config["excellent_ratio"]):
        score, label = 60 + (ratio - float(config["good_ratio"])) / (float(config["excellent_ratio"]) - float(config["good_ratio"])) * 24, "良好"
    else:
        score, label = min(100.0, 85 + (ratio - float(config["excellent_ratio"])) * 5), "优秀"
    return {
        "entry_price": round(entry, 4), "stop_price": round(stop, 4),
        "target_price": round(target, 4), "risk_reward_ratio": round(ratio, 4),
        "risk_reward_score": round(score, 2), "risk_reward_label": label,
        "risk_reward_evidence": evidence,
    }
