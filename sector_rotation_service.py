from __future__ import annotations

import math
from typing import Any

import pandas as pd

from indicator_settings import calculate_macd, macd_provenance
from market_cache import (
    get_complete_dates,
    load_market_snapshot,
    load_moneyflow,
    load_recent_daily,
)

# 周线 MACD 使用独立参数：行情缓存仅约 24 周日线，全局日线参数 (5/34/5)
# 的慢线 EMA 在周线上无法收敛，4/10/5 可保证初始残留 <2%。
WEEKLY_MACD_SETTINGS = {
    "fast_period": 4,
    "slow_period": 10,
    "signal_period": 5,
}
# 周线根数下限：slow(10) + signal(5) 再加 1 根缓冲，不足则按"数据不足"降级。
MIN_WEEKLY_BARS = 16
# 板块轮动取用的日线历史深度（约 24 周用于周线聚合）。
SECTOR_HISTORY_DAYS = 120

CAPITAL_TREND_TEXT = {
    "inflow": "连续3日流入",
    "outflow": "连续3日流出",
    "oscillation": "近3日震荡",
    "unknown": "资金数据不足",
}
MACD_STATUS_TEXT = {
    "golden_cross": "金叉",
    "death_cross": "死叉",
    "critical": "临界",
}
ZERO_AXIS_TEXT = {
    "above": "零轴上方",
    "below": "零轴下方",
    "crossing": "零轴临界",
}
HISTOGRAM_TREND_TEXT = {
    "red_expand": "红柱放大",
    "red_shrink": "红柱缩短",
    "green_expand": "绿柱放大",
    "green_shrink": "绿柱缩短",
    "flat": "柱体持平",
    "unknown": "柱体未知",
}
RATING_TEXT = {
    "strong": "强趋势",
    "bullish": "偏强",
    "watch": "待确认",
    "weak": "偏弱",
    "avoid": "回避",
}


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
        "macd_basis": macd_provenance(WEEKLY_MACD_SETTINGS),
        "warnings": warnings,
        "groups": {
            "priority_focus": [],
            "trend_watch": [],
            "caution_avoid": [],
        },
    })


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _capital_flow_trend(values: list[Any]) -> tuple[str, str]:
    """按近 3 日净流入序列判断资金趋势，数据不足一律 unknown。"""
    clean = [_optional_float(value) for value in values]
    if len(clean) < 3 or any(value is None for value in clean):
        return "unknown", CAPITAL_TREND_TEXT["unknown"]
    if all(value > 0 for value in clean):
        return "inflow", CAPITAL_TREND_TEXT["inflow"]
    if all(value < 0 for value in clean):
        return "outflow", CAPITAL_TREND_TEXT["outflow"]
    return "oscillation", CAPITAL_TREND_TEXT["oscillation"]


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
    today = dates[-1]
    today_df = moneyflow[moneyflow["trade_date"] == today].copy()
    if today_df.empty:
        return pd.DataFrame()

    def day_series(date: str, column: str) -> pd.Series:
        chunk = moneyflow[moneyflow["trade_date"] == date]
        if chunk.empty:
            return pd.Series(dtype=float)
        chunk = chunk.rename(columns={"name": "industry"})
        values = pd.to_numeric(chunk[column], errors="coerce")
        return pd.Series(values.to_numpy(), index=chunk["industry"].to_numpy())

    net_by_date = {date: day_series(date, "net_amount") for date in dates}
    rate_by_date = {date: day_series(date, "net_amount_rate") for date in dates}

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

    # 统一为固定 3 日窗口（时间升序），缺失交易日前补 None，不做任何伪造。
    padded_dates = ([None] * (3 - len(dates))) + list(dates)
    result["net_amount_3d"] = result["industry"].map(
        lambda industry: [
            _optional_float(net_by_date[date].get(industry)) if date else None
            for date in padded_dates
        ]
    )

    prev_date = dates[-2] if len(dates) >= 2 else None
    if prev_date:
        result["net_amount_prev"] = result["industry"].map(
            lambda industry: _optional_float(net_by_date[prev_date].get(industry))
        )
        result["net_amount_rate_prev"] = result["industry"].map(
            lambda industry: _optional_float(rate_by_date[prev_date].get(industry))
        )
    else:
        result["net_amount_prev"] = None
        result["net_amount_rate_prev"] = None
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
    trend = result["net_amount_3d"].map(_capital_flow_trend)
    result["capital_trend"] = trend.map(lambda item: item[0])
    result["capital_trend_text"] = trend.map(lambda item: item[1])
    return result


def _empty_macd_state(weeks_available: int = 0) -> dict[str, Any]:
    return {
        "ready": False,
        "weeks_available": weeks_available,
        "dif": None,
        "dea": None,
        "histogram": None,
        "previous_dif": None,
        "previous_dea": None,
        "previous_histogram": None,
        "status": None,
        "zero_axis": None,
        "histogram_trend": None,
    }


def _sector_weekly_closes(history: pd.DataFrame) -> dict[str, pd.Series]:
    """按板块等权聚合日线收盘，重采样为自然周收盘序列（参考 strategy.py 先例）。"""
    if history.empty:
        return {}
    frame = history[["industry", "trade_date", "close"]].dropna(
        subset=["industry", "trade_date"]
    )
    if frame.empty:
        return {}
    frame = frame.copy()
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["close"])
    if frame.empty:
        return {}
    daily = frame.groupby(["industry", "trade_date"], as_index=False)["close"].mean()
    daily["dt"] = pd.to_datetime(
        daily["trade_date"], format="%Y%m%d", errors="coerce"
    )
    daily = daily.dropna(subset=["dt"])
    if daily.empty:
        return {}
    daily["week"] = daily["dt"].dt.to_period("W")
    daily = daily.sort_values("dt")
    weekly = daily.groupby(["industry", "week"], as_index=False).agg(
        close=("close", "last"),
        day_count=("trade_date", "nunique"),
    )
    # 最后一周若未走满 5 个交易日（当前周未完结），剔除该根周线，
    # 避免半周增量被当成整周变化歪曲 MACD 金叉/柱体判断。
    last_week = weekly["week"].max()
    weekly = weekly[
        ~((weekly["week"] == last_week) & (weekly["day_count"] < 5))
    ]
    return {
        str(industry): chunk.sort_values("week")["close"].reset_index(drop=True)
        for industry, chunk in weekly.groupby("industry")
    }


def _weekly_macd_state(weekly_close: pd.Series | None) -> dict[str, Any]:
    """计算板块周线 MACD 状态；周线根数不足时整体降级为数据不足。"""
    if weekly_close is None or len(weekly_close) == 0:
        return _empty_macd_state()
    series = pd.to_numeric(pd.Series(weekly_close), errors="coerce").dropna()
    series = series.reset_index(drop=True)
    weeks = len(series)
    if weeks < MIN_WEEKLY_BARS:
        return _empty_macd_state(weeks)
    dif, dea, hist = calculate_macd(
        series,
        settings=WEEKLY_MACD_SETTINGS,
        min_periods=False,
    )
    cur_dif = _optional_float(dif.iloc[-1])
    cur_dea = _optional_float(dea.iloc[-1])
    cur_hist = _optional_float(hist.iloc[-1])
    prev_dif = _optional_float(dif.iloc[-2])
    prev_dea = _optional_float(dea.iloc[-2])
    prev_hist = _optional_float(hist.iloc[-2])
    state = _empty_macd_state(weeks)
    if None in (cur_dif, cur_dea, cur_hist, prev_dif, prev_dea, prev_hist):
        return state
    state.update({
        "ready": True,
        "dif": cur_dif,
        "dea": cur_dea,
        "histogram": cur_hist,
        "previous_dif": prev_dif,
        "previous_dea": prev_dea,
        "previous_histogram": prev_hist,
    })
    if cur_dif > cur_dea:
        state["status"] = "golden_cross"
    elif cur_dif < cur_dea:
        state["status"] = "death_cross"
    else:
        state["status"] = "critical"
    if cur_dif > 0 and cur_dea > 0:
        state["zero_axis"] = "above"
    elif cur_dif < 0 and cur_dea < 0:
        state["zero_axis"] = "below"
    else:
        state["zero_axis"] = "crossing"
    if cur_hist > 0:
        if cur_hist > prev_hist:
            state["histogram_trend"] = "red_expand"
        elif cur_hist < prev_hist:
            state["histogram_trend"] = "red_shrink"
        else:
            state["histogram_trend"] = "flat"
    elif cur_hist < 0:
        if cur_hist < prev_hist:
            state["histogram_trend"] = "green_expand"
        elif cur_hist > prev_hist:
            state["histogram_trend"] = "green_shrink"
        else:
            state["histogram_trend"] = "flat"
    else:
        state["histogram_trend"] = "flat"
    return state


def _macd_strength(macd: dict[str, Any] | None) -> float:
    """周线 MACD 强弱打分（-100~100），正为偏强，仅用于同评级内部排序。"""
    if not macd or not macd.get("ready"):
        return 0.0
    score = 0.0
    status = macd.get("status")
    if status == "golden_cross":
        score += 34
    elif status == "death_cross":
        score -= 34
    zero_axis = macd.get("zero_axis")
    if zero_axis == "above":
        score += 33
    elif zero_axis == "below":
        score -= 33
    histogram_trend = macd.get("histogram_trend")
    if histogram_trend == "red_expand":
        score += 33
    elif histogram_trend == "green_expand":
        score -= 33
    elif histogram_trend == "red_shrink":
        score -= 15
    elif histogram_trend == "green_shrink":
        score += 15
    return score


def _trend_description(capital_trend: str, macd: dict[str, Any], rating: str) -> str:
    flow_clause = {
        "inflow": "近3日主力资金持续流入",
        "outflow": "近3日主力资金持续流出",
        "oscillation": "近3日主力资金进出交替",
        "unknown": "近3日资金数据不足",
    }.get(capital_trend, "资金方向未知")
    if macd and macd.get("ready"):
        parts = [MACD_STATUS_TEXT.get(macd.get("status"), "周线MACD状态未知")]
        zero_text = ZERO_AXIS_TEXT.get(macd.get("zero_axis"))
        if zero_text:
            parts.append(zero_text)
        hist_text = HISTOGRAM_TREND_TEXT.get(macd.get("histogram_trend"))
        if hist_text:
            parts.append(hist_text)
        macd_clause = "、" + "、".join(parts)
    else:
        macd_clause = "、周线MACD数据不足"
    conclusion = {
        "strong": "资金与中期趋势形成共振",
        "bullish": "短线资金偏强，中期趋势继续观察",
        "watch": "信号未共振，暂按观察处理",
        "weak": "短线资金与中期趋势均偏弱",
        "avoid": "资金与中期趋势同步走弱，暂不关注",
    }.get(rating, "暂按观察处理")
    return f"{flow_clause}{macd_clause}，{conclusion}。"


def select_sector_trend(capital_trend: Any, macd: dict[str, Any] | None) -> tuple[str, str]:
    """综合资金趋势与周线 MACD 状态输出趋势评级与说明。

    规则：
    - 资金或 MACD 数据不足/缺失 -> 待确认，不做任何猜测补全；
    - 连续流入 + 金叉 + 红柱放大 -> 强趋势（共振优先）；
    - 连续流入 + MACD 未走弱 -> 偏强（含零轴下方、临界等未完全确认场景）；
    - 连续流入 + MACD 明确走弱 -> 待确认（信号冲突不强行给强趋势）；
    - 连续流出 + 死叉 + (绿柱放大或零轴下方) -> 回避；
    - 连续流出 -> 偏弱（MACD 金叉/红柱放大的明确强势信号形成冲突时转待确认）。
    """
    trend = capital_trend if capital_trend in CAPITAL_TREND_TEXT else "unknown"
    macd_ready = bool(macd and macd.get("ready"))
    if trend == "unknown" or not macd_ready:
        rating = "watch"
        return rating, _trend_description(trend, macd or {}, rating)
    status = macd.get("status")
    zero_axis = macd.get("zero_axis")
    histogram_trend = macd.get("histogram_trend")
    macd_bull = (
        status == "golden_cross"
        or histogram_trend == "red_expand"
        or zero_axis == "above"
    )
    # 明确强势信号：仅金叉/红柱放大可抵消资金流出（零轴上方单独不算）。
    macd_strong_bull = status == "golden_cross" or histogram_trend == "red_expand"
    macd_bear = (
        status == "death_cross"
        or histogram_trend in ("red_shrink", "green_expand")
    )
    if trend == "inflow":
        if status == "golden_cross" and histogram_trend == "red_expand":
            rating = "strong"
        elif macd_bear:
            rating = "watch"
        else:
            rating = "bullish"
    elif trend == "outflow":
        if status == "death_cross" and (
            histogram_trend == "green_expand" or zero_axis == "below"
        ):
            rating = "avoid"
        elif macd_strong_bull:
            rating = "watch"
        else:
            rating = "weak"
    else:
        if macd_bear:
            rating = "weak"
        else:
            rating = "watch"
    return rating, _trend_description(trend, macd, rating)


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


def _capital_output(row: pd.Series) -> dict[str, Any]:
    values = row.get("net_amount_3d")
    series = [float(value) for value in values if value is not None] if isinstance(values, list) else []
    return {
        "trend": row.get("capital_trend"),
        "trend_text": row.get("capital_trend_text") or CAPITAL_TREND_TEXT["unknown"],
        "net_amount_today": row.get("net_amount_today"),
        "net_amount_3d": values if isinstance(values, list) else [],
        "net_amount_sum_3d": sum(series) if len(series) == 3 else None,
    }


def _macd_output(row: pd.Series) -> dict[str, Any]:
    state = row.get("macd_state") if isinstance(row.get("macd_state"), dict) else {}
    status = state.get("status")
    zero_axis = state.get("zero_axis")
    histogram_trend = state.get("histogram_trend")
    return {
        "ready": bool(state.get("ready")),
        "weeks_available": state.get("weeks_available"),
        "dif": state.get("dif"),
        "dea": state.get("dea"),
        "histogram": state.get("histogram"),
        "previous_dif": state.get("previous_dif"),
        "previous_dea": state.get("previous_dea"),
        "previous_histogram": state.get("previous_histogram"),
        "status": status,
        "status_text": MACD_STATUS_TEXT.get(status),
        "zero_axis": zero_axis,
        "zero_axis_text": ZERO_AXIS_TEXT.get(zero_axis),
        "histogram_trend": histogram_trend,
        "histogram_trend_text": HISTOGRAM_TREND_TEXT.get(histogram_trend),
    }


def _sector_output(row: pd.Series, rank: int) -> dict[str, Any]:
    metrics = {
        "net_amount_today": row.get("net_amount_today"),
        "net_amount_prev": row.get("net_amount_prev"),
        "net_amount_change": row.get("net_amount_change"),
        "net_amount_rate_today": row.get("net_amount_rate_today"),
        "avg_pct_chg": row.get("avg_pct_chg"),
        "up_ratio": row.get("up_ratio"),
        "amount_expand_rate": row.get("amount_expand_rate"),
        "amount_today": (
            float(row["amount_sum"]) * 1000.0
            if pd.notna(row.get("amount_sum"))
            else None
        ),
        "ret_5": row.get("ret_5"),
        "ret_20": row.get("ret_20"),
        "position_20": row.get("position_20"),
    }
    rating = row.get("trend_rating") or "watch"
    return {
        "rank": rank,
        "industry_name": row.get("industry"),
        "trend_rating": rating,
        "trend_rating_text": RATING_TEXT.get(rating, RATING_TEXT["watch"]),
        "trend_desc": row.get("trend_desc"),
        "capital_flow": _capital_output(row),
        "macd": _macd_output(row),
        "continuation_score": row.get("continuation_score"),
        "rotation_score": row.get("rotation_score"),
        "confidence": row.get("confidence"),
        "signal": row.get("signal"),
        "reason": row.get("reason"),
        "metrics": metrics,
        "attack_leaders": row.get("attack_leaders") or [],
        "catchup_candidates": row.get("catchup_candidates") or [],
    }


RATING_GROUPS = {
    "strong": "priority_focus",
    "bullish": "priority_focus",
    "watch": "trend_watch",
    "weak": "caution_avoid",
    "avoid": "caution_avoid",
}


def _trend_group_output(
    frame: pd.DataFrame,
    sort_columns: list[str],
    ascending: list[bool],
    limit: int,
) -> list[dict[str, Any]]:
    ordered = frame.sort_values(by=sort_columns, ascending=ascending).head(limit)
    return [
        _sector_output(row, index + 1)
        for index, (_, row) in enumerate(ordered.iterrows())
    ]


def build_tomorrow_sector_rotation(
    trade_date: str | None = None,
    limit: int = 10,
    stocks_per_sector: int = 5,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 30))
    stocks_per_sector = max(1, min(int(stocks_per_sector), 10))
    complete_dates = [str(date) for date in get_complete_dates(SECTOR_HISTORY_DAYS)]
    if trade_date:
        trade_date = str(trade_date)
        usable_dates = [date for date in complete_dates if date <= trade_date]
    else:
        usable_dates = complete_dates
    if len(usable_dates) < 2:
        return _empty_payload(
            trade_date or (usable_dates[0] if usable_dates else None),
            usable_dates[:3],
            ["完整交易日不足 2 天，无法生成明日轮动榜"],
        )

    ordered_dates = list(reversed(usable_dates))
    current = trade_date or usable_dates[0]
    lookback_dates = ordered_dates[-3:]
    short_context = ordered_dates[-5:]
    market = _prepare_market(load_market_snapshot(current))
    history = _prepare_history(
        load_recent_daily(current, n=SECTOR_HISTORY_DAYS), market
    )
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
            "medium_count": len(ordered_dates[-SECTOR_HISTORY_DAYS:]),
        }
        return payload

    market_metrics = _sector_market_metrics(market, history)
    moneyflow_metrics = _sector_moneyflow_metrics(moneyflow, moneyflow_dates)
    sectors = market_metrics.merge(moneyflow_metrics, on="industry", how="inner")
    sectors = sectors[sectors["stock_count"] >= 8].copy()
    complete_moneyflow = len(moneyflow_dates) >= 3
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

    weekly_closes = _sector_weekly_closes(history)
    sectors["macd_state"] = sectors["industry"].map(
        lambda name: _weekly_macd_state(weekly_closes.get(str(name)))
    )
    ratings = sectors.apply(
        lambda row: select_sector_trend(row.get("capital_trend"), row.get("macd_state")),
        axis=1,
    )
    sectors["trend_rating"] = ratings.map(lambda item: item[0])
    sectors["trend_desc"] = ratings.map(lambda item: item[1])
    capital_sum = sectors["net_amount_3d"].map(
        lambda values: (
            sum(float(value) for value in values if value is not None)
            if isinstance(values, list) else 0.0
        )
    )
    sectors["capital_strength"] = capital_sum
    sectors["macd_strength"] = sectors["macd_state"].map(_macd_strength)
    # 待确认区内部排序：资金偏强但 MACD 未确认 优先于 MACD 偏强但资金未确认。
    sectors["watch_order"] = sectors["capital_trend"].map(
        lambda trend: 0 if trend == "inflow" else 1
    )

    payload = _empty_payload(current, lookback_dates, warnings)
    payload["context_trade_dates"] = {
        "short": short_context,
        "medium_count": len(ordered_dates[-SECTOR_HISTORY_DAYS:]),
    }
    payload["moneyflow_trade_dates"] = moneyflow_dates
    payload["groups"] = {
        "priority_focus": _trend_group_output(
            sectors,
            ["trend_rating", "capital_strength", "macd_strength", "avg_pct_chg"],
            [True, False, False, False],
            limit,
        ),
        "trend_watch": _trend_group_output(
            sectors,
            ["watch_order", "capital_strength", "macd_strength", "avg_pct_chg"],
            [True, False, False, False],
            limit,
        ),
        "caution_avoid": _trend_group_output(
            sectors,
            ["trend_rating", "capital_strength", "macd_strength", "avg_pct_chg"],
            [True, True, False, True],
            limit,
        ),
    }
    return _json_safe(payload)
