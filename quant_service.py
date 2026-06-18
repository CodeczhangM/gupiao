from datetime import datetime
import math

import pandas as pd

from ai_agent import analyze_stocks
from data_service import get_market_data, get_recent_daily_data, get_sector_data
from strategy import (
    format_for_ai,
    format_sectors_for_ai,
    pick_dip_sectors,
    pick_dip_stocks,
    pick_strong_base_candidates,
    pick_stocks,
)


def _clean_value(value):
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if hasattr(value, "item"):
        return _clean_value(value.item())
    if isinstance(value, dict):
        return {key: _clean_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_value(item) for item in value]
    return value


def dataframe_to_records(df: pd.DataFrame, limit: int | None = None):
    if df is None or df.empty:
        return []
    if limit:
        df = df.head(limit)
    records = df.to_dict(orient="records")
    return _clean_value(records)


def run_quant_scan(include_ai: bool = False, limit: int = 20):
    df, trade_date = get_market_data()

    strong_base = pick_strong_base_candidates(df)
    try:
        hist_days = 61 if not strong_base.empty else 40
        hist_df = get_recent_daily_data(trade_date, n=hist_days)
    except Exception:
        hist_df = pd.DataFrame()

    strong = pick_stocks(df, hist_df)
    strong_text = (
        format_for_ai(strong, label="优势股", limit=10)
        if not strong.empty
        else "【优势股】无符合条件的股票。"
    )

    dip = pick_dip_stocks(df, hist_df)

    sector_result = get_sector_data(trade_date)
    if isinstance(sector_result, tuple):
        sector_df, stock_merged = sector_result
        dip_sectors, rep_stocks = pick_dip_sectors(sector_df, stock_merged)
    else:
        dip_sectors, rep_stocks = pd.DataFrame(), pd.DataFrame()

    dip_text = format_sectors_for_ai(dip_sectors, rep_stocks)
    if not dip.empty:
        dip_text += "\n\n" + format_for_ai(dip, label="抄底候选个股", limit=10)

    ai_analysis = None
    if include_ai:
        ai_analysis = analyze_stocks(strong_text, dip_text, trade_date)

    return {
        "trade_date": trade_date,
        "status": "success",
        "include_ai": include_ai,
        "strong": dataframe_to_records(strong, limit),
        "dip": dataframe_to_records(dip, limit),
        "sectors": dataframe_to_records(dip_sectors, limit),
        "rep_stocks": dataframe_to_records(rep_stocks, limit),
        "ai_analysis": ai_analysis,
        "error_message": None,
        "created_at": datetime.now(),
    }
