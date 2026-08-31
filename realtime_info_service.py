from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import threading
import time
from typing import Any, Callable

import pandas as pd

from chip_peak_service import attach_chip_peak_fields, clear_chip_peak_cache
from data_service import get_trade_dates, sync_cached_market_data
from indicator_settings import macd_parameter_key, macd_provenance
from intraday_monitor_service import _main_force_status, _market_phase
from market_cache import get_complete_dates, load_market_snapshot, load_recent_daily
from realtime_market_source import (
    MinuteLoadResult,
    invalidate_realtime_minute_cache,
    load_eastmoney_market_snapshot,
    load_minutes_with_fallback,
)
from realtime_cache import (
    load_minute_cache,
    load_result_cache,
    minute_cache_next_fetch_start,
    minute_cache_is_fresh,
    prune_realtime_cache,
    save_minute_cache,
    save_result_cache,
)
from overnight_monitor_service import (
    _cached_minute_bars,
    _datetime_window,
    _history_window,
    _json_safe,
    _leader_codes_from_ranked_sector_potential,
    _mask_unavailable_tail_fields,
    _realtime_end_datetime,
)
from realtime_tail_premium_service import (
    build_realtime_tail_premium_monitor,
)
from position_candidate_scoring import (
    position_score_version,
    rank_scored_position_candidates,
    score_position_candidate,
)
from position_candidate_history import (
    extract_limit_gene,
    extract_pullback_confirmation,
    extract_resonance_events,
)
from strategy import (
    _attach_intraday_signal_stocks,
    _is_mainboard_a_stock,
    _macd_kdj_60m_signal,
    rank_sector_potential,
)


_REALTIME_SECTOR_LIMIT = 8
_REALTIME_CANDIDATES_PER_SECTOR = 6
_REALTIME_PICKS_PER_SECTOR = 5
_REALTIME_TAIL_CANDIDATE_LIMIT = 15
POSITION_ENRICHMENT_LIMIT = 40
_REALTIME_OVERNIGHT_MAX_FETCH = 30
_REALTIME_OVERNIGHT_MAX_LEADERS = 15
_REALTIME_INTRADAY_CACHE_TTL_SECONDS = 58
_REALTIME_MARKET_RELATIVE_RULE_VERSION = "market-relative-v1"
_REALTIME_HISTORICAL_RESILIENCE_RULE_VERSION = "historical-resilience-v1"
_REALTIME_BOTTOM_CONSOLIDATION_RULE_VERSION = "bottom-consolidation-v2"
_REALTIME_CHIP_PEAK_RULE_VERSION = "chip-peak-washout-v1"
_MARKET_RELATIVE_UP_THRESHOLD = 0.3
_MARKET_RELATIVE_DOWN_THRESHOLD = -0.3
_MARKET_RELATIVE_MIN_SAMPLE_COUNT = 20
_HISTORICAL_RESILIENCE_WINDOW = 20
_HISTORICAL_RESILIENCE_MIN_SAMPLE_COUNT = 5
_REALTIME_OUTPUT_EXCLUDE_PREFIXES = ("3", "8", "9", "688", "689")
_REALTIME_INTRADAY_RESULT_CACHE: dict[tuple, tuple[float, dict[str, Any]]] = {}
_REALTIME_RESULT_CACHE: dict[tuple, dict[str, Any]] = {}
_LAST_SUCCESSFUL_REALTIME_RESULTS: dict[tuple, dict[str, Any]] = {}
_REALTIME_RESULT_LOCK = threading.Lock()
_POSITION_HISTORY_FEATURE_CACHE: dict[tuple[str, str, str], dict[str, Any]] = {}


def clear_realtime_derived_caches() -> None:
    with _REALTIME_RESULT_LOCK:
        _REALTIME_RESULT_CACHE.clear()
        _LAST_SUCCESSFUL_REALTIME_RESULTS.clear()
        _REALTIME_INTRADAY_RESULT_CACHE.clear()
    clear_chip_peak_cache()


def _clear_realtime_result_caches() -> None:
    clear_realtime_derived_caches()


def _realtime_output_allowed(ts_code: Any) -> bool:
    code = str(ts_code or "")
    return bool(code) and not code.startswith(_REALTIME_OUTPUT_EXCLUDE_PREFIXES)


def _build_history_position_pool(
    market: pd.DataFrame,
    history: pd.DataFrame,
    trade_date: str,
) -> tuple[list[dict[str, Any]], dict[str, int], list[str]]:
    debug = {
        "source_main_board": 0,
        "no_limit_gene": 0,
        "history_insufficient": 0,
        "no_recent_resonance": 0,
        "support_broken": 0,
    }
    warnings: list[str] = []
    if market is None or market.empty or "ts_code" not in market.columns:
        return [], debug, warnings
    market_records = market.to_dict("records")
    mainboard_records = [
        snapshot for snapshot in market_records
        if str(snapshot.get("ts_code") or "").endswith((".SH", ".SZ"))
        and not str(snapshot.get("ts_code") or "").startswith(
            _REALTIME_OUTPUT_EXCLUDE_PREFIXES
        )
        and "ST" not in str(snapshot.get("name") or "").upper()
        and "退市" not in str(snapshot.get("name") or "").upper()
    ]
    debug["source_main_board"] = len(mainboard_records)
    eligible_codes: set[str] | None = None
    if (
        history is not None and not history.empty
        and {"ts_code", "trade_date"}.issubset(history.columns)
        and ("pct_chg" in history.columns or "limit_flag" in history.columns)
    ):
        prior = history[
            history["trade_date"].astype(str) < str(trade_date)
        ].copy()
        prior = prior.sort_values(["ts_code", "trade_date"])
        recent_ten = prior.groupby("ts_code", group_keys=False).tail(10)
        pct = pd.to_numeric(
            recent_ten.get(
                "pct_chg", pd.Series(pd.NA, index=recent_ten.index)
            ),
            errors="coerce",
        )
        limit_mask = pct.ge(9.5).fillna(False)
        if "limit_flag" in recent_ten.columns:
            limit_mask |= recent_ten["limit_flag"].astype(str).str.lower().isin(
                {"1", "true", "yes", "是", "涨停"}
            )
        eligible_codes = set(
            recent_ten.loc[limit_mask, "ts_code"].astype(str)
        )
        main_codes = {str(row.get("ts_code") or "") for row in mainboard_records}
        debug["no_limit_gene"] = len(main_codes - eligible_codes)
    grouped = {
        str(code): bars.copy()
        for code, bars in (
            history[
                history["ts_code"].astype(str).isin(eligible_codes)
            ].groupby("ts_code")
            if history is not None and not history.empty and "ts_code" in history.columns
            and eligible_codes is not None
            else history.groupby("ts_code")
            if history is not None and not history.empty and "ts_code" in history.columns
            else []
        )
    }
    rows: list[dict[str, Any]] = []
    for snapshot in mainboard_records:
        code = str(snapshot.get("ts_code") or "")
        if eligible_codes is not None and code not in eligible_codes:
            continue
        bars = grouped.get(code, pd.DataFrame())
        cache_key = (code, str(trade_date), position_score_version())
        try:
            features = _POSITION_HISTORY_FEATURE_CACHE.get(cache_key)
            if features is None:
                gene = extract_limit_gene(bars, trade_date)
                resonance = extract_resonance_events(bars, trade_date)
                features = {**gene, **resonance}
                _POSITION_HISTORY_FEATURE_CACHE[cache_key] = dict(features)
            else:
                features = dict(features)
            if not features.get("limit_history_sufficient"):
                debug["history_insufficient"] += 1
                continue
            if not features.get("limit_gene_eligible"):
                if eligible_codes is None:
                    debug["no_limit_gene"] += 1
                continue
            if not features.get("resonance_events"):
                debug["no_recent_resonance"] += 1
                continue
            pullback = extract_pullback_confirmation(
                bars,
                features,
                {
                    **snapshot,
                    "current_price": snapshot.get("current_price", snapshot.get("close")),
                    "trade_date": trade_date,
                },
            )
            if not pullback.get("support_held"):
                debug["support_broken"] += 1
                continue
            rows.append({**snapshot, **features, **pullback})
        except Exception as exc:
            warnings.append(f"{code} 历史建仓特征计算失败: {str(exc)[:120]}")
    return rows, debug, warnings


def _overnight_pct_allowed(value: Any) -> bool:
    try:
        pct = float(value)
    except (TypeError, ValueError):
        return False
    return 2 <= pct <= 7


def _fallback_market_relative_benchmark() -> dict[str, Any]:
    return {
        "market_pct_chg": None,
        "market_state": "fallback",
        "market_state_label": "大盘不可用",
        "sample_count": 0,
    }


def _build_market_relative_benchmark(market: pd.DataFrame) -> dict[str, Any]:
    if market is None or market.empty or "ts_code" not in market.columns:
        return _fallback_market_relative_benchmark()
    data = market.copy()
    data["ts_code"] = data["ts_code"].astype(str)
    allowed = _is_mainboard_a_stock(data["ts_code"]) & data["ts_code"].apply(
        _realtime_output_allowed
    )
    if "name" in data.columns:
        allowed = allowed & ~data["name"].astype(str).str.upper().str.contains("ST")
    data = data[allowed].copy()
    if "pct_chg" not in data.columns:
        return _fallback_market_relative_benchmark()
    data["pct_chg"] = pd.to_numeric(data["pct_chg"], errors="coerce")
    pct = data["pct_chg"].dropna()
    if pct.empty:
        return _fallback_market_relative_benchmark()
    market_pct = round(float(pct.mean()), 6)
    if market_pct >= _MARKET_RELATIVE_UP_THRESHOLD:
        state, label = "up", "大盘上涨"
    elif market_pct <= _MARKET_RELATIVE_DOWN_THRESHOLD:
        state, label = "down", "大盘下跌"
    else:
        state, label = "flat", "大盘震荡"
    return {
        "market_pct_chg": market_pct,
        "market_state": state,
        "market_state_label": label,
        "sample_count": int(len(pct)),
    }


def _safe_market_relative_benchmark(market: pd.DataFrame) -> dict[str, Any]:
    try:
        return _build_market_relative_benchmark(market)
    except Exception:
        return _fallback_market_relative_benchmark()


def _market_relative_label(state: str) -> str:
    return {
        "up": "强于大盘",
        "flat": "震荡走强",
        "down": "逆势抗跌",
    }.get(str(state), "原规则")


def _market_relative_reason(stock_pct: Any, benchmark: dict[str, Any]) -> str:
    market_pct = benchmark.get("market_pct_chg")
    try:
        stock_value = float(stock_pct)
        market_value = float(market_pct)
    except (TypeError, ValueError):
        return "大盘基准不可用，沿用原实时涨幅规则"
    relative = stock_value - market_value
    return (
        f"大盘 {market_value:.2f}%，个股 {stock_value:.2f}%，"
        f"相对强 {relative:.2f}pct"
    )


def _numeric_value(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return default if pd.isna(result) else result


def _market_relative_score(row: pd.Series) -> float:
    relative = _numeric_value(row.get("relative_strength"))
    stock_pct = _numeric_value(row.get("pct_chg"))
    volume_ratio = _numeric_value(row.get("volume_ratio"))
    turnover = _numeric_value(row.get("turnover_rate"))
    return round(
        relative * 40
        + max(stock_pct, 0) * 10
        + min(max(volume_ratio, 0), 4) * 8
        + (10 if 2 <= turnover <= 8 else 0),
        2,
    )


def _empty_historical_resilience_fields(reason: str = "近20日有效日线不足") -> dict[str, Any]:
    return {
        "historical_resilience_score": pd.NA,
        "historical_resilience_label": "历史不足",
        "historical_resilience_reason": reason,
        "historical_resilience_weighted_relative": pd.NA,
        "historical_resilience_down_relative": pd.NA,
        "historical_resilience_beat_ratio": pd.NA,
        "historical_resilience_sample_count": 0,
    }


def _historical_resilience_label(score: float) -> str:
    if score >= 80:
        return "强抗跌"
    if score >= 65:
        return "抗跌"
    if score >= 50:
        return "一般"
    return "偏弱"


def _signed_pct_text(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "--"
    if pd.isna(number):
        return "--"
    return f"{number:+.2f}pct"


def _build_historical_resilience_lookup(
    history: pd.DataFrame,
    trade_date: str,
) -> dict[str, dict[str, Any]]:
    required = {"ts_code", "trade_date", "pct_chg"}
    if history is None or history.empty or not required.issubset(history.columns):
        return {}
    hist = history.copy()
    hist["ts_code"] = hist["ts_code"].astype(str)
    hist["trade_date"] = hist["trade_date"].astype(str)
    hist["pct_chg"] = pd.to_numeric(hist["pct_chg"], errors="coerce")
    allowed = _is_mainboard_a_stock(hist["ts_code"]) & hist["ts_code"].apply(
        _realtime_output_allowed
    )
    if "name" in hist.columns:
        allowed = allowed & ~hist["name"].astype(str).str.upper().str.contains("ST")
    hist = hist[allowed].dropna(subset=["pct_chg", "trade_date"]).copy()
    if hist.empty:
        return {}
    prior = hist[hist["trade_date"] < str(trade_date)].copy()
    if not prior.empty:
        hist = prior
    market_by_date = (
        hist.groupby("trade_date", as_index=False)["pct_chg"]
        .mean()
        .rename(columns={"pct_chg": "historical_market_pct_chg"})
    )
    hist = hist.merge(market_by_date, on="trade_date", how="inner")
    hist["historical_relative"] = (
        hist["pct_chg"] - hist["historical_market_pct_chg"]
    )
    lookup: dict[str, dict[str, Any]] = {}
    for ts_code, group in hist.groupby("ts_code"):
        recent = (
            group.sort_values("trade_date", kind="mergesort")
            .tail(_HISTORICAL_RESILIENCE_WINDOW)
            .dropna(subset=["historical_relative"])
            .copy()
        )
        sample_count = int(len(recent))
        if sample_count < _HISTORICAL_RESILIENCE_MIN_SAMPLE_COUNT:
            lookup[str(ts_code)] = _empty_historical_resilience_fields()
            continue
        weights = pd.Series(
            range(1, sample_count + 1),
            index=recent.index,
            dtype="float64",
        )
        relative = recent["historical_relative"].astype(float)
        weighted_relative = float((relative * weights).sum() / weights.sum())
        beat_ratio = float(((relative > 0).astype(float) * weights).sum() / weights.sum())
        down = recent[recent["historical_market_pct_chg"] <= _MARKET_RELATIVE_DOWN_THRESHOLD]
        if down.empty:
            down_relative = None
            down_score_component = weighted_relative
            down_text = "下跌日样本不足"
        else:
            down_weights = weights.loc[down.index]
            down_relative = float(
                (down["historical_relative"].astype(float) * down_weights).sum()
                / down_weights.sum()
            )
            down_score_component = down_relative
            down_text = f"下跌日跑赢 {_signed_pct_text(down_relative)}"
        score = 50 + weighted_relative * 8 + down_score_component * 10 + (beat_ratio - 0.5) * 30
        score = round(float(max(0, min(100, score))), 2)
        lookup[str(ts_code)] = {
            "historical_resilience_score": score,
            "historical_resilience_label": _historical_resilience_label(score),
            "historical_resilience_reason": (
                f"近20日加权跑赢 {_signed_pct_text(weighted_relative)}，"
                f"{down_text}，跑赢率 {beat_ratio:.0%}，样本 {sample_count}日"
            ),
            "historical_resilience_weighted_relative": round(weighted_relative, 6),
            "historical_resilience_down_relative": (
                round(down_relative, 6) if down_relative is not None else pd.NA
            ),
            "historical_resilience_beat_ratio": round(beat_ratio, 6),
            "historical_resilience_sample_count": sample_count,
        }
    return lookup


def _attach_historical_resilience_fields(
    market: pd.DataFrame,
    history: pd.DataFrame,
    trade_date: str,
) -> pd.DataFrame:
    if market is None or market.empty:
        return market
    result = market.copy()
    empty = _empty_historical_resilience_fields()
    lookup = _build_historical_resilience_lookup(history, trade_date)
    for key, value in empty.items():
        result[key] = value
    if "ts_code" not in result.columns or not lookup:
        return result
    for index, ts_code in result["ts_code"].astype(str).items():
        fields = lookup.get(ts_code, empty)
        for key, value in fields.items():
            result.at[index, key] = value
    return result


def _empty_bottom_consolidation_fields(reason: str = "近20日有效日线不足") -> dict[str, Any]:
    return {
        "bottom_consolidation": False,
        "resonance_type": "强势共振",
        "resonance_stage": None,
        "bottom_window_days": 0,
        "bottom_position_20d": pd.NA,
        "bottom_box_amplitude_20d": pd.NA,
        "bottom_ma_slope_5d": pd.NA,
        "bottom_limit_up_date": None,
        "bottom_pullback_pct": pd.NA,
        "bottom_breakout_days": 0,
        "bottom_volume_expansion": pd.NA,
        "bottom_short_amplitude": pd.NA,
        "bottom_ma_convergence_pct": pd.NA,
        "bottom_volume_contraction": pd.NA,
        "bottom_setup_score": pd.NA,
        "bottom_breakout_strength": pd.NA,
        "bottom_consolidation_reason": reason,
    }


def _attach_bottom_consolidation_fields(
    market: pd.DataFrame,
    history: pd.DataFrame,
    trade_date: str,
) -> pd.DataFrame:
    if market is None or market.empty:
        return market
    result = market.copy()
    empty = _empty_bottom_consolidation_fields()
    for key, value in empty.items():
        result[key] = value
    required = {"ts_code", "trade_date", "close", "high", "low"}
    if history is None or history.empty or not required.issubset(history.columns):
        return result
    hist = history.copy()
    hist["ts_code"] = hist["ts_code"].astype(str)
    hist["trade_date"] = hist["trade_date"].astype(str)
    hist = hist[hist["trade_date"] < str(trade_date)].copy()
    for column in ("close", "high", "low", "pct_chg", "vol"):
        if column not in hist:
            hist[column] = pd.NA
        hist[column] = pd.to_numeric(hist[column], errors="coerce")
    lookup: dict[str, dict[str, Any]] = {}
    for ts_code, group in hist.groupby("ts_code"):
        prior = (
            group.sort_values("trade_date", kind="mergesort")
            .dropna(subset=["close", "high", "low"])
            .tail(19)
        )
        current_rows = result[result["ts_code"].astype(str) == str(ts_code)]
        if len(prior) < 9 or current_rows.empty:
            continue
        current = current_rows.iloc[0]
        current_close = _numeric_value(current.get("close"), float(prior["close"].iloc[-1]))
        current_high = _numeric_value(current.get("high"), current_close)
        current_low = _numeric_value(current.get("low"), current_close)
        current_pct = _numeric_value(current.get("pct_chg"))
        current_vol = _numeric_value(current.get("vol"))
        current_frame = pd.DataFrame([{
            "ts_code": str(ts_code),
            "trade_date": str(trade_date),
            "close": current_close,
            "high": current_high,
            "low": current_low,
            "pct_chg": current_pct,
            "vol": current_vol,
            "limit_flag": current.get("limit_flag"),
        }])
        for column in prior.columns:
            if column not in current_frame:
                current_frame[column] = pd.NA
        window = pd.concat([prior, current_frame[prior.columns]], ignore_index=True).tail(20)
        close20 = pd.to_numeric(window["close"], errors="coerce")
        high20_series = pd.to_numeric(window["high"], errors="coerce")
        low20_series = pd.to_numeric(window["low"], errors="coerce")
        volume20 = pd.to_numeric(window.get("vol"), errors="coerce")
        pct20 = pd.to_numeric(window.get("pct_chg"), errors="coerce")
        low20 = float(low20_series.min())
        high20 = float(high20_series.max())
        amplitude20 = (high20 / low20 - 1) * 100 if low20 > 0 else float("inf")
        span20 = high20 - low20
        position20 = (current_close - low20) / span20 if span20 > 0 else 0.5
        prior5 = float(close20.iloc[-10:-5].mean())
        latest5 = float(close20.iloc[-5:].mean())
        ma_slope5 = (latest5 / prior5 - 1) * 100 if prior5 > 0 else -100.0

        base_window = window.iloc[:-1].tail(10)
        base_close = pd.to_numeric(base_window["close"], errors="coerce")
        base_high = pd.to_numeric(base_window["high"], errors="coerce")
        base_low = pd.to_numeric(base_window["low"], errors="coerce")
        base_volume = pd.to_numeric(base_window["vol"], errors="coerce")
        short_low = float(base_low.min())
        short_high = float(base_high.max())
        short_amplitude = (
            (short_high / short_low - 1) * 100 if short_low > 0 else float("inf")
        )
        ma5 = float(close20.tail(5).mean())
        ma10 = float(close20.tail(10).mean())
        ma5_previous = float(close20.iloc[-6:-1].mean())
        ma_convergence = abs(ma5 / ma10 - 1) * 100 if ma10 > 0 else float("inf")
        volume_baseline = base_volume.iloc[-8:-3].dropna()
        volume_recent = base_volume.tail(3).dropna()
        volume_contraction = (
            float(volume_recent.mean() / volume_baseline.mean())
            if not volume_baseline.empty
            and not volume_recent.empty
            and float(volume_baseline.mean()) > 0
            else None
        )
        preceding_lows = base_low.iloc[-6:-3].dropna()
        recent_lows = base_low.tail(3).dropna()
        stopped_falling = bool(
            not preceding_lows.empty
            and not recent_lows.empty
            and float(recent_lows.min()) >= float(preceding_lows.min()) * 0.985
        )
        stable_base = bool(
            short_amplitude <= 12.0
            and ma_convergence <= 2.0
            and ma5 >= ma5_previous
            and volume_contraction is not None
            and volume_contraction <= 0.75
            and stopped_falling
            and ma_slope5 >= -1.5
        )
        current_volume_ratio = _numeric_value(current.get("volume_ratio"))
        prior5_close_high = float(base_close.tail(5).max())
        prior_high = float(base_high.tail(5).max())
        first_bullish_trigger = bool(
            stable_base
            and current_close > prior5_close_high
            and (current_pct >= 2.0 or current_high > prior_high)
            and current_volume_ratio >= 1.2
        )
        breakout_strength = (
            (current_close / prior5_close_high - 1) * 100
            if prior5_close_high > 0 else 0.0
        )
        setup_score = max(
            0.0,
            min(
                100.0,
                100.0 - short_amplitude * 3 - ma_convergence * 10
                + max(0.0, 0.75 - float(volume_contraction or 0.75)) * 40,
            ),
        )

        explicit_limit = window.get("limit_flag", pd.Series(pd.NA, index=window.index))
        limit_mask = explicit_limit.astype(str).str.lower().isin(
            {"1", "true", "u", "up", "涨停", "yes"}
        ) | pct20.ge(9.5).fillna(False)
        limit_indexes = [int(index) for index in window.index[limit_mask] if int(index) <= len(window) - 3]
        limit_date = None
        pullback_pct = None
        limit_pullback = False
        if limit_indexes:
            limit_index = limit_indexes[-1]
            peak = float(high20_series.iloc[limit_index:].max())
            pullback_pct = (peak - current_close) / peak * 100 if peak > 0 else None
            recent5_high = float(high20_series.tail(5).max())
            recent5_low = float(low20_series.tail(5).min())
            recent5_amplitude = (
                (recent5_high / recent5_low - 1) * 100
                if recent5_low > 0
                else float("inf")
            )
            limit_pullback = bool(
                pullback_pct is not None
                and 5.0 <= pullback_pct <= 20.0
                and recent5_amplitude <= 8.0
                and ma_slope5 >= -2.5
            )
            limit_date = str(window.iloc[limit_index].get("trade_date") or "") or None

        breakout_days = 0
        volume_expansion = None
        for days in (3, 2, 1):
            if len(window) < days + 5:
                continue
            baseline = volume20.iloc[-days - 5:-days].dropna()
            active = volume20.iloc[-days:].dropna()
            if baseline.empty or len(active) != days or float(baseline.mean()) <= 0:
                continue
            expansion = float(active.mean() / baseline.mean())
            start_close = float(close20.iloc[-days - 1])
            gain = (current_close / start_close - 1) * 100 if start_close > 0 else 0
            base = window.iloc[:-days].tail(17)
            base_low = float(pd.to_numeric(base["low"], errors="coerce").min())
            base_high = float(pd.to_numeric(base["high"], errors="coerce").max())
            base_amplitude = (base_high / base_low - 1) * 100 if base_low > 0 else float("inf")
            if expansion >= 1.5 and 2.0 <= gain <= 10.0 and base_amplitude <= 15.0:
                breakout_days = days
                volume_expansion = expansion
                break
        volume_breakout = breakout_days > 0
        is_bottom = limit_pullback or volume_breakout or stable_base
        resonance_stage = (
            "launch" if volume_breakout
            else "trigger" if first_bullish_trigger
            else "observation" if (stable_base or limit_pullback)
            else None
        )
        resonance_type = (
            "涨停回落筑底" if resonance_stage == "observation" and limit_pullback
            else {
                "launch": "底部放量启动",
                "trigger": "底部首阳触发",
                "observation": "缩量企稳观察",
            }.get(resonance_stage, "强势共振")
        )
        lookup[str(ts_code)] = {
            "bottom_consolidation": is_bottom,
            "resonance_type": resonance_type,
            "resonance_stage": resonance_stage,
            "bottom_window_days": int(len(window)),
            "bottom_position_20d": round(position20, 6),
            "bottom_box_amplitude_20d": round(amplitude20, 6),
            "bottom_ma_slope_5d": round(ma_slope5, 6),
            "bottom_limit_up_date": limit_date,
            "bottom_pullback_pct": round(pullback_pct, 6) if pullback_pct is not None else pd.NA,
            "bottom_breakout_days": breakout_days,
            "bottom_volume_expansion": (
                round(volume_expansion, 6) if volume_expansion is not None else pd.NA
            ),
            "bottom_short_amplitude": round(short_amplitude, 6),
            "bottom_ma_convergence_pct": round(ma_convergence, 6),
            "bottom_volume_contraction": (
                round(volume_contraction, 6) if volume_contraction is not None else pd.NA
            ),
            "bottom_setup_score": round(setup_score, 2),
            "bottom_breakout_strength": round(breakout_strength, 6),
            "bottom_consolidation_reason": (
                f"20日位置 {position20:.0%}，区间振幅 {amplitude20:.2f}%，"
                f"均价斜率 {ma_slope5:+.2f}%；"
                + (
                    f"短箱体缩量企稳，收敛 {ma_convergence:.2f}%"
                    if resonance_stage == "observation" else
                    f"突破近5日收盘高点 {breakout_strength:.2f}%"
                    if resonance_stage == "trigger" else
                    f"连续{breakout_days}日放量启动 {volume_expansion:.2f}倍"
                    if volume_breakout else "未出现涨停回落或1至3日放量启动"
                )
            ),
        }
    for index, ts_code in result["ts_code"].astype(str).items():
        fields = lookup.get(ts_code, empty)
        for key, value in fields.items():
            result.at[index, key] = value
    return result


def _attach_market_relative_fields(
    market: pd.DataFrame,
    benchmark: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if market is None or market.empty:
        return market, benchmark or _safe_market_relative_benchmark(market)
    benchmark = benchmark or _safe_market_relative_benchmark(market)
    result = market.copy()
    market_pct = benchmark.get("market_pct_chg")
    result["market_pct_chg"] = market_pct
    result["market_resonance_state"] = benchmark.get("market_state")
    result["market_resonance_state_label"] = benchmark.get("market_state_label")
    result["market_relative_sample_count"] = benchmark.get("sample_count")
    result["market_resonance_label"] = _market_relative_label(
        str(benchmark.get("market_state"))
    )
    result["pct_chg"] = (
        pd.to_numeric(result["pct_chg"], errors="coerce")
        if "pct_chg" in result.columns
        else pd.NA
    )
    if market_pct is None:
        result["relative_strength"] = pd.NA
    else:
        result["relative_strength"] = result["pct_chg"] - float(market_pct)
    result["market_resonance_reason"] = result["pct_chg"].apply(
        lambda value: _market_relative_reason(value, benchmark)
    )
    result["realtime_relative_strength_score"] = result.apply(
        _market_relative_score,
        axis=1,
    )
    return result, benchmark


def _refresh_market_relative_fields(
    signal: dict[str, Any],
    benchmark: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(signal, dict):
        return signal
    resolved = benchmark or {
        "market_pct_chg": signal.get("market_pct_chg"),
        "market_state": signal.get("market_resonance_state"),
        "market_state_label": signal.get("market_resonance_state_label"),
        "sample_count": signal.get("market_relative_sample_count"),
    }
    refreshed, _resolved = _attach_market_relative_fields(
        pd.DataFrame([signal]),
        resolved,
    )
    if refreshed is None or refreshed.empty:
        return signal
    return {**signal, **refreshed.iloc[0].to_dict()}


def _market_relative_candidate_mask(
    candidates: pd.DataFrame,
    benchmark: dict[str, Any],
) -> pd.Series:
    if candidates is None or candidates.empty:
        return pd.Series([], dtype=bool)
    pct = (
        pd.to_numeric(candidates["pct_chg"], errors="coerce")
        if "pct_chg" in candidates.columns
        else pd.Series(pd.NA, index=candidates.index)
    )
    market_pct = benchmark.get("market_pct_chg") if benchmark else None
    state = str((benchmark or {}).get("market_state") or "fallback")
    sample_count = int((benchmark or {}).get("sample_count") or 0)
    if (
        market_pct is None
        or state == "fallback"
        or sample_count < _MARKET_RELATIVE_MIN_SAMPLE_COUNT
    ):
        return pct.ge(0.2).fillna(False)
    relative = (
        pd.to_numeric(candidates["relative_strength"], errors="coerce")
        if "relative_strength" in candidates.columns
        else pct - float(market_pct)
    )
    if state == "up":
        return (
            (pct >= float(market_pct) + 1.0).fillna(False)
            & pct.ge(1.5).fillna(False)
        )
    if state == "down":
        return relative.ge(1.5).fillna(False) & pct.ge(-0.5).fillna(False)
    return pct.ge(1.0).fillna(False) & relative.ge(1.0).fillna(False)


def _filter_realtime_output(result: dict[str, Any]) -> dict[str, Any]:
    filtered = _json_safe(result)
    for section_name in ("intraday", "overnight"):
        section = filtered.get(section_name)
        if not isinstance(section, dict):
            continue
        stocks = section.get("stocks")
        if not isinstance(stocks, list):
            continue
        filtered_stocks = []
        output_reasons: dict[str, int] = {}
        output_samples: list[dict[str, Any]] = []
        for row in stocks:
            if not isinstance(row, dict):
                continue
            if not _realtime_output_allowed(row.get("ts_code")):
                reason = "最终展示市场范围过滤"
                output_reasons[reason] = output_reasons.get(reason, 0) + 1
                if len(output_samples) < 20:
                    output_samples.append({
                        "ts_code": row.get("ts_code"),
                        "name": row.get("name"),
                        "reason": reason,
                    })
                continue
            if section_name == "overnight" and not _overnight_pct_allowed(
                row.get("pct_chg")
            ):
                reason = "最终展示涨幅不在2%~7%"
                output_reasons[reason] = output_reasons.get(reason, 0) + 1
                if len(output_samples) < 20:
                    output_samples.append({
                        "ts_code": row.get("ts_code"),
                        "name": row.get("name"),
                        "pct_chg": row.get("pct_chg"),
                        "reason": reason,
                    })
                continue
            filtered_stocks.append(row)
        section["stocks"] = filtered_stocks
        if "candidate_count" in section:
            section["candidate_count"] = len(filtered_stocks)
        if "failed_count" in section:
            section["failed_count"] = sum(
                1
                for row in filtered_stocks
                if row.get("minute_data_warnings")
            )
        debug = section.get("filter_debug")
        if isinstance(debug, dict) and output_reasons:
            debug["output_filtered_count"] = sum(output_reasons.values())
            debug["candidate_count_after_output_filter"] = len(filtered_stocks)
            top_reasons = list(debug.get("top_reasons") or [])
            top_reasons.extend(
                {"reason": reason, "count": count}
                for reason, count in output_reasons.items()
            )
            debug["top_reasons"] = top_reasons[:12]
            debug["samples"] = list(debug.get("samples") or []) + output_samples
            for stage in debug.get("stages") or []:
                if stage.get("name") == "最终展示":
                    stage["count"] = len(filtered_stocks)
                    stage["filtered"] = int(stage.get("filtered") or 0) + sum(
                        output_reasons.values()
                    )
    return filtered


def _realtime_result_key(limit: int, now: datetime) -> tuple:
    bucket = "0" if now.second < 30 else "1"
    return (
        int(limit),
        f"{now.strftime('%Y%m%d%H%M')}{bucket}",
        macd_parameter_key(),
        _REALTIME_MARKET_RELATIVE_RULE_VERSION,
        _REALTIME_HISTORICAL_RESILIENCE_RULE_VERSION,
        _REALTIME_BOTTOM_CONSOLIDATION_RULE_VERSION,
        _REALTIME_CHIP_PEAK_RULE_VERSION,
        position_score_version(),
    )


def _request_minute_loader(
    result_loader: Callable[..., MinuteLoadResult],
    stats: dict[str, Any] | None = None,
) -> Callable[..., MinuteLoadResult]:
    cache: dict[tuple[str, str, str, str, str], MinuteLoadResult] = {}
    lock = threading.Lock()

    def load(
        ts_code: str,
        start: str,
        end: str,
        freq: str,
        trade_date: str,
    ) -> MinuteLoadResult:
        key = (
            str(ts_code),
            str(start),
            str(end),
            str(freq),
            str(trade_date),
        )
        with lock:
            cached = cache.get(key)
        if cached is not None:
            if stats is not None:
                with lock:
                    stats["minute_cache_hit_count"] = int(
                        stats.get("minute_cache_hit_count", 0)
                    ) + 1
            return MinuteLoadResult(
                cached.bars.copy(),
                cached.source,
                list(cached.warnings),
            )
        started = time.perf_counter()
        loaded = result_loader(ts_code, start, end, freq, trade_date)
        elapsed_ms = (time.perf_counter() - started) * 1000
        stored = MinuteLoadResult(
            loaded.bars.copy(),
            loaded.source,
            list(loaded.warnings),
        )
        with lock:
            cache[key] = stored
            if stats is not None:
                stats["minute_request_count"] = int(
                    stats.get("minute_request_count", 0)
                ) + 1
                timing_key = (
                    "tail_1m_ms"
                    if str(freq) == "1min"
                    else "intraday_60m_ms"
                )
                stats[timing_key] = float(
                    stats.get(timing_key, 0.0)
                ) + elapsed_ms
        return MinuteLoadResult(
            stored.bars.copy(),
            stored.source,
            list(stored.warnings),
        )

    return load


def _force_minute_provider_refresh(force_refresh: bool, freq: str) -> bool:
    return bool(force_refresh and str(freq) == "1min")


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        try:
            if pd.isna(value):
                continue
        except (TypeError, ValueError):
            pass
        return value
    return None


def _trading_session_progress(now: datetime) -> float:
    minutes = now.hour * 60 + now.minute + now.second / 60
    morning_start = 9 * 60 + 30
    morning_end = 11 * 60 + 30
    afternoon_start = 13 * 60
    afternoon_end = 15 * 60
    if minutes <= morning_start:
        return 0.0
    if minutes < morning_end:
        return (minutes - morning_start) / 240
    if minutes < afternoon_start:
        return 0.5
    if minutes < afternoon_end:
        return (120 + minutes - afternoon_start) / 240
    return 1.0


def _fill_missing_realtime_volume_ratio(
    market: pd.DataFrame,
    history: pd.DataFrame,
    trade_date: str,
    now: datetime,
) -> pd.DataFrame:
    if market is None or market.empty:
        return market
    result = market.copy()
    if "volume_ratio" not in result:
        result["volume_ratio"] = pd.NA
    result["volume_ratio"] = pd.to_numeric(result["volume_ratio"], errors="coerce")
    if (
        "ts_code" not in result
        or "vol" not in result
        or history is None
        or history.empty
        or not {"ts_code", "trade_date", "vol"}.issubset(history.columns)
    ):
        return result
    progress = _trading_session_progress(now)
    if progress <= 0:
        return result

    prior = history.copy()
    prior["trade_date"] = prior["trade_date"].astype(str)
    prior["vol"] = pd.to_numeric(prior["vol"], errors="coerce")
    prior = prior[
        (prior["trade_date"] < str(trade_date))
        & prior["vol"].notna()
        & (prior["vol"] > 0)
    ].sort_values(["ts_code", "trade_date"])
    if prior.empty:
        return result
    average_volume = (
        prior.groupby("ts_code", group_keys=False)
        .tail(5)
        .groupby("ts_code")["vol"]
        .mean()
    )
    current_volume = pd.to_numeric(result["vol"], errors="coerce")
    baseline = result["ts_code"].astype(str).map(average_volume) * progress
    estimate = current_volume / baseline
    missing = result["volume_ratio"].isna() | (result["volume_ratio"] <= 0)
    valid = current_volume.gt(0) & baseline.gt(0) & estimate.notna()
    result.loc[missing & valid, "volume_ratio"] = estimate[missing & valid]
    return result


def _market_price_map(market: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if market is None or market.empty or "ts_code" not in market.columns:
        return {}
    result = {}
    for row in market.to_dict("records"):
        ts_code = str(row.get("ts_code") or "")
        if not ts_code:
            continue
        result[ts_code] = {
            "current_price": row.get("close"),
            "day_high": row.get("high"),
            "pct_chg": row.get("pct_chg"),
        }
    return result


def _tail_availability(now: datetime) -> tuple[bool, bool]:
    clock = now.strftime("%H:%M:%S")
    return clock > "14:30:00", clock >= "09:30:00"


def _tail_available_from_end_datetime(end_datetime: str) -> bool:
    return str(end_datetime)[-8:] > "14:30:00"


def _with_realtime_display_flags(row: dict[str, Any], now: datetime) -> dict[str, Any]:
    result = dict(row)
    tail_available, auction_available = _tail_availability(now)
    result["tail_after_1430_available"] = bool(result.get("tail_after_1430_available", tail_available))
    result["tail_auction_available"] = bool(result.get("tail_auction_available", auction_available))
    if not tail_available:
        result["tail_after_1430_available"] = False
        for key in ("tail_strength_score", "tail_return_after_1430", "tail_volume_ratio", "tail_close_position"):
            result[key] = None
    if not auction_available:
        result["tail_auction_available"] = False
        result["tail_auction_return"] = None
    return result


def _enrich_rows_with_market(
    rows: list[dict[str, Any]],
    price_map: dict[str, dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    enriched = []
    for row in rows or []:
        ts_code = str(row.get("ts_code") or "")
        price = price_map.get(ts_code, {})
        merged = {
            **row,
            "current_price": _first_present(row.get("current_price"), row.get("close"), price.get("current_price")),
            "day_high": _first_present(row.get("day_high"), row.get("high"), price.get("day_high")),
            "pct_chg": _first_present(price.get("pct_chg"), row.get("pct_chg")),
        }
        enriched.append(_with_realtime_display_flags(merged, now))
    return enriched


def _enrich_section(section: dict[str, Any], price_map: dict[str, dict[str, Any]], now: datetime) -> dict[str, Any]:
    result = dict(section or {})
    result["stocks"] = _enrich_rows_with_market(result.get("stocks") or [], price_map, now)
    return result


def _snapshot_matches_trade_date(market: pd.DataFrame, trade_date: str) -> bool:
    if market is None or market.empty or "ts_code" not in market.columns:
        return False
    if "trade_date" not in market.columns:
        return True
    return bool(market["trade_date"].astype(str).eq(str(trade_date)).any())


def _snapshot_supports_realtime_filters(market: pd.DataFrame) -> bool:
    required = {
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
    pct_chg = pd.to_numeric(market["pct_chg"], errors="coerce")
    turnover = pd.to_numeric(
        market["turnover_rate"], errors="coerce"
    )
    volume_ratio = pd.to_numeric(
        market["volume_ratio"], errors="coerce"
    )
    amount = pd.to_numeric(market["amount"], errors="coerce")
    relative_market, benchmark = _attach_market_relative_fields(
        market,
        _safe_market_relative_benchmark(market),
    )
    intraday_candidate = (
        _market_relative_candidate_mask(relative_market, benchmark)
        & turnover.between(1.0, 12, inclusive="both")
        & volume_ratio.ge(1.0)
    )
    overnight_candidate = (
        pct_chg.between(0.5, 9.5, inclusive="both")
        & turnover.between(1, 18, inclusive="both")
        & volume_ratio.ge(1.0)
        & amount.ge(100_000)
    )
    return bool((intraday_candidate | overnight_candidate).any())


def _load_realtime_market_inputs(
    latest_trade_date: str,
    sync_metadata: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, str, str, bool, list[str]]:
    market = load_market_snapshot(latest_trade_date)
    if _snapshot_matches_trade_date(market, latest_trade_date):
        return (
            market, load_recent_daily(latest_trade_date, 120),
            latest_trade_date, "current_snapshot", True, [],
        )

    external_market, external_error = load_eastmoney_market_snapshot(latest_trade_date)
    if (
        _snapshot_matches_trade_date(external_market, latest_trade_date)
        and _snapshot_supports_realtime_filters(external_market)
    ):
        return (
            external_market, load_recent_daily(latest_trade_date, 120),
            latest_trade_date, "eastmoney_snapshot_fallback", True, [],
        )
    if (
        _snapshot_matches_trade_date(external_market, latest_trade_date)
        and not external_error
    ):
        external_error = "东方财富快照筛选字段不可用"

    fallback_trade_date = str(sync_metadata.get("data_trade_date") or "")
    if not fallback_trade_date or fallback_trade_date == latest_trade_date:
        try:
            dates = get_trade_dates(n=2)
            fallback_trade_date = str(dates[1]) if len(dates) > 1 else latest_trade_date
        except Exception:
            fallback_trade_date = latest_trade_date

    fallback_market = load_market_snapshot(fallback_trade_date)
    fallback_history = load_recent_daily(fallback_trade_date, 120)
    warnings = [external_error] if external_error else ["东方财富快照未返回有效数据"]
    return fallback_market, fallback_history, fallback_trade_date, "previous_snapshot", False, warnings


def _select_intraday_trade_date(
    latest_trade_date: str,
    base_trade_date: str,
    now: datetime,
    data_source: str,
) -> tuple[str, str]:
    return str(latest_trade_date), data_source


def _minute_price_snapshot(ts_code: str, bars: dict[str, pd.DataFrame], trade_date: str, previous_close: Any) -> dict[str, Any]:
    day_text = datetime.strptime(str(trade_date), "%Y%m%d").strftime("%Y-%m-%d")
    frames = []
    for frame in (bars.get("60m"), bars.get("tail_1m")):
        if frame is None or frame.empty or "trade_time" not in frame.columns:
            continue
        current = frame[frame["trade_time"].astype(str).str.startswith(day_text)].copy()
        if not current.empty:
            current["trade_time"] = pd.to_datetime(
                current["trade_time"], errors="coerce"
            )
            current = current.dropna(subset=["trade_time"])
            frames.append(current)
    if not frames:
        return {}

    intraday = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["ts_code", "trade_time"], keep="last")
    intraday = intraday.sort_values("trade_time")
    for column in ("open", "high", "low", "close", "vol", "amount"):
        intraday[column] = pd.to_numeric(intraday[column], errors="coerce") if column in intraday else None
    latest_close = _first_present(intraday["close"].dropna().iloc[-1] if intraday["close"].notna().any() else None)
    if latest_close is None:
        return {}

    previous = _first_present(previous_close)
    pct_chg = None
    try:
        previous = float(previous)
        if previous:
            pct_chg = (float(latest_close) / previous - 1) * 100
    except (TypeError, ValueError):
        pct_chg = None

    return {
        "close": float(latest_close),
        "high": float(intraday["high"].max()) if "high" in intraday and intraday["high"].notna().any() else float(latest_close),
        "low": float(intraday["low"].min()) if "low" in intraday and intraday["low"].notna().any() else float(latest_close),
        "pct_chg": round(float(pct_chg), 6) if pct_chg is not None else None,
        "amount": float(intraday["amount"].sum()) if "amount" in intraday and intraday["amount"].notna().any() else None,
        "vol": float(intraday["vol"].sum()) if "vol" in intraday and intraday["vol"].notna().any() else None,
    }


def _has_trade_date_minutes(bars: dict[str, pd.DataFrame], trade_date: str) -> bool:
    day_text = datetime.strptime(str(trade_date), "%Y%m%d").strftime("%Y-%m-%d")
    for frame in bars.values():
        if not isinstance(frame, pd.DataFrame) or frame.empty or "trade_time" not in frame.columns:
            continue
        if frame["trade_time"].astype(str).str.startswith(day_text).any():
            return True
    return False


def _fallback_1459_end_datetime(trade_date: str, end_datetime: str) -> str | None:
    _start, fallback_end = _datetime_window(trade_date, "09:30:00", "14:59:00")
    if str(end_datetime) >= fallback_end and str(end_datetime) != fallback_end:
        return fallback_end
    return None


def _minute_bars_with_1459_fallback(
    ts_code: str,
    start_datetime: str,
    end_datetime: str,
    freq: str,
    trade_date: str,
) -> pd.DataFrame:
    return _minute_result_with_1459_fallback(
        ts_code, start_datetime, end_datetime, freq, trade_date
    ).bars


def _minute_result_with_1459_fallback(
    ts_code: str,
    start_datetime: str,
    end_datetime: str,
    freq: str,
    trade_date: str,
    force_refresh: bool = False,
) -> MinuteLoadResult:
    stale_primary: list[pd.DataFrame] = []
    if force_refresh:
        invalidate_realtime_minute_cache(ts_code, freq, trade_date)

    def primary_loader(code, start, end, freq="60min"):
        if force_refresh:
            bars = _cached_minute_bars(
                code,
                start,
                end,
                freq=freq,
                force_refresh=True,
            )
        else:
            bars = _cached_minute_bars(code, start, end, freq=freq)
        if isinstance(bars, pd.DataFrame) and not bars.empty:
            stale_primary[:] = [bars]
        if _has_trade_date_minutes({freq: bars}, trade_date):
            return bars
        fallback_end = _fallback_1459_end_datetime(trade_date, end)
        if not fallback_end:
            return bars
        if force_refresh:
            fallback = _cached_minute_bars(
                code,
                start,
                fallback_end,
                freq=freq,
                force_refresh=True,
            )
        else:
            fallback = _cached_minute_bars(
                code,
                start,
                fallback_end,
                freq=freq,
            )
        return fallback if _has_trade_date_minutes({freq: fallback}, trade_date) else bars

    loaded = load_minutes_with_fallback(
        ts_code,
        start_datetime,
        end_datetime,
        freq,
        trade_date,
        primary_loader=primary_loader,
    )
    if (
        loaded.bars.empty
        and stale_primary
        and not stale_primary[0].empty
    ):
        return MinuteLoadResult(
            stale_primary[0].copy(),
            "tushare_stale",
            list(loaded.warnings),
        )
    return loaded


def _persistent_minute_result(
    ts_code: str,
    start_datetime: str,
    end_datetime: str,
    freq: str,
    trade_date: str,
    now: datetime,
    force_refresh: bool = False,
) -> MinuteLoadResult:
    cache_warnings: list[str] = []
    cached = pd.DataFrame()
    try:
        cached = load_minute_cache(
            ts_code,
            start_datetime,
            end_datetime,
            freq,
        )
        if not force_refresh and minute_cache_is_fresh(
            cached,
            start_datetime,
            end_datetime,
            now,
            freq,
        ):
            return MinuteLoadResult(cached.copy(), "database", [])
    except Exception as exc:
        cache_warnings.append(f"分钟数据库读取失败: {exc}")
        cached = pd.DataFrame()
    fetch_start = start_datetime
    if cached is not None and not cached.empty:
        fetch_start, cache_hit = minute_cache_next_fetch_start(
            cached,
            start_datetime,
            end_datetime,
            freq,
        )
        if (cache_hit or not fetch_start) and not force_refresh:
            return MinuteLoadResult(cached.copy(), "database", cache_warnings)
        if cache_hit and force_refresh and str(freq) == "60min":
            latest = pd.to_datetime(
                cached.get("trade_time"), errors="coerce"
            ).dropna().max()
            if pd.notna(latest):
                fetch_start = latest.strftime("%Y-%m-%d %H:%M:%S")
        if not fetch_start:
            fetch_start = end_datetime

    loaded = _minute_result_with_1459_fallback(
        ts_code,
        fetch_start,
        end_datetime,
        freq,
        trade_date,
        force_refresh=force_refresh,
    )
    if loaded.bars is not None and not loaded.bars.empty:
        try:
            save_minute_cache(
                loaded.bars,
                freq,
                loaded.source,
                trade_date,
            )
        except Exception as exc:
            cache_warnings.append(f"分钟数据库写入失败: {exc}")
    if (
        cached is not None
        and not cached.empty
        and loaded.bars is not None
        and not loaded.bars.empty
    ):
        cached_normalized = cached.copy()
        loaded_normalized = loaded.bars.copy()
        cached_normalized["trade_time"] = pd.to_datetime(
            cached_normalized["trade_time"], errors="coerce"
        )
        loaded_normalized["trade_time"] = pd.to_datetime(
            loaded_normalized["trade_time"], errors="coerce"
        )
        combined = (
            pd.concat(
                [cached_normalized, loaded_normalized],
                ignore_index=True,
            )
            .dropna(subset=["trade_time"])
            .drop_duplicates(subset=["ts_code", "trade_time"], keep="last")
            .sort_values("trade_time")
            .reset_index(drop=True)
        )
        return MinuteLoadResult(
            combined,
            loaded.source,
            list(loaded.warnings) + cache_warnings,
        )
    if (
        cached is not None
        and not cached.empty
        and (loaded.bars is None or loaded.bars.empty)
    ):
        return MinuteLoadResult(
            cached.copy(),
            "database_stale",
            list(loaded.warnings) + cache_warnings,
        )
    return MinuteLoadResult(
        loaded.bars.copy(),
        loaded.source,
        list(loaded.warnings) + cache_warnings,
    )


def _minute_missing_reason(trade_date: str, end_datetime: str) -> str:
    fallback_end = _fallback_1459_end_datetime(trade_date, end_datetime)
    if fallback_end:
        attempted = f"{str(end_datetime)[-8:-3]}和{fallback_end[-8:-3]}"
        return f"当日分时未返回，已尝试{attempted}，等待数据源更新"
    return "当日分时未返回，等待数据源更新"


def _market_previous_close_for_minute_pct(
    row: dict[str, Any],
    trade_date: str,
    base_trade_date: str | None = None,
) -> Any:
    explicit = _first_present(row.get("previous_close_for_pct"))
    if explicit is not None:
        return explicit
    if base_trade_date and str(base_trade_date) != str(trade_date):
        return _first_present(
            row.get("close"),
            row.get("pre_close"),
            row.get("previous_close"),
        )
    direct = _first_present(row.get("pre_close"), row.get("previous_close"))
    if direct is not None:
        return direct
    close = _first_present(row.get("close"))
    pct_chg = _first_present(row.get("pct_chg"))
    try:
        close_value = float(close)
        pct_value = float(pct_chg)
        if close_value and pct_value > -99:
            return close_value / (1 + pct_value / 100)
    except (TypeError, ValueError):
        pass
    return _first_present(
        row.get("close"),
    )


def _apply_minute_snapshots_to_market(
    market: pd.DataFrame,
    bars_by_code: dict[str, dict[str, pd.DataFrame]],
    trade_date: str,
    base_trade_date: str | None = None,
) -> pd.DataFrame:
    if market is None or market.empty or not bars_by_code or "ts_code" not in market.columns:
        return market
    result = market.copy()
    result["ts_code"] = result["ts_code"].astype(str)
    for ts_code, bars in bars_by_code.items():
        mask = result["ts_code"] == str(ts_code)
        if not mask.any():
            continue
        market_row = result.loc[mask].iloc[0].to_dict()
        previous_close = _market_previous_close_for_minute_pct(
            market_row,
            trade_date,
            base_trade_date=base_trade_date,
        )
        snapshot = _minute_price_snapshot(str(ts_code), bars, trade_date, previous_close)
        snapshot["previous_close_for_pct"] = previous_close
        for key, value in snapshot.items():
            if key not in result.columns:
                result[key] = None
            result.loc[mask, key] = value
    return result


def _load_realtime_intraday_signal_bars(
    market: pd.DataFrame,
    sector_potential: pd.DataFrame,
    trade_date: str,
    now: datetime,
    minute_loader: Callable[..., MinuteLoadResult] | None = None,
) -> dict[str, dict[str, pd.DataFrame]]:
    if market is None or market.empty or sector_potential is None or sector_potential.empty:
        return {}
    if "industry_name" not in sector_potential.columns or "industry" not in market.columns or "ts_code" not in market.columns:
        return {}

    industries = set(sector_potential["industry_name"].dropna().astype(str))
    hot_candidates = market[market["industry"].astype(str).isin(industries)].copy()
    bottom_mask = (
        market["bottom_consolidation"].fillna(False).astype(bool)
        if "bottom_consolidation" in market
        else pd.Series(False, index=market.index)
    )
    bottom_candidates = market[bottom_mask].copy()
    candidates = pd.concat(
        [hot_candidates, bottom_candidates],
        ignore_index=True,
    ).drop_duplicates(subset=["ts_code"], keep="first")
    candidates = candidates[_is_mainboard_a_stock(candidates["ts_code"])].copy()
    if "name" in candidates:
        candidates = candidates[
            ~candidates["name"].astype(str).str.upper().str.contains("ST")
        ].copy()
    if candidates.empty:
        return {}
    benchmark = _safe_market_relative_benchmark(market)
    for column in ("turnover_rate", "volume_ratio", "amount", "pct_chg"):
        candidates[column] = pd.to_numeric(candidates[column], errors="coerce") if column in candidates else 0
    candidates, benchmark = _attach_market_relative_fields(
        candidates,
        benchmark,
    )
    candidate_bottom = (
        candidates["bottom_consolidation"].fillna(False).astype(bool)
        if "bottom_consolidation" in candidates
        else pd.Series(False, index=candidates.index)
    )
    resonance_type = candidates.get(
        "resonance_type", pd.Series("", index=candidates.index)
    ).astype(str)
    resonance_stage = candidates.get(
        "resonance_stage", pd.Series("", index=candidates.index)
    ).astype(str)
    bottom_volume_allowed = (
        (resonance_stage.eq("observation") | resonance_type.eq("涨停回落筑底"))
        & candidates["volume_ratio"].ge(0.6)
    ) | (
        resonance_stage.eq("trigger")
        & candidates["volume_ratio"].ge(1.2)
    ) | (
        (resonance_stage.eq("launch") | resonance_type.eq("底部放量启动"))
        & candidates["volume_ratio"].ge(1.5)
    )
    candidates = candidates[
        candidates["turnover_rate"].between(0.6, 12, inclusive="both")
        & (
            (
                candidates["volume_ratio"].ge(1.0)
                & _market_relative_candidate_mask(candidates, benchmark)
            )
            | (candidate_bottom & bottom_volume_allowed)
        )
    ].copy()
    if candidates.empty:
        return {}

    hot_selected = (
        candidates[candidates["industry"].astype(str).isin(industries)]
        .sort_values(["industry", "amount", "volume_ratio"], ascending=[True, False, False])
        .groupby("industry", group_keys=False)
        .head(_REALTIME_CANDIDATES_PER_SECTOR)
    )
    selected_bottom_mask = (
        candidates["bottom_consolidation"].fillna(False).astype(bool)
        if "bottom_consolidation" in candidates
        else pd.Series(False, index=candidates.index)
    )
    bottom_selected = (
        candidates[selected_bottom_mask]
        .sort_values(["volume_ratio", "amount"], ascending=[False, False])
        .head(30)
    )
    candidates = pd.concat([hot_selected, bottom_selected], ignore_index=True).drop_duplicates(
        subset=["ts_code"], keep="first"
    )
    start_60m, _default_end_60m = _history_window(trade_date)
    end_datetime = _realtime_end_datetime(trade_date, now=now)

    codes = candidates["ts_code"].dropna().astype(str).drop_duplicates().tolist()

    def load_one(ts_code: str) -> tuple[str, MinuteLoadResult | None]:
        try:
            loaded = (
                minute_loader(
                    ts_code,
                    start_60m,
                    end_datetime,
                    "60min",
                    trade_date,
                )
                if minute_loader
                else _minute_result_with_1459_fallback(
                    ts_code,
                    start_60m,
                    end_datetime,
                    "60min",
                    trade_date,
                )
            )
        except Exception as exc:
            print(f"实时共振60分钟更新失败 {ts_code}: {exc}")
            return ts_code, None
        return ts_code, loaded

    bars_by_code: dict[str, dict[str, pd.DataFrame]] = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        loaded_results = executor.map(load_one, codes)
        for ts_code, loaded in loaded_results:
            if loaded is None or loaded.bars.empty:
                continue

            bars_by_code[ts_code] = {
                "60m": loaded.bars,
                "60m_source": loaded.source,
                "warnings": loaded.warnings,
            }
    return bars_by_code


def _today_sector_potential(market: pd.DataFrame, limit: int) -> pd.DataFrame:
    if market is None or market.empty or "industry" not in market:
        return pd.DataFrame()
    data = market.copy()
    data["industry"] = data["industry"].fillna("").astype(str)
    data = data[data["industry"] != ""].copy()
    if data.empty:
        return pd.DataFrame()
    for column in ("pct_chg", "amount", "volume_ratio", "turnover_rate"):
        data[column] = (
            pd.to_numeric(data[column], errors="coerce")
            if column in data
            else 0
        )
    grouped = data.groupby("industry").agg(
        stock_count=("ts_code", "count"),
        avg_pct_chg=("pct_chg", "mean"),
        up_ratio=("pct_chg", lambda values: float((values > 0).mean())),
        strong_ratio=("pct_chg", lambda values: float((values >= 3).mean())),
        amount_sum=("amount", "sum"),
        volume_ratio=("volume_ratio", "mean"),
        turnover_rate=("turnover_rate", "mean"),
    ).reset_index()
    grouped = grouped[grouped["stock_count"] >= 1].copy()
    if grouped.empty:
        return pd.DataFrame()
    grouped["potential_score"] = (
        grouped["avg_pct_chg"].clip(lower=0, upper=8) * 8
        + grouped["up_ratio"].clip(0, 1) * 20
        + grouped["strong_ratio"].clip(0, 1) * 20
        + grouped["volume_ratio"].clip(0, 4) * 5
        + (grouped["amount_sum"] / 100_000_000).clip(0, 10)
    ).round(2)
    grouped = (
        grouped.sort_values(
            ["potential_score", "amount_sum"],
            ascending=[False, False],
            kind="mergesort",
        )
        .head(limit)
        .reset_index(drop=True)
    )
    grouped["rank"] = grouped.index + 1
    return grouped.rename(columns={"industry": "industry_name"})


def _include_bottom_candidate_sectors(
    sector_potential: pd.DataFrame,
    market: pd.DataFrame,
) -> pd.DataFrame:
    result = (
        sector_potential.copy()
        if isinstance(sector_potential, pd.DataFrame)
        else pd.DataFrame()
    )
    if market is None or market.empty or "industry" not in market:
        return result
    if "bottom_consolidation" not in market:
        return result
    bottom = market[market["bottom_consolidation"].fillna(False).astype(bool)]
    industries = [
        value for value in bottom["industry"].dropna().astype(str).unique()
        if value
    ]
    existing = (
        set(result["industry_name"].dropna().astype(str))
        if "industry_name" in result else set()
    )
    missing = [industry for industry in industries if industry not in existing]
    if not missing:
        return result
    additions = pd.DataFrame([
        {
            "industry_name": industry,
            "rank": len(result) + offset + 1,
            "bottom_candidate_sector": True,
        }
        for offset, industry in enumerate(missing)
    ])
    return pd.concat([result, additions], ignore_index=True, sort=False)


def _has_any_trade_date_signal_bars(bars_by_code: dict[str, dict[str, pd.DataFrame]], trade_date: str) -> bool:
    return any(_has_trade_date_minutes(bars, trade_date) for bars in (bars_by_code or {}).values())


def _latest_bar_time(*frames: pd.DataFrame | None) -> str | None:
    latest: pd.Timestamp | None = None
    for frame in frames:
        if (
            frame is None
            or frame.empty
            or "trade_time" not in frame.columns
        ):
            continue
        parsed = pd.to_datetime(frame["trade_time"], errors="coerce").dropna()
        if parsed.empty:
            continue
        candidate = parsed.max()
        if latest is None or candidate > latest:
            latest = candidate
    return (
        latest.strftime("%Y-%m-%d %H:%M:%S")
        if latest is not None
        else None
    )


def _build_bottom_filter_debug(
    market: pd.DataFrame,
    intraday_bars: dict[str, dict[str, pd.DataFrame]],
    preliminary_rows: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    if market is None or market.empty or "ts_code" not in market:
        daily_codes: set[str] = set()
    else:
        mask = (
            market["bottom_consolidation"].fillna(False).astype(bool)
            if "bottom_consolidation" in market
            else pd.Series(False, index=market.index)
        )
        daily_codes = set(market.loc[mask, "ts_code"].astype(str))
    minute_codes = {
        str(code) for code, values in (intraday_bars or {}).items()
        if str(code) in daily_codes
        and any(
            isinstance(frame, pd.DataFrame) and not frame.empty
            for frame in (values or {}).values()
        )
    }
    technical_codes = {
        str(item.get("ts_code") or "")
        for item in preliminary_rows or []
        if bool((item.get("signal") or {}).get("bottom_consolidation"))
    }
    final_codes = {
        str(item.get("ts_code") or "")
        for item in rows or []
        if bool(item.get("bottom_consolidation"))
    }
    return {
        "daily_candidate_count": len(daily_codes),
        "minute_loaded_count": len(minute_codes),
        "minute_missing_count": max(0, len(daily_codes) - len(minute_codes)),
        "technical_confirmed_count": len(technical_codes),
        "technical_rejected_count": max(0, len(minute_codes) - len(technical_codes)),
        "final_output_count": len(final_codes),
    }


def _group_realtime_stage_rows(
    rows: list[dict[str, Any]],
    limit: int,
) -> dict[str, list[dict[str, Any]]]:
    """Return independently ranked bottom-stage lists without truncating peers."""
    cap = max(1, min(int(limit), 100))
    specs = {
        "observation": (
            "observation_stocks",
            lambda row: (
                bool(row.get("chip_build_position")),
                float(row.get("chip_washout_score") or 0),
                float(row.get("bottom_setup_score") or 0),
                -float(row.get("bottom_ma_convergence_pct") or 999),
                -float(row.get("bottom_volume_contraction") or 999),
            ),
        ),
        "trigger": (
            "trigger_stocks",
            lambda row: (
                bool(row.get("chip_build_position")),
                float(row.get("chip_washout_score") or 0),
                float(row.get("bottom_breakout_strength") or 0),
                float(row.get("realtime_relative_strength_score") or 0),
                float(row.get("intraday_signal_score") or 0),
            ),
        ),
        "launch": (
            "launch_stocks",
            lambda row: (
                bool(row.get("chip_build_position")),
                float(row.get("chip_washout_score") or 0),
                float(row.get("intraday_signal_score") or 0),
                float(row.get("bottom_volume_expansion") or 0),
                float(row.get("relative_strength") or 0),
            ),
        ),
    }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for stage, (field, key) in specs.items():
        matches = [row for row in (rows or []) if row.get("resonance_stage") == stage]
        grouped[field] = sorted(matches, key=key, reverse=True)[:cap]
    return grouped


def _position_filter_reason(row: dict[str, Any]) -> str:
    if row.get("position_level") == "不展示":
        return str(
            row.get("position_filter_reason")
            or row.get("position_level_reason")
            or "未达到统一建仓标准"
        )
    missing = row.get("position_missing_confirmations") or []
    if missing:
        return "；".join(str(item) for item in missing)
    return "统一候选TOP10截断"


def _build_unified_position_candidates(
    rows: list[dict[str, Any]],
    limit: int = 10,
    *,
    market_phase: str = "",
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[str],
    dict[str, Any],
]:
    deduplicated: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        code = str(row.get("ts_code") or "")
        if code:
            deduplicated[code] = dict(row)
    scored: list[dict[str, Any]] = []
    warnings: list[str] = []
    for code, row in deduplicated.items():
        try:
            scored.append(
                score_position_candidate(row, market_phase=market_phase)
            )
        except Exception as exc:
            warnings.append(f"{code} 统一建仓评分失败: {str(exc)[:120]}")
    candidates = rank_scored_position_candidates(
        scored,
        limit=min(max(1, int(limit)), 10),
    )
    visible_codes = {str(row.get("ts_code") or "") for row in candidates}
    reason_counts: Counter = Counter()
    samples: list[dict[str, Any]] = []
    for row in scored:
        if str(row.get("ts_code") or "") in visible_codes:
            continue
        reason = _position_filter_reason(row)
        reason_counts[reason] += 1
        if len(samples) < 20:
            samples.append({
                "ts_code": str(row.get("ts_code") or ""),
                "name": str(row.get("name") or ""),
                "current_price": row.get("current_price", row.get("close")),
                "pct_chg": row.get("pct_chg"),
                "quote_time": row.get("quote_time", row.get("data_as_of")),
                "quote_source": row.get("quote_source", row.get("data_source")),
                "latest_limit_up_date": row.get("latest_limit_up_date"),
                "latest_resonance_date": row.get("latest_resonance_date"),
                "primary_support": row.get("primary_support"),
                "confirmation_price": row.get("confirmation_price"),
                "position_score": row.get("position_score"),
                "component_scores": {
                    "support": row.get("support_pullback_score"),
                    "resonance": row.get("historical_resonance_score"),
                    "sector": row.get("sector_hot_score"),
                    "price_volume": row.get("price_volume_score"),
                    "chip": row.get("chip_peak_score"),
                    "macd": row.get("macd_score"),
                    "relative_tail": row.get("relative_tail_score"),
                },
                "risk_penalty": row.get("position_risk_penalty"),
                "reason": reason,
            })
    visible_before_cap = sum(
        row.get("position_level") in {"立即建仓", "等待突破建仓", "观察建仓"}
        for row in scored
    )
    reasons = [_position_filter_reason(row) for row in scored]
    funnel = {
        "source_scored": len(scored),
        "today_limit_up": sum(reason.startswith("当日涨停") for reason in reasons),
        "today_limit_down": sum(reason.startswith("当日跌停") for reason in reasons),
        "source_sealed": sum(reason.startswith("行情源标记封板") for reason in reasons),
        "no_limit_gene": sum(reason == "前1至10日无涨停基因" for reason in reasons),
        "no_recent_resonance": sum(reason == "近20日无有效共振" for reason in reasons),
        "support_broken": sum("跌破" in reason for reason in reasons),
        "score_below_50": sum(reason == "综合分低于50" for reason in reasons),
        "top_truncated": max(0, visible_before_cap - len(candidates)),
        "final": len(candidates),
    }
    debug = {
        "source_count": len(scored),
        "visible_count": len(candidates),
        "filtered_count": max(0, len(scored) - len(candidates)),
        "funnel": funnel,
        "auto_expand": len(candidates) == 0,
        "top_reasons": [
            {"reason": reason, "count": int(count)}
            for reason, count in reason_counts.most_common(10)
        ],
        "samples": samples,
    }
    return scored, candidates, warnings, debug


def _attach_realtime_chip_fields(
    rows: list[dict[str, Any]],
    history: pd.DataFrame,
    trade_date: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    supported_stages = {"observation", "trigger", "launch"}
    source_rows = [dict(row) for row in (rows or [])]
    stage_indexes = [
        index
        for index, row in enumerate(source_rows)
        if row.get("resonance_stage") in supported_stages
        or row.get("limit_gene_eligible")
    ]
    if not stage_indexes:
        return source_rows, []
    stage_rows = [source_rows[index] for index in stage_indexes]
    enriched, warnings = attach_chip_peak_fields(
        stage_rows,
        history,
        trade_date,
    )
    for index, enriched_row in zip(stage_indexes, enriched):
        source_rows[index] = enriched_row
    return source_rows, warnings


def _refresh_position_confirmation_fields(
    rows: list[dict[str, Any]],
    history: pd.DataFrame,
    trade_date: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    grouped = {
        str(code): bars.copy()
        for code, bars in (
            history.groupby("ts_code")
            if history is not None and not history.empty and "ts_code" in history.columns
            else []
        )
    }
    refreshed: list[dict[str, Any]] = []
    warnings: list[str] = []
    for source in rows or []:
        row = dict(source)
        code = str(row.get("ts_code") or "")
        if not row.get("limit_gene_eligible"):
            refreshed.append(row)
            continue
        try:
            confirmation = extract_pullback_confirmation(
                grouped.get(code, pd.DataFrame()),
                row,
                {**row, "trade_date": trade_date},
            )
            refreshed.append({**row, **confirmation})
        except Exception as exc:
            warnings.append(f"{code} 实时突破确认更新失败: {str(exc)[:120]}")
            refreshed.append(row)
    return refreshed, warnings


def _screening_data_trade_date(
    data_as_of: str | None,
    requested_trade_date: str,
    base_trade_date: str | None,
) -> str:
    if data_as_of:
        return str(requested_trade_date)
    return str(base_trade_date or requested_trade_date)


def _load_tail_minute_bars_for_pick(
    ts_code: str,
    trade_date: str,
    end_datetime: str,
    minute_loader: Callable[..., MinuteLoadResult] | None = None,
) -> MinuteLoadResult:
    if not _tail_available_from_end_datetime(end_datetime):
        return MinuteLoadResult(pd.DataFrame(), "not_available", [])
    tail_start, _default_tail_end = _datetime_window(trade_date, "14:25:00", "15:00:00")
    try:
        return (
            minute_loader(
                ts_code,
                tail_start,
                end_datetime,
                "1min",
                trade_date,
            )
            if minute_loader
            else _minute_result_with_1459_fallback(
                ts_code,
                tail_start,
                end_datetime,
                "1min",
                trade_date,
            )
        )
    except Exception as exc:
        print(f"实时共振尾盘1分钟更新失败 {ts_code}: {exc}")
        return MinuteLoadResult(pd.DataFrame(), "unavailable", [str(exc)])


def _load_tail_minutes_for_candidates(
    preliminary_rows: list[dict[str, Any]],
    trade_date: str,
    end_datetime: str,
    minute_loader: Callable[..., MinuteLoadResult] | None = None,
) -> list[MinuteLoadResult]:
    def load(preliminary: dict[str, Any]) -> MinuteLoadResult:
        return _load_tail_minute_bars_for_pick(
            str(preliminary.get("ts_code") or ""),
            trade_date,
            end_datetime,
            minute_loader=minute_loader,
        )

    if len(preliminary_rows) <= 1:
        return [load(row) for row in preliminary_rows]
    with ThreadPoolExecutor(max_workers=min(4, len(preliminary_rows))) as executor:
        return list(executor.map(load, preliminary_rows))


def _build_realtime_intraday_section(
    market: pd.DataFrame,
    history: pd.DataFrame,
    trade_date: str,
    now: datetime,
    limit: int = 10,
    base_trade_date: str | None = None,
    data_source: str = "current_snapshot",
    snapshot_data_current: bool = True,
    minute_loader: Callable[..., MinuteLoadResult] | None = None,
    shared_context: dict[str, Any] | None = None,
    force_refresh: bool = False,
    performance_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cache_key = (
        str(trade_date),
        str(base_trade_date or trade_date),
        int(limit),
        _realtime_end_datetime(trade_date, now=now),
        macd_parameter_key(),
        _REALTIME_MARKET_RELATIVE_RULE_VERSION,
        _REALTIME_HISTORICAL_RESILIENCE_RULE_VERSION,
        _REALTIME_BOTTOM_CONSOLIDATION_RULE_VERSION,
        _REALTIME_CHIP_PEAK_RULE_VERSION,
        position_score_version(),
    )
    cached = (
        None
        if force_refresh
        else _REALTIME_INTRADAY_RESULT_CACHE.get(cache_key)
    )
    if cached and time.monotonic() - cached[0] <= _REALTIME_INTRADAY_CACHE_TTL_SECONDS:
        result = _json_safe(cached[1])
        result["result_cache_hit"] = True
        if shared_context is not None:
            shared_context["leader_codes"] = dict(
                result.get("_leader_codes") or {}
            )
        return result

    phase, should_refresh = _market_phase(now)
    realtime_market = _fill_missing_realtime_volume_ratio(
        market,
        history,
        trade_date,
        now,
    )
    realtime_market, _market_relative_benchmark = _attach_market_relative_fields(
        realtime_market
    )
    realtime_market = _attach_bottom_consolidation_fields(
        realtime_market,
        history,
        trade_date,
    )
    history_pool, history_filter_debug, history_warnings = (
        _build_history_position_pool(
            realtime_market,
            history,
            trade_date,
        )
    )
    history_pool = sorted(
        history_pool,
        key=lambda row: (
            float(row.get("historical_resonance_score") or 0),
            float(row.get("primary_support_strength") or 0),
            -abs(float(row.get("support_distance_pct") or 99)),
        ),
        reverse=True,
    )[:POSITION_ENRICHMENT_LIMIT]
    sector_potential = rank_sector_potential(
        realtime_market,
        history,
        limit=_REALTIME_SECTOR_LIMIT,
    )
    if sector_potential is None or sector_potential.empty:
        sector_potential = _today_sector_potential(
            realtime_market,
            _REALTIME_SECTOR_LIMIT,
        )
    leader_codes = _leader_codes_from_ranked_sector_potential(
        sector_potential
    )
    if shared_context is not None:
        shared_context["leader_codes"] = leader_codes
    sector_potential = _include_bottom_candidate_sectors(
        sector_potential,
        realtime_market,
    )
    minute_market = realtime_market
    intraday_bars = _load_realtime_intraday_signal_bars(
        minute_market,
        sector_potential,
        trade_date,
        now,
        minute_loader=minute_loader,
    )
    if (
        base_trade_date
        and str(base_trade_date) != str(trade_date)
        and not _has_any_trade_date_signal_bars(intraday_bars, trade_date)
    ):
        fallback_bars = _load_realtime_intraday_signal_bars(
            minute_market,
            sector_potential,
            str(base_trade_date),
            now,
            minute_loader=minute_loader,
        )
        if fallback_bars:
            intraday_bars = fallback_bars
    signal_market = _apply_minute_snapshots_to_market(
        realtime_market,
        intraday_bars,
        trade_date,
        base_trade_date=base_trade_date,
    )
    signal_market, _market_relative_benchmark = _attach_market_relative_fields(
        signal_market,
        _market_relative_benchmark,
    )
    signal_market = _attach_historical_resilience_fields(
        signal_market,
        history,
        trade_date,
    )
    signal_market = _attach_bottom_consolidation_fields(
        signal_market,
        history,
        trade_date,
    )
    sector_potential = _attach_intraday_signal_stocks(
        sector_potential,
        signal_market,
        intraday_bars,
        per_sector=_REALTIME_PICKS_PER_SECTOR,
    )

    end_datetime = _realtime_end_datetime(trade_date, now=now)
    signal_market_by_code = (
        signal_market.set_index(signal_market["ts_code"].astype(str)).to_dict("index")
        if signal_market is not None and not signal_market.empty and "ts_code" in signal_market.columns
        else {}
    )
    sector_records = (
        sector_potential.to_dict("records")
        if sector_potential is not None and not sector_potential.empty else []
    )
    sector_by_industry = {
        str(sector.get("industry_name") or sector.get("industry") or ""): sector
        for sector in sector_records
    }
    intraday_signal_by_code = {}
    for sector in sector_records:
        for stock in sector.get("intraday_signal_stocks") or []:
            intraday_signal_by_code[str(stock.get("ts_code") or "")] = stock
    preliminary_rows = []
    for historical in history_pool:
        ts_code = str(historical.get("ts_code") or "")
        market_snapshot = signal_market_by_code.get(ts_code, historical)
        industry = str(
            market_snapshot.get("industry") or historical.get("industry") or ""
        )
        sector = sector_by_industry.get(industry, {})
        stock = intraday_signal_by_code.get(ts_code, {})
        signal = {
            **historical,
            **market_snapshot,
            **stock,
            "industry": industry,
            "sector_avg_pct_chg": sector.get("avg_pct_chg", sector.get("pct_chg")),
            "sector_up_ratio": sector.get("up_ratio"),
            "sector_limit_up_count": sector.get("limit_up_count", sector.get("limit_count")),
            "sector_rank": sector.get("sector_rank", sector.get("rank")),
            "sector_potential_score": sector.get("potential_score"),
            "sector_macd_status": sector.get("sector_macd_status"),
        }
        status, _reason = _main_force_status(signal)
        preliminary_rows.append({
            "ts_code": ts_code,
            "market_snapshot": market_snapshot,
            "signal": signal,
            "preliminary_status": status,
        })
    for sector in sector_records:
        industry = sector.get("industry_name") or sector.get("industry") or ""
        for stock in sector.get("intraday_signal_stocks") or []:
            ts_code = str(stock.get("ts_code") or "")
            market_snapshot = signal_market_by_code.get(ts_code, {})
            existing = next(
                (item for item in preliminary_rows if item["ts_code"] == ts_code),
                None,
            )
            signal = {
                **(existing.get("signal", {}) if existing else {}),
                **market_snapshot,
                **stock,
                "industry": industry,
                "sector_avg_pct_chg": sector.get("avg_pct_chg", sector.get("pct_chg")),
                "sector_up_ratio": sector.get("up_ratio"),
                "sector_limit_up_count": sector.get("limit_up_count", sector.get("limit_count")),
                "sector_rank": sector.get("sector_rank", sector.get("rank")),
                "sector_potential_score": sector.get("potential_score"),
                "sector_macd_status": sector.get("sector_macd_status"),
            }
            status, _reason = _main_force_status(signal)
            merged = {
                "ts_code": ts_code,
                "market_snapshot": market_snapshot,
                "signal": signal,
                "preliminary_status": status,
            }
            if existing:
                preliminary_rows[preliminary_rows.index(existing)] = merged
            else:
                preliminary_rows.append(merged)
    ranked_preliminary_rows = sorted(
        preliminary_rows,
        key=lambda item: (
            item.get("preliminary_status") == "主力抢筹",
            item["signal"].get("next_day_bias") == "高开偏强",
            float(item["signal"].get("realtime_relative_strength_score") or 0),
            float(item["signal"].get("intraday_signal_score") or 0),
            float(item["signal"].get("volume_ratio") or 0),
        ),
        reverse=True,
    )
    stage_fetch_cap = min(
        _REALTIME_TAIL_CANDIDATE_LIMIT,
        max(1, min(int(limit), 100)),
    )
    preliminary_rows = []
    for stage in ("observation", "trigger", "launch"):
        preliminary_rows.extend([
            item for item in ranked_preliminary_rows
            if item["signal"].get("resonance_stage") == stage
        ][:stage_fetch_cap])
    preliminary_rows.extend([
        item for item in ranked_preliminary_rows
        if item["signal"].get("resonance_stage") not in {
            "observation", "trigger", "launch",
        }
    ][:_REALTIME_TAIL_CANDIDATE_LIMIT])

    tail_started = time.perf_counter()
    tail_results = _load_tail_minutes_for_candidates(
        preliminary_rows,
        trade_date,
        end_datetime,
        minute_loader=minute_loader,
    )
    if performance_stats is not None:
        performance_stats["tail_1m_wall_ms"] = (
            time.perf_counter() - tail_started
        ) * 1000
    rows = []
    for preliminary, tail_loaded in zip(preliminary_rows, tail_results):
        ts_code = preliminary["ts_code"]
        market_snapshot = preliminary["market_snapshot"]
        signal = preliminary["signal"]
        tail_1m = tail_loaded.bars
        current_day_minutes = _has_trade_date_minutes(intraday_bars.get(ts_code, {}), trade_date)
        if not tail_1m.empty and ts_code in intraday_bars:
            current_day_minutes = current_day_minutes or _has_trade_date_minutes({"tail_1m": tail_1m}, trade_date)
            tail_snapshot = _minute_price_snapshot(
                ts_code,
                {"60m": intraday_bars[ts_code].get("60m"), "tail_1m": tail_1m},
                trade_date,
                _market_previous_close_for_minute_pct(
                    market_snapshot,
                    trade_date,
                    base_trade_date=base_trade_date,
                ),
            )
            signal = {**signal, **tail_snapshot}
            signal = _refresh_market_relative_fields(
                signal,
                _market_relative_benchmark,
            )
            refreshed = _macd_kdj_60m_signal(
                pd.Series(signal),
                {"60m": intraday_bars[ts_code].get("60m"), "tail_1m": tail_1m},
            )
            if refreshed:
                signal = {**signal, **refreshed}
        signal["minute_data_current"] = current_day_minutes
        signal["minute_data_source"] = (
            tail_loaded.source
            if not tail_1m.empty
            else intraday_bars.get(ts_code, {}).get("60m_source", "unavailable")
        )
        signal["minute_data_warnings"] = list(dict.fromkeys(
            list(intraday_bars.get(ts_code, {}).get("warnings", []))
            + list(tail_loaded.warnings)
        ))
        if not current_day_minutes:
            signal["next_day_bias"] = "数据不足"
            signal["minute_data_attempted_end"] = end_datetime
            signal["minute_data_fallback_end"] = _fallback_1459_end_datetime(trade_date, end_datetime)
            signal["next_day_bias_reason"] = _minute_missing_reason(trade_date, end_datetime)
        signal = _mask_unavailable_tail_fields(signal, end_datetime)
        if not current_day_minutes:
            signal["tail_after_1430_available"] = False
            signal["tail_return_after_1430"] = None
            signal["tail_strength_score"] = None
            signal["tail_volume_ratio"] = None
            signal["tail_close_position"] = None
            if not snapshot_data_current:
                for key in ("current_price", "day_high", "close", "high"):
                    signal[key] = None
        status, reason = _main_force_status(signal)
        if not current_day_minutes:
            status, reason = "观察", "当日分时未返回"
        code_bars = intraday_bars.get(ts_code, {})
        rows.append({
            **signal,
            "data_as_of": _latest_bar_time(
                *(
                    value
                    for value in code_bars.values()
                    if isinstance(value, pd.DataFrame)
                ),
                tail_1m,
            ),
            "main_force_status": status,
            "main_force_reason": reason,
        })

    all_rows = sorted(
        rows,
        key=lambda item: (
            item.get("main_force_status") == "主力抢筹",
            item.get("next_day_bias") == "高开偏强",
            float(item.get("realtime_relative_strength_score") or 0),
            float(item.get("intraday_signal_score") or 0),
            float(item.get("volume_ratio") or 0),
        ),
        reverse=True,
    )
    chip_started = time.perf_counter()
    all_rows, chip_warnings = _attach_realtime_chip_fields(
        all_rows,
        history,
        trade_date,
    )
    all_rows, confirmation_warnings = _refresh_position_confirmation_fields(
        all_rows,
        history,
        trade_date,
    )
    if performance_stats is not None:
        performance_stats["chip_peak_ms"] = (
            time.perf_counter() - chip_started
        ) * 1000
    (
        scored_rows,
        position_candidates,
        position_warnings,
        position_filter_debug,
    ) = _build_unified_position_candidates(
        all_rows,
        limit=min(int(limit), 10),
        market_phase=phase,
    )
    position_filter_debug["history_funnel"] = history_filter_debug
    legacy_rows = scored_rows[:limit]
    stage_groups = _group_realtime_stage_rows(scored_rows, limit)
    data_as_of = max(
        (
            str(row.get("data_as_of"))
            for row in scored_rows
            if row.get("data_as_of")
        ),
        default=None,
    )

    result = {
        "report_id": None,
        "trade_date": trade_date,
        "data_trade_date": _screening_data_trade_date(
            data_as_of,
            trade_date,
            base_trade_date,
        ),
        "data_as_of": data_as_of,
        "base_trade_date": base_trade_date or trade_date,
        "latest_trade_date": trade_date,
        "data_current": snapshot_data_current,
        "data_source": data_source,
        "market_phase": phase,
        "auto_refresh_enabled": should_refresh,
        "updated_at": now.isoformat(sep=" ", timespec="seconds"),
        "refresh_interval_seconds": 30,
        "sector_count": int(0 if sector_potential is None else len(sector_potential)),
        "result_cache_hit": False,
        "position_candidates": position_candidates,
        "position_candidate_count": len(position_candidates),
        "position_score_version": position_score_version(),
        "position_filter_debug": position_filter_debug,
        "bottom_filter_debug": _build_bottom_filter_debug(
            signal_market,
            intraday_bars,
            preliminary_rows,
            position_candidates,
        ),
        "_leader_codes": leader_codes,
        "stocks": legacy_rows,
        **stage_groups,
        "minute_data_sources": sorted({
            str(row.get("minute_data_source"))
            for row in legacy_rows if row.get("minute_data_source")
        }),
        "fallback_warnings": list(dict.fromkeys(
            list(history_warnings)
            + list(chip_warnings)
            + list(confirmation_warnings)
            + list(position_warnings)
            + [
                warning
                for row in legacy_rows
                for warning in (row.get("minute_data_warnings") or [])
            ]
        ))[:20],
    }
    _REALTIME_INTRADAY_RESULT_CACHE[cache_key] = (time.monotonic(), _json_safe(result))
    return result


def _last_history_rows_per_stock(
    history: pd.DataFrame,
    limit: int,
) -> pd.DataFrame:
    if history is None or history.empty:
        return pd.DataFrame() if history is None else history.copy()
    if "ts_code" not in history.columns:
        return history.tail(limit).copy()
    data = history.copy()
    if "trade_date" in data.columns:
        data = data.sort_values(["ts_code", "trade_date"])
    return data.groupby("ts_code", group_keys=False).tail(limit).reset_index(drop=True)


def _build_realtime_info_uncached(
    now: datetime | None = None,
    limit: int = 10,
    force_refresh: bool = False,
    debug: bool = False,
) -> dict[str, Any]:
    current = now or datetime.now()
    performance = {
        "market_sync_ms": 0.0,
        "intraday_total_ms": 0.0,
        "intraday_60m_ms": 0.0,
        "tail_1m_ms": 0.0,
        "tail_1m_wall_ms": 0.0,
        "chip_peak_ms": 0.0,
        "overnight_ms": 0.0,
        "minute_request_count": 0,
        "minute_cache_hit_count": 0,
        "provider_failure_count": 0,
        "used_stale_fallback": False,
    }
    market_started = time.perf_counter()
    entry_warnings = []
    try:
        latest_trade_date = str(get_trade_dates(n=1)[0])
    except Exception as exc:
        latest_trade_date = current.strftime("%Y%m%d")
        entry_warnings.append(f"Tushare交易日失败: {exc}")
    try:
        sync_metadata = sync_cached_market_data(
            force_current=False,
            retry_recent=False,
        )
    except Exception as exc:
        sync_metadata = {}
        entry_warnings.append(f"Tushare同步失败: {exc}")
    (
        market,
        history,
        base_trade_date,
        intraday_data_source,
        snapshot_data_current,
        source_warnings,
    ) = _load_realtime_market_inputs(latest_trade_date, sync_metadata or {})
    performance["market_sync_ms"] = (
        time.perf_counter() - market_started
    ) * 1000
    fallback_warnings = entry_warnings + source_warnings
    intraday_trade_date, intraday_data_source = _select_intraday_trade_date(
        latest_trade_date,
        base_trade_date,
        current,
        intraday_data_source,
    )
    price_map = _market_price_map(market) if base_trade_date == intraday_trade_date else {}

    realtime_minute_loader = _request_minute_loader(
        lambda code, start, end, freq, trade_date: (
            _persistent_minute_result(
                code,
                start,
                end,
                freq,
                trade_date,
                current,
                force_refresh=_force_minute_provider_refresh(
                    force_refresh,
                    freq,
                ),
            )
        ),
        stats=performance,
    )
    shared_context: dict[str, Any] = {}
    intraday_started = time.perf_counter()
    intraday = _build_realtime_intraday_section(
        market,
        history,
        intraday_trade_date,
        current,
        limit=limit,
        base_trade_date=base_trade_date,
        data_source=intraday_data_source,
        snapshot_data_current=snapshot_data_current,
        minute_loader=realtime_minute_loader,
        shared_context=shared_context,
        force_refresh=force_refresh,
        performance_stats=performance,
    )
    performance["intraday_total_ms"] = (
        time.perf_counter() - intraday_started
    ) * 1000
    intraday.pop("_leader_codes", None)

    overnight = {
        "trade_date": intraday_trade_date,
        "latest_trade_date": latest_trade_date,
        "data_current": snapshot_data_current,
        "data_source": "independent_refresh",
        "updated_at": current.isoformat(sep=" ", timespec="seconds"),
        "candidate_count": 0,
        "stocks": [],
        "warnings": [],
        "independent_refresh_required": True,
    }

    combined_warnings = list(dict.fromkeys(
        list(fallback_warnings)
        + list(intraday.get("fallback_warnings") or [])
        + list(overnight.get("warnings") or [])
    ))
    performance["provider_failure_count"] = sum(
        1
        for warning in combined_warnings
        if "失败" in str(warning) or "熔断" in str(warning)
    )
    performance = {
        key: round(value, 3) if isinstance(value, float) else value
        for key, value in performance.items()
    }
    return _json_safe({
        "trade_date": latest_trade_date,
        "base_trade_date": base_trade_date,
        "latest_trade_date": latest_trade_date,
        "intraday_trade_date": intraday_trade_date,
        "data_trade_date": intraday.get(
            "data_trade_date",
            base_trade_date,
        ),
        "data_as_of": intraday.get("data_as_of"),
        "data_current": snapshot_data_current,
        "data_source": intraday_data_source,
        "snapshot_data_source": intraday_data_source,
        "fallback_warnings": combined_warnings[:20],
        "updated_at": current.isoformat(sep=" ", timespec="seconds"),
        "sync_metadata": sync_metadata,
        "performance": performance,
        "intraday": _enrich_section(intraday, price_map, current),
        "overnight": _enrich_section(overnight, price_map, current),
    })


def _stale_age_seconds(data_updated_at: str | None, now: datetime) -> int:
    if not data_updated_at:
        return 0
    try:
        updated = datetime.fromisoformat(str(data_updated_at))
    except (TypeError, ValueError):
        return 0
    return max(0, int((now - updated).total_seconds()))


def _has_realtime_stocks(result: dict[str, Any]) -> bool:
    return bool(
        (result.get("intraday") or {}).get("stocks")
        or (result.get("overnight") or {}).get("stocks")
    )


def _legacy_database_realtime_result_key(limit: int) -> str:
    return (
        f"limit={max(1, min(int(limit), 100))}"
        f"|{macd_parameter_key()}"
        f"|{_REALTIME_MARKET_RELATIVE_RULE_VERSION}"
        f"|{_REALTIME_HISTORICAL_RESILIENCE_RULE_VERSION}"
    )


def _pre_chip_database_realtime_result_key(limit: int) -> str:
    return (
        _legacy_database_realtime_result_key(limit)
        + f"|{_REALTIME_BOTTOM_CONSOLIDATION_RULE_VERSION}"
    )


def _database_realtime_result_key(limit: int) -> str:
    return (
        _pre_chip_database_realtime_result_key(limit)
        + f"|{_REALTIME_CHIP_PEAK_RULE_VERSION}"
        + f"|{position_score_version()}"
    )


def _parse_cache_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def _database_result_is_fresh(
    payload: dict[str, Any],
    updated_at: Any,
    now: datetime,
) -> bool:
    updated = _parse_cache_datetime(updated_at)
    data_as_of = _parse_cache_datetime(payload.get("data_as_of"))
    if updated is None or data_as_of is None:
        return False
    current = now.replace(tzinfo=None) if now.tzinfo else now
    trade_date = str(
        payload.get("data_trade_date")
        or payload.get("trade_date")
        or ""
    )
    today = current.strftime("%Y%m%d")
    clock = current.strftime("%H:%M:%S")
    updated_age = max(0.0, (current - updated).total_seconds())

    if "09:30:00" <= clock <= "11:30:00" or (
        "13:00:00" <= clock < "15:00:00"
    ):
        if trade_date != today or updated_age > 25:
            return False
        return (
            data_as_of.strftime("%Y%m%d") == today
            and max(0.0, (current - data_as_of).total_seconds()) <= 120
        )
    if "11:30:00" < clock < "13:00:00":
        return (
            trade_date == today
            and data_as_of.strftime("%Y%m%d") == today
            and data_as_of.strftime("%H:%M:%S") >= "11:29:00"
        )
    if clock >= "15:00:00":
        return (
            trade_date == today
            and data_as_of.strftime("%Y%m%d") == today
            and data_as_of.strftime("%H:%M:%S") >= "14:59:00"
        )
    # Before the open, the completed prior-session snapshot may be shown,
    # but it must retain its non-current/stale label from the payload.
    return payload.get("data_current") is False


def _load_database_realtime_result(
    limit: int,
    *,
    now: datetime | None = None,
    require_fresh: bool = True,
    allow_legacy: bool = False,
) -> dict[str, Any] | None:
    cache_keys = [(_database_realtime_result_key(limit), False)]
    if allow_legacy:
        cache_keys.extend([
            (_pre_chip_database_realtime_result_key(limit), True),
            (_legacy_database_realtime_result_key(limit), True),
        ])
    for cache_key, legacy_rule_cache in cache_keys:
        try:
            cached = load_result_cache("realtime_info", cache_key)
        except Exception:
            continue
        if not cached or not isinstance(cached.get("payload"), dict):
            continue
        result = _json_safe(cached["payload"])
        if not _has_realtime_stocks(result):
            continue
        if require_fresh and not _database_result_is_fresh(
            result,
            cached.get("updated_at"),
            now or datetime.now(),
        ):
            continue
        result["cache_source"] = "database"
        result["cache_updated_at"] = cached.get("updated_at")
        result["result_cache_hit"] = True
        result["legacy_rule_cache"] = legacy_rule_cache
        return result
    return None


def _save_database_realtime_result(
    limit: int,
    result: dict[str, Any],
) -> list[str]:
    warnings: list[str] = []
    try:
        save_result_cache(
            "realtime_info",
            _database_realtime_result_key(limit),
            _json_safe(result),
        )
        current_trade_date = str(
            result.get("data_trade_date")
            or result.get("trade_date")
            or ""
        )
        keep_dates = list(dict.fromkeys(
            ([current_trade_date] if current_trade_date else [])
            + list(get_complete_dates(5))
        ))[:5]
        prune_realtime_cache(keep_dates)
    except Exception as exc:
        warnings.append(f"实时结果数据库缓存失败: {str(exc)[:120]}")
    return warnings


def _attach_daily_sector_context(market: pd.DataFrame) -> pd.DataFrame:
    if market is None or market.empty or "industry" not in market.columns:
        return pd.DataFrame() if market is None else market.copy()
    data = market.copy()
    data["industry"] = data["industry"].fillna("").astype(str)
    data["pct_chg"] = pd.to_numeric(data.get("pct_chg"), errors="coerce")
    grouped = data[data["industry"] != ""].groupby("industry")
    stats = grouped["pct_chg"].agg(["mean", "count"]).rename(
        columns={"mean": "sector_avg_pct_chg", "count": "sector_stock_count"}
    )
    stats["sector_up_ratio"] = grouped["pct_chg"].apply(
        lambda values: float((values > 0).mean())
    )
    stats["sector_limit_up_count"] = grouped["pct_chg"].apply(
        lambda values: int((values >= 9.5).sum())
    )
    stats = stats.sort_values("sector_avg_pct_chg", ascending=False)
    stats["sector_rank"] = range(1, len(stats) + 1)
    return data.merge(stats.reset_index(), on="industry", how="left")


def _attach_daily_macd_context(
    rows: list[dict[str, Any]],
    history: pd.DataFrame,
) -> list[dict[str, Any]]:
    settings = macd_provenance()
    fast = int(settings["macd_fast_period"])
    slow = int(settings["macd_slow_period"])
    signal_period = int(settings["macd_signal_period"])
    grouped = {
        str(code): bars.sort_values("trade_date")
        for code, bars in (
            history.groupby("ts_code")
            if history is not None and not history.empty and "ts_code" in history.columns
            else []
        )
    }
    result = []
    for source in rows:
        row = dict(source)
        closes = pd.to_numeric(
            grouped.get(str(row.get("ts_code") or ""), pd.DataFrame()).get("close"),
            errors="coerce",
        ).dropna()
        if len(closes) >= slow + signal_period:
            dif = closes.ewm(span=fast, adjust=False).mean() - closes.ewm(
                span=slow, adjust=False
            ).mean()
            dea = dif.ewm(span=signal_period, adjust=False).mean()
            rising = bool(dif.iloc[-1] > dif.iloc[-2])
            row.update({
                "macd_golden_cross": bool(
                    dif.iloc[-1] > dea.iloc[-1] and dif.iloc[-2] <= dea.iloc[-2]
                ),
                "macd_above_zero": bool(dif.iloc[-1] > 0),
                "daily_macd_status": "日线MACD向上" if rising else "日线MACD走弱",
            })
        result.append(row)
    return result


def _cached_chip_fields_by_code() -> dict[str, dict[str, Any]]:
    keys = (
        "chip_peak_price", "chip_secondary_peak_price", "chip_data_complete",
        "chip_build_position", "chip_washout_score", "chip_concentration_70_pct",
        "chip_concentration_90_pct", "chip_price_distance_pct", "chip_winner_rate",
        "chip_peak_bottom_position_pct", "chip_washout_label", "chip_washout_reason",
    )
    cached: dict[str, dict[str, Any]] = {}
    with _REALTIME_RESULT_LOCK:
        payloads = list(_REALTIME_RESULT_CACHE.values())
    for payload in reversed(payloads):
        intraday = payload.get("intraday") or {}
        for row in list(intraday.get("position_candidates") or []) + list(intraday.get("stocks") or []):
            code = str(row.get("ts_code") or "")
            if code and code not in cached and row.get("chip_data_complete"):
                cached[code] = {key: row.get(key) for key in keys}
    return cached


def build_daily_position_candidate_info(
    now: datetime | None = None,
    limit: int = 10,
    force_refresh: bool = False,
    debug: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    current = now or datetime.now()
    dates = get_complete_dates(1)
    if not dates:
        raise RuntimeError("数据库中没有完整日线交易日")
    trade_date = str(dates[0])
    market = load_market_snapshot(trade_date)
    history = load_recent_daily(trade_date, 40)
    market = _attach_daily_sector_context(market)
    pool, history_debug, warnings = _build_history_position_pool(
        market, history, trade_date
    )
    chip_by_code = _cached_chip_fields_by_code()
    pool = [
        {
            **row,
            **chip_by_code.get(str(row.get("ts_code") or ""), {}),
            "current_price": row.get("close"),
            "data_as_of": trade_date,
            "data_source": "database_daily",
            "tail_after_1430_available": False,
        }
        for row in pool
    ]
    pool = _attach_daily_macd_context(pool, history)
    pool, confirmation_warnings = _refresh_position_confirmation_fields(
        pool, history, trade_date
    )
    scored, candidates, score_warnings, filter_debug = (
        _build_unified_position_candidates(
            pool,
            limit=min(max(1, int(limit)), 10),
            market_phase="日线收盘",
        )
    )
    filter_debug["history_funnel"] = history_debug
    elapsed_ms = (time.perf_counter() - started) * 1000
    return _json_safe({
        "trade_date": trade_date,
        "base_trade_date": trade_date,
        "latest_trade_date": trade_date,
        "data_trade_date": trade_date,
        "data_as_of": trade_date,
        "data_current": False,
        "data_source": "database_daily",
        "snapshot_data_source": "database_daily",
        "data_status": "daily",
        "data_status_label": "数据库日线",
        "updated_at": current.isoformat(sep=" ", timespec="seconds"),
        "result_cache_hit": False,
        "cache_source": "database_daily",
        "fallback_warnings": list(dict.fromkeys(
            warnings + confirmation_warnings + score_warnings
        ))[:20],
        "performance": {
            "daily_position_total_ms": round(elapsed_ms, 3),
            "network_request_count": 0,
            "minute_request_count": 0,
            "chip_network_request_count": 0,
        },
        "intraday": {
            "trade_date": trade_date,
            "data_trade_date": trade_date,
            "data_as_of": trade_date,
            "data_source": "database_daily",
            "market_phase": "日线收盘模型",
            "position_candidates": candidates,
            "position_candidate_count": len(candidates),
            "position_score_version": position_score_version(),
            "position_filter_debug": filter_debug,
            "stocks": scored[:max(1, int(limit))],
        },
        "overnight": {"stocks": [], "independent_refresh_required": True},
        **macd_provenance(),
    })


def build_realtime_info(
    now: datetime | None = None,
    limit: int = 20,
    force_refresh: bool = False,
    debug: bool = False,
) -> dict[str, Any]:
    current = now or datetime.now()
    successful_key = (
        int(limit),
        macd_parameter_key(),
        _REALTIME_MARKET_RELATIVE_RULE_VERSION,
        _REALTIME_HISTORICAL_RESILIENCE_RULE_VERSION,
        _REALTIME_BOTTOM_CONSOLIDATION_RULE_VERSION,
        _REALTIME_CHIP_PEAK_RULE_VERSION,
        position_score_version(),
    )
    cache_key = _realtime_result_key(limit, current)
    if not force_refresh and not debug:
        with _REALTIME_RESULT_LOCK:
            cached = _REALTIME_RESULT_CACHE.get(cache_key)
        if cached is not None:
            result = _filter_realtime_output(cached)
            result["result_cache_hit"] = True
            result["cache_source"] = "memory"
            return result
        database_cached = _load_database_realtime_result(
            limit,
            now=current,
        )
        if database_cached is not None:
            return _filter_realtime_output(database_cached)

    build_error = None
    try:
        build_kwargs = {
            "now": current,
            "limit": limit,
            "force_refresh": force_refresh,
        }
        if debug:
            build_kwargs["debug"] = True
        fresh = _build_realtime_info_uncached(**build_kwargs)
    except Exception as exc:
        build_error = str(exc)
        fresh = {
            "trade_date": current.strftime("%Y%m%d"),
            "base_trade_date": None,
            "latest_trade_date": current.strftime("%Y%m%d"),
            "intraday_trade_date": current.strftime("%Y%m%d"),
            "data_current": False,
            "data_source": "unavailable",
            "snapshot_data_source": "unavailable",
            "fallback_warnings": [f"实时信息刷新失败: {build_error}"],
            "updated_at": current.isoformat(sep=" ", timespec="seconds"),
            "sync_metadata": {},
            "performance": {
                "market_sync_ms": 0.0,
                "intraday_total_ms": 0.0,
                "intraday_60m_ms": 0.0,
                "tail_1m_ms": 0.0,
                "tail_1m_wall_ms": 0.0,
                "chip_peak_ms": 0.0,
                "overnight_ms": 0.0,
                "minute_request_count": 0,
                "minute_cache_hit_count": 0,
                "provider_failure_count": 1,
                "used_stale_fallback": False,
            },
            "intraday": {"stocks": []},
            "overnight": {"stocks": []},
        }

    if debug:
        result = _filter_realtime_output(fresh)
        result.update({
            "data_status": "debug",
            "data_status_label": "调试数据",
            "data_updated_at": current.isoformat(
                sep=" ", timespec="seconds"
            ),
            "stale_age_seconds": 0,
            "result_cache_hit": False,
            "cache_source": "debug",
            "cache_updated_at": current.isoformat(
                sep=" ", timespec="seconds"
            ),
        })
        return result

    if _has_realtime_stocks(fresh):
        fresh = _filter_realtime_output(fresh)
        result = {
            **fresh,
            "data_status": "live",
            "data_status_label": "实时数据",
            "data_updated_at": current.isoformat(
                sep=" ", timespec="seconds"
            ),
            "stale_age_seconds": 0,
            "result_cache_hit": False,
            "cache_source": "fresh",
            "cache_updated_at": current.isoformat(
                sep=" ", timespec="seconds"
            ),
        }
        cache_warnings = _save_database_realtime_result(limit, result)
        if cache_warnings:
            result["fallback_warnings"] = list(dict.fromkeys(
                list(result.get("fallback_warnings") or [])
                + cache_warnings
            ))[:20]
        with _REALTIME_RESULT_LOCK:
            _LAST_SUCCESSFUL_REALTIME_RESULTS[successful_key] = _json_safe(result)
    else:
        with _REALTIME_RESULT_LOCK:
            previous = _LAST_SUCCESSFUL_REALTIME_RESULTS.get(successful_key)
        if previous is None:
            previous = _load_database_realtime_result(
                limit,
                now=current,
                require_fresh=False,
                allow_legacy=True,
            )
        if previous is not None:
            previous = _filter_realtime_output(previous)
            warnings = list(previous.get("fallback_warnings") or [])
            stale_performance = dict(previous.get("performance") or {})
            stale_performance["used_stale_fallback"] = True
            warning = (
                f"当前实时源未返回可用数据，展示最近成功结果"
                + (f": {build_error}" if build_error else "")
            )
            result = {
                **_json_safe(previous),
                "data_status": "stale",
                "data_status_label": "备用缓存",
                "stale_age_seconds": _stale_age_seconds(
                    previous.get("data_updated_at"), current
                ),
                "result_cache_hit": False,
                "cache_source": previous.get("cache_source", "memory"),
                "cache_updated_at": previous.get("cache_updated_at"),
                "performance": stale_performance,
                "fallback_warnings": list(
                    dict.fromkeys(warnings + [warning])
                )[:20],
                "updated_at": current.isoformat(
                    sep=" ", timespec="seconds"
                ),
            }
        else:
            result = {
                **fresh,
                "data_status": "unavailable",
                "data_status_label": "数据不可用",
                "data_updated_at": None,
                "stale_age_seconds": 0,
                "result_cache_hit": False,
            }

    safe_result = _filter_realtime_output({
        **result,
        **macd_provenance(),
    })
    with _REALTIME_RESULT_LOCK:
        _REALTIME_RESULT_CACHE[cache_key] = safe_result
        if len(_REALTIME_RESULT_CACHE) > 20:
            oldest_key = next(iter(_REALTIME_RESULT_CACHE))
            _REALTIME_RESULT_CACHE.pop(oldest_key, None)
    return _json_safe(safe_result)


def build_realtime_tail_premium_info(
    now: datetime | None = None,
    limit: int = 20,
    force_refresh: bool = False,
    debug: bool = False,
) -> dict[str, Any]:
    current = now or datetime.now()
    warnings: list[str] = []
    try:
        latest_trade_date = str(get_trade_dates(n=1)[0])
    except Exception as exc:
        latest_trade_date = current.strftime("%Y%m%d")
        warnings.append(f"Tushare交易日失败: {exc}")
    try:
        sync_metadata = sync_cached_market_data(force_current=force_refresh)
    except Exception as exc:
        sync_metadata = {}
        warnings.append(f"Tushare同步失败: {exc}")
    (
        market,
        history,
        base_trade_date,
        data_source,
        data_current,
        source_warnings,
    ) = _load_realtime_market_inputs(latest_trade_date, sync_metadata or {})
    warnings.extend(source_warnings)
    trade_date, data_source = _select_intraday_trade_date(
        latest_trade_date, base_trade_date, current, data_source,
    )

    def minute_loader(code, start, end, freq, requested_trade_date):
        return _persistent_minute_result(
            code,
            start,
            end,
            freq,
            requested_trade_date,
            current,
            force_refresh=force_refresh,
        )

    result = build_realtime_tail_premium_monitor(
        limit=limit,
        max_fetch=max(_REALTIME_OVERNIGHT_MAX_FETCH, limit),
        now=current,
        market_override=market,
        history_override=_last_history_rows_per_stock(history, 100),
        trade_date_override=trade_date,
        minute_loader=minute_loader,
        source_metadata={
            "latest_trade_date": latest_trade_date,
            "data_current": data_current,
            "data_source": data_source,
        },
        debug=debug,
    )
    result["warnings"] = list(dict.fromkeys(
        warnings + list(result.get("warnings") or [])
    ))[:20]
    return _json_safe(result)
