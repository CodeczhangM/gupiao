import json
import os
from contextlib import contextmanager
from datetime import datetime

import settings
import pymysql


def _db_name():
    return os.getenv("MYSQL_DATABASE", "quant")


def _db_config(include_database=True):
    config = {
        "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": os.getenv("MYSQL_PASSWORD", ""),
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": False,
    }
    if include_database:
        config["database"] = _db_name()
    return config


def ensure_database():
    conn = pymysql.connect(**_db_config(include_database=False))
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{_db_name()}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def get_connection():
    ensure_database()
    conn = pymysql.connect(**_db_config())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    settings.load_env_files()
    sql = """
    CREATE TABLE IF NOT EXISTS quant_reports (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        trade_date VARCHAR(16) NOT NULL,
        status VARCHAR(32) NOT NULL DEFAULT 'success',
        include_ai TINYINT(1) NOT NULL DEFAULT 0,
        strong_json LONGTEXT NOT NULL,
        dip_json LONGTEXT NOT NULL,
        sectors_json LONGTEXT NOT NULL,
        rep_stocks_json LONGTEXT NOT NULL,
        moneyflow_json LONGTEXT NULL,
        sector_potential_json LONGTEXT NULL,
        ai_analysis MEDIUMTEXT NULL,
        error_message TEXT NULL,
        created_at DATETIME NOT NULL,
        INDEX idx_trade_date (trade_date),
        INDEX idx_created_at (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            cursor.execute("SHOW COLUMNS FROM quant_reports LIKE 'moneyflow_json'")
            if cursor.fetchone() is None:
                cursor.execute("ALTER TABLE quant_reports ADD COLUMN moneyflow_json LONGTEXT NULL AFTER rep_stocks_json")
            cursor.execute("SHOW COLUMNS FROM quant_reports LIKE 'sector_potential_json'")
            if cursor.fetchone() is None:
                cursor.execute("ALTER TABLE quant_reports ADD COLUMN sector_potential_json LONGTEXT NULL AFTER moneyflow_json")


def save_report(report: dict) -> int:
    init_db()
    sql = """
    INSERT INTO quant_reports (
        trade_date, status, include_ai, strong_json, dip_json, sectors_json,
        rep_stocks_json, moneyflow_json, sector_potential_json, ai_analysis, error_message, created_at
    ) VALUES (
        %(trade_date)s, %(status)s, %(include_ai)s, %(strong_json)s, %(dip_json)s,
        %(sectors_json)s, %(rep_stocks_json)s, %(moneyflow_json)s, %(sector_potential_json)s,
        %(ai_analysis)s, %(error_message)s, %(created_at)s
    )
    """
    payload = {
        "trade_date": report["trade_date"],
        "status": report.get("status", "success"),
        "include_ai": 1 if report.get("include_ai") else 0,
        "strong_json": json.dumps(report.get("strong", []), ensure_ascii=False),
        "dip_json": json.dumps(report.get("dip", []), ensure_ascii=False),
        "sectors_json": json.dumps(report.get("sectors", []), ensure_ascii=False),
        "rep_stocks_json": json.dumps(report.get("rep_stocks", []), ensure_ascii=False),
        "moneyflow_json": json.dumps(report.get("moneyflow_summary", {}), ensure_ascii=False),
        "sector_potential_json": json.dumps(report.get("sector_potential", []), ensure_ascii=False),
        "ai_analysis": report.get("ai_analysis"),
        "error_message": report.get("error_message"),
        "created_at": report.get("created_at") or datetime.now(),
    }
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, payload)
            return cursor.lastrowid


def _decode_report(row: dict | None):
    if not row:
        return None

    return {
        "id": row["id"],
        "trade_date": row["trade_date"],
        "status": row["status"],
        "include_ai": bool(row["include_ai"]),
        "strong": json.loads(row["strong_json"] or "[]"),
        "dip": json.loads(row["dip_json"] or "[]"),
        "sectors": json.loads(row["sectors_json"] or "[]"),
        "rep_stocks": json.loads(row["rep_stocks_json"] or "[]"),
        "moneyflow_summary": json.loads(row.get("moneyflow_json") or "{}"),
        "sector_potential": json.loads(row.get("sector_potential_json") or "[]"),
        "ai_analysis": row["ai_analysis"],
        "error_message": row["error_message"],
        "created_at": row["created_at"].isoformat(sep=" "),
    }


def get_latest_report():
    init_db()
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM quant_reports ORDER BY id DESC LIMIT 1")
            return _decode_report(cursor.fetchone())


def get_report(report_id: int):
    init_db()
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM quant_reports WHERE id = %s", (report_id,))
            return _decode_report(cursor.fetchone())


def list_reports(limit: int = 20):
    init_db()
    limit = max(1, min(limit, 100))
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, trade_date, status, include_ai, error_message, created_at
                FROM quant_reports
                ORDER BY id DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cursor.fetchall()

    for row in rows:
        row["include_ai"] = bool(row["include_ai"])
        row["created_at"] = row["created_at"].isoformat(sep=" ")
    return rows


def list_ai_reports(limit: int = 50):
    init_db()
    limit = max(1, min(limit, 200))
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM quant_reports
                WHERE include_ai = 1 AND ai_analysis IS NOT NULL AND ai_analysis <> ''
                ORDER BY id DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cursor.fetchall()

    return [_decode_report(row) for row in rows]
