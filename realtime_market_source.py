from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess
import threading
import time
from typing import Any, Callable
from urllib.parse import urlencode

import pandas as pd


MINUTE_COLUMNS = ["ts_code", "trade_time", "open", "close", "high", "low", "vol", "amount"]
EASTMONEY_SNAPSHOT_URL = "https://push2.eastmoney.com/api/qt/clist/get"
EASTMONEY_TRENDS_URL = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
SINA_KLINE_URL = (
    "https://quotes.sina.cn/cn/api/jsonp_v2.php/"
    "var%20x=/CN_MarketDataService.getKLineData"
)
SUCCESS_TTL_SECONDS = 20
FAILURE_TTL_SECONDS = 60
PROVIDER_FAILURE_THRESHOLD = 2
PROVIDER_CIRCUIT_SECONDS = 60
_SNAPSHOT_CACHE: dict[str, tuple[float, pd.DataFrame, str | None]] = {}
_MINUTE_CACHE: dict[tuple[str, str, str, str, str], tuple[float, pd.DataFrame, str | None]] = {}
_PROVIDER_HEALTH: dict[str, tuple[int, float]] = {}
_PROVIDER_HEALTH_LOCK = threading.Lock()


@dataclass
class MinuteLoadResult:
    bars: pd.DataFrame
    source: str
    warnings: list[str]


def clear_realtime_source_caches() -> None:
    _SNAPSHOT_CACHE.clear()
    _MINUTE_CACHE.clear()
    with _PROVIDER_HEALTH_LOCK:
        _PROVIDER_HEALTH.clear()


def invalidate_realtime_minute_cache(
    ts_code: str,
    freq: str,
    trade_date: str,
) -> None:
    """Drop mutable provider responses without resetting failure circuits."""
    target = (str(ts_code), str(freq), str(trade_date))
    for key in list(_MINUTE_CACHE):
        if (
            len(key) >= 5
            and (str(key[1]), str(key[3]), str(key[4])) == target
        ):
            _MINUTE_CACHE.pop(key, None)


def _provider_available(provider: str) -> bool:
    with _PROVIDER_HEALTH_LOCK:
        failures, blocked_until = _PROVIDER_HEALTH.get(provider, (0, 0.0))
    return failures < PROVIDER_FAILURE_THRESHOLD or time.monotonic() >= blocked_until


def _record_provider_result(provider: str, success: bool) -> None:
    with _PROVIDER_HEALTH_LOCK:
        if success:
            _PROVIDER_HEALTH.pop(provider, None)
            return
        failures, _ = _PROVIDER_HEALTH.get(provider, (0, 0.0))
        failures += 1
        blocked_until = (
            time.monotonic() + PROVIDER_CIRCUIT_SECONDS
            if failures >= PROVIDER_FAILURE_THRESHOLD
            else 0.0
        )
        _PROVIDER_HEALTH[provider] = (failures, blocked_until)


def _eastmoney_secid(ts_code: str) -> str | None:
    text = str(ts_code or "").upper()
    if text.endswith(".SH") and text[:6].isdigit():
        return f"1.{text[:6]}"
    if text.endswith(".SZ") and text[:6].isdigit():
        return f"0.{text[:6]}"
    return None


def _sina_symbol(ts_code: str) -> str | None:
    text = str(ts_code or "").upper()
    if text.endswith(".SH") and text[:6].isdigit():
        return f"sh{text[:6]}"
    if text.endswith(".SZ") and text[:6].isdigit():
        return f"sz{text[:6]}"
    return None


def _normalize_minutes(records: list[dict[str, Any]], ts_code: str, end_datetime: str) -> pd.DataFrame:
    frame = pd.DataFrame(records, columns=MINUTE_COLUMNS)
    if frame.empty:
        return frame
    frame["ts_code"] = str(ts_code)
    frame["trade_time"] = pd.to_datetime(frame["trade_time"], errors="coerce")
    for column in ("open", "close", "high", "low", "vol", "amount"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["trade_time", "close"])
    frame = frame[frame["trade_time"] <= pd.Timestamp(end_datetime)]
    return (
        frame.drop_duplicates(["ts_code", "trade_time"], keep="last")
        .sort_values("trade_time").reset_index(drop=True).reindex(columns=MINUTE_COLUMNS)
    )


def _parse_eastmoney_snapshot(payload: Any, trade_date: str) -> pd.DataFrame:
    data = payload.get("data") if isinstance(payload, dict) else None
    rows = data.get("diff") if isinstance(data, dict) else None
    records = []
    for row in rows if isinstance(rows, list) else []:
        code = str(row.get("f12") or "")
        suffix = {1: "SH", 0: "SZ"}.get(row.get("f13"))
        if len(code) != 6 or not code.isdigit() or suffix is None:
            continue
        records.append({
            "ts_code": f"{code}.{suffix}", "trade_date": str(trade_date),
            "name": row.get("f14"), "industry": row.get("f100"),
            "open": row.get("f17"), "close": row.get("f2"),
            "high": row.get("f15"), "low": row.get("f16"),
            "pre_close": row.get("f18"), "pct_chg": row.get("f3"),
            "turnover_rate": row.get("f8"), "volume_ratio": row.get("f10"),
            "vol": row.get("f5"), "amount": row.get("f6"),
        })
    frame = pd.DataFrame(records)
    if frame.empty:
        return frame
    numeric = ["open", "close", "high", "low", "pre_close", "pct_chg",
               "turnover_rate", "volume_ratio", "vol", "amount"]
    frame[numeric] = frame[numeric].apply(pd.to_numeric, errors="coerce")
    return frame.dropna(subset=["ts_code", "close"]).reset_index(drop=True)


def _parse_eastmoney_rows(payload, key, ts_code, end_datetime):
    data = payload.get("data") if isinstance(payload, dict) else None
    rows = data.get(key) if isinstance(data, dict) else None
    records = []
    for text in rows if isinstance(rows, list) else []:
        parts = str(text).split(",")
        if len(parts) < 7:
            continue
        records.append({
            "ts_code": ts_code, "trade_time": parts[0], "open": parts[1],
            "close": parts[2], "high": parts[3], "low": parts[4],
            "vol": parts[5], "amount": parts[6],
        })
    return _normalize_minutes(records, ts_code, end_datetime)


def _parse_eastmoney_trends(payload, ts_code, trade_date, end_datetime):
    return _parse_eastmoney_rows(payload, "trends", ts_code, end_datetime)


def _parse_eastmoney_klines(payload, ts_code, trade_date, end_datetime):
    return _parse_eastmoney_rows(payload, "klines", ts_code, end_datetime)


def _parse_sina_klines(text, ts_code, trade_date, end_datetime):
    start, end = str(text or "").find("["), str(text or "").rfind("]")
    if start < 0 or end < start:
        return pd.DataFrame(columns=MINUTE_COLUMNS)
    try:
        payload = json.loads(str(text)[start:end + 1])
    except (TypeError, ValueError):
        return pd.DataFrame(columns=MINUTE_COLUMNS)
    records = [{
        "ts_code": ts_code, "trade_time": row.get("day"), "open": row.get("open"),
        "close": row.get("close"), "high": row.get("high"), "low": row.get("low"),
        "vol": row.get("volume"), "amount": row.get("amount"),
    } for row in payload if isinstance(row, dict)]
    return _normalize_minutes(records, ts_code, end_datetime)


def _run_curl(url: str) -> str:
    completed = subprocess.run(
        ["curl", "-fsSL", "--max-time", "3", "--retry", "1",
         "--retry-all-errors", "--retry-delay", "1", url],
        capture_output=True, text=True, check=True, timeout=8,
    )
    return completed.stdout


def _cached_external(key, fetcher):
    cached = _MINUTE_CACHE.get(key)
    if cached:
        cached_at, bars, error = cached
        ttl = SUCCESS_TTL_SECONDS if not bars.empty else FAILURE_TTL_SECONDS
        if time.monotonic() - cached_at <= ttl:
            return bars.copy(), error
    bars, error = fetcher()
    _MINUTE_CACHE[key] = (time.monotonic(), bars.copy(), error)
    return bars, error


def _fetch_eastmoney_minutes(ts_code, start_datetime, end_datetime, freq, trade_date):
    key = ("eastmoney", ts_code, end_datetime, freq, trade_date)
    def fetch():
        if not _provider_available("eastmoney"):
            return pd.DataFrame(columns=MINUTE_COLUMNS), "东方财富数据源熔断中"
        secid = _eastmoney_secid(ts_code)
        if not secid:
            return pd.DataFrame(columns=MINUTE_COLUMNS), "东方财富不支持该证券代码"
        try:
            if freq == "1min":
                query = urlencode({"secid": secid, "fields1": "f1,f2,f3",
                                   "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
                                   "ndays": 1, "iscr": 0})
                payload = json.loads(_run_curl(f"{EASTMONEY_TRENDS_URL}?{query}"))
                bars = _parse_eastmoney_trends(payload, ts_code, trade_date, end_datetime)
            else:
                query = urlencode({"secid": secid, "fields1": "f1,f2,f3",
                                   "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
                                   "klt": 60, "fqt": 1, "beg": start_datetime[:10].replace("-", ""),
                                   "end": trade_date, "lmt": 500})
                payload = json.loads(_run_curl(f"{EASTMONEY_KLINE_URL}?{query}"))
                bars = _parse_eastmoney_klines(payload, ts_code, trade_date, end_datetime)
            _record_provider_result("eastmoney", not bars.empty)
            return bars, None if not bars.empty else "东方财富未返回有效分钟数据"
        except Exception as exc:
            _record_provider_result("eastmoney", False)
            return pd.DataFrame(columns=MINUTE_COLUMNS), f"东方财富请求失败: {exc}"
    return _cached_external(key, fetch)


def _fetch_sina_minutes(ts_code, start_datetime, end_datetime, freq, trade_date):
    key = ("sina", ts_code, end_datetime, freq, trade_date)
    def fetch():
        if not _provider_available("sina"):
            return pd.DataFrame(columns=MINUTE_COLUMNS), "新浪财经数据源熔断中"
        symbol = _sina_symbol(ts_code)
        if not symbol:
            return pd.DataFrame(columns=MINUTE_COLUMNS), "新浪财经不支持该证券代码"
        try:
            query = urlencode({"symbol": symbol, "scale": 1 if freq == "1min" else 60,
                               "ma": "no", "datalen": 1023})
            bars = _parse_sina_klines(
                _run_curl(f"{SINA_KLINE_URL}?{query}"), ts_code, trade_date, end_datetime
            )
            _record_provider_result("sina", not bars.empty)
            return bars, None if not bars.empty else "新浪财经未返回有效分钟数据"
        except Exception as exc:
            _record_provider_result("sina", False)
            return pd.DataFrame(columns=MINUTE_COLUMNS), f"新浪财经请求失败: {exc}"
    return _cached_external(key, fetch)


def _minutes_are_usable(bars: pd.DataFrame, freq: str, trade_date: str) -> bool:
    if bars is None or bars.empty or "trade_time" not in bars:
        return False
    parsed = pd.to_datetime(bars["trade_time"], errors="coerce")
    close = pd.to_numeric(bars["close"], errors="coerce") if "close" in bars else pd.Series(dtype=float)
    has_trade_date = parsed.dt.strftime("%Y%m%d").eq(
        str(trade_date)
    ).any()
    if freq == "1min":
        return bool(has_trade_date)
    return bool(close.notna().sum() >= 35 and has_trade_date)


def load_minutes_with_fallback(
    ts_code: str, start_datetime: str, end_datetime: str, freq: str,
    trade_date: str, primary_loader: Callable[..., pd.DataFrame],
) -> MinuteLoadResult:
    warnings = []
    try:
        bars = primary_loader(ts_code, start_datetime, end_datetime, freq=freq)
        if _minutes_are_usable(bars, freq, trade_date):
            return MinuteLoadResult(bars.copy(), "tushare", warnings)
        warnings.append("Tushare未返回可用分钟数据")
    except Exception as exc:
        warnings.append(f"Tushare分钟数据失败: {exc}")
    for source, loader in (
        ("eastmoney_fallback", _fetch_eastmoney_minutes),
        ("sina_fallback", _fetch_sina_minutes),
    ):
        bars, error = loader(ts_code, start_datetime, end_datetime, freq, trade_date)
        if _minutes_are_usable(bars, freq, trade_date):
            return MinuteLoadResult(bars.copy(), source, warnings)
        warnings.append(error or f"{source}未返回可用分钟数据")
    return MinuteLoadResult(pd.DataFrame(columns=MINUTE_COLUMNS), "unavailable", warnings)


def load_eastmoney_market_snapshot(trade_date: str) -> tuple[pd.DataFrame, str | None]:
    cached = _SNAPSHOT_CACHE.get(str(trade_date))
    if cached:
        cached_at, frame, error = cached
        ttl = SUCCESS_TTL_SECONDS if not frame.empty else FAILURE_TTL_SECONDS
        if time.monotonic() - cached_at <= ttl:
            return frame.copy(), error
    frames = []
    try:
        for page in range(1, 20):
            query = urlencode({
                "pn": page, "pz": 500, "po": 1, "np": 1,
                "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
                "fields": "f2,f3,f5,f6,f8,f10,f12,f13,f14,f15,f16,f17,f18,f100",
            })
            payload = json.loads(_run_curl(f"{EASTMONEY_SNAPSHOT_URL}?{query}"))
            frame = _parse_eastmoney_snapshot(payload, trade_date)
            if not frame.empty:
                frames.append(frame)
            diff = ((payload.get("data") or {}).get("diff") or []) if isinstance(payload, dict) else []
            if len(diff) < 500:
                break
        result = pd.concat(frames, ignore_index=True).drop_duplicates("ts_code") if frames else pd.DataFrame()
        error = None if not result.empty else "东方财富快照未返回有效数据"
    except Exception as exc:
        result, error = pd.DataFrame(), f"东方财富快照请求失败: {exc}"
    _SNAPSHOT_CACHE[str(trade_date)] = (time.monotonic(), result.copy(), error)
    return result, error
