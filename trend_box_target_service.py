"""Trend box target analysis for one stock's daily candles."""

from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd

from data_service import get_stock_daily_history
from market_cache import load_recent_daily


_TS_CODE_PATTERN = re.compile(r"(\d{6})(?:\.(SH|SZ))?")


DEFAULT_PARAMS = {
    "base_box_days_candidates": [15, 16, 17, 18, 19, 20],
    "relay_box_days_candidates": [3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
    "box_end_offsets": [-1, -2],
    "base_range_min_pct": 6.0,
    "base_range_max_pct": 32.0,
    "relay_range_min_pct": 4.0,
    "relay_range_max_pct": 24.0,
    "base_drift_max_pct": 16.0,
    "relay_drift_max_pct": 12.0,
    "breakout_buffer_pct": 0.5,
    "confirm_next_days": 3,
    "confirm_next_close_floor_pct": 98.0,
    "validation_days": 30,
    "target_lower_multiple": 1.0,
    "target_upper_multiple": 2.0,
    "tolerant_lower_multiple": 0.95,
    "tolerant_upper_multiple": 2.10,
    "relay_search_starts_after_confirm_days": 5,
    "wave_break_drop_pct": -7.0,
    "max_segments": 8,
}


def _normalize_ts_code(value: str) -> str:
    raw = str(value or "").strip().upper()
    match = _TS_CODE_PATTERN.fullmatch(raw)
    if not match:
        raise ValueError("invalid ts_code")

    symbol = match.group(1)
    if symbol.startswith(("5", "6", "9")):
        suffix = "SH"
    elif symbol.startswith(("0", "1", "2", "3")):
        suffix = "SZ"
    else:
        suffix = match.group(2)
    if not suffix:
        raise ValueError("invalid ts_code")
    return f"{symbol}.{suffix}"


def _json_safe(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
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


def _as_history_frame(history: Any) -> pd.DataFrame:
    frame = history.copy() if isinstance(history, pd.DataFrame) else pd.DataFrame(history or [])
    for column in ("ts_code", "trade_date", "open", "high", "low", "close", "vol", "pct_chg"):
        if column not in frame:
            frame[column] = None
    frame = frame.loc[:, ["ts_code", "trade_date", "open", "high", "low", "close", "vol", "pct_chg"]]
    frame["trade_date"] = frame["trade_date"].astype("string")
    for column in ("open", "high", "low", "close", "vol", "pct_chg"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return (
        frame.dropna(subset=["trade_date", "high", "low", "close"])
        .sort_values("trade_date")
        .reset_index(drop=True)
    )


def _load_stock_history(ts_code: str, end_trade_date: str, lookback_days: int) -> pd.DataFrame:
    try:
        cached = load_recent_daily(end_trade_date, lookback_days)
        if not cached.empty and "ts_code" in cached.columns:
            cached = cached[cached["ts_code"].astype(str) == ts_code].copy()
            if not cached.empty:
                return cached
    except Exception:
        pass
    return get_stock_daily_history(ts_code, end_trade_date, n=lookback_days)


def _classify_multiple(multiple: float, lower: float, upper: float) -> str:
    if multiple < lower:
        return "未到"
    if multiple <= upper:
        return "命中"
    return "超出"


def _valid_box(frame: pd.DataFrame, start: int, end: int, kind: str, params: dict[str, Any]):
    if start < 0 or end < start:
        return None
    box = frame.iloc[start : end + 1]
    box_max = float(box["high"].max())
    box_min = float(box["low"].min())
    if box_min <= 0 or box_max <= box_min:
        return None

    distance = box_max - box_min
    range_pct = distance / box_min * 100
    first_close = float(box["close"].iloc[0])
    last_close = float(box["close"].iloc[-1])
    drift_pct = abs(last_close / first_close - 1) * 100 if first_close > 0 else 999.0

    if kind == "base":
        min_range = params["base_range_min_pct"]
        max_range = params["base_range_max_pct"]
        max_drift = params["base_drift_max_pct"]
    else:
        min_range = params["relay_range_min_pct"]
        max_range = params["relay_range_max_pct"]
        max_drift = params["relay_drift_max_pct"]

    if not (min_range <= range_pct <= max_range) or drift_pct > max_drift:
        return None
    return box, box_max, box_min, distance, range_pct, drift_pct


def _choose_breakout_box(frame: pd.DataFrame, confirm_index: int, kind: str, params: dict[str, Any]):
    day_candidates = (
        params["base_box_days_candidates"]
        if kind == "base"
        else params["relay_box_days_candidates"]
    )
    row = frame.loc[confirm_index]
    for end_offset in params["box_end_offsets"]:
        for box_days in day_candidates:
            end = confirm_index + end_offset
            start = end - box_days + 1
            valid = _valid_box(frame, start, end, kind, params)
            if not valid:
                continue

            box, box_max, box_min, distance, range_pct, drift_pct = valid
            close_break = float(row["close"]) > box_max * (1 + params["breakout_buffer_pct"] / 100)
            next_rows = frame.iloc[
                confirm_index + 1 : confirm_index + 1 + params["confirm_next_days"]
            ]
            next_ok = (
                len(next_rows) == params["confirm_next_days"]
                and float(next_rows["close"].min())
                >= box_max * params["confirm_next_close_floor_pct"] / 100
            )
            if not (close_break and next_ok):
                continue

            return {
                "kind": kind,
                "confirm_index": confirm_index,
                "confirm_date": str(row["trade_date"]),
                "confirm_close": float(row["close"]),
                "confirm_high": float(row["high"]),
                "confirm_pct_chg": float(row["pct_chg"]) if pd.notna(row["pct_chg"]) else None,
                "box_days": box_days,
                "box_end_offset": end_offset,
                "box_start": str(box["trade_date"].iloc[0]),
                "box_end": str(box["trade_date"].iloc[-1]),
                "box_max": box_max,
                "box_max_date": str(box.loc[box["high"].idxmax(), "trade_date"]),
                "box_min": box_min,
                "box_min_date": str(box.loc[box["low"].idxmin(), "trade_date"]),
                "distance": distance,
                "range_pct": range_pct,
                "drift_pct": drift_pct,
                "confirm_close_break": close_break,
                "confirm_next_ok": next_ok,
            }
    return None


def _wave_end_index(frame: pd.DataFrame, confirm_index: int, params: dict[str, Any]) -> int:
    max_end = min(len(frame), confirm_index + params["validation_days"] + 1)
    peak_seen = False
    peak_high = None
    for index in range(confirm_index, max_end):
        high = float(frame.loc[index, "high"])
        if peak_high is None or high > peak_high:
            peak_high = high
            peak_seen = True
            continue
        pct_chg = frame.loc[index, "pct_chg"]
        if peak_seen and pd.notna(pct_chg) and float(pct_chg) <= params["wave_break_drop_pct"]:
            return max(confirm_index + 1, index)
    return max_end


def _measure_segment(frame: pd.DataFrame, segment: dict[str, Any], params: dict[str, Any]):
    confirm_index = int(segment["confirm_index"])
    wave_end = _wave_end_index(frame, confirm_index, params)
    post = frame.iloc[confirm_index:wave_end]
    peak_index = post["high"].idxmax()
    actual_peak = float(frame.loc[peak_index, "high"])
    peak_multiple = (actual_peak - segment["box_max"]) / segment["distance"]
    raw_target_low = segment["box_max"] + params["target_lower_multiple"] * segment["distance"]
    raw_target_high = segment["box_max"] + params["target_upper_multiple"] * segment["distance"]
    tolerant_target_low = segment["box_max"] + params["tolerant_lower_multiple"] * segment["distance"]
    tolerant_target_high = segment["box_max"] + params["tolerant_upper_multiple"] * segment["distance"]

    return {
        **segment,
        "target_low": raw_target_low,
        "target_high": raw_target_high,
        "tolerant_target_low": tolerant_target_low,
        "tolerant_target_high": tolerant_target_high,
        "actual_peak": actual_peak,
        "peak_date": str(frame.loc[peak_index, "trade_date"]),
        "wave_end_date": str(frame.loc[wave_end - 1, "trade_date"]),
        "peak_multiple": peak_multiple,
        "raw_result": _classify_multiple(
            peak_multiple,
            params["target_lower_multiple"],
            params["target_upper_multiple"],
        ),
        "tolerant_result": _classify_multiple(
            peak_multiple,
            params["tolerant_lower_multiple"],
            params["tolerant_upper_multiple"],
        ),
    }


def _find_segments(frame: pd.DataFrame, params: dict[str, Any]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    min_start = min(params["base_box_days_candidates"])
    i = min_start
    while i < len(frame) - params["confirm_next_days"]:
        kind = "base" if not segments else "relay"
        segment = _choose_breakout_box(frame, i, kind, params)
        if segment is None:
            i += 1
            continue

        if segments and segment["box_max"] < segments[-1]["box_max"] * 0.96:
            i += 1
            continue
        if segments and str(segment["box_start"]) <= str(segments[-1].get("wave_end_date")):
            i += 1
            continue

        measured = _measure_segment(frame, segment, params)
        measured["segment"] = "base" if not segments else f"relay{len(segments)}"
        segments.append(measured)
        if len(segments) >= params["max_segments"]:
            break
        wave_end_indices = frame.index[frame["trade_date"].astype(str) == measured["wave_end_date"]].tolist()
        i = (wave_end_indices[0] + 1) if wave_end_indices else i + params["relay_search_starts_after_confirm_days"]

    return segments


def _manual_box_segment(frame: pd.DataFrame, manual_box: dict[str, Any] | None, params: dict[str, Any]):
    if not manual_box:
        return None
    start_date = str(manual_box.get("start") or "")
    end_date = str(manual_box.get("end") or "")
    if not re.fullmatch(r"\d{8}", start_date) or not re.fullmatch(r"\d{8}", end_date):
        raise ValueError("manual box start/end must be YYYYMMDD")
    if start_date > end_date:
        raise ValueError("manual box start must be before end")

    box = frame[
        (frame["trade_date"].astype(str) >= start_date)
        & (frame["trade_date"].astype(str) <= end_date)
    ].copy()
    if box.empty:
        raise LookupError("manual box has no daily candles")

    box_max = float(box["high"].max())
    box_min = float(box["low"].min())
    if box_min <= 0 or box_max <= box_min:
        raise ValueError("manual box range is invalid")
    distance = box_max - box_min
    range_pct = distance / box_min * 100
    first_close = float(box["close"].iloc[0])
    last_close = float(box["close"].iloc[-1])
    drift_pct = abs(last_close / first_close - 1) * 100 if first_close > 0 else None
    end_indices = frame.index[frame["trade_date"].astype(str) == str(box["trade_date"].iloc[-1])].tolist()
    if not end_indices:
        raise LookupError("manual box end is outside daily history")

    confirm_segment = None
    probe_segment = None
    raw_target_low = box_max + params["target_lower_multiple"] * distance
    raw_target_high = box_max + params["target_upper_multiple"] * distance
    tolerant_target_low = box_max + params["tolerant_lower_multiple"] * distance
    tolerant_target_high = box_max + params["tolerant_upper_multiple"] * distance
    for confirm_index in range(end_indices[0] + 1, len(frame)):
        row = frame.loc[confirm_index]
        probe_close_break = float(row["close"]) >= box_max
        probe_high_break = float(row["high"]) >= box_max
        close_break = float(row["close"]) > box_max * (1 + params["breakout_buffer_pct"] / 100)
        high_break = float(row["high"]) > box_max * (1 + params["breakout_buffer_pct"] / 100)
        next_rows = frame.iloc[confirm_index + 1 : confirm_index + 1 + params["confirm_next_days"]]
        next_ok = (
            len(next_rows) == params["confirm_next_days"]
            and float(next_rows["close"].min())
            >= box_max * params["confirm_next_close_floor_pct"] / 100
        )
        if (probe_close_break or probe_high_break) and probe_segment is None:
            probe_segment = {
                "kind": "manual",
                "segment": "manual",
                "confirm_index": confirm_index,
                "confirm_date": str(row["trade_date"]),
                "confirm_close": float(row["close"]),
                "confirm_high": float(row["high"]),
                "confirm_pct_chg": float(row["pct_chg"]) if pd.notna(row["pct_chg"]) else None,
                "box_days": len(box),
                "box_end_offset": None,
                "box_start": str(box["trade_date"].iloc[0]),
                "box_end": str(box["trade_date"].iloc[-1]),
                "box_max": box_max,
                "box_max_date": str(box.loc[box["high"].idxmax(), "trade_date"]),
                "box_min": box_min,
                "box_min_date": str(box.loc[box["low"].idxmin(), "trade_date"]),
                "distance": distance,
                "range_pct": range_pct,
                "drift_pct": drift_pct,
                "probe_close_break": probe_close_break,
                "probe_high_break": probe_high_break,
                "confirm_close_break": close_break,
                "confirm_high_break": high_break,
                "confirm_next_ok": next_ok,
            }
        if (close_break or high_break) and next_ok:
            confirm_segment = {
                **(probe_segment or {}),
                "kind": "manual",
                "segment": "manual",
                "confirm_index": confirm_index,
                "confirm_date": str(row["trade_date"]),
                "confirm_close": float(row["close"]),
                "confirm_high": float(row["high"]),
                "confirm_pct_chg": float(row["pct_chg"]) if pd.notna(row["pct_chg"]) else None,
                "box_days": len(box),
                "box_end_offset": None,
                "box_start": str(box["trade_date"].iloc[0]),
                "box_end": str(box["trade_date"].iloc[-1]),
                "box_max": box_max,
                "box_max_date": str(box.loc[box["high"].idxmax(), "trade_date"]),
                "box_min": box_min,
                "box_min_date": str(box.loc[box["low"].idxmin(), "trade_date"]),
                "distance": distance,
                "range_pct": range_pct,
                "drift_pct": drift_pct,
                "probe_close_break": probe_close_break,
                "probe_high_break": probe_high_break,
                "confirm_close_break": close_break,
                "confirm_high_break": high_break,
                "confirm_next_ok": next_ok,
            }
            break

    if confirm_segment is None and probe_segment is not None:
        measured = _measure_segment(frame, probe_segment, params)
        measured["mode"] = "manual"
        measured["breakout_started"] = True
        measured["breakout_stage"] = "probe"
        measured["sideways_ended"] = False
        measured["target_status"] = "预测"
        measured["state_label"] = "突破试探，目标待确认"
        return measured

    if confirm_segment is None:
        return {
            "mode": "manual",
            "breakout_started": False,
            "breakout_stage": "none",
            "sideways_ended": False,
            "box_start": str(box["trade_date"].iloc[0]),
            "box_end": str(box["trade_date"].iloc[-1]),
            "box_days": len(box),
            "box_max": box_max,
            "box_max_date": str(box.loc[box["high"].idxmax(), "trade_date"]),
            "box_min": box_min,
            "box_min_date": str(box.loc[box["low"].idxmin(), "trade_date"]),
            "distance": distance,
            "target_low": raw_target_low,
            "target_high": raw_target_high,
            "tolerant_target_low": tolerant_target_low,
            "tolerant_target_high": tolerant_target_high,
            "range_pct": range_pct,
            "drift_pct": drift_pct,
            "state_label": "手动箱体尚未结束震荡",
        }

    measured = _measure_segment(frame, confirm_segment, params)
    measured["mode"] = "manual"
    measured["breakout_started"] = True
    measured["breakout_stage"] = "confirmed"
    measured["sideways_ended"] = True
    measured["target_status"] = "确认"
    measured["state_label"] = "手动箱体已结束震荡"
    return measured


def _trend_status(frame: pd.DataFrame, latest_segment: dict[str, Any] | None) -> dict[str, Any]:
    if frame.empty or latest_segment is None:
        return {
            "is_uptrend": False,
            "has_reversal_trend": False,
            "sideways_ended": False,
            "target_available": False,
            "state_label": "最近K线未形成反转",
            "reasons": ["没有识别到有效箱体突破"],
        }
    if latest_segment.get("mode") == "manual" and not latest_segment.get("sideways_ended"):
        if latest_segment.get("breakout_started"):
            return {
                "is_uptrend": False,
                "has_reversal_trend": True,
                "sideways_ended": False,
                "target_available": True,
                "state_label": "突破试探，目标待确认",
                "reasons": ["手动箱体已被触碰或突破，但尚未满足站稳确认"],
            }
        return {
            "is_uptrend": False,
            "has_reversal_trend": False,
            "sideways_ended": False,
            "target_available": False,
            "state_label": "最近K线未形成反转",
            "reasons": ["手动箱体尚未出现结束震荡确认端"],
        }
    latest = frame.iloc[-1]
    reasons = []
    close_holds_box = float(latest["close"]) >= latest_segment["box_max"] * 0.98
    if close_holds_box:
        reasons.append("最新收盘仍在最近箱体上沿附近或上方")
    close = frame["close"]
    ma5 = close.rolling(5, min_periods=5).mean().iloc[-1]
    ma10 = close.rolling(10, min_periods=10).mean().iloc[-1]
    ma20 = close.rolling(20, min_periods=20).mean().iloc[-1]
    ma_reversal = pd.notna(ma5) and pd.notna(ma10) and pd.notna(ma20) and ma5 >= ma10 and ma5 >= ma20
    if ma_reversal:
        reasons.append("短期均线保持多头")
    recent_breakout = latest_segment["confirm_index"] >= len(frame) - 30
    if recent_breakout:
        reasons.append("最近30个交易日内出现有效突破")
    sideways_ended = bool(
        (latest_segment.get("confirm_close_break") or latest_segment.get("confirm_high_break"))
        and latest_segment.get("confirm_next_ok")
    )
    has_reversal_trend = recent_breakout and close_holds_box and ma_reversal
    target_available = has_reversal_trend and sideways_ended
    if target_available:
        state_label = "最近K线反转，横盘已结束"
    elif has_reversal_trend:
        state_label = "最近K线反转，等待横盘结束"
    else:
        state_label = "最近K线未形成反转"
    return {
        "is_uptrend": target_available,
        "has_reversal_trend": has_reversal_trend,
        "sideways_ended": sideways_ended,
        "target_available": target_available,
        "state_label": state_label,
        "reasons": reasons or ["最近K线未满足反转确认"],
    }


def analyze_trend_box_target(
    history_or_ts_code: Any,
    end_trade_date: str | None = None,
    lookback_days: int = 90,
    params: dict[str, Any] | None = None,
    ts_code: str | None = None,
    manual_box: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Analyze trend-box targets for one stock.

    ``history_or_ts_code`` may be a ts_code string, in which case daily history is loaded,
    or a DataFrame/list of candle records for pure calculations and tests.
    """
    merged_params = {**DEFAULT_PARAMS, **(params or {})}
    lookback_days = max(45, min(int(lookback_days), 240))
    requested_ts_code = ts_code

    if isinstance(history_or_ts_code, str):
        requested_ts_code = history_or_ts_code
        ts_code = _normalize_ts_code(history_or_ts_code)
        if not end_trade_date or not re.fullmatch(r"\d{8}", str(end_trade_date)):
            raise ValueError("end_trade_date must be YYYYMMDD")
        history = _load_stock_history(ts_code, end_trade_date, lookback_days)
    else:
        history = history_or_ts_code
        if ts_code:
            requested_ts_code = ts_code
            ts_code = _normalize_ts_code(ts_code)

    frame = _as_history_frame(history)
    if frame.empty:
        raise LookupError("stock daily history not found")
    if not ts_code and frame["ts_code"].notna().any():
        ts_code = str(frame["ts_code"].dropna().iloc[0])
    if not ts_code:
        raise ValueError("missing ts_code")

    segments = _find_segments(frame, merged_params)
    manual_segment = _manual_box_segment(frame, manual_box, merged_params)
    latest_segment = manual_segment if manual_segment else (segments[-1] if segments else None)
    trend_status = _trend_status(frame, latest_segment)
    validation_segments = segments[-2:] if len(segments) >= 2 else segments[:1]
    current_target = None
    manual_target_available = bool(latest_segment and latest_segment.get("kind") == "manual" and latest_segment.get("sideways_ended"))
    if latest_segment and (trend_status["target_available"] or manual_target_available):
        current_target = {
            "source_segment": latest_segment["segment"],
            "confirm_date": latest_segment["confirm_date"],
            "box_start": latest_segment["box_start"],
            "box_end": latest_segment["box_end"],
            "box_max": latest_segment["box_max"],
            "box_min": latest_segment["box_min"],
            "distance": latest_segment["distance"],
            "target_low": latest_segment["target_low"],
            "target_high": latest_segment["target_high"],
            "tolerant_target_low": latest_segment["tolerant_target_low"],
            "tolerant_target_high": latest_segment["tolerant_target_high"],
            "target_status": latest_segment.get("target_status", "确认"),
        }

    result = {
        "identity": {
            "ts_code": ts_code,
            "requested_ts_code": requested_ts_code,
            "end_trade_date": end_trade_date or str(frame["trade_date"].iloc[-1]),
            "lookback_days": lookback_days,
        },
        "params": merged_params,
        "trend": {
            "latest_trade_date": str(frame["trade_date"].iloc[-1]),
            "latest_close": float(frame["close"].iloc[-1]),
            **trend_status,
        },
        "wave_backtest": {
            "requested_segments": 2,
            "available_segments": len(validation_segments),
            "segments": validation_segments,
        },
        "segments": segments,
        "manual_box": manual_segment,
        "current_target": current_target,
    }
    return _json_safe(result)
