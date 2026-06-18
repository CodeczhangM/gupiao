from datetime import datetime
import math

import pandas as pd

from data_service import get_market_data_by_date, get_recent_daily_data, get_trade_dates
from quant_service import dataframe_to_records
from strategy import pick_dip_stocks, pick_stocks, pick_strong_base_candidates


def _pct_return(entry_price, exit_price):
    entry = float(entry_price or 0)
    exit_ = float(exit_price or 0)
    if entry <= 0 or exit_ <= 0:
        return None
    return (exit_ / entry - 1) * 100


def _clean_number(value):
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return round(float(value), 4)


def _summary(rows):
    returns = [row["return_pct"] for row in rows if row.get("return_pct") is not None]
    if not returns:
        return {
            "count": 0,
            "win_rate": None,
            "avg_return": None,
            "median_return": None,
            "max_return": None,
            "min_return": None,
        }

    series = pd.Series(returns)
    return {
        "count": len(returns),
        "win_rate": _clean_number((series > 0).mean() * 100),
        "avg_return": _clean_number(series.mean()),
        "median_return": _clean_number(series.median()),
        "max_return": _clean_number(series.max()),
        "min_return": _clean_number(series.min()),
    }


def _append_result(rows, strategy, trade_date, exit_date, stock, exit_prices):
    ts_code = stock.get("ts_code")
    exit_price = exit_prices.get(ts_code)
    return_pct = _pct_return(stock.get("close"), exit_price)
    rows.append({
        "strategy": strategy,
        "trade_date": trade_date,
        "exit_date": exit_date,
        "ts_code": ts_code,
        "name": stock.get("name"),
        "industry": stock.get("industry"),
        "entry_close": _clean_number(stock.get("close")),
        "exit_close": _clean_number(exit_price),
        "return_pct": _clean_number(return_pct),
        "pct_chg": _clean_number(stock.get("pct_chg")),
        "turnover_rate": _clean_number(stock.get("turnover_rate")),
        "volume_ratio": _clean_number(stock.get("volume_ratio")),
        "score": _clean_number(stock.get("score", stock.get("dip_score"))),
    })


def run_backtest(lookback_days: int = 30, hold_days: int = 3, limit: int = 20):
    """
    回测当前选股规则：
    - 选股日复用现有优势股/抄底股规则。
    - 收益按选股日收盘价买入，持有 hold_days 个交易日后按收盘价卖出。
    """
    lookback_days = max(1, min(int(lookback_days), 120))
    hold_days = max(1, min(int(hold_days), 20))
    limit = max(1, min(int(limit), 100))

    dates = get_trade_dates(n=lookback_days + hold_days + 2)
    test_dates = dates[hold_days:hold_days + lookback_days]
    rows = []
    skipped = []

    for trade_date in test_dates:
        date_index = dates.index(trade_date)
        exit_date = dates[date_index - hold_days]

        try:
            df, _ = get_market_data_by_date(trade_date)
            exit_df, _ = get_market_data_by_date(exit_date)
        except Exception as exc:
            skipped.append({"trade_date": trade_date, "reason": str(exc)})
            continue

        exit_prices = dict(zip(exit_df["ts_code"], exit_df["close"]))

        strong_base = pick_strong_base_candidates(df)
        try:
            hist_days = 61 if not strong_base.empty else 40
            hist_df = get_recent_daily_data(trade_date, n=hist_days)
        except Exception:
            hist_df = pd.DataFrame()

        strong = pick_stocks(df, hist_df).head(limit)
        for stock in dataframe_to_records(strong):
            _append_result(rows, "strong", trade_date, exit_date, stock, exit_prices)

        dip = pick_dip_stocks(df, hist_df).head(limit)
        for stock in dataframe_to_records(dip):
            _append_result(rows, "dip", trade_date, exit_date, stock, exit_prices)

    strong_rows = [row for row in rows if row["strategy"] == "strong"]
    dip_rows = [row for row in rows if row["strategy"] == "dip"]

    return {
        "lookback_days": lookback_days,
        "hold_days": hold_days,
        "limit": limit,
        "start_trade_date": test_dates[-1] if test_dates else None,
        "end_trade_date": test_dates[0] if test_dates else None,
        "summary": {
            "all": _summary(rows),
            "strong": _summary(strong_rows),
            "dip": _summary(dip_rows),
        },
        "results": rows,
        "skipped": skipped,
        "created_at": datetime.now().isoformat(sep=" "),
    }
