from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import math
import threading
import time
from typing import Any

from database import get_connection


DEFAULT_POSITION_STRATEGY_SETTINGS = {
    "pressure": {
        "history_days": 60,
        "structure_days": 20,
        "pivot_left_days": 2,
        "pivot_right_days": 2,
        "min_touches": 2,
        "cluster_pct": 1.0,
        "cluster_atr_factor": 0.35,
        "cluster_max_pct": 2.0,
        "volume_surge_ratio": 1.5,
        "rejection_lookahead_days": 5,
        "rejection_min_pct": 2.0,
        "rejection_atr_factor": 0.8,
    },
    "breakout": {
        "trigger_pct": 0.1,
        "trigger_atr_factor": 0.05,
        "confirm_pct": 0.5,
        "confirm_atr_factor": 0.3,
        "volume_confirm_ratio": 1.3,
        "close_position_min": 0.68,
        "long_upper_shadow_ratio": 0.4,
    },
    "distance": {
        "critical_pct": 1.5,
        "waiting_pct": 3.0,
        "observe_pct": 5.0,
    },
    "risk_reward": {
        "minimum_ratio": 1.5,
        "good_ratio": 2.0,
        "excellent_ratio": 3.0,
    },
    "network": {
        "enrichment_limit": 10,
        "workers": 5,
        "request_timeout_seconds": 6,
        "stage_budget_seconds": 15,
        "total_budget_seconds": 45,
    },
}

_CACHE_TTL_SECONDS = 5.0
_schema_ready = False
_schema_lock = threading.Lock()
_cache_lock = threading.Lock()
_settings_cache: tuple[float, dict[str, Any]] | None = None


def _deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in updates.items():
        if key not in result:
            raise ValueError(f"未知策略配置项: {key}")
        if isinstance(result[key], dict):
            if not isinstance(value, dict):
                raise ValueError(f"{key} 必须是对象")
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _finite_number(value: Any, path: str, *, positive: bool = True) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{path} 必须是有限数值")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path} 必须是有限数值") from exc
    if not math.isfinite(parsed) or (positive and parsed <= 0):
        raise ValueError(f"{path} 必须大于0")
    return parsed


def validate_position_strategy_settings(payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("策略配置必须是对象")
    values = _deep_merge(DEFAULT_POSITION_STRATEGY_SETTINGS, payload)
    for group, fields in values.items():
        for key, value in fields.items():
            _finite_number(value, f"{group}.{key}")

    pressure = values["pressure"]
    for key in (
        "history_days", "structure_days", "pivot_left_days",
        "pivot_right_days", "min_touches", "rejection_lookahead_days",
    ):
        if int(pressure[key]) != float(pressure[key]):
            raise ValueError(f"pressure.{key} 必须是整数")
        pressure[key] = int(pressure[key])
    if pressure["history_days"] < pressure["structure_days"]:
        raise ValueError("历史窗口必须不小于结构窗口")
    if pressure["cluster_pct"] > pressure["cluster_max_pct"]:
        raise ValueError("基础聚类距离不能超过最大聚类距离")

    distance = values["distance"]
    if not (
        distance["critical_pct"]
        < distance["waiting_pct"]
        < distance["observe_pct"]
    ):
        raise ValueError("距离阈值必须满足临界 < 等待 < 观察")
    risk_reward = values["risk_reward"]
    if not (
        risk_reward["minimum_ratio"]
        < risk_reward["good_ratio"]
        < risk_reward["excellent_ratio"]
    ):
        raise ValueError("盈亏比阈值必须严格递增")
    network = values["network"]
    for key in ("enrichment_limit", "workers", "request_timeout_seconds", "stage_budget_seconds", "total_budget_seconds"):
        if int(network[key]) != float(network[key]):
            raise ValueError(f"network.{key} 必须是整数")
        network[key] = int(network[key])
    if network["enrichment_limit"] > 10:
        raise ValueError("分钟增强股票数不能超过10")
    if network["stage_budget_seconds"] >= network["total_budget_seconds"]:
        raise ValueError("网络阶段预算必须小于总预算")
    return values


def init_position_strategy_settings() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        defaults = json.dumps(
            DEFAULT_POSITION_STRATEGY_SETTINGS,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS position_strategy_settings (
                        setting_key VARCHAR(32) PRIMARY KEY,
                        settings_json LONGTEXT NOT NULL,
                        version BIGINT NOT NULL DEFAULT 1,
                        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                            ON UPDATE CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )
                cursor.execute(
                    """
                    INSERT INTO position_strategy_settings (
                        setting_key, settings_json, version
                    ) VALUES ('position_strategy', %s, 1)
                    ON DUPLICATE KEY UPDATE setting_key=VALUES(setting_key)
                    """,
                    (defaults,),
                )
        _schema_ready = True


def _serialize_row(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        settings = validate_position_strategy_settings({})
        return {**settings, "version": 1, "updated_at": None}
    raw = row.get("settings_json") or "{}"
    decoded = json.loads(raw) if isinstance(raw, str) else dict(raw)
    settings = validate_position_strategy_settings(decoded)
    updated_at = row.get("updated_at")
    if isinstance(updated_at, datetime):
        updated_at = updated_at.isoformat(sep=" ", timespec="seconds")
    return {
        **settings,
        "version": int(row.get("version") or 1),
        "updated_at": updated_at,
    }


def load_position_strategy_settings(force: bool = False) -> dict[str, Any]:
    global _settings_cache
    now = time.monotonic()
    with _cache_lock:
        if not force and _settings_cache and now - _settings_cache[0] < _CACHE_TTL_SECONDS:
            return deepcopy(_settings_cache[1])
    init_position_strategy_settings()
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT settings_json, version, updated_at
                FROM position_strategy_settings
                WHERE setting_key='position_strategy'
                """
            )
            result = _serialize_row(cursor.fetchone())
    with _cache_lock:
        _settings_cache = (now, result)
    return deepcopy(result)


def update_position_strategy_settings(payload: dict[str, Any]) -> dict[str, Any]:
    global _settings_cache
    init_position_strategy_settings()
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT settings_json, version
                FROM position_strategy_settings
                WHERE setting_key='position_strategy'
                FOR UPDATE
                """
            )
            current = cursor.fetchone() or {}
            raw = current.get("settings_json") or "{}"
            stored = json.loads(raw) if isinstance(raw, str) else dict(raw)
            settings = validate_position_strategy_settings(
                _deep_merge(validate_position_strategy_settings(stored), payload)
            )
            version = int(current.get("version") or 0) + 1
            encoded = json.dumps(settings, ensure_ascii=False, separators=(",", ":"))
            cursor.execute(
                """
                UPDATE position_strategy_settings
                SET settings_json=%s, version=%s, updated_at=NOW()
                WHERE setting_key='position_strategy'
                """,
                (encoded, version),
            )
    result = {
        **settings,
        "version": version,
        "updated_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
    }
    with _cache_lock:
        _settings_cache = (time.monotonic(), result)
    return deepcopy(result)


def position_strategy_parameter_key(settings: dict[str, Any] | None = None) -> str:
    values = settings or load_position_strategy_settings()
    return f"position-strategy-v{int(values.get('version') or 1)}"


def save_position_strategy_settings(payload: dict[str, Any]) -> dict[str, Any]:
    result = update_position_strategy_settings(payload)
    from realtime_info_service import clear_realtime_derived_caches

    clear_realtime_derived_caches()
    return {
        "settings": result,
        "position_strategy_parameter_key": position_strategy_parameter_key(result),
    }
