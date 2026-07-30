from __future__ import annotations

import json
import math
import threading
from datetime import date, datetime
from typing import Any

import pandas as pd

from database import get_connection
from free_review_models import (
    ALLOWED_RANGE_FIELDS,
    ALLOWED_SORT_FIELDS,
    FreeReviewQuery,
)
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


def latest_review_trade_date(
    score_version: str = SCORE_VERSION,
) -> str | None:
    init_free_review_schema()
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT MAX(trade_date) AS trade_date
                FROM review_stock_snapshot WHERE score_version=%s""",
                (str(score_version),),
            )
            row = cursor.fetchone()
    return str(row["trade_date"]) if row and row.get("trade_date") else None


def _list_condition(
    conditions: list[str],
    params: list[Any],
    column: str,
    values: list[str],
) -> None:
    clean = [str(value).strip()[:128] for value in values if str(value).strip()]
    if not clean:
        return
    placeholders = ",".join(["%s"] * len(clean))
    conditions.append(f"`{column}` IN ({placeholders})")
    params.extend(clean)


def compile_review_where(
    request: FreeReviewQuery,
    trade_date: str,
) -> tuple[str, tuple[Any, ...]]:
    conditions = ["trade_date=%s", "score_version=%s"]
    params: list[Any] = [str(trade_date), SCORE_VERSION]
    if request.keyword:
        keyword = f"%{request.keyword}%"
        conditions.append(
            "(ts_code LIKE %s OR name LIKE %s OR industry LIKE %s)"
        )
        params.extend([keyword, keyword, keyword])
    _list_condition(conditions, params, "industry", request.industries)
    _list_condition(conditions, params, "area", request.areas)
    _list_condition(conditions, params, "market", request.markets)
    if request.profit_state:
        conditions.append("profit_state=%s")
        params.append(request.profit_state)
    for field, bounds in request.ranges.items():
        if field not in ALLOWED_RANGE_FIELDS:
            raise ValueError(f"不支持的筛选字段: {field}")
        if bounds.min is not None:
            conditions.append(f"`{field}` >= %s")
            params.append(bounds.min)
        if bounds.max is not None:
            conditions.append(f"`{field}` <= %s")
            params.append(bounds.max)
    return " AND ".join(conditions), tuple(params)


def _decode_snapshot_row(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for column in JSON_COLUMNS:
        raw = result.get(column)
        if isinstance(raw, str):
            try:
                result[column] = json.loads(raw or "[]")
            except json.JSONDecodeError:
                result[column] = []
        elif raw is None:
            result[column] = []
    for column in ("generated_at",):
        value = result.get(column)
        if isinstance(value, (datetime, date)):
            result[column] = value.isoformat(sep=" ")
    return result


def query_review_snapshot(request: FreeReviewQuery) -> dict[str, Any]:
    init_free_review_schema()
    if request.sort_by not in ALLOWED_SORT_FIELDS:
        raise ValueError(f"不支持的排序字段: {request.sort_by}")
    trade_date = request.trade_date or latest_review_trade_date()
    if not trade_date:
        raise LookupError("自由复盘选股快照尚未生成")
    where_sql, params = compile_review_where(request, trade_date)
    direction = "ASC" if request.sort_direction == "asc" else "DESC"
    offset = (request.page - 1) * request.page_size
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""SELECT COUNT(*) AS total
                FROM review_stock_snapshot WHERE {where_sql}""",
                params,
            )
            count_row = cursor.fetchone() or {}
            total = int(count_row.get("total") or 0)
            cursor.execute(
                f"""SELECT * FROM review_stock_snapshot
                WHERE {where_sql}
                ORDER BY `{request.sort_by}` {direction}, ts_code ASC
                LIMIT %s OFFSET %s""",
                (*params, request.page_size, offset),
            )
            rows = cursor.fetchall()
    return {
        "trade_date": str(trade_date),
        "score_version": SCORE_VERSION,
        "page": request.page,
        "page_size": request.page_size,
        "total": total,
        "pages": (
            (total + request.page_size - 1) // request.page_size
            if total else 0
        ),
        "items": [_decode_snapshot_row(row) for row in rows],
    }


def load_review_export_rows(
    request: FreeReviewQuery,
    limit: int = 10000,
) -> tuple[str, list[dict[str, Any]]]:
    init_free_review_schema()
    trade_date = request.trade_date or latest_review_trade_date()
    if not trade_date:
        raise LookupError("自由复盘选股快照尚未生成")
    if request.sort_by not in ALLOWED_SORT_FIELDS:
        raise ValueError(f"不支持的排序字段: {request.sort_by}")
    where_sql, params = compile_review_where(request, trade_date)
    direction = "ASC" if request.sort_direction == "asc" else "DESC"
    row_limit = max(1, min(int(limit), 10000))
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""SELECT * FROM review_stock_snapshot
                WHERE {where_sql}
                ORDER BY `{request.sort_by}` {direction}, ts_code ASC
                LIMIT %s""",
                (*params, row_limit),
            )
            rows = cursor.fetchall()
    return str(trade_date), [_decode_snapshot_row(row) for row in rows]


def load_review_sectors(
    trade_date: str | None = None,
) -> list[dict[str, Any]]:
    init_free_review_schema()
    current = trade_date or latest_review_trade_date()
    if not current:
        raise LookupError("自由复盘选股快照尚未生成")
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """WITH ranked AS (
                    SELECT industry, pct_chg, volume_ratio, turnover_rate,
                        pe_ttm, total_score,
                        ROW_NUMBER() OVER (
                            PARTITION BY industry ORDER BY volume_ratio
                        ) AS volume_rank,
                        COUNT(volume_ratio) OVER (
                            PARTITION BY industry
                        ) AS volume_count
                    FROM review_stock_snapshot
                    WHERE trade_date=%s AND score_version=%s
                        AND industry IS NOT NULL AND industry <> ''
                )
                SELECT industry, COUNT(*) AS stock_count,
                    AVG(pct_chg) AS avg_pct_chg,
                    AVG(CASE WHEN pct_chg > 0 THEN 1 ELSE 0 END) AS up_ratio,
                    AVG(CASE
                        WHEN volume_rank IN (
                            FLOOR((volume_count + 1) / 2),
                            FLOOR((volume_count + 2) / 2)
                        ) THEN volume_ratio
                    END) AS median_volume_ratio,
                    AVG(turnover_rate) AS avg_turnover_rate,
                    AVG(pe_ttm) AS avg_pe_ttm,
                    AVG(total_score) AS avg_total_score
                FROM ranked GROUP BY industry
                ORDER BY avg_total_score DESC, stock_count DESC""",
                (str(current), SCORE_VERSION),
            )
            return [dict(row) for row in cursor.fetchall()]


def load_review_meta(
    trade_date: str | None = None,
) -> dict[str, Any]:
    init_free_review_schema()
    current = trade_date or latest_review_trade_date()
    if not current:
        raise LookupError("自由复盘选股快照尚未生成")
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT MAX(generated_at) AS generated_at,
                    COUNT(*) AS stock_count,
                    COUNT(DISTINCT industry) AS sector_count,
                    AVG(CASE WHEN financial_end_date IS NOT NULL
                        THEN 1 ELSE 0 END) AS financial_coverage
                FROM review_stock_snapshot
                WHERE trade_date=%s AND score_version=%s""",
                (str(current), SCORE_VERSION),
            )
            row = cursor.fetchone() or {}
    generated_at = row.get("generated_at")
    build_status = load_build_status(str(current), SCORE_VERSION) or {}
    return {
        "trade_date": str(current),
        "score_version": SCORE_VERSION,
        "generated_at": (
            generated_at.isoformat(sep=" ")
            if isinstance(generated_at, (datetime, date))
            else generated_at
        ),
        "stock_count": int(row.get("stock_count") or 0),
        "sector_count": int(row.get("sector_count") or 0),
        "financial_coverage": float(row.get("financial_coverage") or 0),
        "available_filters": sorted(ALLOWED_RANGE_FIELDS),
        "data_warnings": build_status.get("warnings", []),
    }
