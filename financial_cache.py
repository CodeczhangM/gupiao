from __future__ import annotations

import calendar
from datetime import datetime
import threading
from typing import Any, Callable

import pandas as pd

from database import get_connection


FINANCIAL_NUMERIC_FIELDS = [
    "eps", "dt_eps", "cfps", "roe", "roe_dt", "roa", "roic",
    "grossprofit_margin", "netprofit_margin",
    "current_ratio", "debt_to_assets",
    "ocf_to_or", "q_ocf_to_sales",
    "tr_yoy", "or_yoy", "netprofit_yoy", "dt_netprofit_yoy",
    "q_sales_yoy", "q_netprofit_yoy", "ocf_yoy",
    "profit_dedt",
    "basic_eps_yoy", "rd_exp",
]
FINANCIAL_FIELDS = ",".join(
    ["ts_code", "ann_date", "end_date", "update_flag"]
    + FINANCIAL_NUMERIC_FIELDS
)

_schema_lock = threading.Lock()
_schema_ready = False


def quarter_periods(as_of_date: str, count: int = 8) -> list[str]:
    current = datetime.strptime(str(as_of_date), "%Y%m%d")
    quarter_month = ((current.month - 1) // 3 + 1) * 3
    quarter_day = calendar.monthrange(current.year, quarter_month)[1]
    quarter_end = datetime(current.year, quarter_month, quarter_day)
    if quarter_end > current:
        quarter_month -= 3
        year = current.year
        if quarter_month <= 0:
            quarter_month += 12
            year -= 1
        quarter_end = datetime(
            year,
            quarter_month,
            calendar.monthrange(year, quarter_month)[1],
        )
    result = []
    year, month = quarter_end.year, quarter_end.month
    for _ in range(max(0, int(count))):
        day = calendar.monthrange(year, month)[1]
        result.append(datetime(year, month, day).strftime("%Y%m%d"))
        month -= 3
        if month <= 0:
            month += 12
            year -= 1
    return result


def init_financial_cache() -> None:
    global _schema_ready
    if _schema_ready:
        return
    numeric_sql = ",\n            ".join(
        f"{field} DOUBLE" for field in FINANCIAL_NUMERIC_FIELDS
    )
    statements = [
        f"""CREATE TABLE IF NOT EXISTS financial_indicator_cache (
            ts_code VARCHAR(16) NOT NULL,
            end_date VARCHAR(8) NOT NULL,
            ann_date VARCHAR(8) NOT NULL,
            update_flag VARCHAR(8),
            {numeric_sql},
            source_name VARCHAR(32) NOT NULL,
            fetched_at DATETIME NOT NULL,
            PRIMARY KEY (ts_code, end_date, ann_date),
            INDEX idx_financial_period (end_date, ann_date),
            INDEX idx_financial_code_announcement (ts_code, ann_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        """CREATE TABLE IF NOT EXISTS financial_cache_sync (
            source_name VARCHAR(32) NOT NULL,
            end_date VARCHAR(8) NOT NULL,
            status VARCHAR(16) NOT NULL,
            row_count INT NOT NULL DEFAULT 0,
            started_at DATETIME NULL,
            completed_at DATETIME NULL,
            updated_at DATETIME NOT NULL,
            error_message TEXT NULL,
            PRIMARY KEY (source_name, end_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        """ALTER TABLE financial_indicator_cache
            ADD COLUMN profit_dedt DOUBLE NULL""",
    ]
    with _schema_lock:
        if _schema_ready:
            return
        with get_connection() as conn:
            with conn.cursor() as cursor:
                for statement in statements:
                    try:
                        cursor.execute(statement)
                    except Exception as exc:
                        message = str(exc).lower()
                        duplicate_column = (
                            "duplicate column" in message
                            or "1060" in message
                        )
                        migration = (
                            "add column profit_dedt" in statement.lower()
                        )
                        if migration and duplicate_column:
                            continue
                        raise
        _schema_ready = True


def _complete_periods(periods: list[str]) -> set[str]:
    if not periods:
        return set()
    init_financial_cache()
    placeholders = ",".join(["%s"] * len(periods))
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""SELECT end_date FROM financial_cache_sync
                WHERE source_name='fina_indicator_vip'
                  AND status='complete'
                  AND end_date IN ({placeholders})""",
                tuple(periods),
            )
            return {
                str(row["end_date"]) for row in cursor.fetchall()
            }


def _save_sync_state(
    period: str,
    status: str,
    row_count: int = 0,
    error_message: str | None = None,
) -> None:
    init_financial_cache()
    now = datetime.now()
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO financial_cache_sync (
                    source_name, end_date, status, row_count,
                    started_at, completed_at, updated_at, error_message
                ) VALUES (
                    'fina_indicator_vip', %s, %s, %s, %s, %s, %s, %s
                )
                ON DUPLICATE KEY UPDATE
                    status=VALUES(status),
                    row_count=VALUES(row_count),
                    started_at=CASE
                        WHEN VALUES(status)='running'
                        THEN VALUES(started_at) ELSE started_at END,
                    completed_at=VALUES(completed_at),
                    updated_at=VALUES(updated_at),
                    error_message=VALUES(error_message)""",
                (
                    str(period),
                    str(status),
                    int(row_count),
                    now if status == "running" else None,
                    now if status in {"complete", "failed"} else None,
                    now,
                    error_message,
                ),
            )


def _finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) else None


def _upsert_financial_rows(frame: pd.DataFrame) -> int:
    if frame is None or frame.empty:
        return 0
    init_financial_cache()
    now = datetime.now()
    columns = [
        "ts_code", "end_date", "ann_date", "update_flag",
        *FINANCIAL_NUMERIC_FIELDS,
        "source_name", "fetched_at",
    ]
    rows = []
    for item in frame.to_dict("records"):
        if not item.get("ts_code") or not item.get("end_date"):
            continue
        ann_date = item.get("ann_date")
        if not ann_date:
            continue
        row = [
            str(item["ts_code"]),
            str(item["end_date"]),
            str(ann_date),
            str(item.get("update_flag") or ""),
        ]
        row.extend(
            _finite_or_none(item.get(field))
            for field in FINANCIAL_NUMERIC_FIELDS
        )
        row.extend(["fina_indicator_vip", now])
        rows.append(tuple(row))
    if not rows:
        return 0
    placeholders = ",".join(["%s"] * len(columns))
    updates = ",".join(
        f"{column}=VALUES({column})"
        for column in columns[3:]
    )
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.executemany(
                f"""INSERT INTO financial_indicator_cache (
                    {','.join(columns)}
                ) VALUES ({placeholders})
                ON DUPLICATE KEY UPDATE {updates}""",
                rows,
            )
    return len(rows)


def sync_financial_indicators(
    query_loader: Callable[..., pd.DataFrame],
    as_of_date: str,
    quarters: int = 8,
) -> dict[str, Any]:
    periods = quarter_periods(as_of_date, quarters)
    complete = _complete_periods(periods)
    synced = 0
    row_count = 0
    failed: list[dict[str, str]] = []
    for period in periods:
        if period in complete:
            continue
        _save_sync_state(period, "running")
        try:
            frame = query_loader(
                "fina_indicator_vip",
                period=period,
                fields=FINANCIAL_FIELDS,
            )
            if frame is None or frame.empty:
                raise RuntimeError(f"{period} 财务指标返回为空")
            saved = _upsert_financial_rows(frame)
            if saved <= 0:
                raise RuntimeError(f"{period} 财务指标没有有效记录")
            _save_sync_state(period, "complete", saved)
            synced += 1
            row_count += saved
        except Exception as exc:
            message = str(exc)
            _save_sync_state(period, "failed", 0, message[:500])
            failed.append({"period": period, "error": message[:200]})
            if "权限" in message or "5000" in message:
                raise
    return {
        "source_name": "fina_indicator_vip",
        "periods": periods,
        "synced_periods": synced,
        "cached_periods": len(complete),
        "row_count": row_count,
        "failed_periods": failed,
    }


def _load_financial_period_rows(
    periods: list[str],
) -> pd.DataFrame:
    if not periods:
        return pd.DataFrame()
    init_financial_cache()
    placeholders = ",".join(["%s"] * len(periods))
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""SELECT * FROM financial_indicator_cache
                WHERE end_date IN ({placeholders})""",
                tuple(periods),
            )
            return pd.DataFrame(cursor.fetchall())


def load_financial_as_of(
    trade_date: str,
    periods: int = 8,
) -> pd.DataFrame:
    targets = quarter_periods(trade_date, periods)
    frame = _load_financial_period_rows(targets)
    if frame is None or frame.empty:
        return pd.DataFrame()
    data = frame.copy()
    data["ann_date"] = data["ann_date"].astype(str)
    data = data[data["ann_date"] <= str(trade_date)]
    if data.empty:
        return data.reset_index(drop=True)
    data["_updated"] = (
        data.get("update_flag", pd.Series("", index=data.index))
        .astype(str)
        .eq("1")
        .astype(int)
    )
    data = data.sort_values(
        ["ts_code", "end_date", "ann_date", "_updated"]
    )
    data = data.groupby(
        ["ts_code", "end_date"],
        as_index=False,
    ).tail(1)
    return data.drop(columns=["_updated"]).reset_index(drop=True)
