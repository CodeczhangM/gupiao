from collections import defaultdict
import math
import re

import pandas as pd

from database import list_ai_reports
from data_service import get_market_data_by_date, get_trade_dates


def _clean_number(value):
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return round(float(value), 4)


def _pct_return(entry_price, exit_price):
    entry = float(entry_price or 0)
    exit_ = float(exit_price or 0)
    if entry <= 0 or exit_ <= 0:
        return None
    return (exit_ / entry - 1) * 100


def _summary(rows, expected_positive=True):
    finished = [row for row in rows if row.get("status") == "finished"]
    if not finished:
        return {
            "count": 0,
            "correct": 0,
            "wrong": 0,
            "accuracy": None,
            "error_rate": None,
            "avg_return": None,
        }

    def is_correct(row):
        value = row.get("return_pct")
        if value is None:
            return False
        return value > 0 if expected_positive else value <= 0

    correct = [row for row in finished if is_correct(row)]
    returns = [row["return_pct"] for row in finished if row.get("return_pct") is not None]
    series = pd.Series(returns) if returns else pd.Series(dtype=float)
    accuracy = len(correct) / len(finished) * 100 if finished else None

    return {
        "count": len(finished),
        "correct": len(correct),
        "wrong": len(finished) - len(correct),
        "accuracy": _clean_number(accuracy),
        "error_rate": _clean_number(100 - accuracy) if accuracy is not None else None,
        "avg_return": _clean_number(series.mean()) if not series.empty else None,
    }


def _mentioned_by_ai(ai_text: str, stock: dict):
    if not ai_text:
        return False
    ts_code = str(stock.get("ts_code") or "")
    code = ts_code.split(".")[0]
    name = str(stock.get("name") or "")
    patterns = [re.escape(ts_code), re.escape(code), re.escape(name)]
    return any(pattern and re.search(pattern, ai_text, flags=re.IGNORECASE) for pattern in patterns)


def _build_trade_date_index(max_days=220):
    dates = get_trade_dates(n=max_days)
    return dates, {trade_date: index for index, trade_date in enumerate(dates)}


def _get_exit_date(trade_date, hold_days, dates, index_by_date):
    index = index_by_date.get(str(trade_date))
    if index is None:
        return None, "trade_date_not_in_recent_calendar"
    if index < hold_days:
        return None, "pending_future_exit_date"
    return dates[index - hold_days], None


def _append_rows(rows, report, strategy, stocks, ai_text, exit_date, exit_prices, status):
    for stock in stocks:
        mentioned = _mentioned_by_ai(ai_text, stock)
        exit_price = exit_prices.get(stock.get("ts_code")) if exit_prices else None
        return_pct = _pct_return(stock.get("close"), exit_price)

        rows.append({
            "report_id": report["id"],
            "strategy": strategy,
            "trade_date": report["trade_date"],
            "exit_date": exit_date,
            "ts_code": stock.get("ts_code"),
            "name": stock.get("name"),
            "industry": stock.get("industry"),
            "ai_recommended": mentioned,
            "entry_close": _clean_number(stock.get("close")),
            "exit_close": _clean_number(exit_price),
            "return_pct": _clean_number(return_pct),
            "score": _clean_number(stock.get("score", stock.get("dip_score"))),
            "status": "finished" if status == "finished" and return_pct is not None else status,
        })


def _rank_by_return(rows, reverse=True):
    finished = [row for row in rows if row.get("status") == "finished" and row.get("return_pct") is not None]
    return sorted(finished, key=lambda row: row["return_pct"], reverse=reverse)


def _aggregate_by_stock(rows):
    grouped = defaultdict(list)
    for row in rows:
        if row.get("status") == "finished" and row.get("return_pct") is not None:
            grouped[(row["ts_code"], row.get("name"), row.get("strategy"))].append(row)

    result = []
    for (ts_code, name, strategy), items in grouped.items():
        returns = pd.Series([item["return_pct"] for item in items])
        result.append({
            "ts_code": ts_code,
            "name": name,
            "strategy": strategy,
            "count": len(items),
            "win_rate": _clean_number((returns > 0).mean() * 100),
            "avg_return": _clean_number(returns.mean()),
            "max_return": _clean_number(returns.max()),
            "min_return": _clean_number(returns.min()),
        })

    return sorted(result, key=lambda row: (row["avg_return"], row["win_rate"], row["count"]), reverse=True)


def evaluate_ai_recommendations(hold_days: int = 3, report_limit: int = 50, stock_limit: int = 20):
    hold_days = max(1, min(int(hold_days), 20))
    report_limit = max(1, min(int(report_limit), 200))
    stock_limit = max(1, min(int(stock_limit), 100))

    reports = list_ai_reports(report_limit)
    dates, index_by_date = _build_trade_date_index()
    rows = []
    skipped = []

    for report in reports:
        exit_date, reason = _get_exit_date(report["trade_date"], hold_days, dates, index_by_date)
        exit_prices = {}
        status = "finished"

        if reason:
            status = "pending" if reason == "pending_future_exit_date" else "skipped"
            skipped.append({"report_id": report["id"], "trade_date": report["trade_date"], "reason": reason})
        else:
            try:
                exit_df, _ = get_market_data_by_date(exit_date)
                exit_prices = dict(zip(exit_df["ts_code"], exit_df["close"]))
            except Exception as exc:
                status = "skipped"
                skipped.append({"report_id": report["id"], "trade_date": report["trade_date"], "reason": str(exc)})

        _append_rows(rows, report, "strong", report.get("strong", [])[:stock_limit], report.get("ai_analysis"), exit_date, exit_prices, status)
        _append_rows(rows, report, "dip", report.get("dip", [])[:stock_limit], report.get("ai_analysis"), exit_date, exit_prices, status)

    recommended = [row for row in rows if row["ai_recommended"]]
    not_recommended = [row for row in rows if not row["ai_recommended"]]
    missed = [row for row in not_recommended if row.get("status") == "finished" and (row.get("return_pct") or 0) > 0]

    return {
        "hold_days": hold_days,
        "report_limit": report_limit,
        "stock_limit": stock_limit,
        "reports_used": len(reports),
        "summary": {
            "ai_recommended": _summary(recommended, expected_positive=True),
            "not_recommended": _summary(not_recommended, expected_positive=False),
            "all_candidates": _summary(rows, expected_positive=True),
        },
        "ranking": {
            "recommended_best": _rank_by_return(recommended, reverse=True)[:30],
            "recommended_worst": _rank_by_return(recommended, reverse=False)[:30],
            "missed_best": _rank_by_return(missed, reverse=True)[:30],
            "stock_aggregate": _aggregate_by_stock(recommended)[:50],
        },
        "results": rows,
        "skipped": skipped,
        "note": "当前版本通过股票代码或名称是否出现在 AI 分析文本中判断是否被 AI 推荐。更严谨的版本需要让 AI 输出结构化推荐结果。",
    }

