from __future__ import annotations

from datetime import datetime, timedelta
import threading
from typing import Any

import pandas as pd

from cycle_watch_repository import (
    delete_watch_stock,
    latest_effective_evaluation,
    list_cycle_history,
    list_watch_stocks,
    mark_cycle_alerts_read,
    save_cycle_evaluation,
    update_watch_stock,
    upsert_watch_stock,
)
from cycle_watch_scoring import (
    STATUS_LABELS,
    evaluate_cycle_entry,
    normalize_cycle_watch_code,
)
from data_service import get_trade_dates
from market_cache import load_market_snapshot, load_recent_daily
from overnight_monitor_service import _cached_minute_bars


_CHECK_LOCK = threading.Lock()
_STATUS_PRIORITY = {"data_delayed": 0, "watch": 1, "low_buy": 2, "confirmed": 3}


def _validate_changes(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload or {})
    note = result.get("note")
    if note is not None and len(str(note)) > 500:
        raise ValueError("备注不能超过500个字符")
    low = result.get("planned_low_price")
    high = result.get("planned_high_price")
    for label, value in (("计划低吸下限", low), ("计划低吸上限", high)):
        if value is not None and float(value) <= 0:
            raise ValueError(f"{label}必须大于0")
    if low is not None and high is not None and float(low) > float(high):
        raise ValueError("计划低吸下限不能高于上限")
    return result


def _latest_trade_date() -> str:
    dates = get_trade_dates(n=1)
    if not dates:
        raise LookupError("没有可用交易日")
    return str(dates[0])


def _lookup_stock_name(ts_code: str) -> str | None:
    try:
        trade_date = _latest_trade_date()
        snapshot = load_market_snapshot(trade_date)
        if snapshot is not None and not snapshot.empty and "ts_code" in snapshot:
            rows = snapshot[snapshot["ts_code"].astype(str) == ts_code]
            if not rows.empty and rows.iloc[0].get("name"):
                return str(rows.iloc[0]["name"])
        history = load_recent_daily(trade_date, 5)
        if history is not None and not history.empty and "name" in history:
            rows = history[history["ts_code"].astype(str) == ts_code]
            if not rows.empty and rows.iloc[-1].get("name"):
                return str(rows.iloc[-1]["name"])
    except Exception:
        return None
    return None


def add_cycle_watch(payload: dict[str, Any]) -> dict[str, Any]:
    values = _validate_changes(payload)
    ts_code = normalize_cycle_watch_code(values.get("ts_code"))
    values["ts_code"] = ts_code
    values["name"] = values.get("name") or _lookup_stock_name(ts_code)
    return upsert_watch_stock(values)


def edit_cycle_watch(ts_code: str, changes: dict[str, Any]) -> dict[str, Any]:
    code = normalize_cycle_watch_code(ts_code)
    values = _validate_changes(changes)
    saved = update_watch_stock(code, values)
    if not saved:
        raise LookupError("关注股票不存在")
    return saved


def remove_cycle_watch(ts_code: str) -> None:
    code = normalize_cycle_watch_code(ts_code)
    if not delete_watch_stock(code):
        raise LookupError("关注股票不存在")


def get_cycle_watch_history(ts_code: str, limit: int = 50) -> list[dict[str, Any]]:
    return list_cycle_history(normalize_cycle_watch_code(ts_code), limit)


def _group_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    confirmed = sorted(
        [row for row in rows if row.get("status") == "confirmed"],
        key=lambda row: (
            float(row.get("opportunity_score") or 0),
            float((row.get("factors") or {}).get("confirmation_count") or 0),
            float((row.get("factors") or {}).get("relative_strength") or 0),
        ),
        reverse=True,
    )
    low_buy = sorted(
        [row for row in rows if row.get("status") == "low_buy"],
        key=lambda row: (
            float((row.get("factors") or {}).get("support_distance_pct") or 999),
            float((row.get("factors") or {}).get("volume_contraction") or 999),
            -float(row.get("opportunity_score") or 0),
        ),
    )
    watch = sorted(
        [row for row in rows if row.get("status") == "watch"],
        key=lambda row: float(row.get("opportunity_score") or 0),
        reverse=True,
    )
    delayed = [row for row in rows if row.get("status") == "data_delayed"]
    return {
        "confirmed_stocks": confirmed,
        "low_buy_stocks": low_buy,
        "watch_stocks": watch,
        "delayed_stocks": delayed,
    }


def get_cycle_watchlist() -> dict[str, Any]:
    stocks = list_watch_stocks(enabled_only=False)
    rows = []
    for item in stocks:
        history = list_cycle_history(str(item.get("ts_code")), 1)
        rows.append({**item, **(history[0] if history else {})})
    groups = _group_rows(rows)
    return {
        "stocks": rows,
        **groups,
        "unread_alert_count": sum(
            1 for row in rows
            if row.get("is_new_alert") and not row.get("alert_read")
        ),
    }


def _evaluate_watch_stock(
    watch: dict[str, Any],
    trade_date: str,
    now: datetime,
) -> dict[str, Any]:
    ts_code = str(watch["ts_code"])
    history = load_recent_daily(trade_date, 60)
    daily = (
        history[history["ts_code"].astype(str) == ts_code].copy()
        if history is not None and not history.empty and "ts_code" in history
        else pd.DataFrame()
    )
    snapshot = load_market_snapshot(trade_date)
    realtime: dict[str, Any] = {}
    if snapshot is not None and not snapshot.empty and "ts_code" in snapshot:
        rows = snapshot[snapshot["ts_code"].astype(str) == ts_code]
        if not rows.empty:
            realtime = rows.iloc[0].to_dict()
    start = (now - timedelta(days=45)).strftime("%Y-%m-%d 09:30:00")
    end = now.strftime("%Y-%m-%d %H:%M:%S")
    bars_60m = _cached_minute_bars(ts_code, start, end, freq="60min")
    result = evaluate_cycle_entry(
        ts_code,
        daily,
        bars_60m,
        realtime,
        watch.get("planned_low_price"),
        watch.get("planned_high_price"),
    )
    result["name"] = watch.get("name") or realtime.get("name")
    result["note"] = watch.get("note")
    result["data_as_of"] = (
        str(pd.to_datetime(bars_60m["trade_time"], errors="coerce").max())
        if isinstance(bars_60m, pd.DataFrame)
        and not bars_60m.empty
        and "trade_time" in bars_60m
        else None
    )
    return result


def _delayed_evaluation(
    watch: dict[str, Any],
    reason: str,
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "ts_code": watch["ts_code"],
        "name": watch.get("name"),
        "note": watch.get("note"),
        "status": "data_delayed",
        "status_label": STATUS_LABELS["data_delayed"],
        "opportunity_score": 0,
        "current_price": None,
        "pct_chg": None,
        "support_price": None,
        "matched_conditions": [],
        "missing_conditions": [reason],
        "risk_items": [],
        "invalidation_reason": reason,
        "factors": {"previous_effective_status": (previous or {}).get("status")},
        "data_as_of": None,
    }


def check_cycle_watchlist(
    ts_code: str | None = None,
    schedule_slot: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now()
    trade_date = _latest_trade_date()
    current_date = current.strftime("%Y%m%d")
    slot = schedule_slot or f"manual-{current.strftime('%H%M%S')}"
    if current_date != trade_date:
        return {
            "trade_date": trade_date,
            "checked_at": current.isoformat(sep=" ", timespec="seconds"),
            "schedule_slot": slot,
            "skipped": True,
            "skip_reason": "当前不是交易日",
            "success_count": 0,
            "failure_count": 0,
            "unread_alert_count": 0,
            "stocks": [],
            **_group_rows([]),
        }
    target = normalize_cycle_watch_code(ts_code) if ts_code else None
    watched = list_watch_stocks(enabled_only=True)
    if target:
        watched = [row for row in watched if str(row.get("ts_code")) == target]
        if not watched:
            raise LookupError("关注股票不存在或已暂停")

    rows: list[dict[str, Any]] = []
    success_count = 0
    failure_count = 0
    with _CHECK_LOCK:
        for watch in watched:
            previous = latest_effective_evaluation(str(watch["ts_code"]))
            try:
                evaluation = _evaluate_watch_stock(watch, trade_date, current)
                if evaluation.get("status") == "data_delayed":
                    failure_count += 1
                else:
                    success_count += 1
            except Exception as exc:
                failure_count += 1
                evaluation = _delayed_evaluation(watch, f"行情加载失败: {str(exc)[:160]}", previous)
            previous_priority = _STATUS_PRIORITY.get((previous or {}).get("status"), 1)
            current_priority = _STATUS_PRIORITY.get(evaluation.get("status"), 0)
            evaluation["is_new_alert"] = bool(
                evaluation.get("status") != "data_delayed"
                and current_priority > previous_priority
            )
            evaluation["trade_date"] = trade_date
            evaluation["checked_at"] = current
            saved = save_cycle_evaluation(evaluation, slot)
            saved["checked_at"] = current.isoformat(sep=" ", timespec="seconds")
            rows.append(saved)

    groups = _group_rows(rows)
    return {
        "trade_date": trade_date,
        "checked_at": current.isoformat(sep=" ", timespec="seconds"),
        "schedule_slot": slot,
        "skipped": False,
        "success_count": success_count,
        "failure_count": failure_count,
        "unread_alert_count": sum(1 for row in rows if row.get("is_new_alert")),
        "stocks": rows,
        **groups,
    }


def read_cycle_watch_alerts(trade_date: str | None = None) -> dict[str, Any]:
    resolved = str(trade_date or _latest_trade_date())
    return {"trade_date": resolved, "updated_count": mark_cycle_alerts_read(resolved)}
