from __future__ import annotations

from datetime import datetime, timedelta
import math
import time
from typing import Any, Callable

import pandas as pd

from data_service import get_cached_scan_inputs, get_market_data, get_stock_minute_bars, get_trade_dates, sync_cached_market_data
from market_cache import load_market_snapshot, load_recent_daily
from realtime_market_source import MinuteLoadResult
from strategy import _macd_kdj_60m_signal, rank_sector_potential


_MINUTE_BAR_CACHE: dict[tuple[str, str, str, str], tuple[float, pd.DataFrame]] = {}
_FAILED_MINUTE_BAR_CACHE: dict[tuple[str, str, str, str], tuple[float, Exception]] = {}
_OVERNIGHT_RESULT_CACHE: dict[tuple[int, int, str], tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SECONDS = {"60min": 600, "1min": 25}
_FAILED_CACHE_TTL_SECONDS = 90
_RESULT_CACHE_TTL_SECONDS = 25


def _json_safe(value: Any) -> Any:
    """Convert pandas/numpy values and non-finite floats into JSON-safe values."""
    if value is None or value is pd.NA:
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat(sep=" ")
    if isinstance(value, bool):
        return value
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except ValueError:
            pass
    if isinstance(value, float):
        return round(value, 6) if math.isfinite(value) else None
    return value


def _cached_minute_bars(ts_code: str, start_datetime: str, end_datetime: str, freq: str) -> pd.DataFrame:
    key = (ts_code, start_datetime, end_datetime, freq)
    now = time.monotonic()
    ttl = _CACHE_TTL_SECONDS.get(freq, 30)
    cached = _MINUTE_BAR_CACHE.get(key)
    if cached and now - cached[0] <= ttl:
        return cached[1].copy()
    failed = _FAILED_MINUTE_BAR_CACHE.get(key)
    if failed and now - failed[0] <= _FAILED_CACHE_TTL_SECONDS:
        raise failed[1]

    try:
        bars = get_stock_minute_bars(ts_code, start_datetime, end_datetime, freq=freq)
    except Exception as exc:
        _FAILED_MINUTE_BAR_CACHE[key] = (now, exc)
        raise
    frame = bars.copy() if isinstance(bars, pd.DataFrame) else pd.DataFrame(bars)
    _MINUTE_BAR_CACHE[key] = (now, frame.copy())
    _FAILED_MINUTE_BAR_CACHE.pop(key, None)
    return frame


def _result_cache_key(limit: int, max_fetch: int, trade_date: str, now: datetime | None = None) -> tuple[int, int, str]:
    return (int(limit), int(max_fetch), _realtime_end_datetime(trade_date, now=now))


def _get_cached_result(key: tuple[int, int, str]) -> dict[str, Any] | None:
    cached = _OVERNIGHT_RESULT_CACHE.get(key)
    if not cached:
        return None
    age = time.monotonic() - cached[0]
    if age > _RESULT_CACHE_TTL_SECONDS:
        _OVERNIGHT_RESULT_CACHE.pop(key, None)
        return None
    result = _json_safe(cached[1])
    result["result_cache_hit"] = True
    return result


def _store_result_cache(key: tuple[int, int, str], result: dict[str, Any]) -> None:
    _OVERNIGHT_RESULT_CACHE[key] = (time.monotonic(), _json_safe(result))


def _preload_result_cache_key(limit: int, max_fetch: int, now: datetime | None = None) -> tuple[int, int, str]:
    current = now or datetime.now()
    return (int(limit), int(max_fetch), current.strftime("%Y-%m-%d %H:%M"))


def _market_phase(now: datetime | None = None) -> tuple[str, bool]:
    clock = (now or datetime.now()).strftime("%H:%M:%S")
    if clock >= "15:00:00":
        return "收盘结果", False
    if clock < "09:30:00":
        return "盘前", False
    return "尾盘盯盘", True


def _load_overnight_inputs(now: datetime | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    phase, should_refresh = _market_phase(now)
    if should_refresh:
        try:
            latest_trade_date = str(get_trade_dates(n=1)[0])
            sync_metadata = sync_cached_market_data(force_current=True)
            market = load_market_snapshot(latest_trade_date)
            if market is not None and not market.empty:
                history = load_recent_daily(latest_trade_date, 100)
                return market, history, {
                    **(sync_metadata or {}),
                    "data_trade_date": latest_trade_date,
                    "latest_trade_date": latest_trade_date,
                    "data_current": True,
                    "data_source": "current_snapshot",
                }
        except Exception as exc:
            print(f"隔夜溢价今日行情加载失败，回退最近完整日: {exc}")

    market, history, metadata = get_cached_scan_inputs(100)
    trade_date = str(metadata.get("data_trade_date") or "")
    latest_trade_date = trade_date
    try:
        latest_trade_date = str(get_trade_dates(n=1)[0])
    except Exception:
        pass
    return market, history, {
        **metadata,
        "data_trade_date": trade_date,
        "latest_trade_date": latest_trade_date,
        "data_current": trade_date == latest_trade_date,
        "data_source": "complete_cache",
    }


def _datetime_window(trade_date: str, start_clock: str, end_clock: str) -> tuple[str, str]:
    day = datetime.strptime(str(trade_date), "%Y%m%d")
    return day.strftime(f"%Y-%m-%d {start_clock}"), day.strftime(f"%Y-%m-%d {end_clock}")


def _realtime_end_datetime(trade_date: str, now: datetime | None = None, lag_minutes: int = 1) -> str:
    day = datetime.strptime(str(trade_date), "%Y%m%d")
    current = now or datetime.now()
    if current.strftime("%Y%m%d") != str(trade_date):
        return day.strftime("%Y-%m-%d 15:00:00")
    latest = current - timedelta(minutes=max(0, lag_minutes))
    clock = latest.strftime("%H:%M:%S")
    if clock < "09:30:00":
        return day.strftime("%Y-%m-%d 09:30:00")
    if clock > "15:00:00":
        return day.strftime("%Y-%m-%d 15:00:00")
    return latest.strftime("%Y-%m-%d %H:%M:00")


def _history_window(trade_date: str, lookback_days=70) -> tuple[str, str]:
    end_day = datetime.strptime(str(trade_date), "%Y%m%d")
    start_day = end_day - timedelta(days=lookback_days)
    return start_day.strftime("%Y-%m-%d 09:30:00"), end_day.strftime("%Y-%m-%d 15:00:00")


def _leader_codes_from_sector_potential(market: pd.DataFrame, history: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if market is None or market.empty or history is None:
        return {}
    try:
        sector_potential = rank_sector_potential(market, history, limit=50, leaders_per_sector=5)
    except Exception as exc:
        print(f"隔夜溢价龙头池生成失败: {exc}")
        return {}
    if sector_potential is None or sector_potential.empty or "leader_stocks" not in sector_potential.columns:
        return {}

    leaders: dict[str, dict[str, Any]] = {}
    for leaders_value in sector_potential["leader_stocks"]:
        if not isinstance(leaders_value, list):
            continue
        for item in leaders_value:
            if not isinstance(item, dict):
                continue
            ts_code = str(item.get("ts_code") or "")
            if ts_code:
                leaders[ts_code] = item
    return leaders


def _limit_leader_codes(leader_codes: dict[str, dict[str, Any]], max_leaders: int | None = None) -> dict[str, dict[str, Any]]:
    if max_leaders is None or max_leaders <= 0 or len(leader_codes) <= max_leaders:
        return leader_codes
    ordered = sorted(
        leader_codes.items(),
        key=lambda item: float((item[1] or {}).get("leader_score") or 0),
        reverse=True,
    )
    return dict(ordered[:max_leaders])


def _candidate_universe(market: pd.DataFrame, max_fetch: int, leader_codes: dict[str, dict[str, Any]] | None = None) -> pd.DataFrame:
    data = market.copy()
    for column in ["pct_chg", "turnover_rate", "volume_ratio", "amount", "close"]:
        data[column] = pd.to_numeric(data[column], errors="coerce") if column in data else 0
    base = data[
        data["pct_chg"].between(2, 8.5, inclusive="both")
        & data["turnover_rate"].between(2, 15, inclusive="both")
        & (data["volume_ratio"] > 1.3)
        & (data["amount"] >= 100_000)
    ].copy()
    if not base.empty:
        base["prefilter_score"] = (
            base["pct_chg"].clip(0, 8.5) * 4
            + base["volume_ratio"].clip(0, 4) * 8
            + (15 - (base["turnover_rate"] - 7).abs()).clip(lower=0) * 1.2
            + (base["amount"] / 100_000).clip(0, 10)
        )
        base = base.sort_values("prefilter_score", ascending=False).head(max_fetch)
    else:
        base = data.iloc[0:0].copy()
        base["prefilter_score"] = pd.Series(dtype="float64")
    base["overnight_pool_source"] = "常规"
    base["overnight_sector_leader"] = False

    leader_codes = leader_codes or {}
    leaders = data[data["ts_code"].astype(str).isin(leader_codes.keys())].copy()
    if not leaders.empty:
        leaders["prefilter_score"] = (
            leaders["pct_chg"].clip(-3, 8.5) * 3
            + leaders["volume_ratio"].clip(0, 4) * 6
            + leaders["turnover_rate"].clip(0, 15) * 0.8
            + (leaders["amount"] / 100_000).clip(0, 10)
        )
        leaders["overnight_pool_source"] = "龙头"
        leaders["overnight_sector_leader"] = True
        leaders["sector_leader_score"] = leaders["ts_code"].astype(str).map(
            lambda code: leader_codes.get(code, {}).get("leader_score")
        )
    result = pd.concat([base, leaders], ignore_index=True) if not leaders.empty else base
    if result.empty:
        return result
    result["overnight_sector_leader"] = result.groupby("ts_code")["overnight_sector_leader"].transform("max").astype(bool)
    result["overnight_pool_source"] = result.groupby("ts_code")["overnight_pool_source"].transform(
        lambda values: "龙头" if "龙头" in set(values) else "常规"
    )
    return result.drop_duplicates(subset=["ts_code"], keep="last").sort_values("prefilter_score", ascending=False).reset_index(drop=True)


def _sector_representative_universe(market: pd.DataFrame, candidates: pd.DataFrame, per_sector: int = 5) -> pd.DataFrame:
    if market is None or market.empty or candidates is None or candidates.empty:
        return pd.DataFrame()
    if "industry" not in market.columns or "industry" not in candidates.columns or "ts_code" not in market.columns:
        return pd.DataFrame()
    industries = {str(item) for item in candidates["industry"].dropna().unique().tolist() if str(item)}
    if not industries:
        return pd.DataFrame()

    data = market[market["industry"].astype(str).isin(industries)].copy()
    if data.empty:
        return data
    for column in ["amount", "volume_ratio", "pct_chg"]:
        data[column] = pd.to_numeric(data[column], errors="coerce") if column in data else 0
    data["_sector_rep_score"] = (
        (data["amount"] / 100_000).clip(0, 12)
        + data["volume_ratio"].clip(0, 5) * 4
        + data["pct_chg"].clip(-3, 10) * 1.5
    )
    top_reps = data.sort_values("_sector_rep_score", ascending=False).groupby("industry", group_keys=False).head(per_sector)
    candidate_rows = data[data["ts_code"].astype(str).isin(candidates["ts_code"].astype(str))]
    return (
        pd.concat([top_reps, candidate_rows], ignore_index=True)
        .drop_duplicates(subset=["ts_code"], keep="first")
        .drop(columns=["_sector_rep_score"], errors="ignore")
        .reset_index(drop=True)
    )


def _sector_60m_signal_from_bars(market: pd.DataFrame, bars_by_code: dict[str, pd.DataFrame]) -> dict[str, dict[str, Any]]:
    if market is None or market.empty or not bars_by_code:
        return {}
    if "industry" not in market.columns or "ts_code" not in market.columns:
        return {}

    industry_map = market.dropna(subset=["industry"]).assign(ts_code=lambda df: df["ts_code"].astype(str)).drop_duplicates("ts_code").set_index("ts_code")["industry"].to_dict()
    normalized_frames = []
    for ts_code, frame in bars_by_code.items():
        industry = industry_map.get(str(ts_code))
        if not industry or frame is None or frame.empty or "trade_time" not in frame.columns or "close" not in frame.columns:
            continue
        bars = frame.copy().sort_values("trade_time").reset_index(drop=True)
        for column in ["open", "high", "low", "close"]:
            bars[column] = pd.to_numeric(bars[column], errors="coerce") if column in bars else pd.NA
        bars = bars.dropna(subset=["trade_time", "close"])
        if len(bars) < 35:
            continue
        base_close = float(bars["close"].iloc[0] or 0)
        if not base_close:
            continue
        scale = 100 / base_close
        normalized_frames.append(pd.DataFrame({
            "industry": str(industry),
            "trade_time": bars["trade_time"].astype(str),
            "close": bars["close"] * scale,
            "high": bars["high"].fillna(bars["close"]) * scale,
            "low": bars["low"].fillna(bars["close"]) * scale,
        }))
    if not normalized_frames:
        return {}

    sector_bars = (
        pd.concat(normalized_frames, ignore_index=True)
        .groupby(["industry", "trade_time"])
        .agg(close=("close", "mean"), high=("high", "mean"), low=("low", "mean"))
        .reset_index()
    )
    result: dict[str, dict[str, Any]] = {}
    for industry, group in sector_bars.groupby("industry"):
        group = group.sort_values("trade_time")
        if len(group) < 20:
            continue
        close = group["close"]
        high = group["high"]
        low = group["low"]
        dif = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
        dea = dif.ewm(span=9, adjust=False).mean()
        macd = (dif - dea) * 2
        low9 = low.rolling(9, min_periods=9).min()
        high9 = high.rolling(9, min_periods=9).max()
        rsv = ((close - low9) / (high9 - low9) * 100).where(high9 != low9, 50)
        k = rsv.ewm(com=2, adjust=False, min_periods=1).mean()
        d = k.ewm(com=2, adjust=False, min_periods=1).mean()
        j = 3 * k - 2 * d
        if any(pd.isna(value) for value in (macd.iloc[-1], macd.iloc[-2], macd.iloc[-3], dif.iloc[-1], dea.iloc[-1], dif.iloc[-2], dea.iloc[-2], k.iloc[-1], d.iloc[-1], k.iloc[-2], d.iloc[-2])):
            continue
        golden_cross = bool(dif.iloc[-1] > dea.iloc[-1] and dif.iloc[-2] <= dea.iloc[-2])
        above_zero = bool(dif.iloc[-1] > 0 and dea.iloc[-1] > 0)
        trending_up = bool(macd.iloc[-1] > macd.iloc[-2] or (dif.iloc[-1] > dea.iloc[-1] and dif.iloc[-1] > dif.iloc[-2]))
        macd_down = bool((dif.iloc[-1] < dea.iloc[-1] and macd.iloc[-1] < macd.iloc[-2]) or (macd.iloc[-1] < macd.iloc[-2] < macd.iloc[-3]))
        water_golden_cross = bool(golden_cross and above_zero)
        kdj_dead_cross = bool(k.iloc[-1] < d.iloc[-1] and k.iloc[-2] >= d.iloc[-2])
        kdj_down = bool(k.iloc[-1] < k.iloc[-2] and d.iloc[-1] <= d.iloc[-2] and j.iloc[-1] < j.iloc[-2])
        excluded = bool(macd_down or kdj_dead_cross or kdj_down)
        bonus = (6 if trending_up else 0) + (8 if golden_cross else 0) + (10 if water_golden_cross else 0)
        if excluded:
            status = "板块60分MACD/KDJ向下"
            bonus = 0
        elif water_golden_cross:
            status = "板块60分MACD水上金叉"
        elif golden_cross:
            status = "板块60分MACD金叉"
        elif above_zero and trending_up:
            status = "板块60分MACD水上走强"
        elif trending_up:
            status = "板块60分MACD趋势向上"
        else:
            status = "板块60分MACD未确认"
        result[str(industry)] = {
            "sector_macd_dif": round(float(dif.iloc[-1]), 6),
            "sector_macd_dea": round(float(dea.iloc[-1]), 6),
            "sector_macd": round(float(macd.iloc[-1]), 6),
            "sector_macd_trending_up": trending_up,
            "sector_macd_golden_cross": golden_cross,
            "sector_macd_above_zero": above_zero,
            "sector_macd_water_golden_cross": water_golden_cross,
            "sector_macd_down_60m": macd_down,
            "sector_kdj_k_60m": round(float(k.iloc[-1]), 6),
            "sector_kdj_d_60m": round(float(d.iloc[-1]), 6),
            "sector_kdj_j_60m": round(float(j.iloc[-1]), 6),
            "sector_kdj_dead_cross_60m": kdj_dead_cross,
            "sector_kdj_down_60m": kdj_down,
            "sector_60m_excluded": excluded,
            "sector_macd_bonus": bonus,
            "sector_macd_status": status,
        }
    return result


def _mask_unavailable_tail_fields(signal: dict[str, Any], end_datetime: str) -> dict[str, Any]:
    result = dict(signal)
    clock = str(end_datetime)[-8:]
    tail_available = clock > "14:30:00"
    auction_available = clock >= "09:30:00"
    result["tail_after_1430_available"] = tail_available
    result["tail_auction_available"] = auction_available
    if not tail_available:
        for key in ("tail_strength_score", "tail_return_after_1430", "tail_volume_ratio", "tail_close_position"):
            result[key] = None
    if not auction_available:
        result["tail_auction_return"] = None
    return result


def _numeric_or_none(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _opening_auction_signal(stock: dict[str, Any], end_datetime: str) -> dict[str, Any]:
    clock = str(end_datetime)[-8:]
    available = clock >= "09:30:00"
    empty = {
        "opening_auction_return": None,
        "tail_auction_return": None,
        "tail_auction_available": available,
    }
    if not available:
        return empty

    open_price = _numeric_or_none(stock.get("open"))
    previous_close = (
        _numeric_or_none(stock.get("pre_close"))
        or _numeric_or_none(stock.get("previous_close"))
    )
    if previous_close is None:
        close = _numeric_or_none(stock.get("close"))
        pct_chg = _numeric_or_none(stock.get("pct_chg"))
        if close is not None and pct_chg is not None and pct_chg > -99:
            previous_close = close / (1 + pct_chg / 100)
    if open_price is None or previous_close is None or previous_close == 0:
        return empty

    auction_return = round((open_price / previous_close - 1) * 100, 6)
    return {
        "opening_auction_return": auction_return,
        "tail_auction_return": auction_return,
        "tail_auction_available": True,
    }


def _overnight_labels(signal: dict[str, Any], pct_chg: float, volume_ratio: float | None = None) -> tuple[str, str, str, float]:
    if not signal.get("tail_after_1430_available", True):
        return "盘中观察", "观察", "未到14:30，暂不判断尾盘承接", 0
    if not signal.get("tail_auction_available", True):
        return "盘中观察", "观察", "未到9:30，暂不判断早盘集合竞价", 0

    tail_score = float(signal.get("tail_strength_score") or 50)
    tail_return = float(signal.get("tail_return_after_1430") or 0)
    auction_return = float(signal.get("tail_auction_return") or 0)
    close_position = float(signal.get("tail_close_position") or 0)
    volume_ratio = float(volume_ratio or 0)
    macd_bonus = 12 if signal.get("macd_above_zero_60m") else 8 if signal.get("macd_bullish_60m") else 0
    kdj_bonus = 8 if signal.get("kdj_bullish_60m") or signal.get("kdj_recent_golden_cross_60m") else 0
    heat_penalty = max(0, pct_chg - 7.5) * 6
    overextended_tail = tail_return > 1.5 and auction_return < 0.15
    hot_volume = volume_ratio > 3.8
    mild_tail_overdraft = 1.35 < tail_return <= 2.2
    overextension_penalty = (18 if overextended_tail else 0) + (12 if hot_volume else 0) + (8 if mild_tail_overdraft else 0)
    sector_macd_bonus = float(signal.get("sector_macd_bonus") or 0)
    sector_macd_status = str(signal.get("sector_macd_status") or "")
    score = max(
        0,
        min(
            100,
            tail_score * 0.58
            + macd_bonus
            + kdj_bonus
            + max(0, 8.0 - pct_chg) * 1.2
            + max(0, min(auction_return, 0.6)) * 8
            + sector_macd_bonus
            - heat_penalty
            - overextension_penalty,
        ),
    )

    if signal.get("next_day_bias") == "低开风险" or tail_return <= -0.5:
        return "低开风险", "不买", "尾盘回落，隔夜不占优", round(score, 2)
    if overextended_tail or hot_volume:
        return "尾盘透支风险", "观察", "尾盘拉升偏急或量比过热，等待次日承接确认", round(score, 2)
    if mild_tail_overdraft:
        return "早盘冲高套利", "轻仓观察", "尾盘已有透支，次日冲高优先兑现", round(score, 2)
    if score >= 82 and 0.35 <= tail_return <= 1.35 and auction_return >= 0.12 and close_position >= 0.85 and pct_chg <= 6.5 and volume_ratio <= 3.2:
        reason = "尾盘温和抢筹且集合竞价继续确认"
        if sector_macd_status and sector_macd_status != "板块MACD未确认":
            reason = f"{reason}，{sector_macd_status}加分"
        return "隔夜高开优先", "尾盘可买", reason, round(score, 2)
    if score >= 62 and auction_return >= 0:
        return "早盘冲高套利", "轻仓观察", "尾盘承接尚可，次日冲高兑现", round(score, 2)
    return "尾盘抢筹观察", "观察", "信号不够集中，等待临近收盘确认", round(score, 2)


def _build_row(
    stock: dict[str, Any],
    trade_date: str,
    sector_macd_map: dict[str, dict[str, Any]] | None = None,
    end_datetime: str | None = None,
    minute_loader: Callable[..., MinuteLoadResult] | None = None,
    loaded_60m: MinuteLoadResult | None = None,
) -> dict[str, Any] | None:
    ts_code = str(stock.get("ts_code") or "")
    if not ts_code:
        return None
    start_60m, default_end_60m = _history_window(trade_date)
    end_60m = end_datetime or default_end_60m
    tail_start, default_tail_end = _datetime_window(trade_date, "14:25:00", "15:00:00")
    tail_end = end_datetime or default_tail_end
    if minute_loader:
        current_60m = loaded_60m or minute_loader(
            ts_code, start_60m, end_60m, "60min", trade_date
        )
        loaded_1m = minute_loader(
            ts_code, tail_start, tail_end, "1min", trade_date
        )
    else:
        current_60m = loaded_60m or MinuteLoadResult(
            _cached_minute_bars(ts_code, start_60m, end_60m, freq="60min"),
            "tushare", [],
        )
        loaded_1m = MinuteLoadResult(
            _cached_minute_bars(
                ts_code, tail_start, tail_end, freq="1min"
            ),
            "tushare",
            [],
        )
    bars_60m, tail_1m = current_60m.bars, loaded_1m.bars
    sector_signal = (sector_macd_map or {}).get(str(stock.get("industry") or ""), {})
    if sector_signal.get("sector_60m_excluded"):
        return None
    signal = _macd_kdj_60m_signal(pd.Series(stock), {"60m": bars_60m, "tail_1m": tail_1m})
    if not signal:
        return None
    signal = {**signal, **sector_signal}
    signal.update(_opening_auction_signal(stock, tail_end))
    signal = _mask_unavailable_tail_fields(signal, tail_end)
    pct_chg = float(stock.get("pct_chg") or 0)
    volume_ratio = float(stock.get("volume_ratio") or 0)
    overnight_bias, buyable_tail_signal, overnight_reason, overnight_score = _overnight_labels(signal, pct_chg, volume_ratio=volume_ratio)
    if buyable_tail_signal == "不买":
        return None
    return {
        **stock,
        **signal,
        "overnight_bias": overnight_bias,
        "buyable_tail_signal": buyable_tail_signal,
        "overnight_reason": overnight_reason,
        "overnight_candidate_score": overnight_score,
        "overheat_risk": "偏高" if pct_chg >= 7.5 else "正常",
        "next_morning_sell_plan": "高开3%以上先卖一半，冲高不封板逐步兑现；低开破分时均价止损",
        "minute_data_source": (
            loaded_1m.source if not tail_1m.empty else current_60m.source
        ),
        "minute_data_warnings": list(dict.fromkeys(
            current_60m.warnings + loaded_1m.warnings
        )),
    }


def build_overnight_monitor(
    limit: int = 10,
    max_fetch: int = 30,
    max_leaders: int | None = None,
    now: datetime | None = None,
    *,
    market_override: pd.DataFrame | None = None,
    history_override: pd.DataFrame | None = None,
    trade_date_override: str | None = None,
    minute_loader: Callable[..., MinuteLoadResult] | None = None,
    source_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    use_override = market_override is not None and bool(trade_date_override)
    preload_cache_key = _preload_result_cache_key(limit, max_fetch, now=now)
    if not use_override:
        preload_cached_result = _get_cached_result(preload_cache_key)
        if preload_cached_result is not None:
            return preload_cached_result

    phase, should_refresh = _market_phase(now)
    if use_override:
        market = market_override.copy()
        history = (
            history_override.copy()
            if isinstance(history_override, pd.DataFrame)
            else pd.DataFrame()
        )
        trade_date = str(trade_date_override)
        metadata = {
            "data_trade_date": trade_date,
            "latest_trade_date": trade_date,
            "data_current": True,
            "data_source": "runtime_override",
            **(source_metadata or {}),
        }
    else:
        try:
            market, history, metadata = _load_overnight_inputs(now=now)
            trade_date = str(metadata.get("data_trade_date") or "")
        except Exception:
            market, trade_date = get_market_data()
            history = pd.DataFrame()
            metadata = {"data_trade_date": str(trade_date), "latest_trade_date": str(trade_date), "data_current": True, "data_source": "direct_tushare"}
            trade_date = str(trade_date)

    cache_key = _result_cache_key(limit, max_fetch, trade_date, now=now)
    if not use_override:
        cached_result = _get_cached_result(cache_key)
        if cached_result is not None:
            return cached_result

    leader_codes = _limit_leader_codes(_leader_codes_from_sector_potential(market, history), max_leaders=max_leaders)
    candidates = _candidate_universe(market, max_fetch=max_fetch, leader_codes=leader_codes)
    sector_representatives = _sector_representative_universe(market, candidates, per_sector=2)
    start_60m, _default_end_60m = _history_window(trade_date)
    end_60m = _realtime_end_datetime(trade_date, now=now)
    bars_by_code = {}
    loaded_60m_by_code: dict[str, MinuteLoadResult] = {}
    minute_sources = set()
    rows = []
    warnings = []
    for stock in sector_representatives.to_dict("records"):
        ts_code = str(stock.get("ts_code") or "")
        if not ts_code:
            continue
        try:
            if minute_loader:
                loaded = minute_loader(
                    ts_code, start_60m, end_60m, "60min", trade_date
                )
                bars_by_code[ts_code] = loaded.bars
                loaded_60m_by_code[ts_code] = loaded
                minute_sources.add(loaded.source)
                warnings.extend(loaded.warnings)
            else:
                bars = _cached_minute_bars(
                    ts_code, start_60m, end_60m, freq="60min"
                )
                bars_by_code[ts_code] = bars
                loaded_60m_by_code[ts_code] = MinuteLoadResult(
                    bars, "tushare", []
                )
        except Exception as exc:
            warning = f"隔夜溢价候选60分钟更新失败 {stock.get('ts_code')}: {exc}"
            print(warning)
            warnings.append(warning)

    sector_macd_map = _sector_60m_signal_from_bars(sector_representatives, bars_by_code)
    for stock in candidates.to_dict("records"):
        ts_code = str(stock.get("ts_code") or "")
        if ts_code not in bars_by_code:
            continue
        try:
            row = _build_row(
                stock, trade_date, sector_macd_map=sector_macd_map,
                end_datetime=end_60m, minute_loader=minute_loader,
                loaded_60m=loaded_60m_by_code.get(ts_code),
            )
        except Exception as exc:
            warning = f"隔夜溢价候选更新失败 {stock.get('ts_code')}: {exc}"
            print(warning)
            warnings.append(warning)
            row = None
        if row:
            rows.append(row)
            minute_sources.add(str(row.get("minute_data_source") or ""))

    rows = sorted(
        rows,
        key=lambda item: (
            item.get("buyable_tail_signal") == "尾盘可买",
            item.get("overnight_sector_leader") is True,
            float(item.get("overnight_candidate_score") or 0),
            float(item.get("tail_strength_score") or 0),
        ),
        reverse=True,
    )[: max(1, min(int(limit), 100))]
    result = _json_safe({
        "trade_date": trade_date,
        "latest_trade_date": metadata.get("latest_trade_date", trade_date),
        "data_current": metadata.get("data_current", True),
        "data_source": metadata.get("data_source"),
        "market_phase": phase,
        "auto_refresh_enabled": should_refresh,
        "updated_at": (now or datetime.now()).isoformat(sep=" ", timespec="seconds"),
        "refresh_interval_seconds": 30,
        "candidate_count": int(len(candidates)),
        "failed_count": int(len(warnings)),
        "warnings": warnings[:20],
        "result_cache_hit": False,
        "stocks": rows,
        "minute_data_sources": sorted(source for source in minute_sources if source),
    })
    if not use_override:
        _store_result_cache(preload_cache_key, result)
        _store_result_cache(cache_key, result)
    return result
