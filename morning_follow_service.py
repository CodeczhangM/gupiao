from __future__ import annotations

from datetime import datetime, timedelta
import json
import math
import subprocess
import time
from typing import Any
from urllib.parse import urlencode

import pandas as pd

from data_service import get_trade_dates, sync_cached_market_data
from market_cache import load_market_snapshot, load_recent_daily
from realtime_market_source import load_eastmoney_market_snapshot
from overnight_monitor_service import (
    _cached_minute_bars,
    _datetime_window,
    _history_window,
    _json_safe,
    _leader_codes_from_sector_potential,
    _sector_60m_signal_from_bars,
    _sector_representative_universe,
)
from strategy import _macd_kdj_60m_signal


_MORNING_FOLLOW_RESULT_CACHE: dict[
    tuple[int, int, str, str, str],
    tuple[float, dict[str, Any]],
] = {}
_LIVE_RESULT_CACHE_TTL_SECONDS = 25
_STATIC_RESULT_CACHE_TTL_SECONDS = 300
_MORNING_MINUTE_COLUMNS = [
    "ts_code",
    "trade_time",
    "open",
    "close",
    "high",
    "low",
    "vol",
    "amount",
]
_EASTMONEY_TRENDS_URL = (
    "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
)
_EASTMONEY_MORNING_CACHE: dict[
    tuple[str, str],
    tuple[float, pd.DataFrame, str | None],
] = {}
_EASTMONEY_SUCCESS_TTL_SECONDS = 20
_EASTMONEY_FAILURE_TTL_SECONDS = 5
_SINA_KLINE_URL = (
    "https://quotes.sina.cn/cn/api/jsonp_v2.php/"
    "var%20x=/CN_MarketDataService.getKLineData"
)
_SINA_MORNING_CACHE: dict[
    tuple[str, str],
    tuple[float, pd.DataFrame, str | None],
] = {}


def _select_candidate_trade_date(trade_dates: list[str], now: datetime) -> str:
    dates = sorted({str(value) for value in trade_dates}, reverse=True)
    if not dates:
        raise LookupError("没有可用交易日")
    today = now.strftime("%Y%m%d")
    if today in dates and now.strftime("%H:%M:%S") >= "14:30:00":
        return today
    return next((date for date in dates if date < today), dates[0])


def _fill_effective_volume_ratio(
    market: pd.DataFrame,
    history: pd.DataFrame,
    trade_date: str,
) -> pd.DataFrame:
    result = market.copy()
    source = pd.to_numeric(
        result["volume_ratio"]
        if "volume_ratio" in result
        else pd.Series(index=result.index, dtype=float),
        errors="coerce",
    )
    estimate = pd.Series(index=result.index, dtype=float)
    if (
        not result.empty
        and {"ts_code", "vol"}.issubset(result.columns)
        and history is not None
        and not history.empty
        and {"ts_code", "trade_date", "vol"}.issubset(history.columns)
    ):
        prior = history.copy()
        prior["trade_date"] = prior["trade_date"].astype(str)
        prior["vol"] = pd.to_numeric(prior["vol"], errors="coerce")
        prior = prior[
            (prior["trade_date"] < str(trade_date))
            & (prior["vol"] > 0)
        ]
        averages = (
            prior.sort_values(["ts_code", "trade_date"])
            .groupby("ts_code", group_keys=False)
            .tail(5)
            .groupby("ts_code")["vol"]
            .mean()
        )
        current = pd.to_numeric(result["vol"], errors="coerce")
        estimate = current / result["ts_code"].astype(str).map(averages)
    result["estimated_volume_ratio"] = estimate
    result["effective_volume_ratio"] = source.where(source > 0, estimate)
    return result


def _daily_follow_candidates(
    market: pd.DataFrame,
    history: pd.DataFrame,
    trade_date: str,
    leader_codes: set[str],
    max_fetch: int,
) -> pd.DataFrame:
    data = _fill_effective_volume_ratio(market, history, trade_date)
    for column in ("close", "pct_chg", "turnover_rate", "amount"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    code = data["ts_code"].astype(str)
    name = data.get("name", pd.Series("", index=data.index)).astype(str)
    eligible = data[
        ~name.str.contains("ST", case=False, na=False)
        & ~code.str.startswith(("688", "689"))
        & (data["close"] > 3)
        & data["pct_chg"].between(1.5, 6.5, inclusive="both")
        & data["turnover_rate"].between(2, 12, inclusive="both")
        & data["effective_volume_ratio"].between(1.2, 3.2, inclusive="both")
        & (data["amount"] >= 200_000)
    ].copy()
    eligible["morning_follow_sector_leader"] = (
        eligible["ts_code"].astype(str).isin(leader_codes)
    )
    eligible["daily_follow_prefilter_score"] = (
        eligible["pct_chg"].clip(0, 6.5) * 4
        + eligible["effective_volume_ratio"].clip(0, 3.2) * 8
        + (12 - (eligible["turnover_rate"] - 7).abs()).clip(lower=0)
        + (eligible["amount"] / 200_000).clip(0, 10)
    )
    result = (
        eligible.sort_values("daily_follow_prefilter_score", ascending=False)
        .head(max_fetch)
        .reset_index(drop=True)
    )
    result.attrs["hard_filter_count"] = len(eligible)
    return result


def _follow_snapshot_is_usable(
    market: pd.DataFrame | None,
    trade_date: str,
) -> bool:
    required = {
        "ts_code",
        "trade_date",
        "close",
        "pct_chg",
        "turnover_rate",
        "volume_ratio",
        "amount",
    }
    if (
        market is None
        or market.empty
        or not required.issubset(market.columns)
    ):
        return False
    data = market.copy()
    for column in (
        "close",
        "pct_chg",
        "turnover_rate",
        "volume_ratio",
        "amount",
    ):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    code = data["ts_code"].astype(str)
    name = data.get("name", pd.Series("", index=data.index)).astype(str)
    same_date = data["trade_date"].astype(str).eq(str(trade_date))
    eligible = (
        same_date
        & ~name.str.contains("ST", case=False, na=False)
        & ~code.str.startswith(("688", "689"))
        & (data["close"] > 3)
        & data["pct_chg"].between(1.5, 6.5, inclusive="both")
        & data["turnover_rate"].between(2, 12, inclusive="both")
        & data["volume_ratio"].between(1.2, 3.2, inclusive="both")
        & (data["amount"] >= 200_000)
    )
    return bool(eligible.any())


def _normalize_confirmation_bars(
    bars: pd.DataFrame,
    confirmation_trade_date: str,
    now: datetime,
) -> pd.DataFrame:
    if bars is None or bars.empty:
        return pd.DataFrame()
    required = {"trade_time", "open", "close", "vol"}
    if not required.issubset(bars.columns):
        return pd.DataFrame()
    usable = bars.copy()
    usable["trade_time"] = pd.to_datetime(usable["trade_time"], errors="coerce")
    for column in ("open", "close", "high", "low", "vol", "amount"):
        if column in usable:
            usable[column] = pd.to_numeric(usable[column], errors="coerce")
    usable = usable.replace([float("inf"), float("-inf")], pd.NA)
    usable = usable.dropna(subset=["trade_time", "open", "close", "vol"])
    date_text = usable["trade_time"].dt.strftime("%Y%m%d")
    clock = usable["trade_time"].dt.strftime("%H:%M")
    usable = usable[
        (date_text == str(confirmation_trade_date))
        & (clock >= "09:30")
        & (clock <= "10:00")
        & (usable["trade_time"] <= pd.Timestamp(now))
    ]
    return usable.sort_values("trade_time").reset_index(drop=True)


def _eastmoney_secid(ts_code: str) -> str | None:
    text = str(ts_code or "").upper()
    if text.endswith(".SH") and text[:6].isdigit():
        return f"1.{text[:6]}"
    if text.endswith(".SZ") and text[:6].isdigit():
        return f"0.{text[:6]}"
    return None


def _empty_morning_bars() -> pd.DataFrame:
    return pd.DataFrame(columns=_MORNING_MINUTE_COLUMNS)


def _parse_eastmoney_trends(
    payload: Any,
    ts_code: str,
    confirmation_trade_date: str,
    now: datetime,
) -> pd.DataFrame:
    data = payload.get("data") if isinstance(payload, dict) else None
    trends = data.get("trends") if isinstance(data, dict) else None
    if not isinstance(trends, list):
        return _empty_morning_bars()
    records: list[dict[str, Any]] = []
    for trend in trends:
        parts = str(trend).split(",")
        if len(parts) < 8:
            continue
        records.append({
            "ts_code": ts_code,
            "trade_time": parts[0],
            "open": parts[1],
            "close": parts[2],
            "high": parts[3],
            "low": parts[4],
            "vol": parts[5],
            "amount": parts[6],
        })
    frame = pd.DataFrame(records, columns=_MORNING_MINUTE_COLUMNS)
    return _normalize_confirmation_bars(
        frame,
        confirmation_trade_date,
        now,
    ).reindex(columns=_MORNING_MINUTE_COLUMNS)


def _sina_symbol(ts_code: str) -> str | None:
    text = str(ts_code or "").upper()
    if text.endswith(".SH") and text[:6].isdigit():
        return f"sh{text[:6]}"
    if text.endswith(".SZ") and text[:6].isdigit():
        return f"sz{text[:6]}"
    return None


def _parse_sina_klines(
    text: str,
    ts_code: str,
    confirmation_trade_date: str,
    now: datetime,
) -> pd.DataFrame:
    start = str(text or "").find("[")
    end = str(text or "").rfind("]")
    if start < 0 or end < start:
        return _empty_morning_bars()
    try:
        payload = json.loads(str(text)[start:end + 1])
    except (TypeError, ValueError):
        return _empty_morning_bars()
    if not isinstance(payload, list):
        return _empty_morning_bars()
    records = [
        {
            "ts_code": ts_code,
            "trade_time": row.get("day"),
            "open": row.get("open"),
            "close": row.get("close"),
            "high": row.get("high"),
            "low": row.get("low"),
            "vol": row.get("volume"),
            "amount": row.get("amount"),
        }
        for row in payload
        if isinstance(row, dict)
    ]
    frame = pd.DataFrame(records, columns=_MORNING_MINUTE_COLUMNS)
    return _normalize_confirmation_bars(
        frame,
        confirmation_trade_date,
        now,
    ).reindex(columns=_MORNING_MINUTE_COLUMNS)


def _eastmoney_morning_bars(
    ts_code: str,
    confirmation_trade_date: str,
    now: datetime,
) -> tuple[pd.DataFrame, str | None]:
    key = (str(ts_code), str(confirmation_trade_date))
    cached = _EASTMONEY_MORNING_CACHE.get(key)
    if cached:
        cached_at, cached_bars, cached_error = cached
        ttl = (
            _EASTMONEY_SUCCESS_TTL_SECONDS
            if not cached_bars.empty
            else _EASTMONEY_FAILURE_TTL_SECONDS
        )
        if time.monotonic() - cached_at <= ttl:
            return cached_bars.copy(), cached_error

    secid = _eastmoney_secid(ts_code)
    if not secid:
        error = "东方财富备用源不支持该证券代码"
        bars = _empty_morning_bars()
        _EASTMONEY_MORNING_CACHE[key] = (
            time.monotonic(),
            bars.copy(),
            error,
        )
        return bars, error

    try:
        query = urlencode({
            "secid": secid,
            "fields1": (
                "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
            ),
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            "ndays": 1,
            "iscr": 0,
        })
        completed = subprocess.run(
            [
                "curl",
                "-fsSL",
                "--max-time",
                "6",
                "--retry",
                "2",
                "--retry-all-errors",
                "--retry-delay",
                "1",
                f"{_EASTMONEY_TRENDS_URL}?{query}",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        bars = _parse_eastmoney_trends(
            json.loads(completed.stdout),
            ts_code,
            confirmation_trade_date,
            now,
        )
        error = (
            None
            if not bars.empty
            else "东方财富备用源未返回有效数据"
        )
    except Exception as exc:
        bars = _empty_morning_bars()
        error = f"东方财富备用源请求失败: {exc}"

    _EASTMONEY_MORNING_CACHE[key] = (
        time.monotonic(),
        bars.copy(),
        error,
    )
    return bars, error


def _sina_morning_bars(
    ts_code: str,
    confirmation_trade_date: str,
    now: datetime,
) -> tuple[pd.DataFrame, str | None]:
    key = (str(ts_code), str(confirmation_trade_date))
    cached = _SINA_MORNING_CACHE.get(key)
    if cached:
        cached_at, cached_bars, cached_error = cached
        ttl = (
            _EASTMONEY_SUCCESS_TTL_SECONDS
            if not cached_bars.empty
            else _EASTMONEY_FAILURE_TTL_SECONDS
        )
        if time.monotonic() - cached_at <= ttl:
            return cached_bars.copy(), cached_error

    symbol = _sina_symbol(ts_code)
    if not symbol:
        error = "新浪财经备用源不支持该证券代码"
        bars = _empty_morning_bars()
        _SINA_MORNING_CACHE[key] = (
            time.monotonic(),
            bars.copy(),
            error,
        )
        return bars, error

    try:
        query = urlencode({
            "symbol": symbol,
            "scale": 1,
            "ma": "no",
            "datalen": 480,
        })
        completed = subprocess.run(
            [
                "curl",
                "-fsSL",
                "--max-time",
                "6",
                "--retry",
                "2",
                "--retry-all-errors",
                "--retry-delay",
                "1",
                f"{_SINA_KLINE_URL}?{query}",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        bars = _parse_sina_klines(
            completed.stdout,
            ts_code,
            confirmation_trade_date,
            now,
        )
        error = None if not bars.empty else "新浪财经备用源未返回有效数据"
    except Exception as exc:
        bars = _empty_morning_bars()
        error = f"新浪财经备用源请求失败: {exc}"

    _SINA_MORNING_CACHE[key] = (
        time.monotonic(),
        bars.copy(),
        error,
    )
    return bars, error


def _morning_confirmation(
    setup: dict[str, Any],
    bars: pd.DataFrame,
    now: datetime,
    confirmation_trade_date: str | None,
    minute_failure_reason: str | None = None,
) -> dict[str, Any]:
    relaxed = setup.get("setup_tier") == "relaxed"
    observation_status = "宽松观察" if relaxed else "明日观察"
    entry_plan = (
        "仅在9:35–10:00早盘条件全部成立时轻仓考虑；"
        "不可追高，尾盘条件未完全确认"
        if relaxed
        else "仅在9:35–10:00条件全部成立时考虑跟进"
    )
    base = {
        "follow_status": observation_status,
        "morning_open": None,
        "morning_current_price": None,
        "morning_open_gap_pct": None,
        "morning_current_gain_pct": None,
        "morning_first5_vwap": None,
        "morning_above_open": False,
        "morning_above_vwap": False,
        "morning_entry_plan": entry_plan,
        "t1_exit_plan": (
            "买入后的下一交易日优先退出：高开分批兑现，"
            "低开或跌破前一日尾盘承接位优先控制风险"
        ),
    }
    if not confirmation_trade_date:
        return {**base, "follow_reason": "等待下一交易日早盘确认"}
    if now.strftime("%Y%m%d") != confirmation_trade_date:
        return {**base, "follow_reason": "当前不在确认交易日"}

    usable = _normalize_confirmation_bars(
        bars,
        confirmation_trade_date,
        now,
    )
    if usable.empty:
        return {
            **base,
            "follow_status": "数据未就绪",
            "follow_reason": (
                minute_failure_reason or "缺少确认日分钟数据"
            ),
        }

    opening = float(usable.iloc[0]["open"])
    current = float(usable.iloc[-1]["close"])
    clock = usable["trade_time"].dt.strftime("%H:%M")
    first_five = usable[(clock >= "09:30") & (clock <= "09:34")]
    volume = float(first_five["vol"].sum()) if not first_five.empty else 0.0
    if first_five.empty or not math.isfinite(volume) or volume <= 0:
        return {
            **base,
            "follow_status": "数据未就绪",
            "follow_reason": "首5分钟VWAP数据不足",
        }
    vwap = float(
        (first_five["close"] * first_five["vol"]).sum()
        / first_five["vol"].sum()
    )
    try:
        previous_close = float(setup["close"])
        support = float(setup["previous_tail_support"])
    except (KeyError, TypeError, ValueError):
        return {
            **base,
            "follow_status": "数据未就绪",
            "follow_reason": "前日收盘或尾盘支撑数据缺失",
        }
    if not all(
        math.isfinite(value) and value > 0
        for value in (opening, current, vwap, previous_close, support)
    ):
        return {
            **base,
            "follow_status": "数据未就绪",
            "follow_reason": "早盘确认价格数据无效",
        }

    gap = (opening / previous_close - 1) * 100
    gain = (current / previous_close - 1) * 100
    metrics = {
        **base,
        "morning_open": opening,
        "morning_current_price": current,
        "morning_open_gap_pct": gap,
        "morning_current_gain_pct": gain,
        "morning_first5_vwap": vwap,
        "morning_above_open": current >= opening,
        "morning_above_vwap": current >= vwap,
    }
    if now.strftime("%H:%M") < "09:35":
        return {
            **metrics,
            "follow_status": "等待9:35确认",
            "follow_reason": "首5分钟尚未结束",
        }
    if gap < -1 or gap > 3:
        return {
            **metrics,
            "follow_status": "放弃",
            "follow_reason": "开盘缺口超出风控区间",
        }
    if gain > 4:
        return {
            **metrics,
            "follow_status": "放弃",
            "follow_reason": "当前涨幅超过4%，不追高",
        }
    if current < support:
        return {
            **metrics,
            "follow_status": "放弃",
            "follow_reason": "跌破前日尾盘支撑",
        }
    if current < opening and current < vwap:
        return {
            **metrics,
            "follow_status": "放弃",
            "follow_reason": "同时跌破开盘价和首5分钟VWAP",
        }
    if (
        -0.5 <= gap <= 2.5
        and gain <= 3.5
        and current >= previous_close
        and current >= opening
        and current >= vwap
        and current >= support
    ):
        return {
            **metrics,
            "follow_status": "谨慎跟进" if relaxed else "可以跟进",
            "follow_reason": (
                "早盘承接确认，但前日尾盘条件未全部达标"
                if relaxed
                else "开盘幅度适中且承接确认"
            ),
        }
    return {
        **metrics,
        "follow_status": "等待确认",
        "follow_reason": "未触发放弃条件，但确认条件尚未全部满足",
    }


def _morning_follow_phase(
    now: datetime,
    candidate_trade_date: str,
    confirmation_trade_date: str | None,
) -> tuple[str, bool]:
    today = now.strftime("%Y%m%d")
    clock = now.strftime("%H:%M")
    if today == candidate_trade_date and "14:30" <= clock < "15:00":
        return "观察池构建中", True
    if today == confirmation_trade_date and "09:30" <= clock < "09:35":
        return "等待9:35确认", True
    if today == confirmation_trade_date and "09:35" <= clock <= "10:00":
        return "早盘确认", True
    if today == confirmation_trade_date and "10:00" < clock < "15:00":
        return "确认结束", False
    return "明日观察池", False


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _tail_condition_evaluation(
    tail_return: float,
    tail_volume: float,
    tail_position: float,
) -> tuple[int, list[str]]:
    notes: list[str] = []
    passed = 0

    if 0.15 <= tail_return <= 1.20:
        passed += 1
    elif tail_return < 0.15:
        notes.append(f"尾盘涨幅{tail_return:.2f}%，低于0.15%")
    else:
        notes.append(f"尾盘涨幅{tail_return:.2f}%，高于1.20%")

    if 1.20 <= tail_volume <= 3.00:
        passed += 1
    elif tail_volume < 1.20:
        notes.append(f"尾盘量能{tail_volume:.2f}倍，低于1.20倍")
    else:
        notes.append(f"尾盘量能{tail_volume:.2f}倍，高于3.00倍")

    if tail_position >= 0.75:
        passed += 1
    else:
        notes.append(
            f"尾盘收盘位置{tail_position * 100:.0f}%，低于75%"
        )

    return passed, notes


def _setup_row(
    stock: dict[str, Any],
    stock_bars: dict[str, pd.DataFrame],
    sector_signal: dict[str, Any],
    leader_codes: set[str],
) -> dict[str, Any] | None:
    if not sector_signal or sector_signal.get("sector_60m_excluded"):
        return None
    bars_60m = stock_bars.get("60m")
    tail_1m = stock_bars.get("tail_1m")
    if (
        bars_60m is None
        or bars_60m.empty
        or tail_1m is None
        or tail_1m.empty
    ):
        return None

    effective_volume_ratio = _finite_number(
        stock.get("effective_volume_ratio")
    )
    turnover_rate = _finite_number(stock.get("turnover_rate"))
    amount = _finite_number(stock.get("amount"))
    if None in (effective_volume_ratio, turnover_rate, amount):
        return None
    signal_stock = {
        **stock,
        "volume_ratio": effective_volume_ratio,
        "volume_ratio_num": effective_volume_ratio,
        "turnover_num": turnover_rate,
        "amount_num": amount,
    }
    signal = _macd_kdj_60m_signal(pd.Series(signal_stock), stock_bars)
    if signal is None:
        return None

    tail_return = _finite_number(signal.get("tail_return_after_1430"))
    tail_volume = _finite_number(signal.get("tail_volume_ratio"))
    tail_position = _finite_number(signal.get("tail_close_position"))
    if None in (tail_return, tail_volume, tail_position):
        return None
    tail_condition_pass_count, tail_condition_notes = (
        _tail_condition_evaluation(
            tail_return,
            tail_volume,
            tail_position,
        )
    )

    if "low" not in tail_1m.columns:
        return None
    tail_lows = pd.to_numeric(tail_1m["low"], errors="coerce")
    tail_lows = tail_lows[
        tail_lows.map(lambda value: math.isfinite(float(value)))
    ]
    if tail_lows.empty:
        return None
    previous_tail_support = float(tail_lows.min())

    tail_score = tail_condition_pass_count * 10
    stock_score = (
        10 * bool(signal.get("macd_above_zero_60m"))
        + 6 * bool(signal.get("macd_recent_golden_cross_60m"))
        + 4 * bool(signal.get("kdj_bullish_60m"))
    )
    sector_score = (
        10 * bool(sector_signal.get("sector_macd_above_zero"))
        + 10 * bool(sector_signal.get("sector_macd_trending_up"))
    )
    volume_score = 8 if 1.5 <= effective_volume_ratio <= 2.5 else 5
    turnover_score = 5 if 4 <= turnover_rate <= 9 else 3
    amount_score = 7 if amount >= 500_000 else 4
    ts_code = str(stock.get("ts_code") or "")
    leader_score = 10 if ts_code in leader_codes else 0
    liquidity_score = volume_score + turnover_score + amount_score
    follow_setup_score = (
        tail_score
        + stock_score
        + sector_score
        + liquidity_score
        + leader_score
    )
    tail_conditions_all_pass = tail_condition_pass_count == 3
    strict = tail_conditions_all_pass and follow_setup_score >= 70
    setup_tier = "strict" if strict else "relaxed"
    setup_tier_label = "严格候选" if strict else "宽松观察"
    if strict:
        setup_tier_reason = "尾盘三项及观察分均达标"
    elif tail_condition_notes:
        setup_tier_reason = "；".join(tail_condition_notes)
    else:
        setup_tier_reason = (
            f"观察分{follow_setup_score}，低于严格候选70分"
        )

    reason_parts = [
        setup_tier_label,
        f"尾盘涨幅{tail_return:.2f}%",
        f"尾盘量能{tail_volume:.2f}倍",
        str(sector_signal.get("sector_macd_status") or "板块60分趋势确认"),
        setup_tier_reason,
    ]
    if leader_score:
        reason_parts.append("板块龙头加分")
    return {
        "ts_code": ts_code,
        "name": stock.get("name"),
        "industry": stock.get("industry"),
        "close": _finite_number(stock.get("close")),
        "pct_chg": _finite_number(stock.get("pct_chg")),
        "turnover_rate": turnover_rate,
        "effective_volume_ratio": effective_volume_ratio,
        "estimated_volume_ratio": _finite_number(
            stock.get("estimated_volume_ratio")
        ),
        "amount": amount,
        "tail_return_after_1430": tail_return,
        "tail_volume_ratio": tail_volume,
        "tail_close_position": tail_position,
        "tail_condition_pass_count": tail_condition_pass_count,
        "tail_conditions_all_pass": tail_conditions_all_pass,
        "tail_condition_notes": tail_condition_notes,
        "previous_tail_support": previous_tail_support,
        "macd_above_zero_60m": bool(
            signal.get("macd_above_zero_60m")
        ),
        "macd_recent_golden_cross_60m": bool(
            signal.get("macd_recent_golden_cross_60m")
        ),
        "kdj_bullish_60m": bool(signal.get("kdj_bullish_60m")),
        "sector_macd_status": sector_signal.get("sector_macd_status"),
        "sector_macd_above_zero": bool(
            sector_signal.get("sector_macd_above_zero")
        ),
        "sector_macd_trending_up": bool(
            sector_signal.get("sector_macd_trending_up")
        ),
        "morning_follow_sector_leader": bool(leader_score),
        "follow_setup_score": follow_setup_score,
        "setup_tier": setup_tier,
        "setup_tier_label": setup_tier_label,
        "setup_tier_reason": setup_tier_reason,
        "follow_score_breakdown": {
            "tail": tail_score,
            "stock_60m": stock_score,
            "sector_60m": sector_score,
            "liquidity": liquidity_score,
            "leader": leader_score,
        },
        "follow_status": "明日观察" if strict else "宽松观察",
        "morning_entry_plan": (
            "仅在9:35–10:00早盘条件全部成立时轻仓考虑；"
            "不可追高，尾盘条件未完全确认"
            if not strict
            else "仅在9:35–10:00条件全部成立时考虑跟进"
        ),
        "t1_exit_plan": (
            "买入后的下一交易日优先退出：高开分批兑现，"
            "低开或跌破前一日尾盘承接位优先控制风险"
        ),
        "follow_reason": "、".join(reason_parts),
    }


def _load_follow_inputs(
    now: datetime,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    calendar_end = (now + timedelta(days=14)).strftime("%Y%m%d")
    trade_dates = [str(value) for value in get_trade_dates(
        n=20,
        end_date=calendar_end,
    )]
    requested_candidate_trade_date = _select_candidate_trade_date(
        trade_dates,
        now,
    )
    candidate_trade_date = requested_candidate_trade_date
    ascending_dates = sorted(set(trade_dates))
    sync_metadata: dict[str, Any] = {}
    fallback_errors: list[str] = []
    data_source = "local_snapshot"
    today = now.strftime("%Y%m%d")
    clock = now.strftime("%H:%M")
    market = load_market_snapshot(candidate_trade_date)
    should_sync_live = (
        candidate_trade_date == today
        and "14:30" <= clock < "15:00"
    )
    should_fill_missing = (
        candidate_trade_date == today
        and clock >= "14:30"
        and (market is None or market.empty)
    )
    if (
        should_sync_live
        or should_fill_missing
    ):
        sync_metadata = sync_cached_market_data(force_current=True) or {}
        market = load_market_snapshot(candidate_trade_date)
    if market is None or market.empty:
        fallback_errors.append(
            f"{candidate_trade_date} 本地市场快照未就绪"
        )
        fallback_market, fallback_error = load_eastmoney_market_snapshot(
            candidate_trade_date
        )
        if _follow_snapshot_is_usable(
            fallback_market,
            candidate_trade_date,
        ):
            market = fallback_market
            data_source = "eastmoney_snapshot_fallback"
        else:
            fallback_errors.append(
                fallback_error or "东方财富快照未返回可用候选"
            )
    if market is None or market.empty:
        previous_dates = sorted(
            (
                date
                for date in ascending_dates
                if date < requested_candidate_trade_date
            ),
            reverse=True,
        )
        for previous_date in previous_dates:
            previous_market = load_market_snapshot(previous_date)
            if _follow_snapshot_is_usable(previous_market, previous_date):
                market = previous_market
                candidate_trade_date = previous_date
                data_source = "previous_snapshot"
                break
            fallback_errors.append(
                f"{previous_date} 本地市场快照不可用"
            )
    if market is None or market.empty:
        detail = "；".join(fallback_errors)
        raise LookupError(
            f"{requested_candidate_trade_date} 市场快照未就绪"
            + (f"（{detail}）" if detail else "")
        )
    confirmation_trade_date = next(
        (
            date
            for date in ascending_dates
            if date > candidate_trade_date
        ),
        None,
    )
    data_current = candidate_trade_date == requested_candidate_trade_date
    history = load_recent_daily(candidate_trade_date, 100)
    return market, history, {
        **sync_metadata,
        "requested_candidate_trade_date": requested_candidate_trade_date,
        "candidate_trade_date": candidate_trade_date,
        "confirmation_trade_date": confirmation_trade_date,
        "data_trade_date": candidate_trade_date,
        "data_source": data_source,
        "data_status": "live" if data_current else "stale",
        "data_status_label": "实时数据" if data_current else "备用缓存",
        "data_current": data_current,
        "snapshot_fallback_warnings": fallback_errors,
    }


def _load_setup_bars(
    market: pd.DataFrame,
    candidates: pd.DataFrame,
    candidate_trade_date: str,
    now: datetime,
) -> tuple[
    dict[str, dict[str, pd.DataFrame]],
    pd.DataFrame,
    dict[str, pd.DataFrame],
    list[str],
]:
    del now
    warnings: list[str] = []
    candidate_bars: dict[str, dict[str, pd.DataFrame]] = {}
    history_start, history_end = _history_window(candidate_trade_date)
    tail_start, tail_end = _datetime_window(
        candidate_trade_date,
        "14:30:00",
        "15:00:00",
    )
    for row in candidates.to_dict("records"):
        ts_code = str(row.get("ts_code") or "")
        if not ts_code:
            continue
        try:
            candidate_bars[ts_code] = {
                "60m": _cached_minute_bars(
                    ts_code,
                    history_start,
                    history_end,
                    "60min",
                ),
                "tail_1m": _cached_minute_bars(
                    ts_code,
                    tail_start,
                    tail_end,
                    "1min",
                ),
            }
        except Exception as exc:
            warnings.append(f"{ts_code} 前日分钟数据加载失败: {exc}")

    representatives = _sector_representative_universe(
        market,
        candidates,
    )
    sector_bars: dict[str, pd.DataFrame] = {}
    for row in representatives.to_dict("records"):
        ts_code = str(row.get("ts_code") or "")
        if not ts_code:
            continue
        try:
            if ts_code in candidate_bars:
                sector_bars[ts_code] = candidate_bars[ts_code]["60m"]
            else:
                sector_bars[ts_code] = _cached_minute_bars(
                    ts_code,
                    history_start,
                    history_end,
                    "60min",
                )
        except Exception as exc:
            warnings.append(f"{ts_code} 板块60分钟数据加载失败: {exc}")
    return candidate_bars, representatives, sector_bars, warnings


def _morning_bars_for_candidate(
    ts_code: str,
    confirmation_trade_date: str | None,
    now: datetime,
) -> tuple[pd.DataFrame, str, str | None]:
    if (
        not confirmation_trade_date
        or now.strftime("%Y%m%d") != confirmation_trade_date
        or now.strftime("%H:%M") < "09:30"
    ):
        return _empty_morning_bars(), "unavailable", None
    day = datetime.strptime(confirmation_trade_date, "%Y%m%d")
    start = day.replace(hour=9, minute=30, second=0)
    end = min(
        now.replace(second=0, microsecond=0) - timedelta(minutes=1),
        day.replace(hour=10, minute=0, second=0),
    )
    if end < start:
        return _empty_morning_bars(), "unavailable", None
    tushare_error: str | None = None
    try:
        tushare_bars = _cached_minute_bars(
            ts_code,
            start.strftime("%Y-%m-%d %H:%M:%S"),
            end.strftime("%Y-%m-%d %H:%M:%S"),
            "1min",
        )
    except Exception as exc:
        tushare_bars = _empty_morning_bars()
        tushare_error = f"Tushare分钟请求失败: {exc}"
    usable_tushare = _normalize_confirmation_bars(
        tushare_bars,
        confirmation_trade_date,
        now,
    )
    if not usable_tushare.empty:
        return usable_tushare, "tushare", None

    fallback_bars, fallback_error = _eastmoney_morning_bars(
        ts_code,
        confirmation_trade_date,
        now,
    )
    if not fallback_bars.empty:
        return fallback_bars, "eastmoney_fallback", None
    sina_bars, sina_error = _sina_morning_bars(
        ts_code,
        confirmation_trade_date,
        now,
    )
    if not sina_bars.empty:
        return sina_bars, "sina_fallback", None
    return (
        _empty_morning_bars(),
        "unavailable",
        (tushare_error or "Tushare当日分钟为空") + "；"
        + (fallback_error or "东方财富备用源未返回有效数据")
        + "；"
        + (sina_error or "新浪财经备用源未返回有效数据"),
    )


def _morning_result_cache_key(
    limit: int,
    max_fetch: int,
    metadata: dict[str, Any],
    phase: str,
) -> tuple[int, int, str, str, str]:
    return (
        int(limit),
        int(max_fetch),
        str(metadata.get("candidate_trade_date") or ""),
        str(metadata.get("confirmation_trade_date") or ""),
        phase,
    )


def _follow_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    status_order = {
        "可以跟进": 0,
        "谨慎跟进": 1,
        "等待确认": 2,
        "等待9:35确认": 3,
        "明日观察": 4,
        "宽松观察": 5,
        "数据未就绪": 6,
        "放弃": 7,
    }
    return (
        status_order.get(str(row.get("follow_status")), 9),
        -float(row.get("follow_setup_score") or 0),
        -int(row.get("tail_condition_pass_count") or 0),
        -float(row.get("tail_close_position") or 0),
        -float(row.get("amount") or 0),
    )


def build_morning_follow_monitor(
    limit: int = 10,
    max_fetch: int = 30,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now()
    market, history, metadata = _load_follow_inputs(current)
    phase, should_refresh = _morning_follow_phase(
        current,
        metadata["candidate_trade_date"],
        metadata.get("confirmation_trade_date"),
    )
    cache_key = _morning_result_cache_key(
        limit,
        max_fetch,
        metadata,
        phase,
    )
    cached = _MORNING_FOLLOW_RESULT_CACHE.get(cache_key)
    cache_ttl = (
        _LIVE_RESULT_CACHE_TTL_SECONDS
        if should_refresh
        else _STATIC_RESULT_CACHE_TTL_SECONDS
    )
    if cached and time.monotonic() - cached[0] <= cache_ttl:
        result = _json_safe(cached[1])
        result["result_cache_hit"] = True
        return result

    leader_map = _leader_codes_from_sector_potential(market, history)
    leader_codes = set(leader_map)
    candidates = _daily_follow_candidates(
        market,
        history,
        metadata["candidate_trade_date"],
        leader_codes,
        max_fetch,
    )
    (
        bars_by_code,
        sector_representatives,
        sector_60m_bars,
        warnings,
    ) = _load_setup_bars(
        market,
        candidates,
        metadata["candidate_trade_date"],
        current,
    )
    sector_signals = _sector_60m_signal_from_bars(
        sector_representatives,
        sector_60m_bars,
    )

    setups: list[dict[str, Any]] = []
    for stock in candidates.to_dict("records"):
        ts_code = str(stock.get("ts_code") or "")
        try:
            setup = _setup_row(
                stock,
                bars_by_code.get(ts_code, {}),
                sector_signals.get(str(stock.get("industry") or ""), {}),
                leader_codes,
            )
        except Exception as exc:
            warnings.append(f"{ts_code} 前日观察评分失败: {exc}")
            continue
        if setup is not None:
            setups.append(setup)

    for setup in setups:
        try:
            (
                morning_bars,
                minute_source,
                minute_failure_reason,
            ) = _morning_bars_for_candidate(
                setup["ts_code"],
                metadata.get("confirmation_trade_date"),
                current,
            )
        except Exception as exc:
            warnings.append(
                f"{setup['ts_code']} 早盘确认数据加载失败: {exc}"
            )
            morning_bars = _empty_morning_bars()
            minute_source = "unavailable"
            minute_failure_reason = f"早盘确认数据加载失败: {exc}"
        setup["morning_minute_source"] = minute_source
        setup.update(_morning_confirmation(
            setup,
            morning_bars,
            current,
            metadata.get("confirmation_trade_date"),
            minute_failure_reason=minute_failure_reason,
        ))

    setups.sort(key=_follow_sort_key)
    stocks = [_json_safe(row) for row in setups[:max(1, int(limit))]]
    result = {
        **metadata,
        "market_phase": phase,
        "auto_refresh": should_refresh,
        "daily_rows": len(market),
        "daily_hard_filter_count": int(
            candidates.attrs.get("hard_filter_count", len(candidates))
        ),
        "setup_qualified_count": len(setups),
        "count": len(stocks),
        "stocks": stocks,
        "warnings": warnings,
        "result_cache_hit": False,
    }
    safe_result = _json_safe(result)
    _MORNING_FOLLOW_RESULT_CACHE[cache_key] = (
        time.monotonic(),
        safe_result,
    )
    return safe_result
