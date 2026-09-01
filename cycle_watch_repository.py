from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from database import get_connection


def init_cycle_watch_schema() -> None:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS cycle_watchlist (
                    ts_code VARCHAR(16) PRIMARY KEY,
                    name VARCHAR(64) NULL,
                    note VARCHAR(500) NULL,
                    planned_low_price DECIMAL(18,4) NULL,
                    planned_high_price DECIMAL(18,4) NULL,
                    enabled TINYINT(1) NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    last_checked_at DATETIME NULL,
                    latest_evaluation_id BIGINT NULL,
                    INDEX idx_cycle_watch_enabled (enabled, updated_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS cycle_watch_evaluations (
                    id BIGINT PRIMARY KEY AUTO_INCREMENT,
                    ts_code VARCHAR(16) NOT NULL,
                    trade_date VARCHAR(8) NOT NULL,
                    schedule_slot VARCHAR(16) NOT NULL,
                    checked_at DATETIME NOT NULL,
                    data_as_of DATETIME NULL,
                    status VARCHAR(32) NOT NULL,
                    status_label VARCHAR(32) NOT NULL,
                    opportunity_score DECIMAL(8,2) NOT NULL DEFAULT 0,
                    current_price DECIMAL(18,4) NULL,
                    pct_chg DECIMAL(10,4) NULL,
                    support_price DECIMAL(18,4) NULL,
                    matched_conditions_json LONGTEXT NOT NULL,
                    missing_conditions_json LONGTEXT NOT NULL,
                    risk_items_json LONGTEXT NOT NULL,
                    invalidation_reason TEXT NULL,
                    factors_json LONGTEXT NOT NULL,
                    is_new_alert TINYINT(1) NOT NULL DEFAULT 0,
                    alert_read TINYINT(1) NOT NULL DEFAULT 0,
                    UNIQUE KEY uq_cycle_eval_slot (ts_code, trade_date, schedule_slot),
                    INDEX idx_cycle_eval_history (ts_code, checked_at),
                    INDEX idx_cycle_eval_alerts (trade_date, is_new_alert, alert_read)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )


def _time_text(value: Any) -> Any:
    return value.isoformat(sep=" ") if isinstance(value, datetime) else value


def _decode_watch(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    result = dict(row)
    result["enabled"] = bool(result.get("enabled"))
    for key in ("planned_low_price", "planned_high_price"):
        if result.get(key) is not None:
            result[key] = float(result[key])
    for key in ("created_at", "updated_at", "last_checked_at"):
        result[key] = _time_text(result.get(key))
    return result


def _decode_evaluation(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    result = dict(row)
    for target, source, fallback in (
        ("matched_conditions", "matched_conditions_json", []),
        ("missing_conditions", "missing_conditions_json", []),
        ("risk_items", "risk_items_json", []),
        ("factors", "factors_json", {}),
    ):
        result[target] = json.loads(result.pop(source, None) or json.dumps(fallback))
    result["is_new_alert"] = bool(result.get("is_new_alert"))
    result["alert_read"] = bool(result.get("alert_read"))
    for key in ("opportunity_score", "current_price", "pct_chg", "support_price"):
        if result.get(key) is not None:
            result[key] = float(result[key])
    for key in ("checked_at", "data_as_of"):
        result[key] = _time_text(result.get(key))
    return result


def upsert_watch_stock(payload: dict[str, Any]) -> dict[str, Any]:
    init_cycle_watch_schema()
    now = datetime.now()
    params = {
        "ts_code": payload["ts_code"],
        "name": payload.get("name"),
        "note": payload.get("note"),
        "planned_low_price": payload.get("planned_low_price"),
        "planned_high_price": payload.get("planned_high_price"),
        "created_at": now,
        "updated_at": now,
    }
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO cycle_watchlist (
                    ts_code, name, note, planned_low_price, planned_high_price,
                    enabled, created_at, updated_at
                ) VALUES (
                    %(ts_code)s, %(name)s, %(note)s, %(planned_low_price)s,
                    %(planned_high_price)s, 1, %(created_at)s, %(updated_at)s
                ) ON DUPLICATE KEY UPDATE
                    name = COALESCE(VALUES(name), name),
                    note = VALUES(note),
                    planned_low_price = VALUES(planned_low_price),
                    planned_high_price = VALUES(planned_high_price),
                    enabled = 1,
                    updated_at = VALUES(updated_at)
                """,
                params,
            )
            cursor.execute(
                "SELECT * FROM cycle_watchlist WHERE ts_code = %s",
                (payload["ts_code"],),
            )
            return _decode_watch(cursor.fetchone()) or {**params, "enabled": True}


def update_watch_stock(ts_code: str, changes: dict[str, Any]) -> dict[str, Any] | None:
    init_cycle_watch_schema()
    allowed = ("note", "planned_low_price", "planned_high_price", "enabled", "name")
    values = {key: changes[key] for key in allowed if key in changes}
    if not values:
        return get_watch_stock(ts_code)
    values["updated_at"] = datetime.now()
    values["ts_code"] = ts_code
    assignments = ", ".join(f"{key} = %({key})s" for key in values if key != "ts_code")
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"UPDATE cycle_watchlist SET {assignments} WHERE ts_code = %(ts_code)s",
                values,
            )
            cursor.execute("SELECT * FROM cycle_watchlist WHERE ts_code = %s", (ts_code,))
            return _decode_watch(cursor.fetchone())


def get_watch_stock(ts_code: str) -> dict[str, Any] | None:
    init_cycle_watch_schema()
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM cycle_watchlist WHERE ts_code = %s", (ts_code,))
            return _decode_watch(cursor.fetchone())


def list_watch_stocks(enabled_only: bool = False) -> list[dict[str, Any]]:
    init_cycle_watch_schema()
    where = "WHERE enabled = 1" if enabled_only else ""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM cycle_watchlist {where} ORDER BY updated_at DESC, ts_code"
            )
            return [_decode_watch(row) for row in cursor.fetchall()]


def delete_watch_stock(ts_code: str) -> bool:
    init_cycle_watch_schema()
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM cycle_watchlist WHERE ts_code = %s", (ts_code,))
            return cursor.rowcount > 0


def save_cycle_evaluation(
    evaluation: dict[str, Any],
    schedule_slot: str,
) -> dict[str, Any]:
    init_cycle_watch_schema()
    params = {
        key: evaluation.get(key)
        for key in (
            "ts_code",
            "trade_date",
            "checked_at",
            "data_as_of",
            "status",
            "status_label",
            "opportunity_score",
            "current_price",
            "pct_chg",
            "support_price",
            "invalidation_reason",
        )
    }
    params.update({
        "schedule_slot": schedule_slot,
        "matched_conditions_json": json.dumps(evaluation.get("matched_conditions", []), ensure_ascii=False),
        "missing_conditions_json": json.dumps(evaluation.get("missing_conditions", []), ensure_ascii=False),
        "risk_items_json": json.dumps(evaluation.get("risk_items", []), ensure_ascii=False),
        "factors_json": json.dumps(evaluation.get("factors", {}), ensure_ascii=False),
        "is_new_alert": 1 if evaluation.get("is_new_alert") else 0,
    })
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO cycle_watch_evaluations (
                    ts_code, trade_date, schedule_slot, checked_at, data_as_of,
                    status, status_label, opportunity_score, current_price, pct_chg,
                    support_price, matched_conditions_json, missing_conditions_json,
                    risk_items_json, invalidation_reason, factors_json,
                    is_new_alert, alert_read
                ) VALUES (
                    %(ts_code)s, %(trade_date)s, %(schedule_slot)s, %(checked_at)s,
                    %(data_as_of)s, %(status)s, %(status_label)s,
                    %(opportunity_score)s, %(current_price)s, %(pct_chg)s,
                    %(support_price)s, %(matched_conditions_json)s,
                    %(missing_conditions_json)s, %(risk_items_json)s,
                    %(invalidation_reason)s, %(factors_json)s, %(is_new_alert)s, 0
                ) ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)
                """,
                params,
            )
            evaluation_id = cursor.lastrowid
            cursor.execute(
                """
                UPDATE cycle_watchlist
                SET latest_evaluation_id = %s, last_checked_at = %s, updated_at = updated_at
                WHERE ts_code = %s
                """,
                (evaluation_id, evaluation["checked_at"], evaluation["ts_code"]),
            )
    return {**evaluation, "id": evaluation_id, "schedule_slot": schedule_slot}


def list_cycle_history(ts_code: str, limit: int = 50) -> list[dict[str, Any]]:
    init_cycle_watch_schema()
    safe_limit = max(1, min(int(limit), 200))
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM cycle_watch_evaluations
                WHERE ts_code = %s ORDER BY checked_at DESC, id DESC LIMIT %s
                """,
                (ts_code, safe_limit),
            )
            return [_decode_evaluation(row) for row in cursor.fetchall()]


def latest_effective_evaluation(ts_code: str) -> dict[str, Any] | None:
    init_cycle_watch_schema()
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM cycle_watch_evaluations
                WHERE ts_code = %s AND status <> 'data_delayed'
                ORDER BY checked_at DESC, id DESC LIMIT 1
                """,
                (ts_code,),
            )
            return _decode_evaluation(cursor.fetchone())


def mark_cycle_alerts_read(trade_date: str) -> int:
    init_cycle_watch_schema()
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE cycle_watch_evaluations SET alert_read = 1
                WHERE trade_date = %s AND is_new_alert = 1 AND alert_read = 0
                """,
                (trade_date,),
            )
            return int(cursor.rowcount)
