from __future__ import annotations

import json
import math
import threading
from datetime import date, datetime
from typing import Any

import pandas as pd

from database import get_connection
from free_review_scoring import SCORE_VERSION


TEXT_COLUMNS = [
    "name", "industry", "area", "market", "list_status", "list_date",
    "profit_state", "financial_end_date", "financial_ann_date",
]
INTEGER_COLUMNS = [
    "listed_days", "financial_improvement_count",
]
NUMERIC_COLUMNS = [
    "open", "high", "low", "close", "pre_close", "change", "pct_chg",
    "vol", "amount", "turnover_rate", "turnover_rate_f", "volume_ratio",
    "pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ratio", "dv_ttm",
    "total_mv", "circ_mv",
    "ma5", "ma10", "ma20", "ma30", "ma60",
    "ret_5", "ret_10", "ret_20", "ret_60",
    "drawdown_20", "drawdown_60", "position_60",
    "vol_ratio_ma5", "vol_ratio_ma10", "vol_ratio_ma20",
    "ma20_slope", "ma60_slope",
    "macd_dif", "macd_dea", "macd_hist",
    "kdj_k", "kdj_d", "kdj_j",
    "rsi6", "rsi12", "rsi24",
    "boll_position", "boll_width", "atr_pct",
    "eps", "dt_eps", "cfps", "roe", "roe_dt", "roa", "roic",
    "grossprofit_margin", "netprofit_margin",
    "current_ratio", "debt_to_assets", "ocf_to_or", "q_ocf_to_sales",
    "tr_yoy", "or_yoy", "netprofit_yoy", "dt_netprofit_yoy",
    "q_sales_yoy", "q_netprofit_yoy", "ocf_yoy",
    "basic_eps_yoy", "rd_exp",
    "trend_score", "volume_price_score", "momentum_score",
    "valuation_score", "financial_quality_score",
    "financial_growth_score", "risk_penalty", "total_score",
    "data_completeness",
]
JSON_COLUMNS = ["score_reasons", "risk_flags", "missing_fields"]
SNAPSHOT_COLUMNS = (
    ["trade_date", "ts_code", "score_version"]
    + TEXT_COLUMNS
    + INTEGER_COLUMNS
    + NUMERIC_COLUMNS
    + JSON_COLUMNS
    + ["generated_at"]
)

_schema_lock = threading.Lock()
_schema_ready = False


def init_free_review_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    text_sql = ",\n            ".join(
        f"`{column}` VARCHAR(128) NULL" for column in TEXT_COLUMNS
    )
    integer_sql = ",\n            ".join(
        f"`{column}` INT NULL" for column in INTEGER_COLUMNS
    )
    numeric_sql = ",\n            ".join(
        f"`{column}` DOUBLE NULL" for column in NUMERIC_COLUMNS
    )
    json_sql = ",\n            ".join(
        f"`{column}` LONGTEXT NULL" for column in JSON_COLUMNS
    )
    statements = [
        f"""CREATE TABLE IF NOT EXISTS review_stock_snapshot (
            trade_date VARCHAR(8) NOT NULL,
            ts_code VARCHAR(16) NOT NULL,
            score_version VARCHAR(32) NOT NULL,
            {text_sql},
            {integer_sql},
            {numeric_sql},
            {json_sql},
            generated_at DATETIME NOT NULL,
            PRIMARY KEY (trade_date, ts_code, score_version),
            INDEX idx_review_score
                (trade_date, score_version, total_score),
            INDEX idx_review_industry_score
                (trade_date, score_version, industry, total_score),
            INDEX idx_review_volume_ratio
                (trade_date, score_version, volume_ratio),
            INDEX idx_review_pe_ttm
                (trade_date, score_version, pe_ttm)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        """CREATE TABLE IF NOT EXISTS review_snapshot_build (
            trade_date VARCHAR(8) NOT NULL,
            score_version VARCHAR(32) NOT NULL,
            status VARCHAR(16) NOT NULL,
            stage VARCHAR(32) NOT NULL,
            total_count INT NOT NULL DEFAULT 0,
            processed_count INT NOT NULL DEFAULT 0,
            failed_count INT NOT NULL DEFAULT 0,
            financial_coverage DOUBLE NULL,
            started_at DATETIME NULL,
            completed_at DATETIME NULL,
            updated_at DATETIME NOT NULL,
            error_message TEXT NULL,
            warnings_json LONGTEXT NULL,
            PRIMARY KEY (trade_date, score_version),
            INDEX idx_review_build_updated (updated_at)
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


def _database_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value
    try:
        missing = pd.isna(value)
        if isinstance(missing, bool) and missing:
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "item"):
        value = value.item()
    return value


def replace_review_snapshot(
    trade_date: str,
    score_version: str,
    frame: pd.DataFrame,
) -> None:
    init_free_review_schema()
    generated_at = datetime.now()
    rows = []
    if frame is not None and not frame.empty:
        for record in frame.to_dict("records"):
            values = {
                **record,
                "trade_date": str(trade_date),
                "score_version": str(score_version),
                "generated_at": generated_at,
            }
            for column in JSON_COLUMNS:
                value = values.get(column)
                values[column] = json.dumps(
                    value if isinstance(value, (list, dict)) else [],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            rows.append(tuple(
                _database_value(values.get(column))
                for column in SNAPSHOT_COLUMNS
            ))
    quoted_columns = ",".join(f"`{column}`" for column in SNAPSHOT_COLUMNS)
    placeholders = ",".join(["%s"] * len(SNAPSHOT_COLUMNS))
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """DELETE FROM review_stock_snapshot
                WHERE trade_date=%s AND score_version=%s""",
                (str(trade_date), str(score_version)),
            )
            if rows:
                cursor.executemany(
                    f"""INSERT INTO review_stock_snapshot ({quoted_columns})
                    VALUES ({placeholders})""",
                    rows,
                )


def save_build_status(payload: dict[str, Any]) -> None:
    init_free_review_schema()
    now = datetime.now()
    status = str(payload.get("status") or "pending")
    started_at = payload.get("started_at")
    if started_at is None and status == "running":
        started_at = now
    completed_at = payload.get("completed_at")
    if completed_at is None and status in {"success", "failed"}:
        completed_at = now
    warnings = payload.get("warnings", [])
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO review_snapshot_build (
                    trade_date, score_version, status, stage,
                    total_count, processed_count, failed_count,
                    financial_coverage, started_at, completed_at,
                    updated_at, error_message, warnings_json
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                ) ON DUPLICATE KEY UPDATE
                    status=VALUES(status),
                    stage=VALUES(stage),
                    total_count=VALUES(total_count),
                    processed_count=VALUES(processed_count),
                    failed_count=VALUES(failed_count),
                    financial_coverage=VALUES(financial_coverage),
                    started_at=COALESCE(started_at, VALUES(started_at)),
                    completed_at=VALUES(completed_at),
                    updated_at=VALUES(updated_at),
                    error_message=VALUES(error_message),
                    warnings_json=VALUES(warnings_json)""",
                (
                    str(payload["trade_date"]),
                    str(payload.get("score_version") or SCORE_VERSION),
                    status,
                    str(payload.get("stage") or "queued"),
                    int(payload.get("total_count") or 0),
                    int(payload.get("processed_count") or 0),
                    int(payload.get("failed_count") or 0),
                    _database_value(payload.get("financial_coverage")),
                    started_at,
                    completed_at,
                    now,
                    payload.get("error_message"),
                    json.dumps(warnings, ensure_ascii=False),
                ),
            )


def _serialize_status(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    result = dict(row)
    for column in ("started_at", "completed_at", "updated_at"):
        value = result.get(column)
        if isinstance(value, (datetime, date)):
            result[column] = value.isoformat(sep=" ")
    raw_warnings = result.pop("warnings_json", None)
    if raw_warnings is not None:
        try:
            result["warnings"] = json.loads(raw_warnings or "[]")
        except (TypeError, json.JSONDecodeError):
            result["warnings"] = []
    return result


def load_build_status(
    trade_date: str | None = None,
    score_version: str = SCORE_VERSION,
) -> dict[str, Any] | None:
    init_free_review_schema()
    conditions = ["score_version=%s"]
    params: list[Any] = [str(score_version)]
    if trade_date:
        conditions.append("trade_date=%s")
        params.append(str(trade_date))
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""SELECT * FROM review_snapshot_build
                WHERE {' AND '.join(conditions)}
                ORDER BY trade_date DESC, updated_at DESC LIMIT 1""",
                tuple(params),
            )
            return _serialize_status(cursor.fetchone())
