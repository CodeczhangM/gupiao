from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from data_service import get_trade_dates
from database import get_latest_report
from overnight_monitor_service import (
    _cached_minute_bars,
    _mask_unavailable_tail_fields,
    _opening_auction_signal,
    _realtime_end_datetime,
)
from realtime_cache import (
    load_minute_cache,
    minute_cache_is_fresh,
    save_minute_cache,
)
from strategy import _macd_kdj_60m_signal


def _clock_text(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%H:%M:%S")


def _market_phase(now: datetime | None = None) -> tuple[str, bool]:
    clock = _clock_text(now)
    if clock >= "15:00:00":
        return "收盘结果", False
    if clock < "09:30:00":
        return "盘前", False
    return "盘中监控", True


def _flatten_intraday_stocks(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    seen = set()
    for sector in report.get("sector_potential") or []:
        industry = sector.get("industry_name") or sector.get("industry") or ""
        for stock in sector.get("intraday_signal_stocks") or []:
            ts_code = str(stock.get("ts_code") or "")
            if not ts_code or ts_code in seen:
                continue
            seen.add(ts_code)
            rows.append({**stock, "industry": industry})
    return rows


def _datetime_window(trade_date: str, start_clock: str, end_clock: str) -> tuple[str, str]:
    day = datetime.strptime(str(trade_date), "%Y%m%d")
    return day.strftime(f"%Y-%m-%d {start_clock}"), day.strftime(f"%Y-%m-%d {end_clock}")


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


def _persistent_minute_bars(
    ts_code: str,
    start_datetime: str,
    end_datetime: str,
    freq: str,
    now: datetime,
) -> pd.DataFrame:
    try:
        cached = load_minute_cache(
            ts_code,
            start_datetime,
            end_datetime,
            freq,
        )
        if minute_cache_is_fresh(
            cached,
            start_datetime,
            end_datetime,
            now,
            freq,
        ):
            return cached.copy()
    except Exception:
        pass
    bars = _cached_minute_bars(
        ts_code,
        start_datetime,
        end_datetime,
        freq=freq,
    )
    if bars is not None and not bars.empty:
        try:
            save_minute_cache(
                bars,
                freq,
                "tushare",
                str(end_datetime)[:10].replace("-", ""),
            )
        except Exception:
            pass
    return bars


def _main_force_status(signal: dict[str, Any]) -> tuple[str, str]:
    tail_return = signal.get("tail_return_after_1430")
    auction_return = signal.get("tail_auction_return")
    tail_volume_ratio = signal.get("tail_volume_ratio")
    close_position = signal.get("tail_close_position")
    if tail_return is None or close_position is None:
        return "观察", "数据不足"

    tail_return = float(tail_return or 0)
    auction_return = float(auction_return or 0)
    tail_volume_ratio = float(tail_volume_ratio or 0)
    close_position = float(close_position or 0)

    if tail_return <= -0.5 or (tail_volume_ratio >= 1.5 and close_position <= 0.35):
        return "放量分歧", "尾盘放量回落或收在低位"
    if tail_return > 0 and close_position >= 0.7 and (tail_volume_ratio >= 1.2 or auction_return > 0.1):
        return "主力抢筹", "尾盘上推并收在高位"
    return "观察", "尾盘确认不足"


def _monitor_row(stock: dict[str, Any], trade_date: str, fetch_realtime: bool, now: datetime | None = None) -> dict[str, Any]:
    ts_code = str(stock.get("ts_code") or "")
    if not fetch_realtime:
        signal = _mask_unavailable_tail_fields(
            {**stock, **_opening_auction_signal(stock, _realtime_end_datetime(trade_date, now=now))},
            _realtime_end_datetime(trade_date, now=now),
        )
        status, status_reason = _main_force_status(signal)
        return {
            **signal,
            "data_as_of": None,
            "main_force_status": status,
            "main_force_reason": status_reason,
        }

    end_datetime = _realtime_end_datetime(trade_date, now=now)
    start_60m, _default_end_60m = _datetime_window(trade_date, "09:30:00", "15:00:00")
    tail_start, _default_tail_end = _datetime_window(trade_date, "14:25:00", "15:00:00")
    current = now or datetime.now()
    bars_60m = _persistent_minute_bars(
        ts_code,
        start_60m,
        end_datetime,
        "60min",
        current,
    )
    tail_1m = _persistent_minute_bars(
        ts_code,
        tail_start,
        end_datetime,
        "1min",
        current,
    )
    signal = _macd_kdj_60m_signal(
        pd.Series(stock),
        {"60m": bars_60m, "tail_1m": tail_1m},
    )
    if not signal:
        signal = {**stock, "next_day_bias": "数据不足", "tail_strength_score": None}
    signal = {**stock, **signal, **_opening_auction_signal(stock, end_datetime)}
    signal = _mask_unavailable_tail_fields(signal, end_datetime)
    status, status_reason = _main_force_status(signal)
    return {
        **signal,
        "data_as_of": _latest_bar_time(bars_60m, tail_1m),
        "main_force_status": status,
        "main_force_reason": status_reason,
    }


def build_intraday_monitor(fetch_realtime: bool = True, now: datetime | None = None) -> dict[str, Any]:
    report = get_latest_report()
    if not report:
        raise LookupError("还没有选股报告，请先运行扫描")

    phase, should_refresh = _market_phase(now)
    trade_date = str(report.get("trade_date") or "")
    latest_trade_date = None
    data_current = False
    try:
        latest_trade_date = str(get_trade_dates(n=1)[0])
        data_current = trade_date == latest_trade_date
    except Exception:
        latest_trade_date = trade_date
        data_current = True
    stocks = _flatten_intraday_stocks(report)
    rows = []
    for stock in stocks:
        try:
            rows.append(_monitor_row(stock, trade_date, fetch_realtime=fetch_realtime and should_refresh, now=now))
        except Exception as exc:
            end_datetime = _realtime_end_datetime(trade_date, now=now)
            fallback = _mask_unavailable_tail_fields(
                {**stock, **_opening_auction_signal(stock, end_datetime)},
                end_datetime,
            )
            rows.append({
                **fallback,
                "data_as_of": None,
                "next_day_bias": "数据不足",
                "main_force_status": "观察",
                "main_force_reason": f"分时更新失败: {str(exc)[:120]}",
            })

    rows = sorted(
        rows,
        key=lambda item: (
            item.get("main_force_status") == "主力抢筹",
            item.get("next_day_bias") == "高开偏强",
            float(item.get("tail_strength_score") or 0),
            float(item.get("intraday_signal_score") or 0),
        ),
        reverse=True,
    )
    data_as_of = max(
        (
            str(row.get("data_as_of"))
            for row in rows
            if row.get("data_as_of")
        ),
        default=None,
    )
    return {
        "report_id": report.get("id"),
        "trade_date": trade_date,
        "data_trade_date": trade_date,
        "data_as_of": data_as_of,
        "latest_trade_date": latest_trade_date,
        "data_current": data_current,
        "market_phase": phase,
        "auto_refresh_enabled": should_refresh,
        "updated_at": (now or datetime.now()).isoformat(sep=" ", timespec="seconds"),
        "refresh_interval_seconds": 30,
        "stocks": rows,
    }
