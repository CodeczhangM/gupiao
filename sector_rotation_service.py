from __future__ import annotations

import math
from typing import Any

import pandas as pd

from market_cache import (
    get_complete_dates,
    load_market_snapshot,
    load_moneyflow,
    load_recent_daily,
)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _normalize(value: Any, low: float, high: float) -> float:
    number = _num(value)
    if high == low:
        return 0.0
    return _clip((number - low) / (high - low) * 100.0)


def _normalize_series(series: pd.Series, low: float, high: float) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").fillna(0)
    if high == low:
        return pd.Series(0.0, index=values.index)
    return ((values - low) / (high - low) * 100.0).clip(0, 100)


def _percentile(series: pd.Series) -> pd.Series:
    if series.empty:
        return series
    return series.rank(pct=True).fillna(0) * 100.0


def _empty_payload(
    trade_date: str | None,
    lookback_dates: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    return _json_safe({
        "trade_date": trade_date,
        "lookback_trade_dates": lookback_dates,
        "context_trade_dates": {"short": [], "medium_count": 0},
        "moneyflow_trade_dates": [],
        "source": "moneyflow_ind_dc",
        "warnings": warnings,
        "continuation_inflow": [],
        "rotation_rebound": [],
    })


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "item"):
        return _json_safe(value.item())
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _prepare_market(market_df: pd.DataFrame) -> pd.DataFrame:
    if market_df is None or market_df.empty:
        return pd.DataFrame()
    market = market_df.copy()
    if "industry" not in market or "ts_code" not in market:
        return pd.DataFrame()
    market["industry"] = market["industry"].fillna("").astype(str)
    market = market[market["industry"] != ""].copy()
    market["ts_code"] = market["ts_code"].astype(str)
    for column in [
        "pct_chg",
        "amount",
        "turnover_rate",
        "volume_ratio",
        "close",
        "total_mv",
    ]:
        if column not in market:
            market[column] = 0
        market[column] = pd.to_numeric(market[column], errors="coerce")
    return market


def _prepare_history(
    history_df: pd.DataFrame,
    market: pd.DataFrame,
) -> pd.DataFrame:
    if history_df is None or history_df.empty or market.empty:
        return pd.DataFrame()
    if "ts_code" not in history_df or "trade_date" not in history_df:
        return pd.DataFrame()
    history = history_df.copy()
    history["ts_code"] = history["ts_code"].astype(str)
    info = market[["ts_code", "industry"]].drop_duplicates("ts_code")
    history = history.merge(info, on="ts_code", how="inner")
    history["trade_date"] = history["trade_date"].astype(str)
    for column in ["pct_chg", "amount", "close", "high", "low"]:
        if column not in history:
            history[column] = history.get("close", 0)
        history[column] = pd.to_numeric(history[column], errors="coerce")
    return history


def _load_moneyflow_by_date(
    dates: list[str],
) -> tuple[pd.DataFrame, list[str], list[str]]:
    frames = []
    used_dates = []
    warnings = []
    for trade_date in dates:
        frame = load_moneyflow(trade_date)
        if frame is None or frame.empty:
            warnings.append(f"{trade_date} 板块资金流缺失")
            continue
        current = frame.copy()
        current["trade_date"] = str(trade_date)
        if "name" not in current:
            warnings.append(f"{trade_date} 板块资金流缺少 name 字段")
            continue
        for column in ["net_amount", "net_amount_rate", "pct_change"]:
            if column not in current:
                current[column] = 0
            current[column] = pd.to_numeric(current[column], errors="coerce")
        frames.append(current)
        used_dates.append(str(trade_date))
    if not frames:
        return pd.DataFrame(), used_dates, warnings
    return pd.concat(frames, ignore_index=True), used_dates, warnings


def _sector_market_metrics(
    market: pd.DataFrame,
    history: pd.DataFrame,
) -> pd.DataFrame:
    latest = market.groupby("industry").agg(
        stock_count=("ts_code", "count"),
        avg_pct_chg=("pct_chg", "mean"),
        up_ratio=("pct_chg", lambda values: float((values > 0).mean())),
        strong_ratio=("pct_chg", lambda values: float((values >= 5).mean())),
        limit_up_count=("pct_chg", lambda values: int((values >= 9.8).sum())),
        amount_sum=("amount", "sum"),
        turnover_rate=("turnover_rate", "mean"),
        volume_ratio=("volume_ratio", "mean"),
    ).reset_index()
    if history.empty:
        latest["prev_amount_sum"] = pd.NA
        latest["prev_avg_pct_chg"] = 0.0
        latest["ret_5"] = 0.0
        latest["ret_20"] = 0.0
        latest["position_20"] = 0.5
        latest["amount_expand_rate"] = 1.0
        return latest

    dates = sorted(history["trade_date"].dropna().unique().tolist())
    prev_date = dates[-2] if len(dates) >= 2 else dates[-1]
    previous = history[history["trade_date"] == prev_date]
    prev = previous.groupby("industry").agg(
        prev_amount_sum=("amount", "sum"),
        prev_avg_pct_chg=("pct_chg", "mean"),
    ).reset_index()
    latest = latest.merge(prev, on="industry", how="left")

    for window in [5, 20]:
        window_dates = dates[-window:]
        chunk = history[history["trade_date"].isin(window_dates)]
        returns = chunk.groupby("industry").agg(
            ret=("pct_chg", "mean"),
        ).reset_index()
        returns[f"ret_{window}"] = (
            returns["ret"] * min(len(window_dates), window)
        )
        latest = latest.merge(
            returns[["industry", f"ret_{window}"]],
            on="industry",
            how="left",
        )

    position_rows = []
    for industry, chunk in history.groupby("industry"):
        recent = chunk.sort_values("trade_date").tail(20)
        high = _num(recent["high"].max())
        low = _num(recent["low"].min())
        latest_close = (
            _num(recent["close"].iloc[-1]) if not recent.empty else 0
        )
        position = 0.5 if high <= low else (latest_close - low) / (high - low)
        position_rows.append({
            "industry": industry,
            "position_20": _clip(position, 0, 1),
        })
    latest = latest.merge(pd.DataFrame(position_rows), on="industry", how="left")
    latest["amount_expand_rate"] = (
        latest["amount_sum"] / latest["prev_amount_sum"].replace(0, pd.NA)
    ).fillna(1.0)
    return latest


def _sector_moneyflow_metrics(
    moneyflow: pd.DataFrame,
    dates: list[str],
) -> pd.DataFrame:
    if moneyflow.empty or not dates:
        return pd.DataFrame()
    prev_date = dates[0] if len(dates) > 1 else None
    today = dates[-1]
    today_df = moneyflow[moneyflow["trade_date"] == today].copy()
    today_df = today_df.rename(columns={
        "name": "industry",
        "net_amount": "net_amount_today",
        "net_amount_rate": "net_amount_rate_today",
    })
    result = today_df[[
        "industry",
        "net_amount_today",
        "net_amount_rate_today",
    ]].copy()
    if prev_date:
        prev_df = moneyflow[moneyflow["trade_date"] == prev_date].copy()
        prev_df = prev_df.rename(columns={
            "name": "industry",
            "net_amount": "net_amount_prev",
            "net_amount_rate": "net_amount_rate_prev",
        })
        result = result.merge(
            prev_df[["industry", "net_amount_prev", "net_amount_rate_prev"]],
            on="industry",
            how="left",
        )
    else:
        result["net_amount_prev"] = 0.0
        result["net_amount_rate_prev"] = 0.0
    result["net_amount_change"] = (
        result["net_amount_today"].fillna(0)
        - result["net_amount_prev"].fillna(0)
    )
    result["net_amount_rate_change"] = (
        result["net_amount_rate_today"].fillna(0)
        - result["net_amount_rate_prev"].fillna(0)
    )
    result["turned_positive"] = (
        (result["net_amount_prev"].fillna(0) < 0)
        & (result["net_amount_today"].fillna(0) > 0)
    )
    result["outflow_narrowed"] = (
        (result["net_amount_prev"].fillna(0) < 0)
        & (result["net_amount_today"].fillna(0) < 0)
        & (result["net_amount_today"].abs() < result["net_amount_prev"].abs())
    )
    result["two_day_positive"] = (
        (result["net_amount_prev"].fillna(0) > 0)
        & (result["net_amount_today"].fillna(0) > 0)
    )
    return result


def _score_sectors(
    sectors: pd.DataFrame,
    complete_moneyflow: bool,
) -> pd.DataFrame:
    if sectors.empty:
        return sectors
    result = sectors.copy()
    result["flow_today_pct"] = _percentile(result["net_amount_today"].fillna(0))
    result["flow_change_pct"] = _percentile(result["net_amount_change"].fillna(0))
    extension_penalty = (
        (
            (result["position_20"].fillna(0.5) > 0.92)
            & (result["ret_5"].fillna(0) > 12)
        ).astype(int) * 12
        + (
            (result["avg_pct_chg"].fillna(0) > 7)
            & (result["up_ratio"].fillna(0) < 0.45)
        ).astype(int) * 10
    )
    weak_penalty = (
        (
            (result["ret_20"].fillna(0) < -8)
            & (result["net_amount_today"].fillna(0) <= 0)
        ).astype(int) * 12
        + (result["amount_sum"].fillna(0) <= 0).astype(int) * 20
    )
    result["continuation_score"] = (
        result["two_day_positive"].astype(int) * 30
        + result["flow_today_pct"] * 0.20
        + result["flow_change_pct"] * 0.15
        + _normalize_series(result["avg_pct_chg"], -4, 8) * 0.08
        + _normalize_series(result["up_ratio"], 0, 1) * 0.07
        + _normalize_series(result["amount_expand_rate"], 0.6, 1.8) * 0.10
        + _normalize_series(result["strong_ratio"], 0, 0.6) * 0.10
        - extension_penalty
    ).clip(0, 100).round(2)
    result["rotation_score"] = (
        result["flow_change_pct"] * 0.30
        + result["turned_positive"].astype(int) * 20
        + result["outflow_narrowed"].astype(int) * 12
        + _normalize_series(result["avg_pct_chg"], -3, 5) * 0.08
        + _normalize_series(result["up_ratio"], 0, 1) * 0.07
        + _normalize_series(result["ret_5"], -8, 10) * 0.08
        + _normalize_series(result["ret_20"], -15, 20) * 0.07
        + (100 - _normalize_series(result["position_20"], 0.45, 1.0)) * 0.10
        + _normalize_series(result["amount_expand_rate"], 0.6, 1.6) * 0.10
        - weak_penalty
    ).clip(0, 100).round(2)
    result["confidence"] = "高" if complete_moneyflow else "中"
    result.loc[result["stock_count"].fillna(0) < 8, "confidence"] = "低"
    result["signal"] = result.apply(_sector_signal, axis=1)
    result["reason"] = result.apply(_sector_reasons, axis=1)
    return result


def _sector_signal(row: pd.Series) -> str:
    if bool(row.get("two_day_positive")) and _num(row.get("net_amount_change")) > 0:
        return "连续流入放量扩散"
    if bool(row.get("turned_positive")):
        return "资金由流出转流入"
    if bool(row.get("outflow_narrowed")):
        return "流出收窄观察"
    return "资金改善观察"


def _sector_reasons(row: pd.Series) -> list[str]:
    reasons = []
    if bool(row.get("two_day_positive")):
        reasons.append("两日连续净流入")
    if bool(row.get("turned_positive")):
        reasons.append("由流出转流入")
    if _num(row.get("net_amount_change")) > 0:
        reasons.append("今日资金改善")
    if _num(row.get("amount_expand_rate"), 1) >= 1.2:
        reasons.append("成交额放大")
    if _num(row.get("up_ratio")) >= 0.6:
        reasons.append("上涨家数扩散")
    if not reasons:
        reasons.append("资金和行情信号偏观察")
    return reasons


def _relative_five_day_strength(
    history: pd.DataFrame,
    stocks: pd.DataFrame,
) -> pd.DataFrame:
    if history.empty:
        return pd.DataFrame({
            "ts_code": stocks["ts_code"],
            "relative_strength_5": 50.0,
        })
    dates = sorted(history["trade_date"].dropna().unique().tolist())[-5:]
    recent = history[history["trade_date"].isin(dates)].copy()
    if recent.empty:
        return pd.DataFrame({
            "ts_code": stocks["ts_code"],
            "relative_strength_5": 50.0,
        })
    strength = (
        recent.groupby("ts_code")
        .agg(ret_5_stock=("pct_chg", "sum"))
        .reset_index()
    )
    strength["relative_strength_5"] = _percentile(strength["ret_5_stock"])
    return strength[["ts_code", "relative_strength_5"]]


def _stock_records(
    stocks: pd.DataFrame,
    score_key: str,
    limit: int,
) -> list[dict[str, Any]]:
    rows = []
    for row in stocks.head(limit).to_dict("records"):
        reasons = []
        if _num(row.get("pct_chg")) > 0:
            reasons.append(f"涨幅{_num(row.get('pct_chg')):.2f}%")
        if _num(row.get("volume_ratio")) >= 1.5:
            reasons.append("量比活跃")
        if _num(row.get("amount")) > 0:
            reasons.append("成交额靠前")
        rows.append({
            "ts_code": row.get("ts_code"),
            "name": row.get("name"),
            "industry": row.get("industry"),
            "close": row.get("close"),
            "pct_chg": row.get("pct_chg"),
            "amount": row.get("amount"),
            "turnover_rate": row.get("turnover_rate"),
            "volume_ratio": row.get("volume_ratio"),
            score_key: row.get(score_key),
            "free_review_score": row.get("free_review_score"),
            "reason": "、".join(reasons) if reasons else "板块内相对占优",
            "pool_tag": "",
        })
    return rows


def _stock_scores(
    market: pd.DataFrame,
    history: pd.DataFrame,
    industry: str,
    sector_row: pd.Series,
    stocks_per_sector: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    stocks = market[market["industry"] == industry].copy()
    if stocks.empty:
        return [], []
    stocks["amount_rank_score"] = _percentile(stocks["amount"].fillna(0))
    stocks["pct_rank_score"] = _percentile(stocks["pct_chg"].fillna(0))
    stocks["activity_score"] = (
        stocks["volume_ratio"].fillna(0).map(
            lambda value: _normalize(value, 0.8, 2.8)
        ) * 0.5
        + stocks["turnover_rate"].fillna(0).map(
            lambda value: _normalize(value, 0.5, 8)
        ) * 0.5
    )
    relative = _relative_five_day_strength(history, stocks)
    stocks = stocks.merge(relative, on="ts_code", how="left")
    stocks["position_score"] = (
        100 - _normalize_series(stocks["pct_chg"], 0, 10)
    ).clip(0, 100)
    stocks["attack_score"] = (
        stocks["pct_rank_score"] * 0.25
        + stocks["amount_rank_score"] * 0.20
        + stocks["activity_score"] * 0.15
        + stocks["relative_strength_5"].fillna(50) * 0.15
        + (stocks["pct_chg"].fillna(0) >= 5).astype(int) * 15
        + stocks["amount_rank_score"] * 0.10
        - (stocks["pct_chg"].fillna(0) >= 9.5).astype(int) * 8
    ).clip(0, 100).round(2)
    stocks["catchup_score"] = (
        _normalize(sector_row.get("net_amount_change"), -500000000, 1000000000) * 0.20
        + stocks["position_score"] * 0.20
        + stocks["activity_score"] * 0.15
        + _normalize_series(stocks["pct_chg"], -2, 4) * 0.15
        + (100 - stocks["relative_strength_5"].fillna(50)) * 0.15
        + stocks["amount_rank_score"] * 0.10
        + 50 * 0.05
    ).clip(0, 100).round(2)
    attack = _stock_records(
        stocks.sort_values("attack_score", ascending=False),
        "attack_score",
        stocks_per_sector,
    )
    catchup_pool = stocks[stocks["pct_chg"].fillna(0) < 6].copy()
    catchup = _stock_records(
        catchup_pool.sort_values("catchup_score", ascending=False),
        "catchup_score",
        stocks_per_sector,
    )
    return attack, catchup


def _sector_output(row: pd.Series, rank: int) -> dict[str, Any]:
    metrics = {
        "net_amount_today": row.get("net_amount_today"),
        "net_amount_prev": row.get("net_amount_prev"),
        "net_amount_change": row.get("net_amount_change"),
        "net_amount_rate_today": row.get("net_amount_rate_today"),
        "avg_pct_chg": row.get("avg_pct_chg"),
        "up_ratio": row.get("up_ratio"),
        "amount_expand_rate": row.get("amount_expand_rate"),
        "ret_5": row.get("ret_5"),
        "ret_20": row.get("ret_20"),
        "position_20": row.get("position_20"),
    }
    return {
        "rank": rank,
        "industry_name": row.get("industry"),
        "continuation_score": row.get("continuation_score"),
        "rotation_score": row.get("rotation_score"),
        "confidence": row.get("confidence"),
        "signal": row.get("signal"),
        "reason": row.get("reason"),
        "metrics": metrics,
        "attack_leaders": row.get("attack_leaders") or [],
        "catchup_candidates": row.get("catchup_candidates") or [],
    }


def build_tomorrow_sector_rotation(
    trade_date: str | None = None,
    limit: int = 10,
    stocks_per_sector: int = 5,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 30))
    stocks_per_sector = max(1, min(int(stocks_per_sector), 10))
    complete_dates = [str(date) for date in get_complete_dates(20)]
    if trade_date:
        trade_date = str(trade_date)
        usable_dates = [date for date in complete_dates if date <= trade_date]
    else:
        usable_dates = complete_dates
    if len(usable_dates) < 2:
        return _empty_payload(
            trade_date or (usable_dates[0] if usable_dates else None),
            usable_dates[:2],
            ["完整交易日不足 2 天，无法生成明日轮动榜"],
        )

    ordered_dates = list(reversed(usable_dates))
    current = trade_date or usable_dates[0]
    lookback_dates = ordered_dates[-2:]
    short_context = ordered_dates[-5:]
    market = _prepare_market(load_market_snapshot(current))
    history = _prepare_history(load_recent_daily(current, n=20), market)
    if market.empty:
        return _empty_payload(current, lookback_dates, ["市场快照为空，无法生成明日轮动榜"])

    moneyflow, moneyflow_dates, warnings = _load_moneyflow_by_date(lookback_dates)
    if moneyflow.empty:
        payload = _empty_payload(
            current,
            lookback_dates,
            warnings + ["暂无足够资金流数据"],
        )
        payload["context_trade_dates"] = {
            "short": short_context,
            "medium_count": len(ordered_dates[-20:]),
        }
        return payload

    market_metrics = _sector_market_metrics(market, history)
    moneyflow_metrics = _sector_moneyflow_metrics(moneyflow, moneyflow_dates)
    sectors = market_metrics.merge(moneyflow_metrics, on="industry", how="inner")
    sectors = sectors[sectors["stock_count"] >= 8].copy()
    complete_moneyflow = len(moneyflow_dates) >= 2
    sectors = _score_sectors(sectors, complete_moneyflow)
    if sectors.empty:
        return _empty_payload(current, lookback_dates, warnings + ["无满足样本数量的板块"])

    attack_lists = []
    catchup_lists = []
    for _, row in sectors.iterrows():
        attack, catchup = _stock_scores(
            market,
            history,
            str(row["industry"]),
            row,
            stocks_per_sector,
        )
        attack_lists.append(attack)
        catchup_lists.append(catchup)
    sectors["attack_leaders"] = attack_lists
    sectors["catchup_candidates"] = catchup_lists

    continuation = (
        sectors.sort_values("continuation_score", ascending=False)
        .head(limit)
        .reset_index(drop=True)
    )
    rotation = (
        sectors.sort_values("rotation_score", ascending=False)
        .head(limit)
        .reset_index(drop=True)
    )

    return _json_safe({
        "trade_date": current,
        "lookback_trade_dates": lookback_dates,
        "context_trade_dates": {
            "short": short_context,
            "medium_count": len(ordered_dates[-20:]),
        },
        "moneyflow_trade_dates": moneyflow_dates,
        "source": "moneyflow_ind_dc",
        "warnings": warnings,
        "continuation_inflow": [
            _sector_output(row, index + 1)
            for index, row in continuation.iterrows()
        ],
        "rotation_rebound": [
            _sector_output(row, index + 1)
            for index, row in rotation.iterrows()
        ],
    })
