from __future__ import annotations

from typing import Any
from copy import deepcopy
import math

import pandas as pd

from breakout_trade_evaluation import evaluate_breakout
from position_strategy_settings import DEFAULT_POSITION_STRATEGY_SETTINGS
from pressure_zone_service import (
    build_breakout_trade_plan,
    calculate_atr,
    cluster_pressure_candidates,
    extract_pressure_candidates,
    select_actionable_pressure_zone,
)


EVENT_TYPES = (
    "daily_macd",
    "hourly_macd_kdj",
    "volume_breakout",
    "bottom_first_up",
    "volume_contraction",
)


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _truthy(value: Any) -> bool:
    if value is None or value is pd.NA:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {
            "1", "true", "yes", "是", "涨停", "金叉", "转强",
        }
    try:
        return bool(value)
    except (TypeError, ValueError):
        return False


def _normalise_bars(bars: pd.DataFrame, as_of_trade_date: str) -> pd.DataFrame:
    if bars is None or bars.empty or "trade_date" not in bars.columns:
        return pd.DataFrame()
    result = bars.copy()
    result["_trade_timestamp"] = pd.to_datetime(
        result["trade_date"].astype(str),
        format="%Y%m%d",
        errors="coerce",
    )
    as_of = pd.to_datetime(str(as_of_trade_date), format="%Y%m%d", errors="coerce")
    result = result.dropna(subset=["_trade_timestamp"])
    if pd.notna(as_of):
        result = result[result["_trade_timestamp"] <= as_of]
    return result.sort_values("_trade_timestamp").reset_index(drop=True)


def event_decay(trading_days_ago: int) -> float:
    if 1 <= trading_days_ago <= 3:
        return 1.0
    if trading_days_ago <= 7 and trading_days_ago >= 1:
        return 0.8
    if trading_days_ago <= 12 and trading_days_ago >= 1:
        return 0.6
    if trading_days_ago <= 20 and trading_days_ago >= 1:
        return 0.4
    return 0.0


def _historical_rows(bars: pd.DataFrame, as_of_trade_date: str) -> pd.DataFrame:
    normalised = _normalise_bars(bars, as_of_trade_date)
    if normalised.empty:
        return normalised
    as_of = pd.to_datetime(str(as_of_trade_date), format="%Y%m%d", errors="coerce")
    if pd.isna(as_of):
        return pd.DataFrame()
    return normalised[normalised["_trade_timestamp"] < as_of].copy()


def _is_limit_up(row: pd.Series) -> bool:
    pct_chg = _number(row.get("pct_chg"), -math.inf)
    if pct_chg is not None and pct_chg >= 9.5:
        return True
    return _truthy(row.get("limit_flag"))


def extract_limit_gene(
    bars: pd.DataFrame,
    as_of_trade_date: str,
) -> dict[str, Any]:
    history = _historical_rows(bars, as_of_trade_date)
    empty = {
        "limit_gene_eligible": False,
        "limit_history_sufficient": len(history) >= 10,
        "latest_limit_up_date": None,
        "latest_limit_up_days_ago": None,
        "latest_limit_up_close": None,
        "latest_limit_up_body_low": None,
        "latest_limit_up_start_price": None,
        "limit_up_count_10d": 0,
    }
    if history.empty:
        return empty
    window = history.tail(10).copy()
    matches = window[window.apply(_is_limit_up, axis=1)]
    if matches.empty:
        return empty
    latest = matches.iloc[-1]
    latest_index = int(history.index[history["_trade_timestamp"] == latest["_trade_timestamp"]][-1])
    days_ago = len(history) - latest_index
    open_price = _number(latest.get("open"))
    close = _number(latest.get("close"))
    body_values = [value for value in (open_price, close) if value is not None]
    return {
        **empty,
        "limit_gene_eligible": True,
        "latest_limit_up_date": latest["_trade_timestamp"].strftime("%Y%m%d"),
        "latest_limit_up_days_ago": days_ago,
        "latest_limit_up_close": close,
        "latest_limit_up_body_low": min(body_values) if body_values else None,
        "latest_limit_up_start_price": open_price,
        "limit_up_count_10d": int(len(matches)),
    }


def _event_present(row: pd.Series, event_type: str) -> bool:
    explicit = row.get(f"{event_type}_event")
    if _truthy(explicit):
        return True
    aliases = {
        "daily_macd": ("macd_golden_cross", "macd_turned_strong"),
        "hourly_macd_kdj": ("macd_kdj_60m_resonance", "hourly_resonance"),
        "volume_breakout": ("volume_breakout",),
        "bottom_first_up": ("bottom_first_up", "first_positive_trigger"),
        "volume_contraction": ("volume_contraction_stable", "stable_base"),
    }
    return any(_truthy(row.get(key)) for key in aliases[event_type])


def extract_resonance_events(
    bars: pd.DataFrame,
    as_of_trade_date: str,
) -> dict[str, Any]:
    full_history = _historical_rows(bars, as_of_trade_date)
    if not full_history.empty:
        close = pd.to_numeric(full_history.get("close"), errors="coerce")
        high = pd.to_numeric(full_history.get("high"), errors="coerce")
        volume = pd.to_numeric(full_history.get("vol"), errors="coerce")
        prior_high = high.shift(1).rolling(5, min_periods=3).max()
        prior_volume = volume.shift(1).rolling(5, min_periods=3).mean()
        derived_breakout = (
            (close > prior_high) & (volume >= prior_volume * 1.5)
        ).fillna(False)
        existing_breakout = full_history.get(
            "volume_breakout_event",
            pd.Series(False, index=full_history.index),
        ).fillna(False).astype(bool)
        full_history["volume_breakout_event"] = existing_breakout | derived_breakout
        derived_strength = (
            (volume / prior_volume).clip(lower=0, upper=4) * 4
        ).fillna(0)
        existing_strength = pd.to_numeric(
            full_history.get(
                "volume_breakout_strength",
                pd.Series(0, index=full_history.index),
            ),
            errors="coerce",
        ).fillna(0)
        full_history["volume_breakout_strength"] = pd.concat(
            [existing_strength, derived_strength], axis=1
        ).max(axis=1)
        full_history["bottom_first_up_event"] = (
            (close > pd.to_numeric(full_history.get("open"), errors="coerce"))
            & (close.shift(1) <= pd.to_numeric(full_history.get("open"), errors="coerce").shift(1))
        ).fillna(False)
        full_history["volume_contraction_event"] = (
            (volume <= prior_volume * 0.8) & (close >= close.shift(1) * 0.985)
        ).fillna(False)
    history = full_history.tail(20)
    best_by_type: dict[str, dict[str, Any]] = {}
    total_history = len(history)
    for local_index, (_, row) in enumerate(history.iterrows()):
        days_ago = total_history - local_index
        decay = event_decay(days_ago)
        if decay <= 0:
            continue
        for event_type in EVENT_TYPES:
            if not _event_present(row, event_type):
                continue
            raw_strength = _number(row.get(f"{event_type}_strength"), 5.0) or 0.0
            event = {
                "type": event_type,
                "date": row["_trade_timestamp"].strftime("%Y%m%d"),
                "days_ago": days_ago,
                "raw_strength": raw_strength,
                "decay": decay,
                "contribution": round(raw_strength * decay, 2),
            }
            previous = best_by_type.get(event_type)
            if previous is None or (event["contribution"], -days_ago) > (
                previous["contribution"], -previous["days_ago"]
            ):
                best_by_type[event_type] = event
    events = sorted(best_by_type.values(), key=lambda item: item["days_ago"])
    return {
        "resonance_events": events,
        "latest_resonance_date": events[0]["date"] if events else None,
        "latest_resonance_days_ago": events[0]["days_ago"] if events else None,
        "historical_resonance_score": round(
            min(20.0, sum(event["contribution"] for event in events)), 2
        ),
    }


def merge_key_levels(
    levels: list[dict[str, Any]],
    tolerance_pct: float = 1.5,
    limit: int = 3,
) -> list[dict[str, Any]]:
    valid = []
    for level in levels or []:
        price = _number(level.get("price"))
        if price is None or price <= 0:
            continue
        valid.append({
            "price": price,
            "source": str(level.get("source") or "未知"),
            "strength": _number(level.get("strength"), 0.0) or 0.0,
        })
    groups: list[list[dict[str, Any]]] = []
    for level in sorted(valid, key=lambda item: item["price"]):
        if groups:
            group_prices = [item["price"] for item in groups[-1]]
            anchor = sum(group_prices) / len(group_prices)
            if abs(level["price"] - anchor) / min(level["price"], anchor) * 100 <= tolerance_pct:
                groups[-1].append(level)
                continue
        groups.append([level])
    zones = []
    for group in groups:
        prices = [item["price"] for item in group]
        zones.append({
            "lower": min(prices),
            "upper": max(prices),
            "price": round(sum(prices) / len(prices), 4),
            "sources": list(dict.fromkeys(item["source"] for item in group)),
            "strength": round(sum(item["strength"] for item in group), 2),
        })
    return sorted(
        zones,
        key=lambda zone: (zone["strength"], len(zone["sources"]), zone["price"]),
        reverse=True,
    )[:max(1, int(limit))]


def _moving_average_level(bars: pd.DataFrame, period: int) -> float | None:
    if "close" not in bars.columns or len(bars) < period:
        return None
    values = pd.to_numeric(bars["close"], errors="coerce").dropna().tail(period)
    return float(values.mean()) if len(values) == period else None


def extract_pullback_confirmation(
    bars: pd.DataFrame,
    gene: dict[str, Any],
    current: dict[str, Any],
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    strategy_settings = deepcopy(settings or DEFAULT_POSITION_STRATEGY_SETTINGS)
    normalised = _normalise_bars(
        bars,
        str(current.get("trade_date") or "29991231"),
    )
    limit_date = pd.to_datetime(
        str(gene.get("latest_limit_up_date") or ""),
        format="%Y%m%d",
        errors="coerce",
    )
    post_limit = normalised[
        normalised["_trade_timestamp"] > limit_date
    ].head(10) if pd.notna(limit_date) else normalised.tail(10)
    platform_lows = pd.to_numeric(post_limit.get("low"), errors="coerce").dropna()
    platform_highs = pd.to_numeric(post_limit.get("high"), errors="coerce").dropna()
    platform_lower = float(platform_lows.median()) if not platform_lows.empty else None
    platform_upper = float(platform_highs.max()) if not platform_highs.empty else None
    before_limit = normalised[
        normalised["_trade_timestamp"] < limit_date
    ] if pd.notna(limit_date) else pd.DataFrame()
    prior_highs = (
        pd.to_numeric(before_limit["high"], errors="coerce").dropna()
        if "high" in before_limit.columns else pd.Series(dtype=float)
    )
    prior_breakout = (
        _number(current.get("prior_breakout_price"))
        or (float(prior_highs.tail(20).max()) if not prior_highs.empty else None)
    )
    levels = [
        {"price": gene.get("latest_limit_up_body_low"), "source": "涨停实体低点", "strength": 8},
        {"price": gene.get("latest_limit_up_start_price"), "source": "涨停启动价", "strength": 9},
        {"price": platform_lower, "source": "平台下沿", "strength": 7},
        {"price": _moving_average_level(normalised, 10), "source": "MA10", "strength": 5},
        {"price": _moving_average_level(normalised, 20), "source": "MA20", "strength": 6},
        {"price": current.get("chip_peak_price"), "source": "筹码峰", "strength": 7},
        {"price": prior_breakout, "source": "前期突破位", "strength": 7},
    ]
    zones = merge_key_levels(levels)
    current_price = _number(
        current.get("current_price"),
        _number(current.get("close")),
    )
    nearby = [
        zone for zone in zones
        if current_price is not None and zone["lower"] <= current_price * 1.015
    ]
    primary = max(
        nearby or zones,
        key=lambda zone: (zone["strength"], zone["price"]),
        default=None,
    )
    result = {
        "support_zones": zones,
        "platform_lower": platform_lower,
        "platform_upper": platform_upper,
        "primary_support": None if primary is None else primary["price"],
        "primary_support_lower": None if primary is None else primary["lower"],
        "primary_support_upper": None if primary is None else primary["upper"],
        "primary_support_sources": [] if primary is None else primary["sources"],
        "primary_support_strength": 0.0 if primary is None else primary["strength"],
        "support_distance_pct": None,
        "support_held": False,
        "pullback_state": "有效跌破关键位",
        "support_volume_break_veto": False,
        "confirmation_price": None,
        "confirmation_source": None,
        "breakout_pct": None,
        "price_volume_confirmation": False,
        "breakout_confirmed": False,
        "pressure_zones": [],
        "pressure_low": None,
        "pressure_high": None,
        "breakout_trigger": None,
        "breakout_confirm": None,
        "invalid_price": None,
        "target_price": None,
        "distance_to_pressure_pct": None,
        "distance_to_trigger_pct": None,
        "distance_to_confirm_pct": None,
        "pressure_strength_score": 0.0,
        "pressure_sources": [],
        "pressure_selection_reason": "压力区不可用",
        "breakout_state": "NOT_TRIGGERED",
        "breakout_quality_score": None,
        "breakout_quality_label": "未触发",
        "false_breakout_risk_score": 0.0,
        "false_breakout_risk": "LOW",
        "breakout_evidence": [],
        "false_breakout_evidence": [],
    }
    if primary is None or current_price is None or normalised.empty:
        return result
    support = primary["price"]
    result["support_distance_pct"] = round((current_price / support - 1) * 100, 2)
    lows = pd.to_numeric(post_limit.get("low"), errors="coerce").dropna()
    closes = pd.to_numeric(post_limit.get("close"), errors="coerce").dropna()
    volumes = pd.to_numeric(post_limit.get("vol"), errors="coerce").dropna()
    minimum_low = float(lows.min()) if not lows.empty else current_price
    minimum_close = float(closes.min()) if not closes.empty else current_price
    broke_intraday = minimum_low < support * 0.985
    broke_close = minimum_close < support * 0.985
    if broke_close:
        state = "有效跌破关键位"
    elif broke_intraday:
        state = "盘中跌破但收回"
    elif minimum_low <= support * 1.015:
        state = "回踩关键位未破"
    else:
        state = "关键位上方企稳"
    support_held = not broke_close and current_price >= support * 0.985
    result.update({"pullback_state": state, "support_held": support_held})
    if broke_close and not volumes.empty:
        break_rows = post_limit[pd.to_numeric(post_limit["close"], errors="coerce") < support * 0.985]
        break_volume = pd.to_numeric(break_rows.get("vol"), errors="coerce").max()
        baseline = float(volumes.median())
        result["support_volume_break_veto"] = bool(
            pd.notna(break_volume) and baseline > 0 and float(break_volume) >= baseline * 1.5
        )
    atr = calculate_atr(normalised)
    pressure_candidates = extract_pressure_candidates(
        normalised,
        gene,
        current,
        strategy_settings,
    )
    pressure_zones = cluster_pressure_candidates(
        pressure_candidates,
        atr,
        strategy_settings,
    )
    selected_pressure, selection_reason = select_actionable_pressure_zone(
        pressure_zones,
        current_price,
        strategy_settings,
    )
    higher_zones = [
        zone for zone in pressure_zones
        if selected_pressure is None or zone is not selected_pressure
    ]
    plan = build_breakout_trade_plan(
        primary,
        selected_pressure,
        higher_zones,
        current_price,
        atr,
        strategy_settings,
    )
    latest = normalised.iloc[-1].to_dict()
    daily_bar = {
        "open": current.get("open", latest.get("open")),
        "high": current.get("high", latest.get("high")),
        "low": current.get("low", latest.get("low")),
        "close": current_price,
        "vol": current.get("vol", latest.get("vol")),
    }
    prior_volumes = pd.to_numeric(
        normalised["vol"] if "vol" in normalised else pd.Series(dtype=float),
        errors="coerce",
    ).dropna().tail(6)
    avg_volume_5 = (
        float(prior_volumes.iloc[:-1].mean())
        if len(prior_volumes) >= 2 else None
    )
    evaluation = evaluate_breakout(
        daily_bar,
        plan,
        {
            **current,
            "avg_volume_5": current.get("avg_volume_5", avg_volume_5),
        },
        strategy_settings,
    )
    confirmation = plan.get("breakout_confirm")
    breakout_pct = (
        (current_price / confirmation - 1) * 100
        if confirmation is not None and confirmation > 0 else None
    )
    result.update({
        **plan,
        **evaluation,
        "pressure_zones": pressure_zones,
        "pressure_strength_score": (
            0.0 if selected_pressure is None
            else selected_pressure.get("strength_score", 0.0)
        ),
        "pressure_sources": (
            [] if selected_pressure is None else selected_pressure.get("sources", [])
        ),
        "pressure_selection_reason": selection_reason,
        "confirmation_price": confirmation,
        "confirmation_source": "压力区ATR确认价" if confirmation is not None else None,
        "breakout_pct": None if breakout_pct is None else round(breakout_pct, 2),
        "breakout_confirmed": bool(
            support_held and evaluation.get("breakout_state") == "CONFIRMED"
        ),
    })
    return result
