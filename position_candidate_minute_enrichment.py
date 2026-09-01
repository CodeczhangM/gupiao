from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
import math
import time
from typing import Any, Callable

import pandas as pd


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _unavailable(warning: str) -> dict[str, Any]:
    return {
        "minute_breakout_data_available": False,
        "intraday_vwap": None,
        "above_vwap": None,
        "breakout_hold_ratio": None,
        "tail_stable_above_pressure": None,
        "tail_return_after_1430": None,
        "minute_breakout_warning": warning,
    }


def calculate_minute_breakout_context(
    bars: pd.DataFrame,
    plan: dict[str, Any],
) -> dict[str, Any]:
    if bars is None or bars.empty or "trade_time" not in bars.columns:
        return _unavailable("分钟数据为空")
    data = bars.copy()
    data["_timestamp"] = pd.to_datetime(data["trade_time"], errors="coerce")
    data = data.dropna(subset=["_timestamp"]).sort_values("_timestamp")
    if data.empty or "close" not in data.columns:
        return _unavailable("分钟时间或价格不可用")
    for column in ("close", "high", "low", "vol", "amount"):
        if column in data:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=["close"])
    if data.empty:
        return _unavailable("分钟价格不可用")
    volume = pd.to_numeric(data.get("vol"), errors="coerce")
    amount = pd.to_numeric(data.get("amount"), errors="coerce")
    valid_amount = (
        volume.notna() & amount.notna() & volume.gt(0)
        if isinstance(volume, pd.Series) and isinstance(amount, pd.Series)
        else pd.Series(False, index=data.index)
    )
    if valid_amount.any():
        vwap = float(amount[valid_amount].sum() / volume[valid_amount].sum())
    elif isinstance(volume, pd.Series) and volume.notna().any() and volume.sum() > 0:
        typical = (
            pd.to_numeric(data.get("high", data["close"]), errors="coerce")
            + pd.to_numeric(data.get("low", data["close"]), errors="coerce")
            + data["close"]
        ) / 3
        vwap = float((typical * volume).sum() / volume.sum())
    else:
        vwap = float(data["close"].mean())
    final_close = float(data.iloc[-1]["close"])
    trigger = _number(plan.get("breakout_trigger"))
    pressure_high = _number(plan.get("pressure_high"))
    hold_ratio = None
    if trigger is not None:
        triggered = data[data["close"] >= trigger]
        if not triggered.empty:
            after = data[data["_timestamp"] >= triggered.iloc[0]["_timestamp"]]
            hold_ratio = float((after["close"] >= trigger).mean())
    tail = data[
        data["_timestamp"].dt.strftime("%H:%M:%S") >= "14:30:00"
    ]
    tail_return = None
    tail_stable = None
    if not tail.empty:
        first = float(tail.iloc[0]["close"])
        tail_return = (final_close / first - 1) * 100 if first > 0 else None
        tail_stable = bool(
            pressure_high is not None
            and final_close >= pressure_high
            and float((tail["close"] >= pressure_high).mean()) >= 0.7
        )
    return {
        "minute_breakout_data_available": True,
        "intraday_vwap": round(vwap, 4),
        "above_vwap": bool(final_close >= vwap),
        "breakout_hold_ratio": None if hold_ratio is None else round(hold_ratio, 4),
        "tail_stable_above_pressure": tail_stable,
        "tail_return_after_1430": None if tail_return is None else round(tail_return, 4),
        "minute_breakout_warning": None,
    }


def _bars_from_result(result: Any) -> pd.DataFrame:
    if isinstance(result, pd.DataFrame):
        return result
    bars = getattr(result, "bars", None)
    return bars if isinstance(bars, pd.DataFrame) else pd.DataFrame()


def enrich_position_candidates_with_minutes(
    rows: list[dict[str, Any]],
    trade_date: str,
    loader: Callable[[str, str], Any],
    settings: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    started = time.monotonic()
    config = settings["network"]
    limit = min(10, int(config["enrichment_limit"]), len(rows or []))
    source = [dict(row) for row in (rows or [])]
    warnings: list[str] = []
    failed = 0
    completed = 0
    timed_out = 0
    executor = ThreadPoolExecutor(max_workers=min(int(config["workers"]), max(1, limit)))
    futures = {
        executor.submit(loader, str(source[index].get("ts_code") or ""), str(trade_date)): index
        for index in range(limit)
    }
    done, pending = wait(
        futures,
        timeout=float(config["stage_budget_seconds"]),
    )
    for future in done:
        index = futures[future]
        code = str(source[index].get("ts_code") or "")
        try:
            context = calculate_minute_breakout_context(
                _bars_from_result(future.result()), source[index]
            )
            source[index].update(context)
            completed += int(context["minute_breakout_data_available"])
            if not context["minute_breakout_data_available"]:
                failed += 1
                warnings.append(f"{code} {context['minute_breakout_warning']}")
        except Exception as exc:
            failed += 1
            warning = f"{code} 分钟增强失败: {str(exc)[:120]}"
            warnings.append(warning)
            source[index].update(_unavailable(str(exc)))
            source[index]["missing_data"] = list(dict.fromkeys(
                list(source[index].get("missing_data") or []) + ["分钟突破数据缺失"]
            ))
    for future in pending:
        index = futures[future]
        code = str(source[index].get("ts_code") or "")
        future.cancel()
        timed_out += 1
        source[index].update(_unavailable("分钟增强超过阶段预算"))
        source[index]["missing_data"] = list(dict.fromkeys(
            list(source[index].get("missing_data") or []) + ["分钟突破数据超时"]
        ))
        warnings.append(f"{code} 分钟增强超过阶段预算")
    executor.shutdown(wait=False, cancel_futures=True)
    elapsed_ms = (time.monotonic() - started) * 1000
    return source, warnings, {
        "minute_enrichment_attempted": limit,
        "minute_enrichment_completed": completed,
        "minute_enrichment_failed": failed,
        "minute_enrichment_timed_out": timed_out,
        "minute_enrichment_budget_seconds": int(config["stage_budget_seconds"]),
        "minute_enrichment_ms": round(elapsed_ms, 3),
    }
