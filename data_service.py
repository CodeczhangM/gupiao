import tushare as ts
import pandas as pd
import os
import requests
import time
from datetime import datetime, timedelta

import settings
from market_cache import (
    ensure_market_cache,
    get_cache_config,
    load_market_snapshot,
    load_moneyflow,
    load_recent_daily,
    sync_market_cache,
)

token = os.getenv("TUSHARE_TOKEN")

ts.set_token(token)

pro = ts.pro_api()

# if token:
#     pro._DataApi__token = token

tushare_http_url = os.getenv("TUSHARE_HTTP_URL", "https://ts.gyzcloud.top/api")
if tushare_http_url:
    pro._DataApi__http_url = tushare_http_url

# ── 沪深A股过滤：排除创业板、科创板，后续如需也可排除北交所 ─────────────────────
MAINBOARD_SUFFIX = (".SH", ".SZ")
MAINBOARD_EXCLUDE_PREFIX = ("3", "688", "689")   # 创业板、科创板
TUSHARE_RETRY_DELAYS = (3, 6, 10)
TUSHARE_STK_MINS_TIMEOUT_SECONDS = int(os.getenv("TUSHARE_STK_MINS_TIMEOUT_SECONDS", "6"))
TUSHARE_STK_MINS_RETRY_DELAYS = tuple(
    int(value.strip())
    for value in os.getenv("TUSHARE_STK_MINS_RETRY_DELAYS", "").split(",")
    if value.strip()
)

_query_cache = {}


def get_cached_scan_inputs(history_days=100):
    """同步缺失行情，并从 MySQL 返回扫描所需的市场快照和历史日线。"""
    metadata = ensure_market_cache(_query_tushare, get_trade_dates)
    trade_date = metadata.get("data_trade_date")
    if not trade_date:
        raise RuntimeError("行情缓存中没有完整交易日")
    history = load_recent_daily(trade_date, history_days)
    required = get_cache_config().required_days
    available_days = history["trade_date"].astype(str).nunique() if "trade_date" in history.columns else 0
    if available_days < min(required, history_days):
        raise RuntimeError(f"行情缓存仅有 {available_days} 个完整交易日，需要 {min(required, history_days)} 个")
    return load_market_snapshot(trade_date), history, metadata


def sync_cached_market_data(force_current=False):
    return sync_market_cache(_query_tushare, get_trade_dates, force_current=force_current)


class MarketDataUnavailable(Exception):
    """行情接口当天数据尚未可用。"""


def _filter_mainboard_a(df: pd.DataFrame) -> pd.DataFrame:
    """只保留沪深A股，并排除创业板、科创板。"""
    if df.empty or "ts_code" not in df.columns:
        return df

    code = df["ts_code"].astype(str)
    mask = code.str.endswith(MAINBOARD_SUFFIX) & ~code.str.startswith(MAINBOARD_EXCLUDE_PREFIX)
    return df[mask].copy()


def _is_rate_limited(exc: Exception) -> bool:
    message = str(exc).lower()
    return "rate limited" in message or "reduce the concurrency" in message


def _is_retryable_tushare_error(exc: Exception) -> bool:
    return _is_rate_limited(exc) or isinstance(exc, requests.exceptions.RequestException)


def _query_policy(api_name: str) -> tuple[int | None, tuple[int, ...]]:
    if api_name == "stk_mins":
        return TUSHARE_STK_MINS_TIMEOUT_SECONDS, TUSHARE_STK_MINS_RETRY_DELAYS
    return None, TUSHARE_RETRY_DELAYS


def _query_tushare(api_name: str, **kwargs):
    """调用 Tushare，遇到限流自动等待重试，并缓存相同请求。"""
    if not token:
        raise RuntimeError("缺少 TUSHARE_TOKEN 环境变量，无法获取行情数据")

    cache_key = (api_name, tuple(sorted(kwargs.items())))
    if cache_key in _query_cache:
        cached = _query_cache[cache_key]
        return cached.copy() if isinstance(cached, pd.DataFrame) else cached

    timeout, retry_delays = _query_policy(api_name)
    original_timeout = getattr(pro, "_DataApi__timeout", None)
    last_error = None
    for attempt in range(len(retry_delays) + 1):
        try:
            if timeout is not None and original_timeout is not None:
                pro._DataApi__timeout = timeout
            result = getattr(pro, api_name)(**kwargs)
            _query_cache[cache_key] = result.copy() if isinstance(result, pd.DataFrame) else result
            return result
        except Exception as exc:
            last_error = exc
            if not _is_retryable_tushare_error(exc) or attempt >= len(retry_delays):
                break

            delay = retry_delays[attempt]
            print(f"Tushare 请求失败，{delay} 秒后重试 {api_name}（第 {attempt + 1} 次）: {exc}")
            time.sleep(delay)
        finally:
            if timeout is not None and original_timeout is not None:
                pro._DataApi__timeout = original_timeout

    raise last_error


def get_trade_dates(n=2, end_date=None):
    """获取最近 n 个交易日，返回列表（降序，最新在前）"""
    end_day_obj = datetime.strptime(end_date, "%Y%m%d") if end_date else datetime.today()
    calendar_days = max(90, int(n * 3))
    start_day = (end_day_obj - timedelta(days=calendar_days)).strftime("%Y%m%d")
    end_day = end_day_obj.strftime("%Y%m%d")

    cal = _query_tushare("trade_cal", start_date=start_day, end_date=end_day)

    if cal.empty:
        raise Exception("trade_cal 返回为空")

    open_days = cal[cal["is_open"].astype(str) == "1"].sort_values("cal_date", ascending=False)

    if len(open_days) < n:
        raise Exception(f"交易日不足 {n} 天")

    return open_days["cal_date"].iloc[:n].tolist()


def get_market_data_by_date(trade_date: str):
    """获取指定交易日的行情 + 基本面数据。"""
    try:
        df = _query_tushare("daily", trade_date=trade_date)
        basic = _query_tushare("daily_basic", trade_date=trade_date)
    except KeyError as exc:
        if str(exc).strip("'\"") == "fields":
            raise MarketDataUnavailable(f"{trade_date} 行情数据尚未发布") from exc
        raise

    if df.empty or basic.empty:
        raise MarketDataUnavailable(f"{trade_date} 行情或基本面数据为空")

    stock_basic = _query_tushare(
        "stock_basic",
        exchange="",
        list_status="L",
        fields="ts_code,name,industry",
    )

    df = _filter_mainboard_a(df)
    basic = _filter_mainboard_a(basic)

    df = pd.merge(df, basic, on="ts_code", how="inner", suffixes=("", "_basic"))
    df = pd.merge(df, stock_basic, on="ts_code", how="left")

    return df, trade_date


def get_market_data():
    """获取最新可用交易日的行情 + 基本面数据。"""
    date_offset = int(os.getenv("MARKET_DATE_OFFSET", "0"))
    date_offset = max(0, min(date_offset, 5))
    dates = get_trade_dates(n=max(date_offset + 6, 6))
    candidate_dates = dates[date_offset:]

    label = "最新可用交易日" if date_offset == 0 else f"前 {date_offset} 个交易日起的最新可用交易日"
    last_error = None
    for trade_date in candidate_dates:
        try:
            result = get_market_data_by_date(trade_date)
            print(f"使用交易日（{label}）: {trade_date}")
            return result
        except MarketDataUnavailable as exc:
            last_error = exc
            print(f"{exc}，尝试前一交易日...")

    raise last_error or Exception("未找到可用行情数据")


def get_recent_daily_data(end_trade_date: str, n=20):
    """获取截至 end_trade_date 的最近 n 个交易日全市场日线数据。"""
    dates = get_trade_dates(n=n, end_date=end_trade_date)
    start_date = dates[-1]

    daily_fields = "ts_code,trade_date,close,high,low,vol,amount,pct_chg"
    hist = _query_tushare("daily", start_date=start_date, end_date=end_trade_date, fields=daily_fields)
    if hist.empty:
        hist = pd.DataFrame()
    else:
        hist = _filter_mainboard_a(hist)
        hist = hist[hist["trade_date"].astype(str).isin(dates)].copy()

    loaded_dates = set(hist["trade_date"].astype(str)) if "trade_date" in hist.columns else set()
    missing_dates = [date for date in dates if date not in loaded_dates]

    if missing_dates:
        extra_frames = []
        print(f"区间历史数据不足，逐日补拉 {len(missing_dates)} 个交易日...")
        for trade_date in missing_dates:
            daily = _query_tushare("daily", trade_date=trade_date, fields=daily_fields)
            if not daily.empty:
                extra_frames.append(_filter_mainboard_a(daily))

        if extra_frames:
            hist = pd.concat([hist, *extra_frames], ignore_index=True)
            hist = hist.drop_duplicates(subset=["ts_code", "trade_date"])

    if hist.empty or "trade_date" not in hist.columns:
        return hist

    return hist[hist["trade_date"].astype(str).isin(dates)].copy()


def get_stock_daily_history(ts_code: str, end_trade_date: str, n=120):
    """获取单只股票截至指定交易日的最近日线数据。"""
    dates = get_trade_dates(n=n, end_date=end_trade_date)
    daily_fields = "ts_code,trade_date,open,high,low,close,vol,pct_chg"
    columns = daily_fields.split(",")
    hist = _query_tushare(
        "daily",
        ts_code=ts_code,
        start_date=dates[-1],
        end_date=end_trade_date,
        fields=daily_fields,
    )

    if hist.empty:
        return pd.DataFrame(columns=columns)

    hist = hist.reindex(columns=columns)
    hist = hist[
        (hist["ts_code"].astype(str) == ts_code)
        & hist["trade_date"].astype(str).isin(dates)
    ].copy()
    return hist.drop_duplicates(subset=["ts_code", "trade_date"]).sort_values("trade_date").reset_index(drop=True)


def get_stock_daily_history_range(ts_code: str, start_trade_date: str, end_trade_date: str):
    """获取单只股票在指定日期区间内的 OHLCV 日线。"""
    daily_fields = "ts_code,trade_date,open,high,low,close,vol,pct_chg"
    columns = daily_fields.split(",")
    hist = _query_tushare(
        "daily",
        ts_code=ts_code,
        start_date=start_trade_date,
        end_date=end_trade_date,
        fields=daily_fields,
    )
    if hist.empty:
        return pd.DataFrame(columns=columns)

    hist = hist.reindex(columns=columns)
    hist = hist[
        (hist["ts_code"].astype(str) == ts_code)
        & hist["trade_date"].astype(str).between(start_trade_date, end_trade_date)
    ].copy()
    return hist.drop_duplicates(subset=["ts_code", "trade_date"]).sort_values("trade_date").reset_index(drop=True)


def get_stock_minute_bars(ts_code: str, start_datetime: str, end_datetime: str, freq="60min"):
    """获取单只股票分钟线，时间格式为 YYYY-MM-DD HH:MM:SS。"""
    fields = "ts_code,trade_time,open,close,high,low,vol,amount"
    columns = fields.split(",")
    hist = _query_tushare(
        "stk_mins",
        ts_code=ts_code,
        freq=freq,
        start_date=start_datetime,
        end_date=end_datetime,
        fields=fields,
    )
    if hist is None or hist.empty:
        return pd.DataFrame(columns=columns)
    hist = hist.reindex(columns=columns)
    hist = hist[hist["ts_code"].astype(str) == ts_code].copy()
    return hist.drop_duplicates(subset=["ts_code", "trade_time"]).sort_values("trade_time").reset_index(drop=True)


def get_sector_data(trade_date: str):
    """
    获取申万行业板块当日涨跌数据。
    返回 DataFrame，包含 industry_name, avg_pct_chg, stock_count 等字段。
    通过个股数据 + 行业映射聚合计算。
    """
    if get_cache_config().enabled:
        merged = load_market_snapshot(trade_date)
        if merged.empty:
            return pd.DataFrame()
        merged = _filter_mainboard_a(merged)
        sector_df = (
            merged.dropna(subset=["industry"]).groupby("industry")["pct_chg"]
            .agg([("avg_pct_chg", "mean"), ("stock_count", "count"), ("max_pct_chg", "max")])
            .reset_index().rename(columns={"industry": "industry_name"})
        )
        return sector_df, merged
    try:
        # 获取股票基本信息（含行业）
        stock_basic = _query_tushare(
            "stock_basic",
            exchange='',
            list_status='L',
            fields='ts_code,name,industry'
        )
    except Exception as e:
        print(f"获取股票基本信息失败: {e}")
        return pd.DataFrame()

    try:
        df_daily = _query_tushare("daily", trade_date=trade_date)
    except Exception as e:
        print(f"获取日线数据失败: {e}")
        return pd.DataFrame()

    if df_daily.empty or stock_basic.empty:
        return pd.DataFrame()

    df_daily = _filter_mainboard_a(df_daily)
    stock_basic = _filter_mainboard_a(stock_basic)

    merged = pd.merge(df_daily, stock_basic[["ts_code", "name", "industry"]], on="ts_code", how="inner")
    merged["pct_chg"] = pd.to_numeric(merged["pct_chg"], errors="coerce")
    merged = merged.dropna(subset=["pct_chg", "industry"])

    sector_df = (
        merged.groupby("industry")
        .agg(
            avg_pct_chg=("pct_chg", "mean"),
            stock_count=("ts_code", "count"),
            min_pct_chg=("pct_chg", "min"),
            max_pct_chg=("pct_chg", "max"),
        )
        .reset_index()
        .rename(columns={"industry": "industry_name"})
    )

    return sector_df, merged


def _moneyflow_records(df: pd.DataFrame, limit: int, ascending: bool) -> list[dict]:
    sorted_df = df.sort_values("net_amount", ascending=ascending).head(limit)
    wanted_cols = [
        "content_type",
        "ts_code",
        "name",
        "pct_change",
        "close",
        "net_amount",
        "net_amount_rate",
        "buy_elg_amount",
        "buy_lg_amount",
        "rank",
    ]
    cols = [col for col in wanted_cols if col in sorted_df.columns]
    return sorted_df[cols].to_dict(orient="records")


def _empty_moneyflow_summary(requested_trade_date: str, trade_date: str | None = None) -> dict:
    actual_trade_date = trade_date or requested_trade_date
    return {
        "requested_trade_date": requested_trade_date,
        "trade_date": actual_trade_date,
        "source": "moneyflow_ind_dc",
        "total_net_amount": 0,
        "inflow_count": 0,
        "outflow_count": 0,
        "top_inflow": [],
        "top_outflow": [],
    }


def get_moneyflow_summary(trade_date: str, limit: int = 8):
    """获取东方财富板块资金流汇总，用于总览展示。"""
    limit = max(1, min(int(limit), 20))
    requested_trade_date = str(trade_date)
    moneyflow_trade_date = requested_trade_date
    df = load_moneyflow(requested_trade_date) if get_cache_config().enabled else _query_tushare("moneyflow_ind_dc", trade_date=requested_trade_date)
    if df is None or df.empty:
        fallback_dates = []
        try:
            fallback_dates = get_trade_dates(n=5, end_date=requested_trade_date)
        except Exception as exc:
            print(f"板块资金流日期 {requested_trade_date} 无数据，获取回退交易日失败: {exc}")

        for fallback_trade_date in fallback_dates:
            fallback_trade_date = str(fallback_trade_date)
            if fallback_trade_date == requested_trade_date:
                continue
            fallback_df = _query_tushare("moneyflow_ind_dc", trade_date=fallback_trade_date)
            if fallback_df is not None and not fallback_df.empty:
                df = fallback_df
                moneyflow_trade_date = fallback_trade_date
                print(f"板块资金流日期 {requested_trade_date} 无数据，使用最近可用日期: {moneyflow_trade_date}")
                break

    if df is None or df.empty:
        print(f"板块资金流日期: {requested_trade_date}，无可用数据")
        return _empty_moneyflow_summary(requested_trade_date)

    result = df.copy()
    numeric_cols = [
        "pct_change",
        "close",
        "net_amount",
        "net_amount_rate",
        "buy_elg_amount",
        "buy_lg_amount",
        "buy_md_amount",
        "buy_sm_amount",
        "rank",
    ]
    for col in numeric_cols:
        if col in result.columns:
            result[col] = pd.to_numeric(result[col], errors="coerce")

    result = result.dropna(subset=["net_amount"])
    if result.empty:
        print(f"板块资金流日期: {moneyflow_trade_date}，净流入字段无有效数据")
        return _empty_moneyflow_summary(requested_trade_date, moneyflow_trade_date)

    print(f"板块资金流日期: {moneyflow_trade_date}")

    return {
        "requested_trade_date": requested_trade_date,
        "trade_date": moneyflow_trade_date,
        "source": "moneyflow_ind_dc",
        "total_net_amount": float(result["net_amount"].sum()),
        "inflow_count": int((result["net_amount"] > 0).sum()),
        "outflow_count": int((result["net_amount"] < 0).sum()),
        "top_inflow": _moneyflow_records(result[result["net_amount"] > 0], limit, ascending=False),
        "top_outflow": _moneyflow_records(result[result["net_amount"] < 0], limit, ascending=True),
    }
