from __future__ import annotations

from datetime import datetime, timedelta
import json
import threading
from typing import Any

import pandas as pd

from database import get_connection


_schema_lock = threading.Lock()
_schema_ready = False


def init_realtime_cache() -> None:
    global _schema_ready
    if _schema_ready:
        return
    statements = [
        """CREATE TABLE IF NOT EXISTS realtime_minute_cache (
            ts_code VARCHAR(16) NOT NULL,
            trade_time DATETIME NOT NULL,
            freq VARCHAR(16) NOT NULL,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            vol DOUBLE,
            amount DOUBLE,
            source_name VARCHAR(32) NOT NULL,
            cache_trade_date VARCHAR(8) NOT NULL,
            fetched_at DATETIME NOT NULL,
            PRIMARY KEY (ts_code, trade_time, freq),
            INDEX idx_realtime_minute_window (
                ts_code, freq, trade_time
            ),
            INDEX idx_realtime_minute_cache_date (
                cache_trade_date, freq
            )
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        """CREATE TABLE IF NOT EXISTS realtime_result_cache (
            cache_scope VARCHAR(64) NOT NULL,
            cache_key VARCHAR(255) NOT NULL,
            trade_date VARCHAR(8) NOT NULL,
            data_as_of DATETIME NULL,
            data_status VARCHAR(32) NOT NULL,
            payload_json LONGTEXT NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            PRIMARY KEY (cache_scope, cache_key, trade_date),
            INDEX idx_realtime_result_latest (
                cache_scope, cache_key, updated_at
            ),
            INDEX idx_realtime_result_trade_date (trade_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    ]
    with _schema_lock:
        if _schema_ready:
            return
        with get_connection() as conn:
            with conn.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement)
        _schema_ready = True


def _datetime_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    try:
        return datetime.fromisoformat(str(value)).isoformat(
            sep=" ",
            timespec="seconds",
        )
    except (TypeError, ValueError):
        return str(value)


def load_result_cache(
    cache_scope: str,
    cache_key: str,
) -> dict[str, Any] | None:
    init_realtime_cache()
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT payload_json, updated_at
                FROM realtime_result_cache
                WHERE cache_scope=%s AND cache_key=%s
                ORDER BY updated_at DESC
                LIMIT 1""",
                (str(cache_scope), str(cache_key)),
            )
            row = cursor.fetchone()
    if not row:
        return None
    return {
        "payload": json.loads(row["payload_json"]),
        "updated_at": _datetime_text(row.get("updated_at")),
    }


def save_result_cache(
    cache_scope: str,
    cache_key: str,
    payload: dict[str, Any],
) -> None:
    init_realtime_cache()
    trade_date = str(
        payload.get("data_trade_date")
        or payload.get("trade_date")
        or ""
    )
    if not trade_date:
        raise ValueError("实时结果缺少 trade_date")
    now = datetime.now()
    params = {
        "cache_scope": str(cache_scope),
        "cache_key": str(cache_key),
        "trade_date": trade_date,
        "data_as_of": payload.get("data_as_of"),
        "data_status": str(payload.get("data_status") or "live"),
        "payload_json": json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
        ),
        "created_at": now,
        "updated_at": now,
    }
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO realtime_result_cache (
                    cache_scope, cache_key, trade_date, data_as_of,
                    data_status, payload_json, created_at, updated_at
                ) VALUES (
                    %(cache_scope)s, %(cache_key)s, %(trade_date)s,
                    %(data_as_of)s, %(data_status)s, %(payload_json)s,
                    %(created_at)s, %(updated_at)s
                )
                ON DUPLICATE KEY UPDATE
                    data_as_of=VALUES(data_as_of),
                    data_status=VALUES(data_status),
                    payload_json=VALUES(payload_json),
                    updated_at=VALUES(updated_at)""",
                params,
            )


def load_minute_cache(
    ts_code: str,
    start: str,
    end: str,
    freq: str,
) -> pd.DataFrame:
    init_realtime_cache()
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT ts_code, trade_time, open, high, low, close,
                    vol, amount
                FROM realtime_minute_cache
                WHERE ts_code=%s AND freq=%s
                    AND trade_time BETWEEN %s AND %s
                ORDER BY trade_time""",
                (str(ts_code), str(freq), str(start), str(end)),
            )
            rows = cursor.fetchall()
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["trade_time"] = pd.to_datetime(
            frame["trade_time"],
            errors="coerce",
        )
        frame = frame.dropna(subset=["trade_time"]).reset_index(drop=True)
    return frame


def save_minute_cache(
    frame: pd.DataFrame,
    freq: str,
    source_name: str,
    cache_trade_date: str,
) -> None:
    if frame is None or frame.empty:
        return
    required = {"ts_code", "trade_time"}
    if not required.issubset(frame.columns):
        raise ValueError("分钟缓存缺少 ts_code 或 trade_time")
    init_realtime_cache()
    data = frame.copy()
    data["trade_time"] = pd.to_datetime(
        data["trade_time"],
        errors="coerce",
    )
    data = data.dropna(subset=["ts_code", "trade_time"])
    for column in ("open", "high", "low", "close", "vol", "amount"):
        if column not in data:
            data[column] = None
        data[column] = pd.to_numeric(data[column], errors="coerce")
    fetched_at = datetime.now()
    rows = []
    for row in data.to_dict("records"):
        rows.append(
            (
                str(row["ts_code"]),
                row["trade_time"].to_pydatetime(),
                str(freq),
                _finite_or_none(row.get("open")),
                _finite_or_none(row.get("high")),
                _finite_or_none(row.get("low")),
                _finite_or_none(row.get("close")),
                _finite_or_none(row.get("vol")),
                _finite_or_none(row.get("amount")),
                str(source_name),
                str(cache_trade_date),
                fetched_at,
            )
        )
    if not rows:
        return
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO realtime_minute_cache (
                    ts_code, trade_time, freq, open, high, low, close,
                    vol, amount, source_name, cache_trade_date, fetched_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    open=VALUES(open), high=VALUES(high),
                    low=VALUES(low), close=VALUES(close),
                    vol=VALUES(vol), amount=VALUES(amount),
                    source_name=VALUES(source_name),
                    cache_trade_date=VALUES(cache_trade_date),
                    fetched_at=VALUES(fetched_at)""",
                rows,
            )


def _finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) else None


def minute_cache_is_fresh(
    frame: pd.DataFrame,
    requested_start: str,
    requested_end: str,
    now: datetime,
    freq: str,
) -> bool:
    if (
        frame is None
        or frame.empty
        or "trade_time" not in frame.columns
    ):
        return False
    times = pd.to_datetime(frame["trade_time"], errors="coerce").dropna()
    if times.empty:
        return False
    start = pd.Timestamp(requested_start)
    end = pd.Timestamp(requested_end)
    start_tolerance = timedelta(days=3)
    if times.min() > start + start_tolerance:
        return False
    end_tolerance = timedelta(
        seconds=3700 if str(freq) == "60min" else 90
    )
    if times.max() < end - end_tolerance:
        return False
    return True


def minute_cache_next_fetch_start(
    frame: pd.DataFrame,
    requested_start: str,
    requested_end: str,
    freq: str,
) -> tuple[str | None, bool]:
    if (
        frame is None
        or frame.empty
        or "trade_time" not in frame.columns
    ):
        return requested_start, False
    times = pd.to_datetime(frame["trade_time"], errors="coerce").dropna()
    if times.empty:
        return requested_start, False
    start = pd.Timestamp(requested_start)
    end = pd.Timestamp(requested_end)
    step = timedelta(minutes=60 if str(freq) == "60min" else 1)
    start_tolerance = timedelta(
        seconds=3700 if str(freq) == "60min" else 60
    )
    end_tolerance = timedelta(
        seconds=3700 if str(freq) == "60min" else 90
    )
    has_start = times.min() <= start + start_tolerance
    latest = times.max()
    if has_start and latest >= end - end_tolerance:
        return None, True
    if not has_start:
        return requested_start, False
    next_start = latest + step
    if next_start > end:
        return None, True
    return next_start.strftime("%Y-%m-%d %H:%M:%S"), False


def prune_realtime_cache(keep_trade_dates: list[str]) -> None:
    keep = tuple(dict.fromkeys(str(value) for value in keep_trade_dates))
    if not keep:
        return
    init_realtime_cache()
    placeholders = ",".join(["%s"] * len(keep))
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""DELETE FROM realtime_minute_cache
                WHERE cache_trade_date NOT IN ({placeholders})""",
                keep,
            )
            cursor.execute(
                f"""DELETE FROM realtime_result_cache
                WHERE trade_date NOT IN ({placeholders})""",
                keep,
            )
