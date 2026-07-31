from __future__ import annotations

from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import os
import threading
import time
from typing import Any, Iterable

import pandas as pd

from data_service import get_stock_minute_bars, get_trade_dates
from market_cache import load_market_snapshot
from realtime_cache import (
    load_minute_cache,
    minute_cache_next_fetch_start,
    save_minute_cache,
)
from realtime_market_source import load_eastmoney_market_snapshot, load_minutes_with_fallback


DEFAULT_WARMUP_INTERVAL_SECONDS = 30
DEFAULT_WARMUP_LIMIT = 60
DEFAULT_WARMUP_MAX_WORKERS = 4
DEFAULT_TUSHARE_PER_MINUTE_LIMIT = 180
_STATUS_LOCK = threading.Lock()
_STOP_EVENT = threading.Event()
_WARMUP_THREAD: threading.Thread | None = None
_STATUS: dict[str, Any] = {
    "enabled": False,
    "running": False,
    "last_started_at": None,
    "last_finished_at": None,
    "last_error": None,
    "last_result": None,
    "run_count": 0,
}


def _time_text(value: datetime | None = None) -> str:
    return (value or datetime.now()).isoformat(sep=" ", timespec="seconds")


def _trade_date_for(now: datetime) -> str:
    try:
        dates = get_trade_dates(n=1)
        if dates:
            return str(dates[0])
    except Exception:
        pass
    return now.strftime("%Y%m%d")


def _minute_end_datetime(trade_date: str, now: datetime) -> str | None:
    day = datetime.strptime(str(trade_date), "%Y%m%d")
    clock = now.strftime("%H:%M:%S")
    if clock < "09:30:00":
        return None
    if "11:30:00" <= clock < "13:00:00":
        end = day.replace(hour=11, minute=30, second=0, microsecond=0)
    elif clock >= "15:00:00":
        end = day.replace(hour=15, minute=0, second=0, microsecond=0)
    else:
        end = now.replace(second=0, microsecond=0) - timedelta(minutes=1)
    if end.date() != day.date() or end < day.replace(hour=9, minute=30):
        return None
    return end.strftime("%Y-%m-%d %H:%M:%S")


def _start_datetime_for_freq(trade_date: str, freq: str) -> str | None:
    day = datetime.strptime(str(trade_date), "%Y%m%d")
    if str(freq) == "1min":
        return day.strftime("%Y-%m-%d 14:25:00")
    if str(freq) == "60min":
        return day.strftime("%Y-%m-%d 09:30:00")
    return None


def _window_for_freq(trade_date: str, freq: str, now: datetime) -> tuple[str, str] | None:
    start = _start_datetime_for_freq(trade_date, freq)
    end = _minute_end_datetime(trade_date, now)
    if not start or not end:
        return None
    if str(freq) == "1min" and end < start:
        return None
    return start, end


def _default_candidate_codes(trade_date: str, limit: int) -> list[str]:
    market = load_market_snapshot(trade_date)
    if market is None or market.empty:
        market, _error = load_eastmoney_market_snapshot(trade_date)
    if market is None or market.empty or "ts_code" not in market.columns:
        return []
    data = market.copy()
    data["ts_code"] = data["ts_code"].astype(str)
    data = data[~data["ts_code"].str.startswith(("688", "689"))].copy()
    if data.empty:
        return []
    for column in ("pct_chg", "volume_ratio", "turnover_rate", "amount"):
        data[column] = pd.to_numeric(data.get(column, 0), errors="coerce").fillna(0)
    if "name" in data:
        data = data[~data["name"].astype(str).str.upper().str.contains("ST|退市", regex=True)]
    data = data[
        (data["pct_chg"] >= 0)
        & (data["volume_ratio"] >= 1.0)
        & (data["amount"] >= 30_000_000)
    ].copy()
    if data.empty:
        return []
    data["_warmup_score"] = (
        data["pct_chg"].clip(-3, 10) * 3
        + data["volume_ratio"].clip(0, 5) * 5
        + data["turnover_rate"].clip(0, 25) * 0.3
        + (data["amount"] / 100_000_000).clip(0, 12)
    )
    return (
        data.sort_values(["_warmup_score", "amount", "ts_code"], ascending=[False, False, True])
        .head(max(1, int(limit)))
        ["ts_code"]
        .dropna()
        .astype(str)
        .drop_duplicates()
        .tolist()
    )


class _PerMinuteRateLimiter:
    def __init__(self, limit: int):
        self.limit = max(1, int(limit))
        self._lock = threading.Lock()
        self._timestamps: list[float] = []

    def wait(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._timestamps = [
                    value for value in self._timestamps if now - value < 60
                ]
                if len(self._timestamps) < self.limit:
                    self._timestamps.append(now)
                    return
                wait_for = max(0.01, 60 - (now - self._timestamps[0]))
            time.sleep(min(wait_for, 1.0))


def _merge_warmup_summary(
    target: dict[str, Any],
    item: dict[str, Any],
) -> None:
    for key in ("fetched_count", "cache_hit_count", "skipped_count", "failed_count"):
        target[key] += int(item.get(key, 0))
    target["warnings"].extend(str(w) for w in (item.get("warnings") or []))


def warm_realtime_minute_cache(
    *,
    now: datetime | None = None,
    limit: int = DEFAULT_WARMUP_LIMIT,
    candidate_codes: Iterable[str] | None = None,
    frequencies: Iterable[str] = ("60min", "1min"),
    max_workers: int = DEFAULT_WARMUP_MAX_WORKERS,
    tushare_per_minute_limit: int = DEFAULT_TUSHARE_PER_MINUTE_LIMIT,
) -> dict[str, Any]:
    current = now or datetime.now()
    trade_date = _trade_date_for(current)
    codes = (
        list(dict.fromkeys(str(code) for code in candidate_codes if str(code)))
        if candidate_codes is not None
        else _default_candidate_codes(trade_date, limit)
    )
    result = {
        "trade_date": trade_date,
        "candidate_count": len(codes),
        "fetched_count": 0,
        "cache_hit_count": 0,
        "skipped_count": 0,
        "failed_count": 0,
        "warnings": [],
        "started_at": _time_text(current),
        "finished_at": None,
        "max_workers": max(1, int(max_workers)),
        "tushare_per_minute_limit": max(1, int(tushare_per_minute_limit)),
    }
    tasks: list[tuple[str, str, str, str]] = []
    for ts_code in codes:
        for freq in frequencies:
            window = _window_for_freq(trade_date, str(freq), current)
            if not window:
                result["skipped_count"] += 1
                continue
            requested_start, requested_end = window
            try:
                cached = load_minute_cache(ts_code, requested_start, requested_end, str(freq))
                fetch_start, cache_hit = minute_cache_next_fetch_start(
                    cached,
                    requested_start,
                    requested_end,
                    str(freq),
                )
                if cache_hit or not fetch_start:
                    result["cache_hit_count"] += 1
                    continue
                tasks.append((ts_code, fetch_start, requested_end, str(freq)))
            except Exception as exc:
                result["failed_count"] += 1
                result["warnings"].append(f"{ts_code} {freq} 预热失败: {str(exc)[:120]}")

    limiter = _PerMinuteRateLimiter(result["tushare_per_minute_limit"])

    def primary_loader(*args, **kwargs):
        limiter.wait()
        return get_stock_minute_bars(*args, **kwargs)

    def fetch(task: tuple[str, str, str, str]) -> dict[str, Any]:
        ts_code, fetch_start, requested_end, freq = task
        item = {
            "fetched_count": 0,
            "cache_hit_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "warnings": [],
        }
        try:
            loaded = load_minutes_with_fallback(
                ts_code,
                fetch_start,
                requested_end,
                freq,
                trade_date,
                primary_loader=primary_loader,
            )
            if loaded.bars is not None and not loaded.bars.empty:
                save_minute_cache(
                    loaded.bars,
                    freq,
                    loaded.source,
                    trade_date,
                )
                item["fetched_count"] += 1
            else:
                item["failed_count"] += 1
            item["warnings"].extend(str(w) for w in (loaded.warnings or []))
        except Exception as exc:
            item["failed_count"] += 1
            item["warnings"].append(f"{ts_code} {freq} 预热失败: {str(exc)[:120]}")
        return item

    if tasks:
        workers = min(result["max_workers"], len(tasks))
        if workers <= 1:
            for task in tasks:
                _merge_warmup_summary(result, fetch(task))
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                for item in executor.map(fetch, tasks):
                    _merge_warmup_summary(result, item)
    result["warnings"] = list(dict.fromkeys(result["warnings"]))[:20]
    result["finished_at"] = _time_text()
    return result


def _warmup_loop(
    interval_seconds: int,
    limit: int,
    initial_delay_seconds: int,
    max_workers: int,
    tushare_per_minute_limit: int,
) -> None:
    if initial_delay_seconds > 0 and _STOP_EVENT.wait(initial_delay_seconds):
        return
    while not _STOP_EVENT.is_set():
        with _STATUS_LOCK:
            _STATUS["last_started_at"] = _time_text()
        try:
            result = warm_realtime_minute_cache(
                limit=limit,
                max_workers=max_workers,
                tushare_per_minute_limit=tushare_per_minute_limit,
            )
            with _STATUS_LOCK:
                _STATUS["last_result"] = result
                _STATUS["last_error"] = None
                _STATUS["last_finished_at"] = _time_text()
                _STATUS["run_count"] = int(_STATUS.get("run_count", 0)) + 1
        except Exception as exc:
            with _STATUS_LOCK:
                _STATUS["last_error"] = str(exc)[:200]
                _STATUS["last_finished_at"] = _time_text()
        if _STOP_EVENT.wait(max(5, int(interval_seconds))):
            return


def start_realtime_minute_warmup(
    *,
    interval_seconds: int = DEFAULT_WARMUP_INTERVAL_SECONDS,
    limit: int = DEFAULT_WARMUP_LIMIT,
    initial_delay_seconds: int = 10,
    max_workers: int = DEFAULT_WARMUP_MAX_WORKERS,
    tushare_per_minute_limit: int = DEFAULT_TUSHARE_PER_MINUTE_LIMIT,
) -> dict[str, Any]:
    global _WARMUP_THREAD
    enabled = os.getenv("REALTIME_MINUTE_WARMUP_ENABLED", "1").lower() not in {
        "0",
        "false",
        "no",
    }
    with _STATUS_LOCK:
        _STATUS["enabled"] = enabled
    if not enabled:
        return {**get_realtime_minute_warmup_status(), "already_running": False}
    if _WARMUP_THREAD is not None:
        return {**get_realtime_minute_warmup_status(), "already_running": True}
    interval_seconds = int(os.getenv("REALTIME_MINUTE_WARMUP_INTERVAL", interval_seconds))
    limit = int(os.getenv("REALTIME_MINUTE_WARMUP_LIMIT", limit))
    max_workers = int(os.getenv("REALTIME_MINUTE_WARMUP_WORKERS", max_workers))
    tushare_per_minute_limit = int(
        os.getenv("REALTIME_MINUTE_TUSHARE_LIMIT", tushare_per_minute_limit)
    )
    _STOP_EVENT.clear()
    _WARMUP_THREAD = threading.Thread(
        target=_warmup_loop,
        args=(
            int(interval_seconds),
            int(limit),
            int(initial_delay_seconds),
            int(max_workers),
            int(tushare_per_minute_limit),
        ),
        name="realtime-minute-warmup",
        daemon=True,
    )
    _WARMUP_THREAD.start()
    with _STATUS_LOCK:
        _STATUS["running"] = True
    return {**get_realtime_minute_warmup_status(), "already_running": False}


def get_realtime_minute_warmup_status() -> dict[str, Any]:
    with _STATUS_LOCK:
        status = dict(_STATUS)
    status["thread_alive"] = bool(
        _WARMUP_THREAD is not None
        and getattr(_WARMUP_THREAD, "is_alive", lambda: False)()
    )
    return status
