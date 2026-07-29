from datetime import datetime, timedelta
import math

import pandas as pd

from ai_agent import analyze_stocks
from data_service import get_cached_scan_inputs, get_market_data, get_moneyflow_summary, get_recent_daily_data, get_sector_data, get_stock_minute_bars
from market_cache import get_cache_config
from strategy import (
    _attach_intraday_signal_stocks,
    format_for_ai,
    format_sectors_for_ai,
    pick_dip_sectors,
    pick_sector_tail_buy_stocks,
    pick_strong_base_candidates,
    rank_sector_potential,
    select_stock_pools,
)


def _clean_value(value):
    if value is None:
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat(sep=" ")
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if hasattr(value, "item"):
        return _clean_value(value.item())
    if isinstance(value, dict):
        return {key: _clean_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_value(item) for item in value]
    return value


def _trade_date_to_datetime_window(trade_date: str, lookback_days=70) -> tuple[str, str]:
    end_day = datetime.strptime(str(trade_date), "%Y%m%d")
    start_day = end_day - timedelta(days=lookback_days)
    return (
        start_day.strftime("%Y-%m-%d 09:30:00"),
        end_day.strftime("%Y-%m-%d 15:00:00"),
    )


def _tail_datetime_window(trade_date: str) -> tuple[str, str]:
    day = datetime.strptime(str(trade_date), "%Y%m%d")
    return day.strftime("%Y-%m-%d 14:25:00"), day.strftime("%Y-%m-%d 15:00:00")


def _load_intraday_signal_bars(market_df: pd.DataFrame, sector_potential: pd.DataFrame, trade_date: str) -> dict[str, dict[str, pd.DataFrame]]:
    if market_df is None or market_df.empty or sector_potential is None or sector_potential.empty:
        return {}
    if "industry_name" not in sector_potential.columns or "industry" not in market_df.columns:
        return {}

    industries = set(sector_potential["industry_name"].dropna().astype(str))
    candidates = market_df[market_df["industry"].astype(str).isin(industries)].copy()
    for column in ("turnover_rate", "volume_ratio", "amount", "pct_chg"):
        candidates[column] = pd.to_numeric(candidates[column], errors="coerce") if column in candidates else 0
    candidates = candidates[
        candidates["turnover_rate"].between(2, 10, inclusive="both") &
        (candidates["volume_ratio"] > 2)
    ].copy()
    if candidates.empty:
        return {}

    candidates = candidates.sort_values(["industry", "amount", "volume_ratio"], ascending=[True, False, False])
    candidates = candidates.groupby("industry", group_keys=False).head(20)
    start_datetime, end_datetime = _trade_date_to_datetime_window(trade_date)
    tail_start_datetime, tail_end_datetime = _tail_datetime_window(trade_date)
    bars_by_code = {}
    for ts_code in candidates["ts_code"].dropna().astype(str).drop_duplicates():
        try:
            bars_60m = get_stock_minute_bars(ts_code, start_datetime, end_datetime, freq="60min")
        except Exception as exc:
            print(f"获取60分钟线失败 {ts_code}: {exc}")
            continue
        if bars_60m.empty:
            continue

        try:
            tail_1m = get_stock_minute_bars(ts_code, tail_start_datetime, tail_end_datetime, freq="1min")
        except Exception as exc:
            print(f"获取尾盘1分钟线失败 {ts_code}: {exc}")
            tail_1m = pd.DataFrame()
        bars_by_code[ts_code] = {"60m": bars_60m, "tail_1m": tail_1m}
    return bars_by_code


def dataframe_to_records(df: pd.DataFrame, limit: int | None = None):
    if df is None or df.empty:
        return []
    if limit:
        df = df.head(limit)
    records = df.to_dict(orient="records")
    return _clean_value(records)


def run_quant_scan(include_ai: bool = False, limit: int = 20):
    cache_metadata = {"data_trade_date": None, "cache_updated": False, "cache_warnings": []}
    cached_history = None
    if get_cache_config().enabled:
        df, cached_history, cache_metadata = get_cached_scan_inputs(100)
        trade_date = cache_metadata["data_trade_date"]
    else:
        df, trade_date = get_market_data()

    strong_base = pick_strong_base_candidates(df)
    try:
        # The longest indicator lookback is MA60's 40-day slope comparison,
        # which needs 100 bars. Asking for 180 days makes the capped market
        # data response fall back to almost 180 sequential daily requests.
        hist_days = 100 if not strong_base.empty else 40
        hist_df = cached_history if cached_history is not None else get_recent_daily_data(trade_date, n=hist_days)
    except Exception:
        hist_df = pd.DataFrame()

    try:
        moneyflow_summary = get_moneyflow_summary(trade_date)
    except Exception:
        moneyflow_summary = {
            "requested_trade_date": trade_date,
            "trade_date": trade_date,
            "source": "moneyflow_ind_dc",
            "total_net_amount": 0,
            "inflow_count": 0,
            "outflow_count": 0,
            "top_inflow": [],
            "top_outflow": [],
        }
    core_inflow_sectors = [
        item.get("name")
        for item in moneyflow_summary.get("top_inflow", [])
        if isinstance(item, dict) and item.get("name")
    ]

    pools = select_stock_pools(df, hist_df, core_inflow_sectors=core_inflow_sectors)
    reversal = pools["reversal"]
    breakout = pools["breakout"]
    first_limit = pools["first_limit"]
    sector_potential = rank_sector_potential(
        df,
        hist_df,
        breakout_pool=breakout,
        first_limit_pool=first_limit,
        limit=limit,
    )
    intraday_bars = _load_intraday_signal_bars(df, sector_potential, trade_date)
    sector_potential = _attach_intraday_signal_stocks(
        sector_potential,
        df,
        intraday_bars,
        per_sector=5,
    )

    strong = breakout
    strong_text = (
        format_for_ai(strong, label="趋势突破", limit=10)
        if not strong.empty
        else "【趋势突破】无符合条件的股票。"
    )

    dip = reversal

    sector_result = get_sector_data(trade_date)
    if isinstance(sector_result, tuple):
        sector_df, stock_merged = sector_result
        dip_sectors, rep_stocks = pick_dip_sectors(sector_df, stock_merged)
        rep_stocks = pick_sector_tail_buy_stocks(df, hist_df, rep_stocks, breakout_pool=breakout)
    else:
        dip_sectors, rep_stocks = pd.DataFrame(), pd.DataFrame()

    sector_text = format_sectors_for_ai(dip_sectors, rep_stocks)
    dip_text = sector_text
    if not dip.empty:
        dip_text += "\n\n" + format_for_ai(dip, label="超跌反转", limit=10)

    first_limit_text = (
        format_for_ai(first_limit, label="主升浪启动", limit=10)
        if not first_limit.empty
        else "【主升浪启动】无符合条件的股票。"
    )

    ai_analysis = None
    if include_ai:
        ai_analysis = analyze_stocks(strong_text, dip_text, trade_date, first_limit_text)

    return {
        "trade_date": trade_date,
        "status": "success",
        "include_ai": include_ai,
        "strong": dataframe_to_records(strong, limit),
        "dip": dataframe_to_records(dip, limit),
        "first_limit": dataframe_to_records(first_limit, limit),
        "pools": {
            "reversal": dataframe_to_records(reversal, limit),
            "breakout": dataframe_to_records(breakout, limit),
            "first_limit": dataframe_to_records(first_limit, limit),
        },
        "sectors": dataframe_to_records(dip_sectors, limit),
        "rep_stocks": dataframe_to_records(rep_stocks, limit),
        "sector_potential": dataframe_to_records(sector_potential, limit),
        "moneyflow_summary": _clean_value(moneyflow_summary),
        "ai_analysis": ai_analysis,
        "error_message": None,
        "data_trade_date": cache_metadata.get("data_trade_date") or trade_date,
        "cache_updated": bool(cache_metadata.get("cache_updated")),
        "cache_warnings": cache_metadata.get("cache_warnings", []),
        "created_at": datetime.now(),
    }
