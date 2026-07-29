import os
import threading
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

from database import get_connection


SHANGHAI = ZoneInfo("Asia/Shanghai")
_sync_lock = threading.Lock()


@dataclass(frozen=True)
class CacheConfig:
    enabled: bool
    bootstrap_days: int
    required_days: int


def get_cache_config() -> CacheConfig:
    enabled = os.getenv("MARKET_CACHE_ENABLED", "true").lower() not in {"0", "false", "no"}
    bootstrap = max(1, int(os.getenv("MARKET_CACHE_BOOTSTRAP_DAYS", "120")))
    required = max(1, int(os.getenv("MARKET_CACHE_REQUIRED_DAYS", "100")))
    if bootstrap < required:
        raise ValueError("MARKET_CACHE_BOOTSTRAP_DAYS 不得小于 MARKET_CACHE_REQUIRED_DAYS")
    return CacheConfig(enabled, bootstrap, required)


def _recent_retry_days() -> int:
    return max(0, min(int(os.getenv("MARKET_CACHE_RECENT_RETRY_DAYS", "2")), 10))


def dataframe_records(frame: pd.DataFrame) -> list[dict]:
    return frame.astype(object).where(pd.notna(frame), None).to_dict(orient="records")


def should_refresh_current_date(current_date, trade_dates, complete_dates, now=None) -> bool:
    now = now or datetime.now(SHANGHAI)
    if now.tzinfo is None:
        now = now.replace(tzinfo=SHANGHAI)
    return (
        current_date in set(trade_dates)
        and current_date in set(complete_dates)
        and now.astimezone(SHANGHAI).time() < time(15, 30)
    )


def init_market_cache():
    statements = [
        """CREATE TABLE IF NOT EXISTS market_daily (
            trade_date VARCHAR(8) NOT NULL, ts_code VARCHAR(16) NOT NULL,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, pre_close DOUBLE,
            `change` DOUBLE, pct_chg DOUBLE, vol DOUBLE, amount DOUBLE,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (trade_date, ts_code), INDEX idx_daily_code_date (ts_code, trade_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        """CREATE TABLE IF NOT EXISTS market_daily_basic (
            trade_date VARCHAR(8) NOT NULL, ts_code VARCHAR(16) NOT NULL,
            turnover_rate DOUBLE, turnover_rate_f DOUBLE, volume_ratio DOUBLE,
            pe DOUBLE, pe_ttm DOUBLE, pb DOUBLE, ps DOUBLE, ps_ttm DOUBLE,
            dv_ratio DOUBLE, dv_ttm DOUBLE, total_share DOUBLE, float_share DOUBLE,
            free_share DOUBLE, total_mv DOUBLE, circ_mv DOUBLE,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (trade_date, ts_code), INDEX idx_basic_code_date (ts_code, trade_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        """CREATE TABLE IF NOT EXISTS stock_basic_cache (
            ts_code VARCHAR(16) PRIMARY KEY, symbol VARCHAR(16), name VARCHAR(64), area VARCHAR(64),
            industry VARCHAR(128), market VARCHAR(32), list_status VARCHAR(8), list_date VARCHAR(8),
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        """CREATE TABLE IF NOT EXISTS sector_moneyflow_daily (
            trade_date VARCHAR(8) NOT NULL, ts_code VARCHAR(32) NOT NULL, content_type VARCHAR(32) NOT NULL,
            name VARCHAR(128), pct_change DOUBLE, close DOUBLE, net_amount DOUBLE, net_amount_rate DOUBLE,
            buy_elg_amount DOUBLE, buy_lg_amount DOUBLE, buy_md_amount DOUBLE, buy_sm_amount DOUBLE, rank_value DOUBLE,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (trade_date, ts_code, content_type)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        """CREATE TABLE IF NOT EXISTS market_cache_sync (
            source_name VARCHAR(32) NOT NULL, trade_date VARCHAR(8) NOT NULL,
            status VARCHAR(16) NOT NULL, row_count INT NOT NULL DEFAULT 0,
            started_at DATETIME NULL, completed_at DATETIME NULL, updated_at DATETIME NOT NULL,
            error_message TEXT NULL, PRIMARY KEY (source_name, trade_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    ]
    with get_connection() as conn:
        with conn.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)


def get_complete_dates(limit=120):
    init_market_cache()
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""SELECT trade_date FROM market_cache_sync
                WHERE source_name IN ('daily','daily_basic') AND status='complete'
                GROUP BY trade_date HAVING COUNT(DISTINCT source_name)=2
                ORDER BY trade_date DESC LIMIT %s""", (limit,))
            return [row["trade_date"] for row in cursor.fetchall()]


def get_cache_status(trade_date_loader=None, complete_date_loader=None):
    config = get_cache_config()
    init_market_cache()
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM market_cache_sync ORDER BY updated_at DESC")
            rows = cursor.fetchall()
    complete_loader = complete_date_loader or get_complete_dates
    all_complete_dates = [str(date) for date in complete_loader(10000)]
    latest_complete_date = all_complete_dates[0] if all_complete_dates else None
    target_trade_dates = []
    if trade_date_loader:
        target_trade_dates = [
            str(date) for date in trade_date_loader(n=config.bootstrap_days)
        ]
    complete_dates = (
        [date for date in target_trade_dates if date in set(all_complete_dates)]
        if target_trade_dates else all_complete_dates[:config.bootstrap_days]
    )
    missing_count = max(0, config.bootstrap_days - len(complete_dates))
    return {
        "complete_dates": len(complete_dates),
        "missing_dates": missing_count,
        "latest_complete_date": latest_complete_date,
        "target_days": config.bootstrap_days,
        "required_days": config.required_days,
        "sources": rows,
    }


_SOURCE_TABLES = {
    "daily": ("market_daily", ["trade_date", "ts_code", "open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount"]),
    "daily_basic": ("market_daily_basic", ["trade_date", "ts_code", "turnover_rate", "turnover_rate_f", "volume_ratio", "pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ratio", "dv_ttm", "total_share", "float_share", "free_share", "total_mv", "circ_mv"]),
    "stock_basic": ("stock_basic_cache", ["ts_code", "symbol", "name", "area", "industry", "market", "list_status", "list_date"]),
    "moneyflow_ind_dc": ("sector_moneyflow_daily", ["trade_date", "ts_code", "content_type", "name", "pct_change", "close", "net_amount", "net_amount_rate", "buy_elg_amount", "buy_lg_amount", "buy_md_amount", "buy_sm_amount", "rank"]),
}


def replace_daily_source(source_name, trade_date, frame):
    table, columns = _SOURCE_TABLES[source_name]
    data = frame.copy().reindex(columns=columns)
    if source_name == "moneyflow_ind_dc":
        data = data.rename(columns={"rank": "rank_value"})
        columns = ["rank_value" if value == "rank" else value for value in columns]
    rows = dataframe_records(data)
    with get_connection() as conn:
        with conn.cursor() as cursor:
            if source_name != "stock_basic":
                cursor.execute(f"DELETE FROM {table} WHERE trade_date=%s", (trade_date,))
            if rows:
                placeholders = ",".join(["%s"] * len(columns))
                updates = ",".join(f"`{column}`=VALUES(`{column}`)" for column in columns)
                sql = f"INSERT INTO {table} ({','.join(f'`{c}`' for c in columns)}) VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {updates}"
                cursor.executemany(sql, [tuple(row.get(column) for column in columns) for row in rows])
            cursor.execute("""INSERT INTO market_cache_sync
                (source_name,trade_date,status,row_count,started_at,completed_at,updated_at,error_message)
                VALUES (%s,%s,'complete',%s,NOW(),NOW(),NOW(),NULL)
                ON DUPLICATE KEY UPDATE status='complete',row_count=VALUES(row_count),completed_at=NOW(),updated_at=NOW(),error_message=NULL""",
                (source_name, trade_date, len(rows)))


def _record_failure(source_name, trade_date, error):
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""INSERT INTO market_cache_sync
                (source_name,trade_date,status,row_count,started_at,updated_at,error_message)
                VALUES (%s,%s,'failed',0,NOW(),NOW(),%s)
                ON DUPLICATE KEY UPDATE status='failed',updated_at=NOW(),error_message=VALUES(error_message)""",
                (source_name, trade_date, str(error)[:2000]))


def sync_market_cache(fetcher, trade_date_loader, force_current=False, now=None):
    config = get_cache_config()
    if not config.enabled:
        return {"cache_updated": False, "data_trade_date": None, "cache_warnings": []}
    now = now or datetime.now(SHANGHAI)
    with _sync_lock:
        init_market_cache()
        dates = sorted(
            {str(value) for value in trade_date_loader(n=config.bootstrap_days)},
            reverse=True,
        )
        complete = set(get_complete_dates(config.bootstrap_days))
        current = now.astimezone(SHANGHAI).strftime("%Y%m%d")
        # Pull newest dates first so a long bootstrap or interrupted request
        # leaves the current market window usable instead of only old history.
        targets = [date for date in dates if date not in complete]
        recent_retry_dates = dates[:_recent_retry_days()]
        for date in recent_retry_dates:
            if date not in targets:
                targets.append(date)
        if dates and dates[0] == current and (force_current or should_refresh_current_date(current, dates, complete, now)):
            if current not in targets:
                targets.append(current)
        warnings = []
        updated = False
        for trade_date in targets:
            for source in ("daily", "daily_basic", "moneyflow_ind_dc"):
                try:
                    frame = fetcher(source, trade_date=trade_date)
                    if source in {"daily", "daily_basic"} and (frame is None or frame.empty):
                        raise ValueError(f"{source} 返回为空")
                    replace_daily_source(source, trade_date, frame if frame is not None else pd.DataFrame())
                    updated = True
                except Exception as exc:
                    _record_failure(source, trade_date, exc)
                    warnings.append(f"{source} {trade_date}: {exc}")
            try:
                basic = fetcher("stock_basic", exchange="", list_status="L")
                replace_daily_source("stock_basic", trade_date, basic)
            except Exception as exc:
                warnings.append(f"stock_basic: {exc}")
        available = get_complete_dates(config.bootstrap_days)
        return {"cache_updated": updated, "data_trade_date": available[0] if available else (dates[0] if updated and dates else None), "cache_warnings": warnings}


def ensure_market_cache(fetcher, trade_date_loader, now=None):
    """Validate scan readiness without performing a long remote bootstrap."""
    config = get_cache_config()
    if not config.enabled:
        return {"cache_updated": False, "data_trade_date": None, "cache_warnings": []}

    init_market_cache()
    complete_dates = get_complete_dates(config.bootstrap_days)
    if len(complete_dates) < config.required_days:
        raise RuntimeError(
            f"行情缓存仅有 {len(complete_dates)} 个完整交易日，需要 {config.required_days} 个；"
            "请先在数据缓存页面执行增量同步"
        )
    return {
        "cache_updated": False,
        "data_trade_date": complete_dates[0],
        "cache_warnings": [],
    }


def _read_frame(sql, params=()):
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return pd.DataFrame(cursor.fetchall())


def load_market_snapshot(trade_date):
    return _read_frame("""SELECT d.*, b.turnover_rate,b.turnover_rate_f,b.volume_ratio,
        b.pe,b.pe_ttm,b.pb,b.total_mv,b.circ_mv,s.name,s.industry
        FROM market_daily d JOIN market_daily_basic b USING (trade_date,ts_code)
        LEFT JOIN stock_basic_cache s USING (ts_code) WHERE d.trade_date=%s""", (trade_date,))


def load_recent_daily(end_trade_date, n):
    dates = list(reversed(get_complete_dates(n)))
    if not dates:
        return pd.DataFrame()
    placeholders = ",".join(["%s"] * len(dates))
    columns = "ts_code,trade_date,open,high,low,close,vol,amount,pct_chg"
    return _read_frame(f"SELECT {columns} FROM market_daily WHERE trade_date IN ({placeholders}) ORDER BY ts_code,trade_date", dates)


def load_moneyflow(trade_date):
    frame = _read_frame("SELECT * FROM sector_moneyflow_daily WHERE trade_date=%s", (trade_date,))
    return frame.rename(columns={"rank_value": "rank"})
