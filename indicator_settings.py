"""Global indicator settings shared by every stock-selection workflow."""

import math
import threading
import time
from datetime import datetime

import pandas as pd

from database import get_connection


DEFAULT_MACD_SETTINGS = {
    "fast_period": 5,
    "slow_period": 34,
    "signal_period": 5,
    "version": 1,
}
_CACHE_TTL_SECONDS = 5.0
_schema_ready = False
_schema_lock = threading.Lock()
_cache_lock = threading.Lock()
_settings_cache = None


def init_indicator_settings():
    """Create the singleton settings row without overwriting saved values."""
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS indicator_settings (
                        setting_key VARCHAR(32) PRIMARY KEY,
                        fast_period INT NOT NULL,
                        slow_period INT NOT NULL,
                        signal_period INT NOT NULL,
                        version BIGINT NOT NULL DEFAULT 1,
                        updated_at DATETIME NOT NULL
                            DEFAULT CURRENT_TIMESTAMP
                            ON UPDATE CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )
                cursor.execute(
                    """
                    INSERT INTO indicator_settings (
                        setting_key, fast_period, slow_period,
                        signal_period, version
                    ) VALUES ('macd', %s, %s, %s, 1)
                    ON DUPLICATE KEY UPDATE setting_key=VALUES(setting_key)
                    """,
                    (
                        DEFAULT_MACD_SETTINGS["fast_period"],
                        DEFAULT_MACD_SETTINGS["slow_period"],
                        DEFAULT_MACD_SETTINGS["signal_period"],
                    ),
                )
        _schema_ready = True


def _period(value, field):
    if isinstance(value, bool):
        raise ValueError(f"{field} 必须是 2–120 的整数")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{field} 必须是 2–120 的整数")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是 2–120 的整数") from exc
    if isinstance(value, float) and value != parsed:
        raise ValueError(f"{field} 必须是 2–120 的整数")
    if isinstance(value, str) and value.strip() != str(parsed):
        raise ValueError(f"{field} 必须是 2–120 的整数")
    if not 2 <= parsed <= 120:
        raise ValueError(f"{field} 必须在 2–120 之间")
    return parsed


def validate_macd_settings(fast_period, slow_period, signal_period):
    settings = {
        "fast_period": _period(fast_period, "快线周期"),
        "slow_period": _period(slow_period, "慢线周期"),
        "signal_period": _period(signal_period, "信号周期"),
    }
    if settings["fast_period"] >= settings["slow_period"]:
        raise ValueError("快线周期必须小于慢线周期")
    return settings


def _serialize_row(row):
    if not row:
        result = dict(DEFAULT_MACD_SETTINGS)
        result["updated_at"] = None
        return result
    updated_at = row.get("updated_at")
    if isinstance(updated_at, datetime):
        updated_at = updated_at.isoformat(sep=" ", timespec="seconds")
    return {
        "fast_period": int(row["fast_period"]),
        "slow_period": int(row["slow_period"]),
        "signal_period": int(row["signal_period"]),
        "version": int(row["version"]),
        "updated_at": updated_at,
    }


def load_macd_settings(force=False):
    global _settings_cache
    now = time.monotonic()
    with _cache_lock:
        if (
            not force
            and _settings_cache is not None
            and now - _settings_cache[0] < _CACHE_TTL_SECONDS
        ):
            return dict(_settings_cache[1])

    init_indicator_settings()
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT fast_period, slow_period, signal_period,
                       version, updated_at
                FROM indicator_settings
                WHERE setting_key='macd'
                """
            )
            result = _serialize_row(cursor.fetchone())
    with _cache_lock:
        _settings_cache = (now, result)
    return dict(result)


def update_macd_settings(fast_period, slow_period, signal_period):
    global _settings_cache
    values = validate_macd_settings(
        fast_period,
        slow_period,
        signal_period,
    )
    init_indicator_settings()
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT version
                FROM indicator_settings
                WHERE setting_key='macd'
                FOR UPDATE
                """
            )
            current = cursor.fetchone() or {"version": 0}
            version = int(current["version"]) + 1
            cursor.execute(
                """
                UPDATE indicator_settings
                SET fast_period=%s, slow_period=%s, signal_period=%s,
                    version=%s, updated_at=NOW()
                WHERE setting_key='macd'
                """,
                (
                    values["fast_period"],
                    values["slow_period"],
                    values["signal_period"],
                    version,
                ),
            )
    result = {
        **values,
        "version": version,
        "updated_at": datetime.now().isoformat(
            sep=" ",
            timespec="seconds",
        ),
    }
    with _cache_lock:
        _settings_cache = (time.monotonic(), result)
    return dict(result)


def macd_parameter_key(settings=None):
    values = settings or load_macd_settings()
    return (
        f"macd-{values['fast_period']}-{values['slow_period']}-"
        f"{values['signal_period']}-v{values.get('version', 1)}"
    )


def calculate_macd(close, settings=None, min_periods=True):
    """Return DIF, DEA and the doubled MACD histogram."""
    values = settings or load_macd_settings()
    series = pd.to_numeric(close, errors="coerce")
    fast = int(values["fast_period"])
    slow = int(values["slow_period"])
    signal = int(values["signal_period"])
    fast_ema = series.ewm(
        span=fast,
        adjust=False,
        min_periods=fast if min_periods else 0,
    ).mean()
    slow_ema = series.ewm(
        span=slow,
        adjust=False,
        min_periods=slow if min_periods else 0,
    ).mean()
    dif = fast_ema - slow_ema
    dea = dif.ewm(
        span=signal,
        adjust=False,
        min_periods=signal if min_periods else 0,
    ).mean()
    return dif, dea, (dif - dea) * 2
