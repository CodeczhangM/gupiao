from __future__ import annotations

from typing import Any
import math

from indicator_settings import (
    load_macd_settings,
    macd_parameter_key,
)
from position_strategy_settings import (
    load_position_strategy_settings,
    position_strategy_parameter_key,
)


BASE_POSITION_SCORE_VERSION = "position-candidate-v4-no-risk-reward"
WEIGHTS = {
    "support": 30.0,
    "resonance": 20.0,
    "sector": 18.0,
    "price_volume": 12.0,
    "chip": 8.0,
    "macd": 7.0,
    "relative_tail": 5.0,
}


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "是", "涨停"}
    try:
        return bool(value)
    except (TypeError, ValueError):
        return False


def _contains(value: Any, *markers: str) -> bool:
    text = str(value or "")
    return any(marker in text for marker in markers)


def position_score_version(
    macd_settings: dict[str, Any] | None = None,
    position_settings: dict[str, Any] | None = None,
) -> str:
    macd = dict(macd_settings or load_macd_settings())
    strategy = dict(position_settings or load_position_strategy_settings())
    return (
        f"{BASE_POSITION_SCORE_VERSION}-{macd_parameter_key(macd)}-"
        f"{position_strategy_parameter_key(strategy)}"
    )


def _sector_hot_score(row: dict[str, Any]) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.0
    change = _number(
        row.get("sector_avg_pct_chg"),
        _number(row.get("sector_change"), 0),
    ) or 0
    if change >= 2:
        score += 8
    elif change >= 1:
        score += 6
    elif change >= 0.3:
        score += 3
    if change >= 1:
        reasons.append("板块涨幅领先")

    up_ratio = _number(row.get("sector_up_ratio"), 0) or 0
    if up_ratio > 1:
        up_ratio /= 100
    if up_ratio >= 0.7:
        score += 5
    elif up_ratio >= 0.55:
        score += 3
    elif up_ratio >= 0.45:
        score += 1

    limit_count = _number(
        row.get("sector_limit_up_count"),
        _number(row.get("sector_limit_count"), 0),
    ) or 0
    if limit_count >= 3:
        score += 6
    elif limit_count >= 1:
        score += 3
    if limit_count >= 1:
        reasons.append("板块存在涨停扩散")

    rank = _number(row.get("sector_rank"))
    if rank is not None:
        if rank <= 5:
            score += 6
        elif rank <= 10:
            score += 4
        elif rank <= 20:
            score += 2
    elif (_number(row.get("sector_potential_score"), 0) or 0) >= 70:
        score += 4

    macd_status = row.get("sector_macd_status")
    if _contains(macd_status, "水上", "多头", "金叉", "增强"):
        score += 5
        reasons.append("板块MACD多头")
    elif _contains(macd_status, "转强", "修复"):
        score += 3
    return min(WEIGHTS["sector"], score), reasons


def _price_volume_score(row: dict[str, Any]) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.0
    pct = _number(row.get("pct_chg"), 0) or 0
    ratio = _number(row.get("volume_ratio"), 0) or 0
    turnover = _number(row.get("turnover_rate"), 0) or 0
    confirmed = _truthy(row.get("price_volume_confirmed"))
    if confirmed and 0.3 <= pct <= 7:
        score += 5
        reasons.append("价涨量增")
    elif 0.5 <= pct <= 5 and 1 <= ratio <= 3:
        score += 3

    if 1.2 <= ratio <= 2.8:
        score += 4
    elif 1 <= ratio < 1.2 or 2.8 < ratio <= 3.5:
        score += 2

    if 2 <= turnover <= 10:
        score += 3
    elif 0.6 <= turnover <= 12:
        score += 2

    stage = str(row.get("resonance_stage") or "")
    contraction = _number(row.get("bottom_volume_contraction"))
    expansion = _number(row.get("bottom_volume_expansion"))
    if stage == "launch" and expansion is not None and 1.5 <= expansion <= 3:
        score += 3
        reasons.append("底部健康放量")
    elif stage == "observation" and contraction is not None and contraction <= 0.8:
        score += 3
        reasons.append("缩量回踩")
    elif stage == "trigger":
        score += 2

    main_force = row.get("main_force_status")
    if _contains(main_force, "抢筹", "流入", "增仓"):
        score += 3
        reasons.append("主力状态积极")
    elif _contains(main_force, "偏强", "关注"):
        score += 1

    tail_return = _number(row.get("tail_return_after_1430"))
    tail_ratio = _number(row.get("tail_volume_ratio"))
    if tail_return is not None and 0.1 <= tail_return <= 1.3:
        score += 1
        if tail_ratio is None or 0.8 <= tail_ratio <= 3:
            score += 1
            reasons.append("尾盘量价承接正常")
    return min(WEIGHTS["price_volume"], score), reasons


def _macd_score(
    row: dict[str, Any],
) -> tuple[float, list[str], bool]:
    reasons: list[str] = []
    score = 0.0
    daily_weak = False
    intraday_weak = False
    if _truthy(row.get("macd_golden_cross")):
        score += 6
        reasons.append("日线MACD金叉")
    if _truthy(row.get("macd_above_zero")):
        score += 4
        reasons.append("日线MACD位于零轴上方")
    daily_text = str(row.get("daily_macd_status") or "")
    if _contains(daily_text, "死叉", "水下空头", "走弱"):
        daily_weak = True

    tier = str(row.get("intraday_signal_tier") or "").lower()
    intraday_text = str(row.get("intraday_signal_reason") or "")
    if tier == "strong" or _contains(intraday_text, "水上金叉", "水上多头", "柱体增强"):
        score += 10
        reasons.append("60分钟MACD强势")
    elif tier in {"medium", "watch"} or _contains(intraday_text, "多头", "金叉"):
        score += 6
    elif tier == "weak" or _contains(intraday_text, "死叉", "水下空头", "走弱"):
        intraday_weak = True
    return min(WEIGHTS["macd"], score), reasons, daily_weak and intraday_weak


def _chip_peak_score(row: dict[str, Any]) -> tuple[float, list[str]]:
    if not _truthy(row.get("chip_data_complete")):
        return 0.0, []
    reasons: list[str] = []
    score = 0.0
    concentration = _number(row.get("chip_concentration_70_pct"))
    if concentration is not None:
        if concentration <= 10:
            score += 4
        elif concentration <= 15:
            score += 3
        elif concentration <= 20:
            score += 1
    bottom_position = _number(row.get("chip_peak_bottom_position_pct"))
    if bottom_position is not None:
        if bottom_position <= 25:
            score += 4
        elif bottom_position <= 40:
            score += 3
        elif bottom_position <= 55:
            score += 1
    distance = _number(row.get("chip_price_distance_pct"))
    if distance is not None:
        if -3 <= distance <= 8:
            score += 3
        elif -8 <= distance <= 15:
            score += 1
    winner_rate = _number(row.get("chip_winner_rate"))
    if winner_rate is not None:
        if 20 <= winner_rate <= 65:
            score += 2
        elif 10 <= winner_rate <= 80:
            score += 1
    if _truthy(row.get("chip_build_position")):
        score += 2
        reasons.append("筹码结构具备建仓条件")
    washout = _number(row.get("chip_washout_score"), 0) or 0
    if washout >= 80 and score < 12:
        score += 1
    if score >= 8:
        reasons.append("筹码峰位置与集中度较好")
    return min(WEIGHTS["chip"], score), reasons


def _relative_tail_score(
    row: dict[str, Any],
    market_phase: str,
) -> tuple[float, list[str], list[str]]:
    reasons: list[str] = []
    missing: list[str] = []
    score = 0.0
    relative_score = _number(row.get("realtime_relative_strength_score"))
    relative = _number(row.get("relative_strength"))
    if relative_score is not None:
        if relative_score >= 75:
            score += 6
        elif relative_score >= 60:
            score += 4
        elif relative_score >= 50:
            score += 2
    elif relative is not None:
        if relative >= 1.5:
            score += 6
        elif relative >= 0.5:
            score += 4
        elif relative >= 0:
            score += 2
    if score >= 4:
        reasons.append("个股相对大盘强势")

    tail_available = _truthy(row.get("tail_after_1430_available"))
    if _contains(market_phase, "日线收盘"):
        return min(WEIGHTS["relative_tail"], score), reasons, missing
    before_tail = _contains(market_phase, "盘中观察", "14:30前", "早盘", "午盘")
    if not tail_available:
        missing.append("尾盘确认未到" if before_tail else "尾盘确认缺失")
    else:
        tail_strength = _number(row.get("tail_strength_score"), 0) or 0
        close_position = _number(row.get("tail_close_position"), 0) or 0
        if tail_strength >= 75:
            score += 2
        elif tail_strength >= 60:
            score += 1
        if close_position >= 0.85:
            score += 2
        elif close_position >= 0.65:
            score += 1
        if tail_strength >= 60 and close_position >= 0.65:
            reasons.append("尾盘承接较好")
    return min(WEIGHTS["relative_tail"], score), reasons, missing


def _bottom_structure_score(row: dict[str, Any]) -> tuple[float, list[str]]:
    stage = str(row.get("resonance_stage") or "")
    reasons: list[str] = []
    if stage == "launch":
        score = 5.0
        reasons.append("底部放量启动")
    elif stage == "trigger":
        score = 4.0
        reasons.append("底部首阳触发")
    elif stage == "observation":
        score = 3.0
        reasons.append(str(row.get("resonance_type") or "缩量企稳观察"))
    else:
        score = 0.0
    return score, reasons


def _support_pullback_score(row: dict[str, Any]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    if _truthy(row.get("support_held")):
        score += 18
        reasons.append("主关键位守住")
    state = str(row.get("pullback_state") or "")
    if state == "回踩关键位未破":
        score += 8
        reasons.append("回踩关键位未破")
    elif state == "盘中跌破但收回":
        score += 7
        reasons.append("盘中跌破后收回")
    elif state == "关键位上方企稳":
        score += 5
        reasons.append("关键位上方企稳")
    strength = _number(row.get("primary_support_strength"), 0) or 0
    if strength >= 12:
        score += 4
    elif strength >= 7:
        score += 2
    return min(WEIGHTS["support"], score), reasons


def _historical_resonance_score(row: dict[str, Any]) -> tuple[float, list[str]]:
    events = row.get("resonance_events") or []
    score = _number(row.get("historical_resonance_score"))
    if score is None:
        score = sum(_number(event.get("contribution"), 0) or 0 for event in events)
    reasons = []
    if events:
        latest = events[0]
        reasons.append(
            f"近20日共振：{latest.get('type') or '有效事件'}"
        )
    return min(WEIGHTS["resonance"], max(0.0, score or 0.0)), reasons


def _risk_penalty(
    row: dict[str, Any],
) -> tuple[float, list[str], bool]:
    risks: list[str] = []
    penalty = 0.0
    veto = False
    turnover = _number(row.get("turnover_rate"), 0) or 0
    volume_ratio = _number(row.get("volume_ratio"), 0) or 0
    pct = _number(row.get("pct_chg"), 0) or 0
    tail_return = _number(row.get("tail_return_after_1430"))
    distance = _number(row.get("chip_price_distance_pct"))
    if turnover > 18:
        risks.append("换手过热")
        penalty += 14
        veto = True
    elif turnover > 12:
        risks.append("换手偏高")
        penalty += 6
    if volume_ratio > 5:
        risks.append("成交量异常放大")
        penalty += 10
    if _truthy(row.get("price_volume_stagnation")):
        risks.append("放量滞涨")
        penalty += 10
    if distance is not None and distance > 20:
        risks.append("现价远离筹码主峰")
        penalty += 8
    if tail_return is not None and tail_return <= -0.8:
        risks.append("尾盘明显抛压")
        penalty += 12
        veto = True
    if pct > 7:
        risks.append("当日涨幅过热")
        penalty += 12
    return min(30.0, penalty), list(dict.fromkeys(risks)), veto


def _hard_visibility_reason(row: dict[str, Any]) -> str | None:
    code = str(row.get("ts_code") or "")
    name = str(row.get("name") or "").upper()
    if not code.endswith((".SH", ".SZ")) or code.startswith(
        ("3", "688", "689", "8", "9")
    ):
        return "非沪深主板"
    if "ST" in name or "退市" in name:
        return "风险股票"
    if (_number(row.get("vol"), 0) or 0) <= 0:
        return "停牌或无成交"
    pct = _number(row.get("pct_chg"))
    if pct is None:
        return "最新价格不可用"
    if pct >= 9.5:
        return f"当日涨停或接近涨停：{pct:.2f}%（阈值9.50%）"
    if pct <= -9.5:
        return f"当日跌停或接近跌停：{pct:.2f}%（阈值-9.50%）"
    if _truthy(row.get("limit_sealed")):
        return (
            f"行情源标记封板：现价{_number(row.get('current_price'), _number(row.get('close'), 0)):.2f}，"
            f"涨跌幅{pct:.2f}%，时间{row.get('quote_time') or row.get('data_as_of') or '--'}，"
            f"来源{row.get('quote_source') or row.get('data_source') or '--'}"
        )
    if not _truthy(row.get("limit_gene_eligible")):
        return "前1至10日无涨停基因"
    if not (row.get("resonance_events") or []):
        return "近20日无有效共振"
    if _truthy(row.get("support_volume_break_veto")):
        return "放量跌破主关键位"
    if not _truthy(row.get("support_held")):
        return "有效跌破关键位"
    return None


def score_position_candidate(
    row: dict[str, Any],
    *,
    market_phase: str = "",
) -> dict[str, Any]:
    source = dict(row)
    support_score, support_reasons = _support_pullback_score(source)
    resonance_score, resonance_reasons = _historical_resonance_score(source)
    sector_score, sector_reasons = _sector_hot_score(source)
    price_volume_score, price_volume_reasons = _price_volume_score(source)
    macd_score, macd_reasons, dual_macd_weak = _macd_score(source)
    chip_score, chip_reasons = _chip_peak_score(source)
    relative_tail_score, relative_reasons, missing = _relative_tail_score(
        source,
        market_phase,
    )
    risk_penalty, risk_items, high_risk_veto = _risk_penalty(source)
    hard_reason = _hard_visibility_reason(source)
    if not _truthy(source.get("chip_data_complete")):
        missing.append("筹码数据缺失")
    if sector_score <= 0:
        missing.append("热点板块确认缺失")
    if dual_macd_weak:
        missing.append("日线与60分钟MACD同时走弱")
        high_risk_veto = True

    stock_components: dict[str, dict[str, Any]] = {
        "sector": {"score": sector_score / WEIGHTS["sector"] * 25, "weight": 25, "available": True},
        "price_volume": {"score": price_volume_score / WEIGHTS["price_volume"] * 20, "weight": 20, "available": True},
        "support": {"score": support_score / WEIGHTS["support"] * 20, "weight": 20, "available": True},
        "limit_resonance": {"score": resonance_score / WEIGHTS["resonance"] * 15, "weight": 15, "available": True},
        "chip": {"score": chip_score / WEIGHTS["chip"] * 10, "weight": 10, "available": _truthy(source.get("chip_data_complete"))},
        "macd": {"score": macd_score / WEIGHTS["macd"] * 5, "weight": 5, "available": True},
        "relative_strength": {"score": relative_tail_score / WEIGHTS["relative_tail"] * 5, "weight": 5, "available": True},
    }
    stock_available = sum(
        item["weight"] for item in stock_components.values() if item["available"]
    )
    stock_quality_score = (
        sum(item["score"] for item in stock_components.values() if item["available"])
        / stock_available * 100
        if stock_available else 0.0
    )

    breakout_state = str(source.get("breakout_state") or (
        "CONFIRMED" if _truthy(source.get("breakout_confirmed")) else "NOT_TRIGGERED"
    ))
    distance_to_trigger = _number(source.get("distance_to_trigger_pct"))
    if distance_to_trigger is None:
        distance_to_trigger = -(_number(source.get("breakout_pct"), 0) or 0)
    if distance_to_trigger <= 1.5:
        distance_score = 30.0
    elif distance_to_trigger <= 3:
        distance_score = 22.0
    elif distance_to_trigger <= 5:
        distance_score = 12.0
    else:
        distance_score = 0.0
    stage_scores = {
        "CONFIRMED": 20.0, "TRIGGERED": 15.0, "TOUCHING": 10.0,
        "NOT_TRIGGERED": 5.0, "OVEREXTENDED": 8.0, "FAILED": 0.0,
    }
    false_risk_score = _number(source.get("false_breakout_risk_score"), 0) or 0
    entry_points = (
        distance_score
        + (20.0 if _truthy(source.get("support_held")) else 0.0)
        + stage_scores.get(breakout_state, 0.0)
        + price_volume_score / WEIGHTS["price_volume"] * 10
        + max(0.0, 10.0 - false_risk_score / 10)
    )
    entry_available = 90.0
    tail_available = _truthy(source.get("tail_after_1430_available"))
    if tail_available:
        entry_available += 10.0
        tail_score = _number(source.get("tail_strength_score"), 0) or 0
        entry_points += max(0.0, min(10.0, tail_score / 10))
    entry_timing_score = entry_points / entry_available * 100

    daily_complete = all(source.get(key) is not None for key in ("close", "vol"))
    pressure_complete = all(source.get(key) is not None for key in ("pressure_low", "pressure_high", "breakout_trigger"))
    sector_complete = bool(source.get("industry")) and (
        source.get("sector_rank") is not None or source.get("sector_avg_pct_chg") is not None
    )
    data_confidence = (
        (40 if daily_complete else 0)
        + (20 if pressure_complete else 0)
        + (15 if sector_complete else 0)
        + (10 if _truthy(source.get("chip_data_complete")) else 0)
        + (15 if tail_available or source.get("above_vwap") is not None else 0)
    )
    breakout_quality = _number(source.get("breakout_quality_score"))
    weighted = [
        (stock_quality_score, 0.40),
        (entry_timing_score, 0.35),
        (data_confidence, 0.05),
    ]
    if breakout_quality is not None:
        weighted.append((breakout_quality, 0.20))
    final_score = sum(value * weight for value, weight in weighted) / sum(
        weight for _, weight in weighted
    )
    final_score = min(100.0, max(0.0, final_score - risk_penalty))

    false_breakout_risk = str(source.get("false_breakout_risk") or "LOW")
    x_reason = hard_reason
    if x_reason is None and high_risk_veto:
        x_reason = "存在高风险否决项"
    if x_reason is None and stock_quality_score < 50:
        x_reason = f"股票质量低于50（{stock_quality_score:.2f}）"
    if x_reason is None and not _truthy(source.get("support_held")):
        x_reason = "支撑位已经失效"
    if x_reason is None and false_breakout_risk == "HIGH" and breakout_state == "FAILED":
        x_reason = "严重假突破风险"
    if x_reason is None and distance_to_trigger > 5:
        x_reason = f"距触发价超过5%（{distance_to_trigger:.2f}%）"

    immediate_confirmed = (
        breakout_state == "CONFIRMED"
        and (breakout_quality or 0) >= 75
        and false_breakout_risk != "HIGH"
        and data_confidence >= 70
        and _truthy(source.get("support_held"))
        and sector_score >= 9
        and _truthy(source.get("chip_data_complete"))
        and (tail_available or _contains(market_phase, "日线收盘"))
        and not dual_macd_weak
    )
    if x_reason:
        build_level, build_status, level_reason = "X", "放弃", x_reason
    elif immediate_confirmed:
        build_level, build_status = "A+", "已确认，可考虑建仓"
        level_reason = "压力突破有效，量价、板块与风险确认通过"
    elif breakout_state == "OVEREXTENDED":
        build_level, build_status, level_reason = "B", "等回踩", "已突破但离确认价偏远"
    elif distance_to_trigger <= 1.5:
        build_level, build_status, level_reason = "A", "临界突破", "距触发价不超过1.5%"
    elif distance_to_trigger <= 3:
        build_level, build_status, level_reason = "B+", "等待突破", "距触发价不超过3%"
    else:
        build_level, build_status, level_reason = "C", "观察", "距触发价在3%至5%之间"

    positive = list(dict.fromkeys(
        support_reasons
        + resonance_reasons
        + sector_reasons
        + price_volume_reasons
        + macd_reasons
        + chip_reasons
        + relative_reasons
    ))
    return {
        **source,
        "position_score": round(final_score, 2),
        "position_level": build_status,
        "position_level_reason": level_reason,
        "position_filter_reason": level_reason if build_level == "X" else None,
        "stock_quality_score": round(stock_quality_score, 2),
        "entry_timing_score": round(entry_timing_score, 2),
        "data_confidence": round(data_confidence, 2),
        "final_score": round(final_score, 2),
        "build_position_level": build_level,
        "build_position_status": build_status,
        "score_components": stock_components,
        "support_pullback_score": round(support_score, 2),
        "historical_resonance_score": round(resonance_score, 2),
        "sector_hot_score": round(sector_score, 2),
        "sector_hot_status": (
            "热点" if sector_score >= 14 else "偏强" if sector_score >= 9 else "非热点"
        ),
        "sector_hot_reason": "；".join(sector_reasons) or "板块热度不足",
        "price_volume_score": round(price_volume_score, 2),
        "macd_score": round(macd_score, 2),
        "macd_strength": "强" if macd_score >= 5 else "中" if macd_score >= 2 else "弱",
        "chip_peak_score": round(chip_score, 2),
        "relative_tail_score": round(relative_tail_score, 2),
        "confirmation_state": (
            "已突破确认" if _truthy(source.get("breakout_confirmed"))
            else "等待突破"
        ),
        "position_risk_penalty": round(risk_penalty, 2),
        "position_risk_items": risk_items,
        "position_high_risk_veto": high_risk_veto,
        "position_positive_reasons": positive,
        "position_missing_confirmations": list(dict.fromkeys(missing)),
    }


def rank_position_candidates(
    rows: list[dict[str, Any]],
    limit: int = 10,
    *,
    market_phase: str = "",
) -> list[dict[str, Any]]:
    scored = [
        score_position_candidate(row, market_phase=market_phase)
        for row in (rows or [])
    ]
    return rank_scored_position_candidates(scored, limit=limit)


def rank_scored_position_candidates(
    rows: list[dict[str, Any]],
    limit: int = 10,
) -> list[dict[str, Any]]:
    tier = {"A+": 0, "A": 1, "B+": 2, "B": 3, "C": 4}
    visible = [row for row in (rows or []) if row.get("build_position_level") in tier]
    cap = max(1, min(int(limit), 10))
    return sorted(
        visible,
        key=lambda row: (
            tier[str(row.get("build_position_level"))],
            -float(row.get("final_score") or 0),
            -float(row.get("entry_timing_score") or 0),
            -float(row.get("stock_quality_score") or 0),
            str(row.get("ts_code") or ""),
        ),
    )[:cap]
