from __future__ import annotations

from collections import defaultdict
import math
from typing import Any

import pandas as pd


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _normalise_bars(bars: pd.DataFrame) -> pd.DataFrame:
    required = {"high", "low", "close"}
    if bars is None or bars.empty or not required.issubset(bars.columns):
        return pd.DataFrame()
    result = bars.copy()
    for column in ("open", "high", "low", "close", "vol", "amount"):
        if column in result:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    if "trade_date" in result:
        result["_date"] = pd.to_datetime(
            result["trade_date"].astype(str), format="%Y%m%d", errors="coerce"
        )
        result = result.sort_values("_date")
    return result.dropna(subset=["high", "low", "close"]).reset_index(drop=True)


def calculate_atr(bars: pd.DataFrame, period: int = 14) -> float | None:
    data = _normalise_bars(bars)
    if data.empty:
        return None
    previous_close = data["close"].shift(1)
    true_range = pd.concat([
        data["high"] - data["low"],
        (data["high"] - previous_close).abs(),
        (data["low"] - previous_close).abs(),
    ], axis=1).max(axis=1)
    tail = true_range.tail(max(1, int(period))).dropna()
    return None if tail.empty else float(tail.mean())


def _date_text(row: pd.Series, index: int) -> str:
    value = row.get("trade_date")
    if value is not None:
        return str(value)
    timestamp = row.get("_date")
    return timestamp.strftime("%Y%m%d") if pd.notna(timestamp) else str(index)


def _rejection_pct(data: pd.DataFrame, index: int, lookahead: int) -> float:
    price = float(data.iloc[index]["high"])
    future = data.iloc[index + 1:index + 1 + lookahead]
    if price <= 0 or future.empty:
        return 0.0
    future_low = pd.to_numeric(future["low"], errors="coerce").min()
    return 0.0 if pd.isna(future_low) else max(0.0, (price - float(future_low)) / price * 100)


def extract_pressure_candidates(
    bars: pd.DataFrame,
    gene: dict[str, Any] | None,
    chip_context: dict[str, Any] | None,
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    data = _normalise_bars(bars)
    if data.empty:
        return []
    config = settings["pressure"]
    structure = data.tail(int(config["structure_days"])).reset_index(drop=True)
    atr = calculate_atr(data)
    latest_close = float(structure.iloc[-1]["close"])
    atr_pct = (atr / latest_close * 100) if atr and latest_close > 0 else 0.0
    rejection_min = max(
        float(config["rejection_min_pct"]),
        float(config["rejection_atr_factor"]) * atr_pct,
    )
    left = int(config["pivot_left_days"])
    right = int(config["pivot_right_days"])
    lookahead = int(config["rejection_lookahead_days"])
    volume = pd.to_numeric(
        structure["vol"] if "vol" in structure else pd.Series(index=structure.index, dtype=float),
        errors="coerce",
    )
    average_volume = volume.shift(1).rolling(5, min_periods=3).mean()
    limit_date = str((gene or {}).get("latest_limit_up_date") or "")
    candidates: list[dict[str, Any]] = []

    for index in range(len(structure)):
        row = structure.iloc[index]
        price = _number(row.get("high"))
        if price is None or price <= 0:
            continue
        rejection = _rejection_pct(structure, index, lookahead)
        volume_ratio = (
            float(volume.iloc[index] / average_volume.iloc[index])
            if index < len(average_volume)
            and pd.notna(volume.iloc[index]) and pd.notna(average_volume.iloc[index])
            and average_volume.iloc[index] > 0
            else None
        )
        date = _date_text(row, index)
        is_pivot = False
        if left <= index < len(structure) - right:
            before = structure.iloc[index - left:index]["high"]
            after = structure.iloc[index + 1:index + 1 + right]["high"]
            is_pivot = bool(price >= before.max() and price >= after.max())
        if is_pivot and rejection >= rejection_min:
            candidates.append({
                "price": price, "source": "局部高点回落", "date": date,
                "rejection_pct": round(rejection, 4),
                "volume_ratio": None if volume_ratio is None else round(volume_ratio, 4),
            })
        if volume_ratio is not None and volume_ratio >= float(config["volume_surge_ratio"]):
            candle_range = max(0.0, float(row["high"] - row["low"]))
            close_position = (
                float((row["close"] - row["low"]) / candle_range)
                if candle_range > 0 else 0.5
            )
            if rejection >= rejection_min or close_position < 0.65:
                candidates.append({
                    "price": price, "source": "放量冲高回落", "date": date,
                    "rejection_pct": round(rejection, 4),
                    "volume_ratio": round(volume_ratio, 4),
                })
        if limit_date and date > limit_date and rejection >= rejection_min:
            candidates.append({
                "price": price, "source": "涨停后平台上沿", "date": date,
                "rejection_pct": round(rejection, 4),
                "volume_ratio": None if volume_ratio is None else round(volume_ratio, 4),
            })

    chip = chip_context or {}
    chip_high = _number(chip.get("chip_pressure_high"))
    if chip.get("chip_pressure_data_available") and chip_high and chip_high > 0:
        candidates.append({
            "price": chip_high, "source": "筹码密集区上沿",
            "date": str(chip.get("chip_data_trade_date") or ""),
            "rejection_pct": 0.0, "volume_ratio": None,
        })

    structural = [item for item in candidates if item["source"] != "筹码密集区上沿"]
    if not structural and not candidates:
        high_index = structure["high"].idxmax()
        high_row = structure.loc[high_index]
        candidates.append({
            "price": float(high_row["high"]), "source": "N日最高价兜底",
            "date": _date_text(high_row, int(high_index)),
            "rejection_pct": _rejection_pct(structure, int(high_index), lookahead),
            "volume_ratio": None,
        })
    return candidates


def _cluster_limit(reference: float, atr: float | None, config: dict[str, Any]) -> float:
    percent_floor = reference * float(config["cluster_pct"]) / 100
    adaptive = max((atr or 0.0) * float(config["cluster_atr_factor"]), percent_floor)
    return min(reference * float(config["cluster_max_pct"]) / 100, adaptive)


def cluster_pressure_candidates(
    candidates: list[dict[str, Any]],
    atr: float | None,
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    valid = [dict(item) for item in candidates or [] if (_number(item.get("price"), 0) or 0) > 0]
    groups: list[list[dict[str, Any]]] = []
    config = settings["pressure"]
    for item in sorted(valid, key=lambda value: float(value["price"])):
        if groups:
            prices = [float(value["price"]) for value in groups[-1]]
            anchor = sum(prices) / len(prices)
            proposed = prices + [float(item["price"])]
            max_span = min(proposed) * float(config["cluster_max_pct"]) / 100
            if (
                abs(float(item["price"]) - anchor) <= _cluster_limit(anchor, atr, config)
                and max(proposed) - min(proposed) <= max_span
            ):
                groups[-1].append(item)
                continue
        groups.append([item])

    all_dates = sorted(str(item.get("date") or "") for item in valid)
    latest_date = all_dates[-1] if all_dates else ""
    zones = []
    for group in groups:
        prices = [float(item["price"]) for item in group]
        sources = list(dict.fromkeys(str(item.get("source") or "未知") for item in group))
        rejections = [float(item.get("rejection_pct") or 0) for item in group]
        volume_ratios = [float(item.get("volume_ratio") or 0) for item in group]
        touch_score = min(25.0, len(group) * 7.0 + (4.0 if len(group) >= 3 else 0.0))
        rejection_score = min(20.0, (sum(rejections) / max(1, len(rejections))) * 4.0)
        volume_score = 15.0 if any(value >= float(config["volume_surge_ratio"]) for value in volume_ratios) else 0.0
        limit_score = 15.0 if "涨停后平台上沿" in sources else 0.0
        chip_score = 10.0 if "筹码密集区上沿" in sources else 0.0
        recency_score = 10.0 if any(str(item.get("date") or "") == latest_date for item in group) else 6.0
        source_score = 5.0 if len(sources) >= 2 else 0.0
        strength = min(100.0, touch_score + rejection_score + volume_score + limit_score + chip_score + recency_score + source_score)
        zones.append({
            "lower": round(min(prices), 4),
            "upper": round(max(prices), 4),
            "price": round(sum(prices) / len(prices), 4),
            "touch_count": len(group),
            "strength_score": round(strength, 2),
            "sources": sources,
            "touches": group,
            "evidence": [
                f"{len(group)}次候选触碰",
                *sources,
            ],
            "latest_date": max(str(item.get("date") or "") for item in group),
        })
    return sorted(zones, key=lambda zone: (-zone["strength_score"], zone["lower"]))


def select_actionable_pressure_zone(
    zones: list[dict[str, Any]],
    current_price: float | None,
    settings: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    price = _number(current_price)
    if price is None or price <= 0 or not zones:
        return None, "当前价或压力区不可用"
    observe = float(settings["distance"]["observe_pct"])

    def metrics(zone: dict[str, Any]) -> tuple:
        distance = (float(zone["lower"]) / price - 1) * 100
        absolute = abs(distance)
        if -3 <= distance <= 3:
            band = 0
        elif -observe <= distance <= observe:
            band = 1
        else:
            band = 2
        return (
            band, absolute, -float(zone.get("strength_score") or 0),
            -int(zone.get("touch_count") or 0), str(zone.get("latest_date") or ""),
        )

    selected = min(zones, key=metrics)
    distance = (float(selected["upper"]) / price - 1) * 100
    stronger_remote = any(
        float(zone.get("strength_score") or 0) > float(selected.get("strength_score") or 0)
        and abs((float(zone["lower"]) / price - 1) * 100) > observe
        for zone in zones if zone is not selected
    )
    reason = (
        f"距现价{abs(distance):.2f}%，强度{float(selected.get('strength_score') or 0):.0f}，"
        f"{int(selected.get('touch_count') or 0)}次触碰"
    )
    if stronger_remote:
        reason += "；当前可交易距离优先于更强远端压力"
    return selected, reason


def build_breakout_trade_plan(
    support_zone: dict[str, Any] | None,
    selected_zone: dict[str, Any] | None,
    higher_zones: list[dict[str, Any]] | None,
    current_price: float | None,
    atr: float | None,
    settings: dict[str, Any],
) -> dict[str, Any]:
    current = _number(current_price)
    atr_value = _number(atr)
    if not support_zone or not selected_zone or current is None or current <= 0 or atr_value is None:
        return {
            "support_price": None, "pressure_low": None, "pressure_high": None,
            "breakout_trigger": None, "breakout_confirm": None,
            "invalid_price": None, "target_price": None,
            "distance_to_pressure_pct": None, "distance_to_trigger_pct": None,
            "distance_to_confirm_pct": None,
            "trade_plan_missing_reason": "支撑、压力、现价或ATR不可用",
        }
    support = _number(support_zone.get("price"), _number(support_zone.get("lower")))
    support_low = _number(support_zone.get("lower"), support)
    low = float(selected_zone["lower"])
    high = float(selected_zone["upper"])
    config = settings["breakout"]
    trigger = max(
        high * (1 + float(config["trigger_pct"]) / 100),
        high + float(config["trigger_atr_factor"]) * atr_value,
    )
    confirm = max(
        high * (1 + float(config["confirm_pct"]) / 100),
        high + float(config["confirm_atr_factor"]) * atr_value,
    )
    invalid = min(support_low * 0.985, support_low - 0.30 * atr_value)
    targets = sorted(
        float(zone["lower"]) for zone in (higher_zones or [])
        if _number(zone.get("lower")) is not None and float(zone["lower"]) > confirm
    )
    target = targets[0] if targets else high + (float(selected_zone.get("price") or (low + high) / 2) - support)
    distance = lambda value: round((value / current - 1) * 100, 2)
    return {
        "support_price": round(float(support), 4),
        "pressure_low": round(low, 4),
        "pressure_high": round(high, 4),
        "breakout_trigger": round(trigger, 4),
        "breakout_confirm": round(confirm, 4),
        "invalid_price": round(invalid, 4),
        "target_price": round(target, 4),
        "distance_to_pressure_pct": distance(low),
        "distance_to_trigger_pct": distance(trigger),
        "distance_to_confirm_pct": distance(confirm),
        "trade_plan_missing_reason": None,
    }
