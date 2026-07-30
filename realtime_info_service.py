from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import threading
import time
from typing import Any, Callable

import pandas as pd

from data_service import get_trade_dates, sync_cached_market_data
from intraday_monitor_service import _main_force_status, _market_phase
from market_cache import load_market_snapshot, load_recent_daily
from realtime_market_source import (
    MinuteLoadResult,
    load_eastmoney_market_snapshot,
    load_minutes_with_fallback,
)
from overnight_monitor_service import (
    build_overnight_monitor,
    _cached_minute_bars,
    _datetime_window,
    _history_window,
    _json_safe,
    _mask_unavailable_tail_fields,
    _realtime_end_datetime,
)
from strategy import _attach_intraday_signal_stocks, _macd_kdj_60m_signal, rank_sector_potential


_REALTIME_SECTOR_LIMIT = 8
_REALTIME_CANDIDATES_PER_SECTOR = 6
_REALTIME_PICKS_PER_SECTOR = 5
_REALTIME_OVERNIGHT_MAX_FETCH = 15
_REALTIME_OVERNIGHT_MAX_LEADERS = 15
_REALTIME_INTRADAY_CACHE_TTL_SECONDS = 58
_REALTIME_INTRADAY_RESULT_CACHE: dict[tuple[str, str, int, str], tuple[float, dict[str, Any]]] = {}


def _request_minute_loader(
    result_loader: Callable[..., MinuteLoadResult],
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
            return MinuteLoadResult(
                cached.bars.copy(),
                cached.source,
                list(cached.warnings),
            )
        loaded = result_loader(ts_code, start, end, freq, trade_date)
        stored = MinuteLoadResult(
            loaded.bars.copy(),
            loaded.source,
            list(loaded.warnings),
        )
        with lock:
            cache[key] = stored
        return MinuteLoadResult(
            stored.bars.copy(),
            stored.source,
            list(stored.warnings),
        )

    return load


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


def _load_realtime_market_inputs(
    latest_trade_date: str,
    sync_metadata: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, str, str, bool, list[str]]:
    market = load_market_snapshot(latest_trade_date)
    if _snapshot_matches_trade_date(market, latest_trade_date):
        return (
            market, load_recent_daily(latest_trade_date, 100),
            latest_trade_date, "current_snapshot", True, [],
        )

    external_market, external_error = load_eastmoney_market_snapshot(latest_trade_date)
    if _snapshot_matches_trade_date(external_market, latest_trade_date):
        return (
            external_market, load_recent_daily(latest_trade_date, 100),
            latest_trade_date, "eastmoney_snapshot_fallback", True, [],
        )

    fallback_trade_date = str(sync_metadata.get("data_trade_date") or "")
    if not fallback_trade_date or fallback_trade_date == latest_trade_date:
        try:
            dates = get_trade_dates(n=2)
            fallback_trade_date = str(dates[1]) if len(dates) > 1 else latest_trade_date
        except Exception:
            fallback_trade_date = latest_trade_date

    fallback_market = load_market_snapshot(fallback_trade_date)
    fallback_history = load_recent_daily(fallback_trade_date, 100)
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
) -> MinuteLoadResult:
    def primary_loader(code, start, end, freq="60min"):
        bars = _cached_minute_bars(code, start, end, freq=freq)
        if _has_trade_date_minutes({freq: bars}, trade_date):
            return bars
        fallback_end = _fallback_1459_end_datetime(trade_date, end)
        if not fallback_end:
            return bars
        fallback = _cached_minute_bars(code, start, fallback_end, freq=freq)
        return fallback if _has_trade_date_minutes({freq: fallback}, trade_date) else bars

    return load_minutes_with_fallback(
        ts_code,
        start_datetime,
        end_datetime,
        freq,
        trade_date,
        primary_loader=primary_loader,
    )


def _minute_missing_reason(trade_date: str, end_datetime: str) -> str:
    fallback_end = _fallback_1459_end_datetime(trade_date, end_datetime)
    if fallback_end:
        attempted = f"{str(end_datetime)[-8:-3]}和{fallback_end[-8:-3]}"
        return f"当日分时未返回，已尝试{attempted}，等待数据源更新"
    return "当日分时未返回，等待数据源更新"


def _apply_minute_snapshots_to_market(market: pd.DataFrame, bars_by_code: dict[str, dict[str, pd.DataFrame]], trade_date: str) -> pd.DataFrame:
    if market is None or market.empty or not bars_by_code or "ts_code" not in market.columns:
        return market
    result = market.copy()
    result["ts_code"] = result["ts_code"].astype(str)
    for ts_code, bars in bars_by_code.items():
        mask = result["ts_code"] == str(ts_code)
        if not mask.any():
            continue
        previous_close = result.loc[mask, "close"].iloc[0] if "close" in result else None
        snapshot = _minute_price_snapshot(str(ts_code), bars, trade_date, previous_close)
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
    candidates = market[market["industry"].astype(str).isin(industries)].copy()
    for column in ("turnover_rate", "volume_ratio", "amount", "pct_chg"):
        candidates[column] = pd.to_numeric(candidates[column], errors="coerce") if column in candidates else 0
    candidates = candidates[
        candidates["turnover_rate"].between(2, 10, inclusive="both")
        & (candidates["volume_ratio"] > 2)
    ].copy()
    if candidates.empty:
        return {}

    candidates = candidates.sort_values(["industry", "amount", "volume_ratio"], ascending=[True, False, False])
    candidates = candidates.groupby("industry", group_keys=False).head(_REALTIME_CANDIDATES_PER_SECTOR)
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


def _has_any_trade_date_signal_bars(bars_by_code: dict[str, dict[str, pd.DataFrame]], trade_date: str) -> bool:
    return any(_has_trade_date_minutes(bars, trade_date) for bars in (bars_by_code or {}).values())


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
) -> dict[str, Any]:
    cache_key = (
        str(trade_date),
        str(base_trade_date or trade_date),
        int(limit),
        _realtime_end_datetime(trade_date, now=now),
    )
    cached = _REALTIME_INTRADAY_RESULT_CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] <= _REALTIME_INTRADAY_CACHE_TTL_SECONDS:
        result = _json_safe(cached[1])
        result["result_cache_hit"] = True
        return result

    phase, should_refresh = _market_phase(now)
    realtime_market = _fill_missing_realtime_volume_ratio(
        market,
        history,
        trade_date,
        now,
    )
    sector_potential = rank_sector_potential(realtime_market, history, limit=_REALTIME_SECTOR_LIMIT)
    intraday_bars = _load_realtime_intraday_signal_bars(
        realtime_market,
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
            realtime_market,
            sector_potential,
            str(base_trade_date),
            now,
            minute_loader=minute_loader,
        )
        if fallback_bars:
            intraday_bars = fallback_bars
    signal_market = _apply_minute_snapshots_to_market(realtime_market, intraday_bars, trade_date)
    sector_potential = _attach_intraday_signal_stocks(
        sector_potential,
        signal_market,
        intraday_bars,
        per_sector=_REALTIME_PICKS_PER_SECTOR,
    )

    rows = []
    end_datetime = _realtime_end_datetime(trade_date, now=now)
    signal_market_by_code = (
        signal_market.set_index(signal_market["ts_code"].astype(str)).to_dict("index")
        if signal_market is not None and not signal_market.empty and "ts_code" in signal_market.columns
        else {}
    )
    for sector in (sector_potential.to_dict("records") if sector_potential is not None and not sector_potential.empty else []):
        industry = sector.get("industry_name") or sector.get("industry") or ""
        for stock in sector.get("intraday_signal_stocks") or []:
            ts_code = str(stock.get("ts_code") or "")
            market_snapshot = signal_market_by_code.get(ts_code, {})
            signal = {**market_snapshot, **stock, "industry": industry}
            tail_loaded = _load_tail_minute_bars_for_pick(
                ts_code,
                trade_date,
                end_datetime,
                minute_loader=minute_loader,
            )
            tail_1m = tail_loaded.bars
            current_day_minutes = _has_trade_date_minutes(intraday_bars.get(ts_code, {}), trade_date)
            if not tail_1m.empty and ts_code in intraday_bars:
                current_day_minutes = current_day_minutes or _has_trade_date_minutes({"tail_1m": tail_1m}, trade_date)
                tail_snapshot = _minute_price_snapshot(
                    ts_code,
                    {"60m": intraday_bars[ts_code].get("60m"), "tail_1m": tail_1m},
                    trade_date,
                    market_snapshot.get("close"),
                )
                signal = {**signal, **tail_snapshot}
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
            rows.append({
                **signal,
                "main_force_status": status,
                "main_force_reason": reason,
            })

    rows = sorted(
        rows,
        key=lambda item: (
            item.get("main_force_status") == "主力抢筹",
            item.get("next_day_bias") == "高开偏强",
            float(item.get("intraday_signal_score") or 0),
            float(item.get("volume_ratio") or 0),
        ),
        reverse=True,
    )[: max(1, min(int(limit), 100))]

    result = {
        "report_id": None,
        "trade_date": trade_date,
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
        "stocks": rows,
        "minute_data_sources": sorted({
            str(row.get("minute_data_source"))
            for row in rows if row.get("minute_data_source")
        }),
        "fallback_warnings": list(dict.fromkeys(
            warning
            for row in rows
            for warning in (row.get("minute_data_warnings") or [])
        ))[:20],
    }
    _REALTIME_INTRADAY_RESULT_CACHE[cache_key] = (time.monotonic(), _json_safe(result))
    return result


def build_realtime_info(now: datetime | None = None, limit: int = 10) -> dict[str, Any]:
    current = now or datetime.now()
    entry_warnings = []
    try:
        latest_trade_date = str(get_trade_dates(n=1)[0])
    except Exception as exc:
        latest_trade_date = current.strftime("%Y%m%d")
        entry_warnings.append(f"Tushare交易日失败: {exc}")
    try:
        sync_metadata = sync_cached_market_data(force_current=True)
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
    fallback_warnings = entry_warnings + source_warnings
    intraday_trade_date, intraday_data_source = _select_intraday_trade_date(
        latest_trade_date,
        base_trade_date,
        current,
        intraday_data_source,
    )
    price_map = _market_price_map(market) if base_trade_date == intraday_trade_date else {}

    realtime_minute_loader = _request_minute_loader(
        _minute_result_with_1459_fallback
    )
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
    )

    try:
        overnight = build_overnight_monitor(
            limit=limit,
            max_fetch=_REALTIME_OVERNIGHT_MAX_FETCH,
            max_leaders=_REALTIME_OVERNIGHT_MAX_LEADERS,
            now=current,
            market_override=market,
            history_override=history,
            trade_date_override=intraday_trade_date,
            minute_loader=realtime_minute_loader,
            source_metadata={
                "latest_trade_date": latest_trade_date,
                "data_current": snapshot_data_current,
                "data_source": intraday_data_source,
            },
        )
    except Exception as exc:
        overnight = {
            "trade_date": latest_trade_date,
            "latest_trade_date": latest_trade_date,
            "data_current": snapshot_data_current,
            "data_source": "overnight_unavailable",
            "market_phase": intraday.get("market_phase"),
            "auto_refresh_enabled": intraday.get("auto_refresh_enabled"),
            "updated_at": current.isoformat(sep=" ", timespec="seconds"),
            "refresh_interval_seconds": 30,
            "candidate_count": 0,
            "failed_count": 1,
            "warnings": [f"隔夜选股快速刷新失败: {str(exc)[:160]}"],
            "stocks": [],
        }

    combined_warnings = list(dict.fromkeys(
        list(fallback_warnings)
        + list(intraday.get("fallback_warnings") or [])
        + list(overnight.get("warnings") or [])
    ))
    return _json_safe({
        "trade_date": latest_trade_date,
        "base_trade_date": base_trade_date,
        "latest_trade_date": latest_trade_date,
        "intraday_trade_date": intraday_trade_date,
        "data_current": snapshot_data_current,
        "data_source": intraday_data_source,
        "snapshot_data_source": intraday_data_source,
        "fallback_warnings": combined_warnings[:20],
        "updated_at": current.isoformat(sep=" ", timespec="seconds"),
        "sync_metadata": sync_metadata,
        "intraday": _enrich_section(intraday, price_map, current),
        "overnight": _enrich_section(overnight, price_map, current),
    })
