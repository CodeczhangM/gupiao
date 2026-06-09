import tushare as ts
import pandas as pd
import os
import time
from datetime import datetime, timedelta

import settings

token = os.getenv("TUSHARE_TOKEN")

pro = ts.pro_api(token) if token else ts.pro_api()

if token:
    pro._DataApi__token = token

tushare_http_url = os.getenv("TUSHARE_HTTP_URL", "https://fastapia.stockai888.top")
if tushare_http_url:
    pro._DataApi__http_url = tushare_http_url

# ── 主板交易所前缀 ──────────────────────────────────────────────
MAINBOARD_SUFFIX = (".SH", ".SZ")
MAINBOARD_EXCLUDE_PREFIX = ("3",)   # 创业板 300xxx、科创板 688xxx 用后缀过滤更准
TUSHARE_RETRY_DELAYS = (3, 6, 10)

_query_cache = {}


class MarketDataUnavailable(Exception):
    """行情接口当天数据尚未可用。"""


def _is_rate_limited(exc: Exception) -> bool:
    message = str(exc).lower()
    return "rate limited" in message or "reduce the concurrency" in message


def _query_tushare(api_name: str, **kwargs):
    """调用 Tushare，遇到限流自动等待重试，并缓存相同请求。"""
    if not token:
        raise RuntimeError("缺少 TUSHARE_TOKEN 环境变量，无法获取行情数据")

    cache_key = (api_name, tuple(sorted(kwargs.items())))
    if cache_key in _query_cache:
        cached = _query_cache[cache_key]
        return cached.copy() if isinstance(cached, pd.DataFrame) else cached

    last_error = None
    for attempt in range(len(TUSHARE_RETRY_DELAYS) + 1):
        try:
            result = getattr(pro, api_name)(**kwargs)
            _query_cache[cache_key] = result.copy() if isinstance(result, pd.DataFrame) else result
            return result
        except Exception as exc:
            last_error = exc
            if not _is_rate_limited(exc) or attempt >= len(TUSHARE_RETRY_DELAYS):
                break

            delay = TUSHARE_RETRY_DELAYS[attempt]
            print(f"Tushare 限流，{delay} 秒后重试 {api_name}（第 {attempt + 1} 次）...")
            time.sleep(delay)

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

    hist = _query_tushare("daily", start_date=start_date, end_date=end_trade_date)
    if hist.empty:
        hist = pd.DataFrame()
    else:
        hist = hist[hist["trade_date"].astype(str).isin(dates)].copy()

    loaded_dates = set(hist["trade_date"].astype(str)) if "trade_date" in hist.columns else set()
    missing_dates = [date for date in dates if date not in loaded_dates]

    if missing_dates:
        extra_frames = []
        print(f"区间历史数据不足，逐日补拉 {len(missing_dates)} 个交易日...")
        for trade_date in missing_dates:
            daily = _query_tushare("daily", trade_date=trade_date)
            if not daily.empty:
                extra_frames.append(daily)

        if extra_frames:
            hist = pd.concat([hist, *extra_frames], ignore_index=True)
            hist = hist.drop_duplicates(subset=["ts_code", "trade_date"])

    if hist.empty or "trade_date" not in hist.columns:
        return hist

    return hist[hist["trade_date"].astype(str).isin(dates)].copy()


def get_sector_data(trade_date: str):
    """
    获取申万行业板块当日涨跌数据。
    返回 DataFrame，包含 industry_name, avg_pct_chg, stock_count 等字段。
    通过个股数据 + 行业映射聚合计算。
    """
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
