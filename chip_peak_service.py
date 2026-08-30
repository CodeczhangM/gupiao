from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time
from typing import Any

import pandas as pd

import data_service
from data_service import _query_tushare


_CHIP_DATA_CACHE: dict[tuple[str, str], tuple[pd.DataFrame, pd.DataFrame]] = {}
_CHIP_FAILURE_CACHE: dict[tuple[str, str], tuple[float, str]] = {}
_CHIP_CACHE_LOCK = threading.Lock()
_CHIP_FAILURE_RETRY_SECONDS = 30


_CHIP_FIELD_DEFAULTS = {
    "chip_peak_price": None,
    "chip_peak_percent": None,
    "chip_secondary_peak_price": None,
    "chip_secondary_peak_percent": None,
    "chip_concentration_70_pct": None,
    "chip_concentration_90_pct": None,
    "chip_peak_bottom_position_pct": None,
    "chip_price_distance_pct": None,
    "chip_weighted_avg_cost": None,
    "chip_winner_rate": None,
    "chip_washout_score": 0.0,
    "chip_washout_label": "筹码数据暂缺",
    "chip_washout_reason": "暂无有效筹码峰数据",
    "chip_build_position": False,
    "chip_data_complete": False,
    "chip_data_trade_date": None,
    "chip_data_warning": None,
}


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _truthy(value: Any) -> bool:
    try:
        return False if pd.isna(value) else bool(value)
    except (TypeError, ValueError):
        return False


def empty_chip_peak_fields(warning: str | None = None) -> dict[str, Any]:
    fields = dict(_CHIP_FIELD_DEFAULTS)
    if warning:
        fields["chip_data_warning"] = str(warning)
        fields["chip_washout_reason"] = str(warning)
    return fields


def clear_chip_peak_cache() -> None:
    with _CHIP_CACHE_LOCK:
        _CHIP_DATA_CACHE.clear()
        _CHIP_FAILURE_CACHE.clear()


def load_chip_data(
    ts_code: str,
    trade_date: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    key = (str(ts_code), str(trade_date))
    with _CHIP_CACHE_LOCK:
        cached = _CHIP_DATA_CACHE.get(key)
        failed = _CHIP_FAILURE_CACHE.get(key)
    if cached is not None:
        return cached[0].copy(), cached[1].copy()
    if failed is not None and time.monotonic() - failed[0] < _CHIP_FAILURE_RETRY_SECONDS:
        raise RuntimeError(failed[1])

    query_args = {"ts_code": key[0], "trade_date": key[1]}
    try:
        chips = _query_tushare(
            "cyq_chips",
            **query_args,
        )
        _validate_chip_frame(
            chips,
            {"price", "percent"},
            key,
            "筹码分布",
        )
        perf = _query_tushare(
            "cyq_perf",
            **query_args,
        )
        _validate_chip_frame(
            perf,
            {
                "cost_5pct", "cost_15pct", "cost_85pct", "cost_95pct",
                "weight_avg", "winner_rate",
            },
            key,
            "筹码绩效",
        )
    except Exception as exc:
        _invalidate_tushare_chip_queries(query_args)
        with _CHIP_CACHE_LOCK:
            _CHIP_FAILURE_CACHE[key] = (time.monotonic(), str(exc))
        raise

    with _CHIP_CACHE_LOCK:
        _CHIP_DATA_CACHE[key] = (chips.copy(), perf.copy())
        _CHIP_FAILURE_CACHE.pop(key, None)
    return chips.copy(), perf.copy()


def _validate_chip_frame(
    frame: Any,
    required_columns: set[str],
    key: tuple[str, str],
    label: str,
) -> None:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise RuntimeError(f"{label}为空")
    missing = required_columns.difference(frame.columns)
    if missing:
        raise RuntimeError(f"{label}缺少字段: {','.join(sorted(missing))}")
    if "ts_code" in frame.columns and not frame["ts_code"].astype(str).eq(key[0]).any():
        raise RuntimeError(f"{label}股票代码不匹配")
    if "trade_date" in frame.columns and not frame["trade_date"].astype(str).eq(key[1]).any():
        raise RuntimeError(f"{label}交易日不匹配")


def _invalidate_tushare_chip_queries(query_args: dict[str, str]) -> None:
    for api_name in ("cyq_chips", "cyq_perf"):
        cache_key = (api_name, tuple(sorted(query_args.items())))
        data_service._query_cache.pop(cache_key, None)


def calculate_concentration(low: Any, high: Any) -> float | None:
    low_value = _finite_float(low)
    high_value = _finite_float(high)
    if low_value is None or high_value is None or high_value < low_value:
        return None
    denominator = high_value + low_value
    if denominator <= 0:
        return None
    return (high_value - low_value) / denominator * 100


def extract_chip_peaks(chips: pd.DataFrame) -> dict[str, float | None]:
    result = {
        "chip_peak_price": None,
        "chip_peak_percent": None,
        "chip_secondary_peak_price": None,
        "chip_secondary_peak_percent": None,
    }
    if chips is None or chips.empty or not {"price", "percent"}.issubset(chips.columns):
        return result

    data = chips[["price", "percent"]].copy()
    data["price"] = pd.to_numeric(data["price"], errors="coerce")
    data["percent"] = pd.to_numeric(data["percent"], errors="coerce")
    data = data.dropna().query("price > 0 and percent >= 0")
    if data.empty:
        return result

    ranked = data.sort_values(["percent", "price"], ascending=[False, True])
    primary = ranked.iloc[0]
    primary_price = float(primary["price"])
    result["chip_peak_price"] = primary_price
    result["chip_peak_percent"] = float(primary["percent"])

    separated = ranked[
        (ranked["price"] / primary_price - 1).abs() >= 0.02
    ]
    if not separated.empty:
        secondary = separated.iloc[0]
        result["chip_secondary_peak_price"] = float(secondary["price"])
        result["chip_secondary_peak_percent"] = float(secondary["percent"])
    return result


def _concentration_score(value: float | None) -> int:
    if value is None:
        return 0
    if value <= 10:
        return 30
    if value <= 15:
        return 24
    if value <= 20:
        return 12
    return 0


def _bottom_position_score(value: float | None) -> int:
    if value is None:
        return 0
    if value <= 20:
        return 25
    if value <= 35:
        return 18
    if value <= 50:
        return 8
    return 0


def _peak_distance_score(value: float | None) -> int:
    if value is None:
        return 0
    if -3 <= value <= 8:
        return 20
    if -8 <= value <= 15:
        return 12
    if -12 <= value <= 20:
        return 5
    return 0


def _structure_score(row: dict[str, Any]) -> int:
    score = 5 if _truthy(row.get("bottom_consolidation")) else 0
    contraction = _finite_float(row.get("bottom_volume_contraction"))
    convergence = _finite_float(row.get("bottom_ma_convergence_pct"))
    if contraction is not None and contraction <= 0.8:
        score += 5
    if convergence is not None and convergence <= 5:
        score += 5
    return score


def _winner_score(value: float | None) -> int:
    if value is None:
        return 0
    if 20 <= value <= 65:
        return 10
    if 10 <= value <= 80:
        return 5
    return 0


def _washout_label(score: float) -> str:
    if score >= 80:
        return "底部洗盘 · 可建仓"
    if score >= 65:
        return "底部筹码密集 · 等待确认"
    if score >= 45:
        return "筹码整理"
    return "筹码结构偏弱"


def _matching_history(
    history: pd.DataFrame,
    ts_code: str,
) -> pd.DataFrame:
    if history is None or history.empty or "ts_code" not in history.columns:
        return pd.DataFrame()
    matched = history[history["ts_code"].astype(str) == ts_code].copy()
    if "trade_date" in matched.columns:
        matched = matched.sort_values("trade_date")
    return matched.tail(120)


def _bottom_position(
    peak_price: float | None,
    history: pd.DataFrame,
    ts_code: str,
) -> float | None:
    matched = _matching_history(history, ts_code)
    if peak_price is None or len(matched) < 20 or not {"low", "high"}.issubset(matched.columns):
        return None
    lows = pd.to_numeric(matched["low"], errors="coerce").dropna()
    highs = pd.to_numeric(matched["high"], errors="coerce").dropna()
    if len(lows) < 20 or len(highs) < 20:
        return None
    low = float(lows.min())
    high = float(highs.max())
    if not math.isfinite(low) or not math.isfinite(high) or high <= low:
        return None
    return round((peak_price - low) / (high - low) * 100, 6)


def _display_percent(value: float | None) -> str:
    return "--" if value is None else f"{value:.2f}%"


def build_chip_peak_fields(
    row: dict[str, Any],
    chips: pd.DataFrame,
    perf: pd.DataFrame,
    history: pd.DataFrame,
) -> dict[str, Any]:
    fields = empty_chip_peak_fields()
    peaks = extract_chip_peaks(chips)
    fields.update(peaks)

    perf_row = None
    if perf is not None and not perf.empty:
        data = perf.copy()
        ts_code = str(row.get("ts_code") or "")
        if "ts_code" in data.columns:
            data = data[data["ts_code"].astype(str) == ts_code]
        if "trade_date" in data.columns:
            data = data.sort_values("trade_date", ascending=False)
        if not data.empty:
            perf_row = data.iloc[0]

    concentration_70 = None
    concentration_90 = None
    winner_rate = None
    if perf_row is not None:
        concentration_70 = calculate_concentration(
            perf_row.get("cost_15pct"),
            perf_row.get("cost_85pct"),
        )
        concentration_90 = calculate_concentration(
            perf_row.get("cost_5pct"),
            perf_row.get("cost_95pct"),
        )
        winner_rate = _finite_float(perf_row.get("winner_rate"))
        fields["chip_weighted_avg_cost"] = _finite_float(
            perf_row.get("weight_avg")
        )
        fields["chip_data_trade_date"] = str(
            perf_row.get("trade_date") or ""
        ) or None

    fields["chip_concentration_70_pct"] = concentration_70
    fields["chip_concentration_90_pct"] = concentration_90
    fields["chip_winner_rate"] = winner_rate

    peak_price = fields["chip_peak_price"]
    ts_code = str(row.get("ts_code") or "")
    bottom_position = _bottom_position(peak_price, history, ts_code)
    current_price = _finite_float(
        row.get("current_price", row.get("close"))
    )
    distance = (
        round((current_price / peak_price - 1) * 100, 6)
        if current_price is not None and peak_price is not None and peak_price > 0
        else None
    )
    fields["chip_peak_bottom_position_pct"] = bottom_position
    fields["chip_price_distance_pct"] = distance

    complete = peak_price is not None and (
        concentration_70 is not None or concentration_90 is not None
    )
    fields["chip_data_complete"] = complete
    if not complete:
        return fields

    score = float(
        max(
            _concentration_score(concentration_70),
            _concentration_score(concentration_90),
        )
        + _bottom_position_score(bottom_position)
        + _peak_distance_score(distance)
        + _structure_score(row)
        + _winner_score(winner_rate)
    )
    fields["chip_washout_score"] = score
    fields["chip_washout_label"] = _washout_label(score)
    fields["chip_build_position"] = bool(
        score >= 80
        and bottom_position is not None
        and bottom_position <= 35
        and distance is not None
        and -8 <= distance <= 15
        and (
            (concentration_70 is not None and concentration_70 <= 15)
            or (concentration_90 is not None and concentration_90 <= 15)
        )
    )
    fields["chip_washout_reason"] = (
        f"主峰 {peak_price:.2f}，"
        f"70%密集度 {_display_percent(concentration_70)}，"
        f"90%密集度 {_display_percent(concentration_90)}，"
        f"底部位置 {_display_percent(bottom_position)}，"
        f"距主峰 {_display_percent(distance)}，洗盘评分 {score:.0f}"
    )
    return fields


def attach_chip_peak_fields(
    rows: list[dict[str, Any]],
    history: pd.DataFrame,
    trade_date: str,
    loader=load_chip_data,
) -> tuple[list[dict[str, Any]], list[str]]:
    source_rows = [dict(row) for row in (rows or [])]
    codes = list(dict.fromkeys(
        str(row.get("ts_code") or "")
        for row in source_rows
        if str(row.get("ts_code") or "")
    ))
    loaded: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}
    failures: dict[str, str] = {}

    if codes:
        max_workers = min(4, len(codes))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(loader, code, str(trade_date)): code
                for code in codes
            }
            for future in as_completed(futures):
                code = futures[future]
                try:
                    loaded[code] = future.result()
                except Exception as exc:
                    failures[code] = str(exc)[:160]

    enriched = []
    for row in source_rows:
        code = str(row.get("ts_code") or "")
        if code in loaded:
            chips, perf = loaded[code]
            fields = build_chip_peak_fields(row, chips, perf, history)
        else:
            warning = (
                f"{code} 筹码数据失败: {failures[code]}"
                if code in failures
                else f"{code or '未知股票'} 筹码数据暂缺"
            )
            fields = empty_chip_peak_fields(warning)
            fields["chip_data_trade_date"] = str(trade_date)
        enriched.append({**row, **fields})

    warnings = list(dict.fromkeys(
        f"{code} 筹码数据失败: {message}"
        for code, message in failures.items()
    ))
    return enriched, warnings
