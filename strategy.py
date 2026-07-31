import pandas as pd

from indicator_settings import (
    calculate_macd,
    load_macd_settings,
    macd_provenance,
)


def clean_data(df: pd.DataFrame):
    df = df.copy()
    numeric_cols = [
        "pct_chg", "turnover_rate", "volume_ratio", "vol", "amount",
        "total_mv", "close", "high", "low"
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    required_cols = ["pct_chg", "turnover_rate", "vol", "total_mv", "close"]
    df = df.dropna(subset=required_cols)
    return df


def _is_mainboard_a_stock(series: pd.Series) -> pd.Series:
    code = series.astype(str)
    return code.str.endswith((".SH", ".SZ")) & ~code.str.startswith(("3", "688", "689"))


def _build_sector_strength(df: pd.DataFrame) -> pd.DataFrame:
    data = clean_data(df)
    if "industry" not in data.columns:
        return pd.DataFrame()

    data = data[_is_mainboard_a_stock(data["ts_code"])].copy()
    data = _ensure_amount_yuan(data)
    data = data.dropna(subset=["industry", "pct_chg"])
    if data.empty:
        return pd.DataFrame()

    data["limit_threshold"] = data["ts_code"].map(_limit_threshold_for_code)
    data["limit_up"] = data["pct_chg"] >= data["limit_threshold"]
    grouped = (
        data.groupby("industry")
        .agg(
            avg_pct_chg=("pct_chg", "mean"),
            stock_count=("ts_code", "count"),
            up_count=("pct_chg", lambda value: int((value > 0).sum())),
            strong_count=("pct_chg", lambda value: int((value >= 3).sum())),
            limit_up_count=("limit_up", "sum"),
            max_pct_chg=("pct_chg", "max"),
            sector_amount_yuan=("amount_yuan", "sum"),
            avg_turnover=("turnover_rate", "mean"),
            avg_volume_ratio=("volume_ratio", "mean"),
        )
        .reset_index()
        .rename(columns={"industry": "industry_name"})
    )
    grouped["up_ratio"] = grouped["up_count"] / grouped["stock_count"]
    grouped["sector_score"] = (
        grouped["avg_pct_chg"] * 18 +
        grouped["up_ratio"] * 25 +
        grouped["strong_count"] * 3 +
        grouped["avg_volume_ratio"].fillna(0).clip(upper=3) * 5 +
        grouped["avg_turnover"].fillna(0).clip(upper=10) * 0.8
    )

    return grouped.sort_values("sector_score", ascending=False)


def _numeric_series(frame: pd.DataFrame, column: str, default=0.0) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce").fillna(default)


def _normalize_score(series: pd.Series, lower=None, upper=None) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").fillna(0.0)
    if lower is not None or upper is not None:
        values = values.clip(lower=lower, upper=upper)
    min_value = values.min()
    max_value = values.max()
    if pd.isna(min_value) or pd.isna(max_value) or max_value == min_value:
        return pd.Series(50.0, index=values.index)
    return ((values - min_value) / (max_value - min_value) * 100).clip(0, 100)


def _sector_return_by_window(history: pd.DataFrame, window: int) -> pd.DataFrame:
    rows = []
    for industry, group in history.groupby("industry"):
        pivot = (
            group.dropna(subset=["trade_date", "close"])
            .sort_values("trade_date")
            .groupby(["trade_date", "ts_code"])["close"]
            .last()
            .unstack()
        )
        if pivot.empty:
            continue
        latest = pivot.iloc[-1]
        base = pivot.iloc[-window - 1] if len(pivot) > window else pivot.iloc[0]
        stock_ret = (latest / base - 1) * 100
        stock_ret = stock_ret.replace([float("inf"), float("-inf")], pd.NA).dropna()
        rows.append({
            "industry": industry,
            f"ret_{window}": float(stock_ret.mean()) if not stock_ret.empty else None,
        })
    return pd.DataFrame(rows)


def _sector_position_by_window(history: pd.DataFrame, window: int) -> pd.DataFrame:
    rows = []
    for industry, group in history.groupby("industry"):
        tail = group.sort_values("trade_date").groupby("ts_code").tail(window)
        latest = tail.sort_values("trade_date").groupby("ts_code")["close"].last()
        low = tail.groupby("ts_code")["low"].min()
        high = tail.groupby("ts_code")["high"].max()
        position = ((latest - low) / (high - low).replace(0, pd.NA)).dropna()
        rows.append({
            "industry": industry,
            f"position_{window}": float(position.mean()) if not position.empty else None,
        })
    return pd.DataFrame(rows)


def _leader_pool_tags(breakout_pool=None, first_limit_pool=None) -> dict:
    tags = {}
    if isinstance(breakout_pool, pd.DataFrame) and "ts_code" in breakout_pool.columns:
        tags.update({str(code): "趋势突破" for code in breakout_pool["ts_code"].dropna().astype(str)})
    if isinstance(first_limit_pool, pd.DataFrame) and "ts_code" in first_limit_pool.columns:
        tags.update({str(code): "主升浪启动" for code in first_limit_pool["ts_code"].dropna().astype(str)})
    return tags


LARGE_CAP_LEADER_THRESHOLD_YUAN = 20_000_000_000


def _build_sector_leader_history_signals(history: pd.DataFrame) -> pd.DataFrame:
    if history is None or history.empty or "ts_code" not in history.columns:
        return pd.DataFrame()

    hist = history.copy()
    for column in ["pct_chg", "close", "high", "low", "vol"]:
        hist[column] = _numeric_series(hist, column)
    hist["trade_date"] = hist["trade_date"].astype(str)
    hist = hist.dropna(subset=["ts_code", "trade_date", "close"]).sort_values(["ts_code", "trade_date"])

    rows = []
    for ts_code, group in hist.groupby("ts_code"):
        group = group.tail(80).copy()
        if len(group) < 25:
            continue

        close = group["close"]
        high = group["high"].fillna(close)
        low = group["low"].fillna(close)
        vol = group["vol"].fillna(0)
        ma5 = close.rolling(5).mean()
        ma10 = close.rolling(10).mean()
        ma20 = close.rolling(20).mean()
        bullish_ma = bool(
            pd.notna(ma20.iloc[-1]) and
            ma5.iloc[-1] >= ma10.iloc[-1] * 0.998 and
            ma10.iloc[-1] > ma20.iloc[-1] and
            ma5.iloc[-1] >= ma5.iloc[-4] * 0.995 and
            ma10.iloc[-1] >= ma10.iloc[-4] * 0.995 and
            ma20.iloc[-1] >= ma20.iloc[-4] * 0.99
        )

        recent60 = group.tail(60).copy()
        threshold = _limit_threshold_for_code(ts_code)
        limit_rows = recent60[recent60["pct_chg"] >= threshold]
        low_limit_up = False
        limit_close = None
        limit_index = None
        if not limit_rows.empty:
            for idx in limit_rows.index:
                prior = recent60.loc[:idx].tail(40)
                prior_low = prior["low"].min()
                low_area_gain = (
                    (recent60.loc[idx, "close"] / prior_low - 1)
                    if prior_low and pd.notna(prior_low)
                    else 1.0
                )
                if low_area_gain <= 0.25:
                    low_limit_up = True
                    limit_close = recent60.loc[idx, "close"]
                    limit_index = idx
            if limit_close is None:
                limit_close = limit_rows.iloc[-1]["close"]
                limit_index = limit_rows.index[-1]

        recent3 = group.tail(3)
        avg_vol20 = float(group.tail(20)["vol"].mean()) if not group.tail(20).empty else 0.0
        latest_ma20 = ma20.iloc[-1]
        support = max(float(latest_ma20) if pd.notna(latest_ma20) else 0.0, float(limit_close or 0) * 0.92)
        pullback_confirmed = False
        if low_limit_up and limit_index is not None and pd.notna(latest_ma20):
            after_limit = recent3[recent3.index > limit_index]
            for _, row in after_limit.iterrows():
                near_support = row["low"] <= max(float(latest_ma20) * 1.05, float(limit_close) * 1.08)
                holds_support = row["close"] >= support * 0.98
                volume_ok = avg_vol20 <= 0 or row["vol"] <= avg_vol20 * 1.35
                not_overextended = row["close"] <= float(limit_close) * 1.16
                if near_support and holds_support and volume_ok and not_overextended:
                    pullback_confirmed = True
                    break

        pre_limit = group.loc[group.index < limit_index].tail(25) if limit_index is not None else group.tail(25)
        pre_range = (
            (pre_limit["high"].max() - pre_limit["low"].min()) / pre_limit["close"].mean()
            if not pre_limit.empty and pre_limit["close"].mean()
            else 1.0
        )
        recent20 = group.tail(20)
        box_range = (
            (recent20["high"].max() - recent20["low"].min()) / recent20["close"].mean()
            if not recent20.empty and recent20["close"].mean()
            else 1.0
        )
        latest_close = close.iloc[-1]
        inside_box = bool(
            not recent20.empty and
            latest_close >= recent20["low"].min() * 0.98 and
            latest_close <= recent20["high"].max() * 1.02
        )
        consolidation_box = bool(pre_range <= 0.18 or (box_range <= 0.24 and inside_box))

        rows.append({
            "ts_code": ts_code,
            "leader_ma_bullish": bullish_ma,
            "leader_low_limit_up": low_limit_up,
            "leader_pullback_confirmed_3d": pullback_confirmed,
            "leader_consolidation_box": consolidation_box,
        })

    return pd.DataFrame(rows)


def _select_sector_leaders(
    sector_market: pd.DataFrame,
    sector_history: pd.DataFrame,
    leader_tags: dict,
    leaders_per_sector: int,
) -> list[dict]:
    if sector_market.empty:
        return []

    candidates = sector_market.copy()
    candidates = _ensure_total_mv_yuan(candidates)
    signals = _build_sector_leader_history_signals(sector_history)
    if signals.empty:
        return []
    candidates = candidates.merge(signals, on="ts_code", how="inner")
    if candidates.empty:
        return []

    candidates["pct_chg_num"] = _numeric_series(candidates, "pct_chg")
    candidates["amount_num"] = _numeric_series(candidates, "amount")
    candidates["turnover_num"] = _numeric_series(candidates, "turnover_rate")
    candidates["volume_ratio_num"] = _numeric_series(candidates, "volume_ratio")
    candidates["large_cap_leader"] = candidates["total_mv_yuan"] >= LARGE_CAP_LEADER_THRESHOLD_YUAN
    candidates["relaxed_cap_leader"] = candidates["total_mv_yuan"] >= 5_000_000_000
    shape_columns = [
        "leader_ma_bullish",
        "leader_low_limit_up",
        "leader_pullback_confirmed_3d",
        "leader_consolidation_box",
    ]
    candidates["leader_shape_count"] = candidates[shape_columns].fillna(False).astype(int).sum(axis=1)
    candidates["liquidity_leader"] = (
        (candidates["amount_num"] >= 80_000)
        | (candidates["volume_ratio_num"] >= 1.2)
        | (candidates["turnover_num"].between(1.5, 15, inclusive="both"))
    )
    candidates["strong_liquidity_leader"] = (
        (
            ((candidates["amount_num"] >= 100_000) & (candidates["volume_ratio_num"] >= 1.2))
            | (candidates["amount_num"] >= 250_000)
        )
        & (candidates["pct_chg_num"] >= 0)
    )
    candidates["strict_shape_leader"] = (
        (candidates["leader_shape_count"] >= 3)
        & (
            ~candidates["leader_low_limit_up"].fillna(False)
            | candidates["leader_pullback_confirmed_3d"].fillna(False)
            | candidates["strong_liquidity_leader"].fillna(False)
        )
    )
    candidates["relaxed_shape_leader"] = (
        candidates["leader_ma_bullish"].fillna(False)
        & candidates["leader_consolidation_box"].fillna(False)
        & candidates["strong_liquidity_leader"].fillna(False)
    )
    candidates = candidates[
        candidates["relaxed_cap_leader"].fillna(False) &
        candidates["liquidity_leader"].fillna(False) &
        (candidates["strict_shape_leader"] | candidates["relaxed_shape_leader"])
    ].copy()
    if candidates.empty:
        return []

    candidates["pool_bonus"] = candidates["ts_code"].astype(str).map(lambda code: 70 if code in leader_tags else 0)
    candidates["leader_score"] = (
        _normalize_score(candidates["pct_chg_num"], -5, 10) * 0.38
        + _normalize_score(candidates["amount_num"]) * 0.24
        + _normalize_score(candidates["turnover_num"], 0, 20) * 0.18
        + _normalize_score(candidates["volume_ratio_num"], 0, 3) * 0.12
        + candidates["leader_shape_count"] * 12
        + candidates["large_cap_leader"].astype(int) * 20
        + candidates["strict_shape_leader"].astype(int) * 25
        + candidates["pool_bonus"]
    ).round(2)

    leaders = []
    for row in candidates.sort_values("leader_score", ascending=False).head(leaders_per_sector).to_dict("records"):
        pool_tag = leader_tags.get(str(row.get("ts_code")))
        reason_parts = [f"涨幅{row.get('pct_chg_num', 0):.2f}%"]
        if pool_tag:
            reason_parts.append(pool_tag)
        if row.get("volume_ratio_num", 0) >= 1.5:
            reason_parts.append("量比活跃")
        reason_parts.append("总市值200亿以上" if row.get("large_cap_leader") else "总市值50亿以上")
        if row.get("leader_ma_bullish"):
            reason_parts.append("多头向上")
        if row.get("leader_low_limit_up"):
            reason_parts.append("低位涨停")
        if row.get("leader_pullback_confirmed_3d"):
            reason_parts.append("3日内回踩确认")
        if row.get("leader_consolidation_box"):
            reason_parts.append("箱体震荡")
        if int(row.get("leader_shape_count") or 0) < 4:
            reason_parts.append(f"放宽龙头形态{int(row.get('leader_shape_count') or 0)}项")
        leaders.append({
            "ts_code": row.get("ts_code"),
            "name": row.get("name"),
            "pct_chg": row.get("pct_chg"),
            "close": row.get("close"),
            "turnover_rate": row.get("turnover_rate"),
            "volume_ratio": row.get("volume_ratio"),
            "amount": row.get("amount"),
            "total_mv_yuan": row.get("total_mv_yuan"),
            "leader_score": row.get("leader_score"),
            "leader_shape_count": row.get("leader_shape_count"),
            "leader_reason": "、".join(reason_parts),
            "pool_tag": pool_tag or "",
        })
    return leaders


def _split_intraday_bars(minute_bars: pd.DataFrame | dict | None) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    if isinstance(minute_bars, dict):
        return minute_bars.get("60m"), minute_bars.get("tail_1m")
    return minute_bars, None


def _tail_next_day_bias(tail_bars: pd.DataFrame | None, pct_chg: float | None = None) -> dict:
    empty = {
        "tail_strength_score": None,
        "next_day_bias": "数据不足",
        "next_day_bias_reason": "缺少14:30后的1分钟分时",
        "tail_return_after_1430": None,
        "tail_auction_return": None,
        "tail_volume_ratio": None,
        "tail_close_position": None,
    }
    if tail_bars is None or tail_bars.empty or "trade_time" not in tail_bars.columns or "close" not in tail_bars.columns:
        return empty

    bars = tail_bars.copy().sort_values("trade_time").reset_index(drop=True)
    for column in ["open", "high", "low", "close", "vol", "amount"]:
        bars[column] = _numeric_series(bars, column)
    if len(bars) < 6:
        return empty

    trade_clock = bars["trade_time"].astype(str).str[-8:]
    before_1430 = bars[trade_clock <= "14:30:00"]
    after_1430 = bars[trade_clock > "14:30:00"]
    if before_1430.empty or after_1430.empty:
        return empty

    pivot_close = float(before_1430["close"].iloc[-1])
    last_close = float(bars["close"].iloc[-1])
    if not pivot_close:
        return empty

    tail_return = (last_close / pivot_close - 1) * 100
    pre_volume_avg = float(before_1430["vol"].replace(0, pd.NA).dropna().mean() or 0)
    tail_volume_avg = float(after_1430["vol"].replace(0, pd.NA).dropna().mean() or 0)
    tail_volume_ratio = tail_volume_avg / pre_volume_avg if pre_volume_avg else None
    tail_high = float(after_1430["high"].max())
    tail_low = float(after_1430["low"].min())
    close_position = (
        (last_close - tail_low) / (tail_high - tail_low)
        if tail_high > tail_low
        else 1.0
    )

    before_auction = bars[trade_clock <= "14:57:00"]
    auction_base = float(before_auction["close"].iloc[-1]) if not before_auction.empty else pivot_close
    auction_return = (last_close / auction_base - 1) * 100 if auction_base else 0.0

    score = 50
    score += max(-30, min(30, tail_return * 18))
    score += max(-15, min(15, auction_return * 20))
    score += (close_position - 0.5) * 20
    if tail_volume_ratio is not None and tail_volume_ratio >= 1.5:
        score += 10 if tail_return > 0 else -10 if tail_return < 0 else 0
    score = max(0, min(100, score))

    pct_chg = float(pct_chg or 0)
    if tail_return <= -0.5 or (tail_volume_ratio is not None and tail_volume_ratio >= 1.8 and close_position <= 0.35):
        bias = "低开风险"
    elif tail_return >= 0.5 and close_position >= 0.9 and auction_return >= 0.2:
        bias = "冲高分歧" if pct_chg >= 12 else "高开偏强"
    elif pct_chg >= 9.5 and close_position >= 0.9 and tail_return >= -0.1:
        bias = "高开偏强"
    elif tail_return >= 0.5 and close_position >= 0.75 and (tail_volume_ratio or 0) >= 1.5:
        bias = "冲高分歧" if pct_chg >= 12 else "高开偏强"
    elif tail_return > 0.2 and close_position >= 0.55:
        bias = "冲高分歧"
    else:
        bias = "平开观察"

    reason_parts = []
    if tail_return > 0:
        reason_parts.append(f"14:30后上涨{tail_return:.2f}%")
    elif tail_return < 0:
        reason_parts.append(f"尾盘回落{abs(tail_return):.2f}%")
    else:
        reason_parts.append("14:30后横盘")
    if auction_return > 0.05:
        reason_parts.append(f"集合竞价抬价{auction_return:.2f}%")
    elif auction_return < -0.05:
        reason_parts.append(f"集合竞价压价{abs(auction_return):.2f}%")
    if tail_volume_ratio is not None:
        reason_parts.append(f"尾盘量能{tail_volume_ratio:.2f}倍")
    reason_parts.append(f"收盘位置{close_position * 100:.0f}%")

    return {
        "tail_strength_score": round(score, 2),
        "next_day_bias": bias,
        "next_day_bias_reason": "、".join(reason_parts),
        "tail_return_after_1430": round(tail_return, 6),
        "tail_auction_return": round(auction_return, 6),
        "tail_volume_ratio": round(float(tail_volume_ratio), 6) if tail_volume_ratio is not None else None,
        "tail_close_position": round(close_position, 6),
    }


def _macd_kdj_60m_signal(row: pd.Series, minute_bars: pd.DataFrame | dict | None) -> dict | None:
    bars_60m, tail_1m = _split_intraday_bars(minute_bars)
    if bars_60m is None or bars_60m.empty:
        return None

    bars = bars_60m.copy()
    if "trade_time" not in bars.columns or "close" not in bars.columns:
        return None
    bars = bars.sort_values("trade_time").reset_index(drop=True)
    for column in ["open", "high", "low", "close", "vol", "amount"]:
        bars[column] = _numeric_series(bars, column)
    macd_settings = load_macd_settings()
    minimum_bars = (
        int(macd_settings["slow_period"])
        + int(macd_settings["signal_period"])
        - 1
    )
    if len(bars) < minimum_bars:
        return None

    close = bars["close"]
    high = bars["high"]
    low = bars["low"]
    dif, dea, histogram = calculate_macd(close, macd_settings)

    low9 = low.rolling(9, min_periods=9).min()
    high9 = high.rolling(9, min_periods=9).max()
    rsv = ((close - low9) / (high9 - low9) * 100).where(high9 != low9, 50)
    k = rsv.ewm(com=2, adjust=False, min_periods=1).mean()
    d = k.ewm(com=2, adjust=False, min_periods=1).mean()
    j = 3 * k - 2 * d

    if any(pd.isna(value) for value in (dif.iloc[-1], dea.iloc[-1], dif.iloc[-2], dea.iloc[-2], k.iloc[-1], d.iloc[-1], k.iloc[-2], d.iloc[-2])):
        return None

    macd_golden_series = (dif > dea) & (dif.shift(1) <= dea.shift(1))
    macd_golden_cross = bool(macd_golden_series.iloc[-1])
    macd_recent_golden_cross = bool(macd_golden_series.tail(6).fillna(False).any())
    macd_bullish = bool(dif.iloc[-1] > dea.iloc[-1])
    macd_histogram_up = bool(len(histogram) >= 3 and histogram.iloc[-1] > histogram.iloc[-2] > histogram.iloc[-3])
    macd_above_zero = bool(dif.iloc[-1] > 0 and dea.iloc[-1] > 0)
    macd_signal_ok = bool(
        macd_golden_cross
        or (macd_bullish and macd_recent_golden_cross)
        or (macd_bullish and macd_above_zero and macd_histogram_up)
    )
    if not macd_signal_ok:
        return None

    kdj_golden_series = (k > d) & (k.shift(1) <= d.shift(1))
    kdj_golden_cross = bool(kdj_golden_series.iloc[-1])
    kdj_recent_golden_cross = bool(kdj_golden_series.tail(6).fillna(False).any())
    kdj_bullish = bool(k.iloc[-1] > d.iloc[-1])
    volume_ratio = row.get("volume_ratio_num", row.get("volume_ratio", 0))
    pct_chg = row.get("pct_chg_num", row.get("pct_chg", 0))
    amount = row.get("amount_num", row.get("amount", 0))
    tail_bias = _tail_next_day_bias(tail_1m, pct_chg=pct_chg)
    score = (
        (70 if macd_above_zero and macd_golden_cross else 0)
        + (55 if macd_above_zero and macd_recent_golden_cross and not macd_golden_cross else 0)
        + (45 if macd_above_zero and not macd_recent_golden_cross else 0)
        + (35 if not macd_above_zero and macd_golden_cross else 0)
        + (25 if not macd_above_zero and macd_recent_golden_cross and not macd_golden_cross else 0)
        + (15 if macd_histogram_up else 0)
        + (20 if kdj_golden_cross else 12 if kdj_recent_golden_cross else 8 if kdj_bullish else 0)
        + min(float(volume_ratio or 0), 5) * 4
        + min(max(float(pct_chg or 0), 0), 10) * 1.5
        + min(float(amount or 0) / 100_000_000, 10)
        + (float(tail_bias["tail_strength_score"] or 50) - 50) * 0.25
    )
    if macd_golden_cross:
        reason_parts = ["60分MACD水上金叉" if macd_above_zero else "60分MACD金叉"]
    elif macd_recent_golden_cross:
        reason_parts = ["60分MACD水上金叉延续" if macd_above_zero else "60分MACD金叉延续"]
    else:
        reason_parts = ["60分MACD水上多头走强" if macd_above_zero else "60分MACD多头走强"]
    if kdj_golden_cross:
        reason_parts.append("KDJ金叉")
    elif kdj_recent_golden_cross:
        reason_parts.append("KDJ金叉延续")
    elif kdj_bullish:
        reason_parts.append("KDJ多头")
    reason_parts.append(f"量比{float(volume_ratio or 0):.2f}")
    reason_parts.append(f"换手{float(row.get('turnover_num', row.get('turnover_rate', 0)) or 0):.2f}%")

    return {
        "ts_code": row.get("ts_code"),
        "name": row.get("name"),
        "pct_chg": row.get("pct_chg"),
        "close": row.get("close"),
        "turnover_rate": row.get("turnover_rate"),
        "volume_ratio": row.get("volume_ratio"),
        "amount": row.get("amount"),
        "trade_time_60m": bars["trade_time"].iloc[-1],
        "macd_dif_60m": round(float(dif.iloc[-1]), 6),
        "macd_dea_60m": round(float(dea.iloc[-1]), 6),
        "macd_histogram_60m": round(float(histogram.iloc[-1]), 6),
        "macd_golden_cross_60m": macd_golden_cross,
        "macd_recent_golden_cross_60m": macd_recent_golden_cross,
        "macd_bullish_60m": macd_bullish,
        "macd_histogram_up_60m": macd_histogram_up,
        "macd_above_zero_60m": macd_above_zero,
        "kdj_k_60m": round(float(k.iloc[-1]), 6),
        "kdj_d_60m": round(float(d.iloc[-1]), 6),
        "kdj_j_60m": round(float(j.iloc[-1]), 6),
        "kdj_golden_cross_60m": kdj_golden_cross,
        "kdj_recent_golden_cross_60m": kdj_recent_golden_cross,
        "kdj_bullish_60m": kdj_bullish,
        "intraday_signal_score": round(score, 2),
        "intraday_signal_reason": "、".join(reason_parts),
        **macd_provenance(macd_settings),
        **tail_bias,
    }


def _select_intraday_signal_stocks(
    sector_market: pd.DataFrame,
    minute_bars_by_code: dict[str, pd.DataFrame],
    per_sector: int = 5,
) -> list[dict]:
    if sector_market.empty or not minute_bars_by_code:
        return []

    candidates = sector_market.copy()
    candidates["turnover_num"] = _numeric_series(candidates, "turnover_rate")
    candidates["volume_ratio_num"] = _numeric_series(candidates, "volume_ratio")
    candidates["pct_chg_num"] = _numeric_series(candidates, "pct_chg")
    candidates["amount_num"] = _numeric_series(candidates, "amount")
    candidates = candidates[
        candidates["turnover_num"].between(2, 10, inclusive="both") &
        (candidates["volume_ratio_num"] > 2)
    ].copy()
    if candidates.empty:
        return []

    picks = []
    for row in candidates.to_dict("records"):
        ts_code = str(row.get("ts_code") or "")
        signal = _macd_kdj_60m_signal(pd.Series(row), minute_bars_by_code.get(ts_code))
        if signal:
            picks.append(signal)
    return sorted(
        picks,
        key=lambda item: (
            item.get("macd_above_zero_60m", False),
            item.get("kdj_golden_cross_60m", False),
            item.get("intraday_signal_score", 0),
            item.get("volume_ratio", 0) or 0,
        ),
        reverse=True,
    )[:per_sector]


def _attach_intraday_signal_stocks(
    sector_potential: pd.DataFrame,
    market_df: pd.DataFrame,
    minute_bars_by_code: dict[str, pd.DataFrame],
    per_sector: int = 5,
) -> pd.DataFrame:
    if sector_potential is None or sector_potential.empty:
        return sector_potential

    result = sector_potential.copy()
    if market_df is None or market_df.empty or not minute_bars_by_code:
        result["intraday_signal_stocks"] = [[] for _ in range(len(result))]
        return result

    market = market_df.copy()
    if "industry" not in market.columns:
        result["intraday_signal_stocks"] = [[] for _ in range(len(result))]
        return result

    result["intraday_signal_stocks"] = result["industry_name"].map(
        lambda industry: _select_intraday_signal_stocks(
            market[market["industry"] == industry],
            minute_bars_by_code,
            per_sector=per_sector,
        )
    )
    return result


def rank_sector_potential(
    market_df: pd.DataFrame,
    history_df: pd.DataFrame,
    breakout_pool: pd.DataFrame | None = None,
    first_limit_pool: pd.DataFrame | None = None,
    limit: int = 20,
    leaders_per_sector: int = 5,
) -> pd.DataFrame:
    if market_df is None or market_df.empty or history_df is None or history_df.empty:
        return pd.DataFrame()
    if "industry" not in market_df.columns or "ts_code" not in market_df.columns:
        return pd.DataFrame()

    market = market_df.copy()
    market["industry"] = market["industry"].fillna("")
    market = market[market["industry"] != ""].copy()
    if market.empty:
        return pd.DataFrame()

    for column in ["pct_chg", "amount", "turnover_rate", "volume_ratio", "close", "high", "low"]:
        market[column] = _numeric_series(market, column)

    history = history_df.copy()
    if "ts_code" not in history.columns or "trade_date" not in history.columns:
        return pd.DataFrame()
    history["ts_code"] = history["ts_code"].astype(str)
    info = market[["ts_code", "industry", "name"]].drop_duplicates("ts_code")
    history = history.merge(info, on="ts_code", how="inner")
    for column in ["pct_chg", "amount", "close", "high", "low"]:
        history[column] = _numeric_series(history, column)
    history["trade_date"] = history["trade_date"].astype(str)
    dates = sorted(history["trade_date"].dropna().unique().tolist())
    if len(dates) < 2:
        return pd.DataFrame()

    prev_date = dates[-2]
    previous = history[history["trade_date"] == prev_date]
    latest_group = market.groupby("industry").agg(
        stock_count=("ts_code", "count"),
        avg_pct_chg=("pct_chg", "mean"),
        up_ratio=("pct_chg", lambda s: float((s > 0).mean())),
        strong_ratio=("pct_chg", lambda s: float((s >= 5).mean())),
        limit_up_count=("pct_chg", lambda s: int((s >= 9.8).sum())),
        amount_sum=("amount", "sum"),
        turnover_rate=("turnover_rate", "mean"),
        volume_ratio=("volume_ratio", "mean"),
    ).reset_index()
    prev_group = previous.groupby("industry").agg(
        prev_avg_pct_chg=("pct_chg", "mean"),
        prev_amount_sum=("amount", "sum"),
    ).reset_index()
    result = latest_group.merge(prev_group, on="industry", how="left")
    result = result[result["stock_count"] >= 8].copy()
    if result.empty:
        return pd.DataFrame()

    for window in [5, 20, 60, 100]:
        result = result.merge(_sector_return_by_window(history, window), on="industry", how="left")
    for window in [60, 100]:
        result = result.merge(_sector_position_by_window(history, window), on="industry", how="left")

    market_return_20 = float(result["ret_20"].mean()) if "ret_20" in result else 0.0
    market_return_60 = float(result["ret_60"].mean()) if "ret_60" in result else 0.0
    result["rs_20"] = result["ret_20"] - market_return_20
    result["rs_60"] = result["ret_60"] - market_return_60
    result["amount_expand_rate"] = (result["amount_sum"] / result["prev_amount_sum"].replace(0, pd.NA)).fillna(1.0)
    result["resilience_score"] = result["prev_avg_pct_chg"].fillna(0).map(lambda value: 15 if value >= 0 else 8 if value >= -1 else 0)

    short_raw = (
        _normalize_score(result["avg_pct_chg"], -5, 8) * 0.28
        + _normalize_score(result["up_ratio"], 0, 1) * 0.18
        + _normalize_score(result["strong_ratio"], 0, 0.7) * 0.18
        + _normalize_score(result["limit_up_count"], 0, 5) * 0.12
        + _normalize_score(result["amount_expand_rate"], 0.5, 2.0) * 0.14
        + _normalize_score(result["volume_ratio"], 0, 2.5) * 0.06
        + result["resilience_score"]
    )
    swing_raw = (
        _normalize_score(result["ret_20"], -20, 35) * 0.24
        + _normalize_score(result["ret_60"], -30, 55) * 0.18
        + _normalize_score(result["rs_20"], -20, 30) * 0.18
        + _normalize_score(result["rs_60"], -30, 40) * 0.16
        + _normalize_score(result["ret_5"], -10, 18) * 0.10
        + _normalize_score(result["position_60"].fillna(0.5), 0, 1) * 0.08
        + _normalize_score(result["up_ratio"], 0, 1) * 0.06
    )
    overheat_penalty = ((result["position_60"].fillna(0) > 0.92) & (result["ret_20"].fillna(0) > 25)).astype(int) * 8
    result["short_score"] = short_raw.clip(0, 100).round(2)
    result["swing_score"] = (swing_raw - overheat_penalty).clip(0, 100).round(2)
    result["potential_score"] = (result["short_score"] * 0.45 + result["swing_score"] * 0.55).round(2)

    def signal_type(row):
        if row["prev_avg_pct_chg"] <= -3 and row["avg_pct_chg"] >= 3:
            return "超跌反包"
        if row["short_score"] >= 70 and row["avg_pct_chg"] >= 2:
            return "短线延续"
        if row["swing_score"] >= 70 and row["rs_20"] > 0:
            return "波段主线"
        return "观察"

    result["signal_type"] = result.apply(signal_type, axis=1)
    leader_tags = _leader_pool_tags(breakout_pool, first_limit_pool)
    result["leader_stocks"] = result["industry"].map(
        lambda industry: _select_sector_leaders(
            market[market["industry"] == industry],
            history[history["industry"] == industry],
            leader_tags,
            leaders_per_sector,
        )
    )
    result["reason"] = result.apply(
        lambda row: (
            f"{row['signal_type']}：今日均涨{row['avg_pct_chg']:.2f}%，"
            f"上涨率{row['up_ratio'] * 100:.0f}%，放量{row['amount_expand_rate']:.2f}倍"
        ),
        axis=1,
    )

    result = result.sort_values("potential_score", ascending=False).head(limit).reset_index(drop=True)
    result["rank"] = result.index + 1
    result = result.rename(columns={"industry": "industry_name"})
    columns = [
        "rank", "industry_name", "potential_score", "short_score", "swing_score", "signal_type",
        "avg_pct_chg", "prev_avg_pct_chg", "up_ratio", "strong_ratio", "limit_up_count",
        "amount_expand_rate", "volume_ratio", "turnover_rate", "ret_5", "ret_20", "ret_60",
        "ret_100", "rs_20", "rs_60", "position_60", "position_100", "leader_stocks", "reason",
    ]
    return result.reindex(columns=columns)


def _build_strong_history_stats(hist_df: pd.DataFrame) -> pd.DataFrame:
    hist = hist_df.copy()
    numeric_cols = ["pct_chg", "vol", "close", "high", "low"]
    for col in numeric_cols:
        if col in hist.columns:
            hist[col] = pd.to_numeric(hist[col], errors="coerce")
    if "high" not in hist.columns:
        hist["high"] = hist["close"]
    if "low" not in hist.columns:
        hist["low"] = hist["close"]
    hist = hist.dropna(subset=["ts_code", "trade_date", "pct_chg", "vol", "close"])
    hist = hist.sort_values(["ts_code", "trade_date"])

    rows = []
    macd_settings = load_macd_settings()
    macd_basis = macd_provenance(macd_settings)
    for ts_code, group in hist.groupby("ts_code"):
        # Breakout and reversal pools share these history statistics. The
        # MA60 slope fields below remain unavailable until 100 daily bars.
        if len(group) < 61:
            continue

        group = group.tail(100)
        close_full = group["close"]
        current_window = group.tail(60)
        close = current_window["close"]
        vol = current_window["vol"]
        high = current_window["high"].fillna(close)
        low = current_window["low"].fillna(close)

        last_close = close.iloc[-1]
        previous_close = close.iloc[-2] if len(close) >= 2 else None
        ma5 = close.tail(5).mean()
        ma10 = close.tail(10).mean()
        ma20 = close.tail(20).mean()
        ma5_series = close_full.rolling(5, min_periods=5).mean()
        previous_ma5 = ma5_series.iloc[-2] if len(ma5_series) >= 6 else None
        ma10_series = close_full.rolling(10, min_periods=10).mean()
        previous_ma10 = ma10_series.iloc[-2] if len(ma10_series) >= 11 else None
        ma30_series = close_full.rolling(30, min_periods=30).mean()
        ma30 = ma30_series.iloc[-1] if len(ma30_series) >= 30 else None
        previous_ma30 = ma30_series.iloc[-2] if len(ma30_series) >= 31 else None
        ma30_slope_5 = (
            (ma30 / ma30_series.iloc[-6] - 1) * 100
            if len(ma30_series) >= 35 and pd.notna(ma30) and pd.notna(ma30_series.iloc[-6]) and ma30_series.iloc[-6]
            else None
        )
        ma60_series = close_full.rolling(60, min_periods=60).mean()
        ma60 = ma60_series.iloc[-1]
        previous_ma60 = ma60_series.iloc[-2] if len(ma60_series) >= 61 else None
        ma60_20days_ago = ma60_series.iloc[-21] if len(close_full) >= 80 else None
        ma60_40days_ago = ma60_series.iloc[-41] if len(close_full) >= 100 else None
        ma60_slope_20 = (
            (ma60 / ma60_20days_ago - 1) * 100
            if pd.notna(ma60_20days_ago) and ma60_20days_ago else None
        )
        ma60_slope_prev20 = (
            (ma60_20days_ago / ma60_40days_ago - 1) * 100
            if pd.notna(ma60_20days_ago) and pd.notna(ma60_40days_ago) and ma60_40days_ago else None
        )
        if ma60_slope_20 is None:
            ma60_trend = "数据不足"
        elif ma60_slope_20 > 1:
            ma60_trend = "向上"
        elif ma60_slope_20 < -1:
            ma60_trend = "向下"
        else:
            ma60_trend = "走平"
        ma60_decline_slowing = bool(
            ma60_slope_20 is not None and
            ma60_slope_prev20 is not None and
            ma60_slope_20 > ma60_slope_prev20
        )
        recent_low_60 = low.min()
        recent_high_60 = high.max()
        high_drawdown_60 = (last_close / recent_high_60 - 1) * 100 if recent_high_60 else 0
        bottom_range_60 = recent_high_60 - recent_low_60
        bottom_position_60 = (
            (last_close - recent_low_60) / bottom_range_60
            if bottom_range_60 > 0
            else 1
        )
        bottom_position_60 = max(0, min(float(bottom_position_60), 1))
        low20 = low.tail(20).min()
        high20 = high.tail(20).max()
        strength20_range = high20 - low20
        strength20 = (last_close - low20) / strength20_range if strength20_range > 0 else 0
        strength20 = max(0, min(float(strength20), 1))
        previous_high_20 = high.iloc[-21:-1].max()
        previous_high_30 = group["high"].fillna(group["close"]).iloc[-31:-1].max() if len(group) >= 31 else previous_high_20
        previous_high_60 = group["high"].fillna(group["close"]).iloc[-61:-1].max() if len(group) >= 61 else previous_high_30
        vol_ma5 = vol.iloc[-6:-1].mean()
        vol_ma10 = vol.iloc[-11:-1].mean() if len(vol) >= 11 else vol.iloc[:-1].mean()
        volume_expand_rate = vol.iloc[-1] / vol_ma5 if pd.notna(vol_ma5) and vol_ma5 > 0 else 0
        volume_expand_rate_ma10 = vol.iloc[-1] / vol_ma10 if pd.notna(vol_ma10) and vol_ma10 > 0 else 0
        washout_recent_vol = vol.iloc[-6:-1].mean() if len(vol) >= 6 else None
        washout_base_vol = vol.iloc[-21:-6].mean() if len(vol) >= 21 else None
        washout_volume_ratio = (
            washout_recent_vol / washout_base_vol
            if pd.notna(washout_recent_vol) and pd.notna(washout_base_vol) and washout_base_vol > 0
            else None
        )
        washout_volume_shrink = bool(
            washout_volume_ratio is not None and washout_volume_ratio <= 0.85
        )
        ma20_series = close_full.rolling(20, min_periods=20).mean()
        previous_ma20 = ma20_series.iloc[-2] if len(ma20_series) >= 21 else None
        ma20_upward = bool(pd.notna(previous_ma20) and previous_ma20 and ma20 > previous_ma20)
        ma5_upward = bool(pd.notna(previous_ma5) and previous_ma5 and ma5 > previous_ma5)
        ma10_upward = bool(pd.notna(previous_ma10) and previous_ma10 and ma10 > previous_ma10)
        ma20_flat_or_up = bool(pd.notna(previous_ma20) and previous_ma20 and ma20 >= previous_ma20 * 0.995)
        ma30_not_fast_down = bool(
            pd.notna(ma30_slope_5) and ma30_slope_5 >= 0
        ) if ma30_slope_5 is not None else bool(pd.notna(previous_ma30) and pd.notna(ma30) and ma30 >= previous_ma30)
        ma60_upward = bool(pd.notna(previous_ma60) and previous_ma60 and ma60 > previous_ma60)
        recent20 = group.tail(20)
        platform_window = group.iloc[-21:-1] if len(group) >= 21 else group.iloc[:-1]
        if platform_window.empty:
            platform_window = group.tail(20)
        platform_low = platform_window["low"].fillna(platform_window["close"]).min()
        platform_high = platform_window["high"].fillna(platform_window["close"]).max()
        platform_days = len(platform_window)
        platform_range_pct = (platform_high / platform_low - 1) * 100 if platform_low else 0
        platform_vol = platform_window["vol"].replace(0, pd.NA)
        platform_vwap = (
            (platform_window["close"] * platform_vol).sum() / platform_vol.sum()
            if platform_vol.notna().any() and platform_vol.sum()
            else platform_window["close"].mean()
        )
        prior_cost_window = group.iloc[-41:-21] if len(group) >= 41 else group.iloc[:-21]
        prior_cost_vol = prior_cost_window["vol"].replace(0, pd.NA) if not prior_cost_window.empty else pd.Series(dtype=float)
        prior_cost_vwap = (
            (prior_cost_window["close"] * prior_cost_vol).sum() / prior_cost_vol.sum()
            if not prior_cost_window.empty and prior_cost_vol.notna().any() and prior_cost_vol.sum()
            else None
        )
        main_cost_upward = bool(pd.notna(prior_cost_vwap) and platform_vwap > prior_cost_vwap)
        price_above_main_cost = bool(last_close >= platform_vwap)
        main_cost_low = min(platform_low, platform_vwap * 0.98)
        main_cost_high = max(platform_high, platform_vwap * 1.02)
        main_cost_distance_pct = (
            max(0, (last_close / main_cost_high - 1) * 100)
            if main_cost_high
            else 0
        )
        main_cost_score = 10 if main_cost_distance_pct <= 5 else 7 if main_cost_distance_pct <= 10 else 4 if main_cost_distance_pct <= 20 else 1
        recent_limit_up_20 = int((recent20["pct_chg"] >= recent20["ts_code"].map(_limit_threshold_for_code)).sum())
        recent20_high = recent20["high"].fillna(recent20["close"]).max()
        pullback_20 = (last_close / recent20_high - 1) * 100 if recent20_high else 0
        recent20_vol = recent20["vol"]
        volume_contracting = bool(
            len(recent20_vol) >= 10 and
            recent20_vol.tail(5).mean() <= recent20_vol.iloc[:-5].tail(15).mean()
        )
        pre_latest_pullback_vol = group["vol"].iloc[-4:-1].mean() if len(group) >= 14 else None
        pre_latest_base_vol = group["vol"].iloc[-14:-4].mean() if len(group) >= 14 else None
        pullback_volume_contracting = bool(
            pd.notna(pre_latest_pullback_vol) and
            pd.notna(pre_latest_base_vol) and
            pre_latest_base_vol > 0 and
            pre_latest_pullback_vol <= pre_latest_base_vol * 0.9
        )
        recent_lows_5 = group["low"].fillna(group["close"]).iloc[-5:] if len(group) >= 5 else pd.Series(dtype=float)
        higher_lows_5d = bool(
            len(recent_lows_5) == 5 and
            (recent_lows_5.diff().dropna() >= 0).all()
        )
        breakout_level = previous_high_20
        breakout_hold = bool(
            pd.notna(breakout_level) and
            last_close > breakout_level and
            group["low"].fillna(group["close"]).iloc[-3:].min() >= breakout_level * 0.98
        )
        pullback_not_break_key = bool(
            last_close > ma20 and
            group["low"].fillna(group["close"]).iloc[-5:].min() >= min(ma10, breakout_level) * 0.98
            if pd.notna(breakout_level) and pd.notna(ma10)
            else False
        )
        previous_high_accel_days_2 = int((group["pct_chg"].iloc[-3:-1] >= 7).sum()) if len(group) >= 3 else 0
        recent3_pct = group["pct_chg"].iloc[-3:] if len(group) >= 3 else pd.Series(dtype=float)
        recent3_positive_return = bool(len(recent3_pct) == 3 and (recent3_pct > 0).all() and recent3_pct.sum() >= 15)
        recent3_return = (
            (close_full.iloc[-1] / close_full.iloc[-4] - 1) * 100
            if len(close_full) >= 4 and close_full.iloc[-4]
            else 0
        )
        recent5_return = (
            (close_full.iloc[-1] / close_full.iloc[-6] - 1) * 100
            if len(close_full) >= 6 and close_full.iloc[-6]
            else 0
        )
        upper_shadow_ratio = (
            (high.iloc[-1] - last_close) / (high.iloc[-1] - low.iloc[-1])
            if (high.iloc[-1] - low.iloc[-1]) > 0
            else 0
        )
        close_position_ratio = (
            (last_close - low.iloc[-1]) / (high.iloc[-1] - low.iloc[-1])
            if (high.iloc[-1] - low.iloc[-1]) > 0
            else 1
        )
        launch_candle = bool(group["pct_chg"].iloc[-1] >= 3 and close_position_ratio >= 0.7)
        red_three_soldiers = bool(
            len(group) >= 3 and
            (group["pct_chg"].iloc[-3:] > 0).all() and
            group["close"].iloc[-1] > group["close"].iloc[-2] > group["close"].iloc[-3]
        )
        bullish_engulfing = False
        if len(group) >= 2:
            prev = group.iloc[-2]
            curr = group.iloc[-1]
            prev_open = prev.get("open", prev["close"])
            curr_open = curr.get("open", curr["close"])
            bullish_engulfing = bool(
                prev["pct_chg"] < 0 and
                curr["pct_chg"] > 0 and
                curr["close"] >= prev_open and
                curr_open <= prev["close"]
            )
        close_60days_ago = close_full.iloc[-61]
        ret60 = (last_close / close_60days_ago - 1) * 100 if close_60days_ago else 0

        dif, dea, macd = calculate_macd(
            close_full,
            macd_settings,
            min_periods=False,
        )

        delta = close_full.diff()
        avg_gain = delta.clip(lower=0).rolling(6, min_periods=6).mean()
        avg_loss = (-delta.clip(upper=0)).rolling(6, min_periods=6).mean()
        rsi6 = 100 - 100 / (1 + avg_gain / avg_loss.replace(0, pd.NA))
        rsi6 = rsi6.mask((avg_loss == 0) & (avg_gain > 0), 100).mask(
            (avg_loss == 0) & (avg_gain == 0), 50
        )

        low9 = low.rolling(9, min_periods=9).min()
        high9 = high.rolling(9, min_periods=9).max()
        rsv = ((close - low9) / (high9 - low9) * 100).where(high9 != low9, 50)
        kdj_k = rsv.ewm(com=2, adjust=False, min_periods=1).mean()
        kdj_d = kdj_k.ewm(com=2, adjust=False, min_periods=1).mean()
        kdj_j = 3 * kdj_k - 2 * kdj_d

        rows.append({
            "ts_code": ts_code,
            "hist_days": len(group),
            "previous_close": previous_close,
            "ma5": ma5,
            "previous_ma5": previous_ma5,
            "ma5_upward": ma5_upward,
            "ma10": ma10,
            "previous_ma10": previous_ma10,
            "ma10_upward": ma10_upward,
            "ma20": ma20,
            "previous_ma20": previous_ma20,
            "ma20_upward": ma20_upward,
            "ma20_flat_or_up": ma20_flat_or_up,
            "ma30": ma30,
            "previous_ma30": previous_ma30,
            "ma30_slope_5": ma30_slope_5,
            "ma30_not_fast_down": ma30_not_fast_down,
            "ma60": ma60,
            "previous_ma60": previous_ma60,
            "ma60_upward": ma60_upward,
            "ma60_slope": ma60_slope_20,
            "ma60_20days_ago": ma60_20days_ago,
            "ma60_40days_ago": ma60_40days_ago,
            "ma60_slope_20": ma60_slope_20,
            "ma60_slope_prev20": ma60_slope_prev20,
            "ma60_trend": ma60_trend,
            "ma60_decline_slowing": ma60_decline_slowing,
            "recent_low_60": recent_low_60,
            "recent_high_60": recent_high_60,
            "high_drawdown_60": high_drawdown_60,
            "bottom_position_60": bottom_position_60,
            "low20": low20,
            "high20": high20,
            "strength20": strength20,
            "ret60": ret60,
            "previous_high_20": previous_high_20,
            "previous_high_30": previous_high_30,
            "previous_high_60": previous_high_60,
            "vol_ma5": vol_ma5,
            "vol_ma10": vol_ma10,
            "volume_expand_rate": volume_expand_rate,
            "volume_expand_rate_ma10": volume_expand_rate_ma10,
            "washout_volume_ratio": washout_volume_ratio,
            "washout_volume_shrink": washout_volume_shrink,
            "recent_limit_up_20": recent_limit_up_20,
            "main_cost_low": main_cost_low,
            "main_cost_high": main_cost_high,
            "main_cost_vwap": platform_vwap,
            "previous_main_cost_vwap": prior_cost_vwap,
            "main_cost_upward": main_cost_upward,
            "price_above_main_cost": price_above_main_cost,
            "main_cost_days": platform_days,
            "main_cost_range_pct": platform_range_pct,
            "main_cost_distance_pct": main_cost_distance_pct,
            "main_cost_score": main_cost_score,
            "pullback_20": pullback_20,
            "volume_contracting": volume_contracting,
            "pullback_volume_contracting": pullback_volume_contracting,
            "higher_lows_5d": higher_lows_5d,
            "breakout_level": breakout_level,
            "breakout_hold": breakout_hold,
            "pullback_not_break_key": pullback_not_break_key,
            "previous_high_accel_days_2": previous_high_accel_days_2,
            "recent3_positive_return": recent3_positive_return,
            "recent3_return": recent3_return,
            "recent5_return": recent5_return,
            "upper_shadow_ratio": upper_shadow_ratio,
            "close_position_ratio": close_position_ratio,
            "launch_candle": launch_candle,
            "red_three_soldiers": red_three_soldiers,
            "bullish_engulfing": bullish_engulfing,
            "macd_dif": dif.iloc[-1],
            "macd_dea": dea.iloc[-1],
            "macd": macd.iloc[-1],
            "previous_macd": macd.iloc[-2],
            "macd_golden_cross": bool(dif.iloc[-1] > dea.iloc[-1]),
            "rsi6": rsi6.iloc[-1],
            "kdj_k": kdj_k.iloc[-1],
            "kdj_d": kdj_d.iloc[-1],
            "kdj_j": kdj_j.iloc[-1],
            "previous_kdj_k": kdj_k.iloc[-2],
            "previous_kdj_d": kdj_d.iloc[-2],
            "previous_kdj_j": kdj_j.iloc[-2],
            **macd_basis,
        })

    return pd.DataFrame(rows)


def _build_breakout_confluence_stats(hist_df: pd.DataFrame) -> pd.DataFrame:
    hist = hist_df.copy()
    numeric_cols = ["pct_chg", "vol", "close", "high", "low"]
    for col in numeric_cols:
        if col in hist.columns:
            hist[col] = pd.to_numeric(hist[col], errors="coerce")
    if "high" not in hist.columns:
        hist["high"] = hist["close"]
    if "low" not in hist.columns:
        hist["low"] = hist["close"]
    hist = hist.dropna(subset=["ts_code", "trade_date", "close", "high", "low"])
    hist = hist.sort_values(["ts_code", "trade_date"])

    rows = []
    macd_settings = load_macd_settings()
    macd_basis = macd_provenance(macd_settings)
    for ts_code, group in hist.groupby("ts_code"):
        group = group.tail(180).copy()
        if len(group) < 61:
            continue

        close = group["close"]
        high = group["high"].fillna(close)
        low = group["low"].fillna(close)
        last_close = close.iloc[-1]

        boll_middle = close.rolling(20, min_periods=20).mean()
        boll_std = close.rolling(20, min_periods=20).std(ddof=0)
        boll_upper = boll_middle + 2 * boll_std
        boll_lower = boll_middle - 2 * boll_std
        boll_width = (boll_upper - boll_lower) / boll_middle
        latest_boll_width = boll_width.iloc[-1] if len(boll_width) else pd.NA
        prior_boll_width = boll_width.iloc[-6] if len(boll_width) >= 6 else pd.NA
        recent_boll_width = boll_width.tail(3)
        boll_width_expand = bool(
            pd.notna(latest_boll_width) and
            pd.notna(prior_boll_width) and
            latest_boll_width > prior_boll_width and
            recent_boll_width.notna().sum() == 3 and
            recent_boll_width.iloc[-1] > recent_boll_width.iloc[0]
        )
        latest_upper = boll_upper.iloc[-1] if len(boll_upper) else pd.NA
        latest_middle = boll_middle.iloc[-1] if len(boll_middle) else pd.NA
        close_near_boll_upper = bool(
            pd.notna(latest_upper) and
            pd.notna(latest_middle) and
            last_close > latest_middle and
            last_close >= latest_upper * 0.97
        )

        parsed_dates = pd.to_datetime(group["trade_date"], format="%Y%m%d", errors="coerce")
        if parsed_dates.notna().sum() == len(group):
            week_index = parsed_dates.dt.to_period("W")
        else:
            week_index = pd.date_range("2026-01-01", periods=len(group), freq="B").to_period("W")
        weekly = group.copy()
        weekly["week_index"] = week_index
        weekly_close = weekly.dropna(subset=["week_index"]).groupby("week_index")["close"].last()
        weekly_ma5 = weekly_close.rolling(5, min_periods=5).mean()
        weekly_ma10 = weekly_close.rolling(10, min_periods=10).mean()
        if len(weekly_close) < 12 or weekly_ma10.dropna().empty:
            weekly_trend_state = "数据不足"
            weekly_trend_ok = False
        else:
            latest_weekly_ma5 = weekly_ma5.iloc[-1]
            latest_weekly_ma10 = weekly_ma10.iloc[-1]
            weekly_ma10_base = weekly_ma10.iloc[-4] if len(weekly_ma10) >= 4 else pd.NA
            weekly_ma10_slope = (
                latest_weekly_ma10 / weekly_ma10_base - 1
                if pd.notna(latest_weekly_ma10) and pd.notna(weekly_ma10_base) and weekly_ma10_base
                else pd.NA
            )
            weekly_close_above_ma10 = bool(
                pd.notna(latest_weekly_ma10) and weekly_close.iloc[-1] >= latest_weekly_ma10 * 0.98
            )
            if (
                pd.notna(latest_weekly_ma5) and
                pd.notna(latest_weekly_ma10) and
                latest_weekly_ma5 >= latest_weekly_ma10 and
                pd.notna(weekly_ma10_slope) and
                weekly_ma10_slope >= 0
            ):
                weekly_trend_state = "上升"
            elif weekly_close_above_ma10 and pd.notna(weekly_ma10_slope) and weekly_ma10_slope >= -0.03:
                weekly_trend_state = "横盘"
            else:
                weekly_trend_state = "下降"
            weekly_trend_ok = weekly_trend_state in {"上升", "横盘"}

        low9 = low.rolling(9, min_periods=9).min()
        high9 = high.rolling(9, min_periods=9).max()
        rsv = ((close - low9) / (high9 - low9) * 100).where(high9 != low9, 50).fillna(50)
        kdj_k = rsv.ewm(com=2, adjust=False, min_periods=1).mean()
        kdj_d = kdj_k.ewm(com=2, adjust=False, min_periods=1).mean()
        kdj_golden_series = (kdj_k > kdj_d) & (kdj_k.shift(1) <= kdj_d.shift(1))
        kdj_golden_cross = bool(kdj_golden_series.iloc[-1])
        kdj_recent_golden_cross = bool(
            kdj_golden_series.tail(3).fillna(False).any() or
            (
                len(kdj_k) >= 2 and
                kdj_k.iloc[-1] > kdj_d.iloc[-1] and
                kdj_k.iloc[-1] >= kdj_k.iloc[-2] and
                kdj_d.iloc[-1] >= kdj_d.iloc[-2]
            )
        )
        kdj_breakout_signal = bool(kdj_golden_cross or kdj_recent_golden_cross)

        dif, dea, _ = calculate_macd(
            close,
            macd_settings,
            min_periods=False,
        )
        macd_gap = dif - dea
        normalized_zero_tolerance = max(last_close * 0.005, 0.03)
        macd_zero_axis_ready = bool(
            (dif.iloc[-1] >= 0 and dea.iloc[-1] >= 0) or
            (dif.iloc[-1] >= -normalized_zero_tolerance and dif.iloc[-1] > dif.iloc[-2])
        )
        macd_golden_series = (dif > dea) & (dif.shift(1) <= dea.shift(1))
        macd_golden_cross = bool(macd_golden_series.iloc[-1])
        macd_recent_golden_cross = bool(macd_golden_series.tail(3).fillna(False).any())
        macd_gap_contracting = bool(
            len(macd_gap) >= 3 and
            macd_gap.iloc[-1] < 0 and
            macd_gap.iloc[-1] > macd_gap.iloc[-2] > macd_gap.iloc[-3]
        )
        macd_cross_ready = bool(macd_golden_cross or macd_recent_golden_cross or macd_gap_contracting)

        confluence_fields = [
            boll_width_expand,
            close_near_boll_upper,
            weekly_trend_state == "上升",
            weekly_trend_state == "横盘",
            kdj_breakout_signal,
            macd_cross_ready,
        ]
        rows.append({
            "ts_code": ts_code,
            "boll_width": latest_boll_width,
            "boll_width_expand": boll_width_expand,
            "close_near_boll_upper": close_near_boll_upper,
            "boll_breakout_ready": bool(boll_width_expand and close_near_boll_upper),
            "weekly_trend_state": weekly_trend_state,
            "weekly_trend_ok": weekly_trend_ok,
            "kdj_recent_golden_cross": kdj_recent_golden_cross,
            "kdj_breakout_signal": kdj_breakout_signal,
            "macd_zero_axis_ready": macd_zero_axis_ready,
            "macd_cross_ready": macd_cross_ready,
            "breakout_confluence_count": int(sum(bool(value) for value in confluence_fields)),
            "breakout_confluence_score": (
                int(boll_width_expand) * 4 +
                int(close_near_boll_upper) * 3 +
                int(weekly_trend_state == "上升") * 4 +
                int(weekly_trend_state == "横盘") * 2 +
                int(kdj_breakout_signal) * 3 +
                int(macd_cross_ready) * 4
            ),
            **macd_basis,
        })

    return pd.DataFrame(rows)


def _build_strong_reason(row):
    if row.get("bottom_position_qualified") and row.get("rebound_volume_confirmed") and row.get("washout_volume_shrink"):
        prefix = "底部放量反弹"
    else:
        prefix = "反转确认"
    labels = [
        ("bottom_position_qualified", "底部区域"),
        ("rebound_volume_confirmed", "放量反弹"),
        ("washout_volume_shrink", "缩量洗盘"),
        ("trend_repairing", "均线修复"),
        ("ma60_above", "站上MA60"),
        ("ma60_improving", "MA60趋势改善"),
        ("rsi6_below_75", "RSI6<75"),
        ("kdj_golden_cross", "KDJ金叉"),
        ("macd_trend_or_above_zero", "MACD向上/零轴上"),
        ("volume_above_ma5_1_5", "量能>5日均量1.5倍"),
    ]
    reasons = [label for field, label in labels if row.get(field)]
    return f"{prefix}({int(row.get('reversal_indicator_count', 0))}/6)-" + "+".join(reasons)


STAGE_META = {
    "S1": ("底部吸筹阶段", "观察，不动手"),
    "S2": ("启动突破阶段", "可试仓"),
    "S3": ("回踩确认阶段", "主建仓区"),
    "S4": ("二次启动阶段", "可加仓"),
    "S5": ("主升浪阶段", "持股不追高"),
    "S6": ("出货风险阶段", "减仓/清仓"),
}


def _truthy(value) -> bool:
    return bool(pd.notna(value) and value)


def _classify_trend_stage(row) -> pd.Series:
    trade_state = str(row.get("trade_state") or row.get("breakout_status") or "")
    breakout_score = row.get("breakout_score")
    has_breakout_entry = (
        pd.notna(breakout_score) and
        trade_state in {"立即建仓", "等待回踩", "尾盘观察", "加入观察池"}
    )
    danger_flags = [
        _truthy(row.get("risk_reject")),
        _truthy(row.get("huge_volume_fade")),
        _truthy(row.get("surge_fade")),
        _truthy(row.get("long_upper_shadow")),
        _truthy(row.get("platform_broken")),
        _truthy(row.get("volume_down")),
    ]
    if sum(danger_flags) >= 1:
        stage = "S6"
        reason = "出现出货或破位风险信号"
    elif _truthy(row.get("strong_consolidation")) and (
        _truthy(row.get("volume_breakout")) or _truthy(row.get("daily_tail_strength"))
    ):
        stage = "S4"
        reason = "缩量回踩后再次转强"
    elif _truthy(row.get("strong_consolidation")):
        stage = "S3"
        reason = "突破后缩量回踩且未破关键均线"
    elif _truthy(row.get("volume_breakout")) or _truthy(row.get("rebound_volume_confirmed")):
        stage = "S2"
        reason = "放量突破或底部放量反弹"
    elif has_breakout_entry:
        stage = "S2"
        reason = "已进入趋势突破交易区，不能按底部观察处理"
    elif (
        _truthy(row.get("ma_bullish")) and
        _truthy(row.get("ma20_upward")) and
        float(row.get("recent5_return") or 0) >= 15
    ):
        stage = "S5"
        reason = "短期涨幅较大且均线多头，偏主升浪持股区"
    elif _truthy(row.get("bottom_position_qualified")) or _truthy(row.get("washout_volume_shrink")):
        stage = "S1"
        reason = "底部区域缩量整理，仍以观察为主"
    else:
        stage = "S1"
        reason = "阶段信号不足，先观察"

    label, action = STAGE_META[stage]
    return pd.Series({
        "trend_stage": stage,
        "stage_label": label,
        "stage_action": action,
        "stage_reason": reason,
    })


def _attach_trend_stage(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    result = df.copy()
    stage_df = result.apply(_classify_trend_stage, axis=1)
    return pd.concat([result, stage_df], axis=1)


def pick_strong_base_candidates(df: pd.DataFrame, relaxed: bool = False):
    """优势股基础范围：沪深主板 A 股、非 ST、股价大于 3 元。"""
    df = clean_data(df)
    if "volume_ratio" not in df.columns:
        df["volume_ratio"] = 0

    condition_price = df["close"] > 3
    condition_market = _is_mainboard_a_stock(df["ts_code"])

    if "name" in df.columns:
        condition_st = ~df["name"].str.contains("ST", na=False)
    else:
        condition_st = True

    return df[
        condition_market &
        condition_price &
        condition_st
    ].copy()


def pick_stocks(
    df: pd.DataFrame,
    hist_df: pd.DataFrame | None = None,
    history_stats: pd.DataFrame | None = None,
):
    """超跌反转池：高位回撤后，以 6 项趋势与过热指标确认反转。"""
    df = clean_data(df)
    if "volume_ratio" not in df.columns:
        df["volume_ratio"] = 0

    base = pick_strong_base_candidates(df)
    if base.empty:
        return base

    if history_stats is not None:
        stats = history_stats
    elif hist_df is None or hist_df.empty:
        return base.iloc[0:0].copy()
    else:
        stats = _build_strong_history_stats(hist_df)
    if stats.empty:
        return base.iloc[0:0].copy()
    # MA60's current, 20-days-ago, and 40-days-ago values require 100
    # trading days. Do not let partial MA60 trend data into reversal.
    stats = stats[stats["hist_days"] >= 100]
    if stats.empty:
        return base.iloc[0:0].copy()
    candidates = pd.merge(base, stats, on="ts_code", how="inner")

    if candidates.empty:
        return candidates

    if "industry" not in candidates.columns:
        candidates["industry"] = None

    if "amount" in candidates.columns:
        raw_amount = pd.to_numeric(candidates["amount"], errors="coerce")
        candidates["amount_yuan"] = raw_amount.where(raw_amount >= 10_000_000, raw_amount * 1000)
    else:
        candidates["amount_yuan"] = candidates["close"] * candidates["vol"] * 100

    candidates = candidates[
        (candidates["high_drawdown_60"] <= -20) &
        (candidates["turnover_rate"] > 3) &
        (candidates["amount_yuan"] > 200_000_000)
    ].copy()
    if candidates.empty:
        return candidates

    sector_strength = _build_sector_strength(df)
    hot_sectors = sector_strength.head(5)
    sector_map = sector_strength.set_index("industry_name")[
        ["avg_pct_chg", "up_ratio", "strong_count", "sector_score"]
    ].to_dict(orient="index") if not sector_strength.empty else {}
    hot_industries = set(hot_sectors["industry_name"]) if not hot_sectors.empty else set()
    candidates["sector_avg_pct_chg"] = candidates["industry"].map(lambda value: sector_map.get(value, {}).get("avg_pct_chg"))
    candidates["sector_up_ratio"] = candidates["industry"].map(lambda value: sector_map.get(value, {}).get("up_ratio"))
    candidates["sector_strong_count"] = candidates["industry"].map(lambda value: sector_map.get(value, {}).get("strong_count"))
    candidates["sector_score"] = candidates["industry"].map(lambda value: sector_map.get(value, {}).get("sector_score"))
    candidates["rs_industry"] = candidates["pct_chg"] - candidates["sector_avg_pct_chg"]
    candidates["rs_industry_penalty"] = 0
    candidates.loc[candidates["rs_industry"] < 0, "rs_industry_penalty"] = -10
    if "industry" in df.columns:
        rank_source = df.dropna(subset=["industry", "pct_chg"]).copy()
        rank_source["relative_strength_rank"] = (
            rank_source.groupby("industry", dropna=False)["pct_chg"]
            .rank(method="min", ascending=False)
            .astype(int)
        )
        rank_map = rank_source.set_index("ts_code")["relative_strength_rank"].to_dict()
        candidates["relative_strength_rank"] = candidates["ts_code"].map(rank_map)
    else:
        candidates["relative_strength_rank"] = pd.NA

    candidates["bottom_position_qualified"] = candidates["bottom_position_60"] <= 0.50
    candidates["rebound_volume_confirmed"] = (
        (candidates["pct_chg"] > 0) &
        (candidates["close"] > candidates["previous_close"]) &
        (candidates["volume_expand_rate"] >= 1.5)
    )
    candidates["trend_repairing"] = (
        candidates["ma20_flat_or_up"].fillna(False) &
        candidates["ma30_not_fast_down"].fillna(False) &
        (candidates["close"] >= candidates["ma20"])
    )

    candidates = candidates[
        candidates["bottom_position_qualified"] &
        candidates["rebound_volume_confirmed"] &
        candidates["washout_volume_shrink"].fillna(False)
    ].copy()
    if candidates.empty:
        return candidates

    # 超跌反转最终确认：6 项中至少满足 4 项。底部反转早期通常
    # 还未站上 MA60 或 MACD 零轴，过严会把有效早期反弹全部过滤。
    candidates["ma60_above"] = candidates["close"] > candidates["ma60"]
    candidates["ma60_up"] = candidates["ma60_trend"] == "向上"
    candidates["ma60_flat"] = candidates["ma60_trend"] == "走平"
    candidates["ma60_improving"] = (
        candidates["ma60_up"] |
        candidates["ma60_flat"] |
        candidates["ma60_decline_slowing"]
    )
    candidates["rsi6_below_75"] = candidates["rsi6"].notna() & (candidates["rsi6"] < 75)
    candidates["kdj_j_below_90"] = candidates["kdj_j"].notna() & (candidates["kdj_j"] < 90)
    for column in ("previous_kdj_k", "previous_kdj_d", "macd", "previous_macd"):
        if column not in candidates.columns:
            candidates[column] = pd.NA
    candidates["kdj_golden_cross"] = (
        candidates["kdj_k"].notna() &
        candidates["kdj_d"].notna() &
        candidates["previous_kdj_k"].notna() &
        candidates["previous_kdj_d"].notna() &
        (candidates["kdj_k"] > candidates["kdj_d"]) &
        (candidates["previous_kdj_k"] <= candidates["previous_kdj_d"])
    )
    candidates["macd_above_zero"] = (
        (candidates["macd_dif"] > 0) &
        (candidates["macd_dea"] > 0)
    )
    candidates["macd_trending_up"] = (
        candidates["macd"].notna() &
        candidates["previous_macd"].notna() &
        (candidates["macd"] > candidates["previous_macd"])
    )
    candidates["macd_trend_or_above_zero"] = (
        candidates["macd_above_zero"] |
        candidates["macd_trending_up"]
    )
    candidates["volume_above_ma5_1_5"] = candidates["volume_expand_rate"] >= 1.5
    reversal_indicators = [
        "ma60_above",
        "ma60_improving",
        "rsi6_below_75",
        "kdj_golden_cross",
        "macd_trend_or_above_zero",
        "volume_above_ma5_1_5",
    ]
    candidates["reversal_indicator_count"] = candidates[reversal_indicators].astype(int).sum(axis=1)

    candidates["in_bottom_area"] = candidates["close"] <= candidates["recent_low_60"] * 1.20
    candidates["ret60_oversold"] = candidates["ret60"] < -30
    candidates["turnover_active"] = candidates["turnover_rate"] > 8
    candidates["volume_ratio_active"] = candidates["volume_ratio"] > 2
    candidates["hot_theme"] = candidates["industry"].isin(hot_industries)

    score_weights = {
        "bottom_position_qualified": 20,
        "rebound_volume_confirmed": 20,
        "washout_volume_shrink": 20,
        "trend_repairing": 15,
        "ma60_above": 5,
        "ma60_improving": 5,
        "rsi6_below_75": 3,
        "kdj_golden_cross": 5,
        "macd_trend_or_above_zero": 5,
        "volume_above_ma5_1_5": 3,
        "in_bottom_area": 1,
        "hot_theme": 1,
    }
    score_columns = []
    for field, weight in score_weights.items():
        score_column = f"{field}_score"
        candidates[score_column] = candidates[field].astype(int) * weight
        score_columns.append(score_column)
    candidates["score"] = (
        candidates[score_columns].sum(axis=1) +
        candidates["rs_industry_penalty"]
    )
    candidates["strong_reason"] = candidates.apply(_build_strong_reason, axis=1)

    candidates = candidates[
        candidates["kdj_golden_cross"] &
        (candidates["reversal_indicator_count"] >= 4)
    ].copy()
    if candidates.empty:
        return candidates
    candidates = _attach_trend_stage(candidates)

    sort_columns = [
        "score",
        "reversal_indicator_count",
        "kdj_golden_cross",
        "macd_trend_or_above_zero",
        "volume_above_ma5_1_5",
        "bottom_position_qualified",
        "rebound_volume_confirmed",
        "washout_volume_shrink",
        "trend_repairing",
        "ma60_improving",
        "turnover_rate",
        "volume_ratio",
        "volume_expand_rate",
        "relative_strength_rank",
    ]
    return candidates.sort_values(
        sort_columns,
        ascending=[False] * (len(sort_columns) - 1) + [True],
    )


def _ensure_amount_yuan(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    if "amount" in result.columns:
        raw_amount = pd.to_numeric(result["amount"], errors="coerce")
        result["amount_yuan"] = raw_amount.where(raw_amount >= 10_000_000, raw_amount * 1000)
    else:
        result["amount_yuan"] = result["close"] * result["vol"] * 100
    return result


def _ensure_total_mv_yuan(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    if "total_mv" in result.columns:
        raw_mv = pd.to_numeric(result["total_mv"], errors="coerce")
        result["total_mv_yuan"] = raw_mv.where(raw_mv >= 100_000_000, raw_mv * 10_000)
    else:
        result["total_mv_yuan"] = pd.NA
    return result


def _matches_core_inflow_sector(df: pd.DataFrame, core_inflow_sectors: set[str]) -> pd.Series:
    if not core_inflow_sectors:
        return pd.Series(True, index=df.index)

    match = pd.Series(False, index=df.index)
    sector_columns = ["industry", "concept", "concepts", "sector", "theme", "area"]
    for column in sector_columns:
        if column not in df.columns:
            continue
        values = df[column].fillna("").astype(str)
        column_match = values.apply(
            lambda text: any(sector and sector in text for sector in core_inflow_sectors)
        )
        match = match | column_match
    return match


def _attach_sector_context(candidates: pd.DataFrame, market_df: pd.DataFrame) -> pd.DataFrame:
    result = candidates.copy()
    if "industry" not in result.columns:
        result["industry"] = None

    sector_strength = _build_sector_strength(market_df)
    hot_sectors = sector_strength.head(5)
    sector_rank_map = {}
    if not sector_strength.empty:
        ranked = sector_strength.reset_index(drop=True).copy()
        ranked["sector_rank"] = ranked.index + 1
        sector_rank_map = ranked.set_index("industry_name")["sector_rank"].to_dict()
    sector_map = sector_strength.set_index("industry_name")[
        ["avg_pct_chg", "up_ratio", "strong_count", "limit_up_count", "sector_amount_yuan", "sector_score"]
    ].to_dict(orient="index") if not sector_strength.empty else {}
    hot_industries = set(hot_sectors["industry_name"]) if not hot_sectors.empty else set()

    result["sector_avg_pct_chg"] = result["industry"].map(lambda value: sector_map.get(value, {}).get("avg_pct_chg"))
    result["sector_up_ratio"] = result["industry"].map(lambda value: sector_map.get(value, {}).get("up_ratio"))
    result["sector_strong_count"] = result["industry"].map(lambda value: sector_map.get(value, {}).get("strong_count"))
    result["sector_limit_up_count"] = result["industry"].map(lambda value: sector_map.get(value, {}).get("limit_up_count"))
    result["sector_amount_yuan"] = result["industry"].map(lambda value: sector_map.get(value, {}).get("sector_amount_yuan"))
    result["sector_score"] = result["industry"].map(lambda value: sector_map.get(value, {}).get("sector_score"))
    result["sector_rank"] = result["industry"].map(lambda value: sector_rank_map.get(value))
    result["rs_industry"] = result["pct_chg"] - result["sector_avg_pct_chg"]
    result["hot_theme"] = result["industry"].isin(hot_industries)

    if "industry" in market_df.columns:
        rank_source = market_df.dropna(subset=["industry", "pct_chg"]).copy()
        rank_source["relative_strength_rank"] = (
            rank_source.groupby("industry", dropna=False)["pct_chg"]
            .rank(method="min", ascending=False)
            .astype(int)
        )
        rank_map = rank_source.set_index("ts_code")["relative_strength_rank"].to_dict()
        result["relative_strength_rank"] = result["ts_code"].map(rank_map)
    else:
        result["relative_strength_rank"] = pd.NA

    return result


def _attach_sector_amount_expansion(
    candidates: pd.DataFrame,
    market_df: pd.DataFrame,
    hist_df: pd.DataFrame | None,
) -> pd.DataFrame:
    result = candidates.copy()
    prev_sector_amount_map = {}

    if "prev_sector_amount_yuan" in market_df.columns and "industry" in market_df.columns:
        prev_source = market_df.dropna(subset=["industry"]).copy()
        prev_source["prev_sector_amount_yuan"] = pd.to_numeric(
            prev_source["prev_sector_amount_yuan"],
            errors="coerce",
        )
        prev_sector_amount_map = (
            prev_source.dropna(subset=["prev_sector_amount_yuan"])
            .groupby("industry")["prev_sector_amount_yuan"]
            .max()
            .to_dict()
        )

    if not prev_sector_amount_map and hist_df is not None and not hist_df.empty and "amount" in hist_df.columns:
        hist = hist_df.copy()
        hist["amount"] = pd.to_numeric(hist["amount"], errors="coerce")
        hist = hist.dropna(subset=["ts_code", "trade_date", "amount"])
        if not hist.empty and "industry" in market_df.columns:
            industry_map = market_df.dropna(subset=["industry"]).set_index("ts_code")["industry"].to_dict()
            previous_rows = []
            for ts_code, group in hist.sort_values(["ts_code", "trade_date"]).groupby("ts_code"):
                if len(group) < 2:
                    continue
                previous = group.iloc[-2].copy()
                previous["industry"] = industry_map.get(ts_code)
                previous_rows.append(previous)
            if previous_rows:
                previous_df = pd.DataFrame(previous_rows).dropna(subset=["industry"])
                previous_df = _ensure_amount_yuan(previous_df)
                prev_sector_amount_map = previous_df.groupby("industry")["amount_yuan"].sum().to_dict()

    result["prev_sector_amount_yuan"] = result["industry"].map(lambda value: prev_sector_amount_map.get(value))
    result["sector_amount_expand_rate"] = result["sector_amount_yuan"] / result["prev_sector_amount_yuan"]
    return result


def _build_breakout_reason(row):
    labels = [
        ("first_platform_breakout", "首次平台突破"),
        ("volume_breakout", "放量突破"),
        ("strong_consolidation", "缩量回踩"),
        ("break_ma30", "突破30日线"),
        ("break_ma60", "突破60日线"),
        ("boll_width_expand", "布林开口"),
        ("close_near_boll_upper", "贴近上轨"),
        ("kdj_breakout_signal", "KDJ金叉"),
        ("macd_cross_ready", "MACD将金叉"),
        ("daily_tail_strength", "尾盘强势"),
        ("sector_top10", "板块前10"),
        ("ma_bullish", "多头排列"),
        ("bullish_engulfing", "阳包阴"),
        ("red_three_soldiers", "红三兵"),
        ("launch_candle", "启动阳线"),
    ]
    weekly_state = row.get("weekly_trend_state")
    weekly_reason = "周线上升" if weekly_state == "上升" else "周线横盘" if weekly_state == "横盘" else None
    reasons = ([weekly_reason] if weekly_reason else []) + [label for field, label in labels if row.get(field)]
    status = row.get("breakout_status", "观察")
    return f"{status}({int(row.get('breakout_score', 0))}分)-" + "+".join(reasons)


def _trade_state(score: float, main_cost_distance_pct: float) -> str:
    if score >= 90 and main_cost_distance_pct <= 8:
        return "立即建仓"
    if score >= 80:
        return "等待回踩"
    if score >= 70:
        return "加入观察池"
    return "放弃"


def _build_main_cost_label(row) -> str:
    low = row.get("main_cost_low")
    high = row.get("main_cost_high")
    distance = row.get("main_cost_distance_pct")
    score = row.get("main_cost_score")
    if pd.isna(low) or pd.isna(high) or pd.isna(distance):
        return "主力成本：历史不足"
    stars = "★★★★★" if score >= 8 else "★★★★☆" if score >= 7 else "★★★☆☆" if score >= 4 else "★☆☆☆☆"
    return f"主力成本 {low:.2f}~{high:.2f}，距离 {distance:.1f}% {stars}"


def _breakout_status(score: float) -> str:
    if score >= 90:
        return "立即关注"
    if score >= 80:
        return "尾盘观察"
    if score >= 70:
        return "加入观察池"
    return "放弃"


def pick_breakout_stocks(
    df: pd.DataFrame,
    hist_df: pd.DataFrame | None = None,
    history_stats: pd.DataFrame | None = None,
):
    """趋势突破池：V1 短线买点评分，趋势硬门槛 + 70 分以上入池。"""
    df = clean_data(df)
    if "volume_ratio" not in df.columns:
        df["volume_ratio"] = 0

    base = pick_strong_base_candidates(df)
    if base.empty:
        return base.iloc[0:0].copy()

    if history_stats is not None:
        stats = history_stats
    elif hist_df is None or hist_df.empty:
        return base.iloc[0:0].copy()
    else:
        stats = _build_strong_history_stats(hist_df)
    if stats.empty:
        return base.iloc[0:0].copy()

    candidates = pd.merge(base, stats, on="ts_code", how="inner")
    if candidates.empty:
        return candidates

    candidates = _ensure_amount_yuan(candidates)
    candidates = _attach_sector_context(candidates, df)
    candidates = _attach_sector_amount_expansion(candidates, df, hist_df)
    candidates["sector_top10"] = candidates["sector_rank"].notna() & (candidates["sector_rank"] <= 10)
    candidates["close_position_strong"] = (
        candidates["high"].notna() &
        candidates["close"].notna() &
        (candidates["high"] > 0) &
        (candidates["close"] >= candidates["high"] * 0.98)
    )
    candidates["close_near_high"] = candidates["close_position_strong"]
    day_range = candidates["high"] - candidates["low"]
    candidates["close_top20_position"] = (
        (day_range > 0) &
        (((candidates["close"] - candidates["low"]) / day_range) >= 0.8)
    ) | ((day_range == 0) & candidates["close_position_strong"])
    candidates["daily_tail_strength"] = (
        candidates["close_position_strong"] &
        candidates["close_top20_position"] &
        (candidates["pct_chg"] >= 3)
    )
    tail_pct = pd.to_numeric(candidates.get("tail_pct_chg", pd.Series(pd.NA, index=candidates.index)), errors="coerce")
    candidates["tail_pct_chg"] = tail_pct
    candidates["tail_price_rise"] = tail_pct.notna() & (tail_pct >= 2)
    tail_amount_ratio = pd.to_numeric(
        candidates.get("tail_amount_ratio", pd.Series(pd.NA, index=candidates.index)),
        errors="coerce",
    )
    candidates["tail_amount_ratio"] = tail_amount_ratio.where(tail_amount_ratio <= 1, tail_amount_ratio / 100)
    candidates["tail_amount_active"] = False
    candidates["tail_data_missing"] = candidates["tail_pct_chg"].isna() | candidates["tail_amount_ratio"].isna()
    candidates["volume_above_ma5_1_8"] = candidates["volume_expand_rate"] >= 1.8
    candidates["volume_above_ma5_1_5"] = candidates["volume_expand_rate"] >= 1.5
    candidates["volume_above_ma5"] = candidates["volume_expand_rate"] > 1
    candidates["volume_above_ma10_1_5"] = candidates["volume_expand_rate_ma10"] >= 1.5
    candidates["ma_bullish"] = (
        candidates["ma5"].notna() &
        candidates["ma10"].notna() &
        candidates["ma20"].notna() &
        (candidates["ma5"] > candidates["ma10"]) &
        (candidates["ma10"] > candidates["ma20"])
    )
    candidates["breakout_new_high"] = candidates["close"] > candidates["previous_high_20"]
    candidates["first_platform_breakout"] = candidates["breakout_new_high"] & (candidates["recent_limit_up_20"].fillna(0) <= 1)
    candidates["box_breakout"] = candidates["close"] > candidates["previous_high_30"]
    candidates["break_ma30"] = (
        candidates["ma30"].notna() &
        (candidates["close"] > candidates["ma30"]) &
        (candidates["close"].shift(0).notna())
    )
    candidates["break_ma60"] = (
        candidates["ma60"].notna() &
        (candidates["close"] > candidates["ma60"]) &
        (candidates["previous_high_60"].notna())
    )
    candidates["trend_position_strong"] = (
        candidates["close"].notna() &
        candidates["ma20"].notna() &
        candidates["ma60"].notna() &
        (candidates["close"] > candidates["ma20"]) &
        (candidates["close"] > candidates["ma60"])
    )
    candidates["macd_buy_signal"] = (
        candidates["macd_golden_cross"] |
        ((candidates["macd"] > 0) & (candidates["macd"] > candidates["previous_macd"]))
    )
    candidates["turnover_active"] = candidates["turnover_rate"].between(8, 20)
    candidates["non_continuous_high_acceleration"] = candidates["previous_high_accel_days_2"].fillna(0) == 0
    candidates["volume_breakout"] = (
        candidates["volume_above_ma5_1_5"] &
        candidates["breakout_new_high"] &
        (candidates["pct_chg"] >= 3) &
        candidates["close_position_strong"] &
        candidates["non_continuous_high_acceleration"]
    )
    candidates["strong_consolidation"] = (
        (candidates["recent_limit_up_20"].fillna(0) >= 1) &
        (candidates["pullback_20"].fillna(-100) >= -8) &
        candidates["volume_contracting"].fillna(False) &
        candidates["ma20_upward"].fillna(False) &
        (candidates["close"] >= candidates["ma20"]) &
        candidates["close_top20_position"]
    )
    candidates["relative_strength_positive"] = candidates["sector_top10"]
    candidates["moderate_gain"] = candidates["pct_chg"] >= 3
    candidates["not_volume_spike"] = candidates["non_continuous_high_acceleration"]
    candidates["intraday_recover_strong"] = candidates["daily_tail_strength"]
    candidates["liquidity_active"] = candidates["amount_yuan"] >= 300_000_000
    candidates["recent_limit_up"] = candidates["recent_limit_up_20"].fillna(0) >= 1
    candidates["sector_amount_expanded"] = candidates["sector_amount_expand_rate"].notna() & (
        candidates["sector_amount_expand_rate"] >= 1.1
    )
    candidates["sector_leading"] = (
        candidates["sector_top10"] &
        (candidates["sector_avg_pct_chg"] >= 1) &
        (candidates["sector_limit_up_count"].fillna(0) >= 2) &
        candidates["sector_amount_expanded"] &
        (candidates["relative_strength_rank"] <= 5)
    )
    candidates["trend_upward"] = (
        candidates["ma_bullish"] &
        candidates["ma20_upward"].fillna(False) &
        candidates["ma30_not_fast_down"].fillna(False) &
        (candidates["close"] > candidates["ma20"])
    )

    if "open" in candidates.columns:
        open_price = pd.to_numeric(candidates["open"], errors="coerce")
        previous_close = candidates["close"] / (1 + candidates["pct_chg"] / 100)
        candidates["high_open_risk"] = previous_close.gt(0) & ((open_price / previous_close - 1) > 0.05)
    else:
        candidates["high_open_risk"] = False
    candidates["continuous_overheated"] = candidates["recent3_positive_return"].fillna(False)
    candidates["long_upper_shadow"] = candidates["upper_shadow_ratio"] >= 0.45
    candidates["surge_fade"] = candidates["long_upper_shadow"] & (candidates["close_position_ratio"] < 0.65)
    candidates["huge_volume"] = candidates["volume_expand_rate"] >= 3
    candidates["huge_volume_fade"] = candidates["huge_volume"] & candidates["surge_fade"]
    candidates["volume_down"] = (candidates["pct_chg"] < 0) & (candidates["volume_expand_rate"] > 1.2)
    candidates["shrink_breakout"] = candidates["breakout_new_high"] & ~candidates["volume_above_ma5"]
    candidates["platform_broken"] = candidates["close"] < candidates["low20"]
    candidates["risk_reject"] = (
        candidates["continuous_overheated"] |
        candidates["high_open_risk"] |
        candidates["long_upper_shadow"] |
        candidates["huge_volume_fade"] |
        candidates["platform_broken"] |
        (candidates["close"] < candidates["ma20"])
    )

    trend_score = (
        candidates["ma_bullish"].astype(int) * 8 +
        candidates["ma20_upward"].fillna(False).astype(int) * 5 +
        candidates["ma30_not_fast_down"].fillna(False).astype(int) * 3 +
        (candidates["close"] > candidates["ma20"]).astype(int) * 4
    )
    position_score = (
        candidates["first_platform_breakout"].astype(int) * 10 +
        candidates["strong_consolidation"].astype(int) * 6 +
        candidates["break_ma30"].astype(int) * 5 +
        candidates["break_ma60"].astype(int) * 5
    )
    position_score = (position_score - (candidates["recent5_return"] >= 15).astype(int) * 5).clip(lower=0, upper=20)
    volume_score = (
        candidates["volume_above_ma5"].astype(int) * 8 +
        candidates["volume_above_ma10_1_5"].astype(int) * 7
    )
    volume_score = (
        volume_score -
        candidates["volume_down"].astype(int) * 7 -
        candidates["shrink_breakout"].astype(int) * 5 -
        candidates["huge_volume_fade"].astype(int) * 8
    ).clip(lower=0, upper=15)
    kline_score = (
        candidates["first_platform_breakout"].astype(int) * 4 +
        candidates["box_breakout"].astype(int) * 3 +
        candidates["strong_consolidation"].astype(int) * 3 +
        candidates["bullish_engulfing"].fillna(False).astype(int) * 2 +
        candidates["red_three_soldiers"].fillna(False).astype(int) * 2 +
        candidates["launch_candle"].fillna(False).astype(int) * 2 +
        candidates["ma_bullish"].astype(int) * 2
    )
    kline_score = (
        kline_score -
        candidates["long_upper_shadow"].astype(int) * 5 -
        candidates["volume_down"].astype(int) * 5
    ).clip(lower=0, upper=15)
    tail_score = (
        candidates["close_position_strong"].astype(int) * 5 +
        candidates["close_top20_position"].astype(int) * 5
    )
    tail_score = (tail_score - candidates["surge_fade"].astype(int) * 5).clip(lower=0, upper=10)
    fund_score = (
        candidates["liquidity_active"].astype(int) * 4 +
        (candidates["volume_ratio"] >= 1.5).astype(int) * 3 +
        (candidates["amount_yuan"] >= 500_000_000).astype(int) * 3
    )
    fund_score = (fund_score - candidates["huge_volume_fade"].astype(int) * 4).clip(lower=0, upper=10)
    sector_score = (
        candidates["sector_top10"].astype(int) * 4 +
        (candidates["sector_avg_pct_chg"] > 0).astype(int) * 2 +
        (candidates["relative_strength_rank"] <= 5).astype(int) * 2 +
        candidates["sector_amount_expanded"].astype(int) * 2
    ).clip(lower=0, upper=10)

    candidates["trend_breakout_score"] = trend_score
    candidates["position_breakout_score"] = position_score
    candidates["volume_breakout_score"] = volume_score
    candidates["kline_breakout_score"] = kline_score
    candidates["tail_breakout_score"] = tail_score
    candidates["fund_breakout_score"] = fund_score
    candidates["sector_breakout_score"] = sector_score
    candidates["pre_cost_breakout_score"] = (
        trend_score + position_score + volume_score + kline_score + tail_score + fund_score + sector_score
    )
    candidates["main_cost_penalty"] = candidates["main_cost_score"] - 10
    candidates["overnight_premium_score"] = (
        candidates["pre_cost_breakout_score"] + candidates["main_cost_penalty"]
    ).clip(lower=0, upper=100)
    candidates["breakout_entry_threshold"] = 70
    candidates["daily_proxy_qualified"] = candidates["daily_tail_strength"]

    candidates = candidates[
        candidates["trend_upward"] &
        ~candidates["risk_reject"] &
        (candidates["overnight_premium_score"] >= candidates["breakout_entry_threshold"])
    ].copy()
    if candidates.empty:
        return candidates

    if hist_df is not None and not hist_df.empty and "ts_code" in hist_df.columns:
        breakout_codes = set(candidates["ts_code"].astype(str))
        confluence_hist = hist_df[hist_df["ts_code"].astype(str).isin(breakout_codes)].copy()
        confluence_stats = _build_breakout_confluence_stats(confluence_hist)
    else:
        confluence_stats = pd.DataFrame()
    if not confluence_stats.empty:
        candidates = pd.merge(candidates, confluence_stats, on="ts_code", how="left")
    for column in (
        "boll_width_expand", "close_near_boll_upper", "boll_breakout_ready",
        "weekly_trend_ok", "kdj_recent_golden_cross", "kdj_breakout_signal",
        "macd_zero_axis_ready", "macd_cross_ready",
    ):
        if column not in candidates.columns:
            candidates[column] = False
        candidates[column] = candidates[column].fillna(False)
    if "weekly_trend_state" not in candidates.columns:
        candidates["weekly_trend_state"] = "数据不足"
    candidates["weekly_trend_state"] = candidates["weekly_trend_state"].fillna("数据不足")
    if "breakout_confluence_score" not in candidates.columns:
        candidates["breakout_confluence_score"] = 0
    if "breakout_confluence_count" not in candidates.columns:
        candidates["breakout_confluence_count"] = 0
    candidates["breakout_confluence_score"] = pd.to_numeric(
        candidates["breakout_confluence_score"],
        errors="coerce",
    ).fillna(0)
    candidates["breakout_confluence_count"] = pd.to_numeric(
        candidates["breakout_confluence_count"],
        errors="coerce",
    ).fillna(0)

    result = candidates[
        candidates["weekly_trend_ok"] &
        candidates["boll_width_expand"] &
        candidates["macd_zero_axis_ready"]
    ].copy()
    if result.empty:
        return result
    result["breakout_score"] = (
        result["overnight_premium_score"] + result["breakout_confluence_score"]
    ).clip(lower=0, upper=100)
    result["score"] = result["breakout_score"]
    result["trade_state"] = result.apply(
        lambda row: _trade_state(row["breakout_score"], row["main_cost_distance_pct"]),
        axis=1,
    )
    result["breakout_status"] = result["trade_state"]
    result["main_cost_label"] = result.apply(_build_main_cost_label, axis=1)
    result = _attach_trend_stage(result)
    result["breakout_reason"] = result.apply(_build_breakout_reason, axis=1)

    return result.sort_values(
        [
            "breakout_confluence_score",
            "breakout_confluence_count",
            "breakout_score",
            "sector_rank",
            "relative_strength_rank",
            "amount_yuan",
            "volume_expand_rate",
        ],
        ascending=[False, False, False, True, True, False, False],
    )


def _limit_threshold_for_code(ts_code: str) -> float:
    code = str(ts_code)
    if code.startswith(("300", "301", "688")):
        return 19.0
    return 9.5


def _build_first_limit_history_stats(hist_df: pd.DataFrame) -> pd.DataFrame:
    hist = hist_df.copy()
    numeric_cols = ["pct_chg", "vol"]
    for col in numeric_cols:
        if col in hist.columns:
            hist[col] = pd.to_numeric(hist[col], errors="coerce")
    hist = hist.dropna(subset=["ts_code", "trade_date", "pct_chg"])
    hist = hist.sort_values(["ts_code", "trade_date"])

    rows = []
    for ts_code, group in hist.groupby("ts_code"):
        group = group.tail(6)
        if group.empty:
            continue
        threshold = _limit_threshold_for_code(ts_code)
        pct_chg = group["pct_chg"]
        previous = pct_chg.iloc[:-1]
        rows.append({
            "ts_code": ts_code,
            "recent_limit_days": int((previous >= threshold).sum()) if not previous.empty else 0,
            "yesterday_limit_up": bool(previous.iloc[-1] >= threshold) if not previous.empty else False,
        })

    return pd.DataFrame(rows)


def _build_first_limit_reason(row):
    labels = [
        ("core_inflow_sector", "核心流入板块"),
        ("sector_leader_rank_ok", "板块龙头"),
        ("leader_market_cap_ok", "市值>=50亿"),
        ("kdj_after_golden_cross", "KDJ金叉后"),
        ("macd_after_golden_cross", "MACD金叉后"),
        ("trend_upward", "趋势多头向上"),
        ("ma_up_confirmed", "均线多头向上"),
        ("pullback_key_support", "回踩不破关键位"),
        ("breakout_hold", "突破后未跌回"),
        ("volume_price_confirmed", "上涨放量回踩缩量"),
        ("higher_lows_5d", "低点抬高"),
        ("main_cost_rising_confirmed", "主力成本上移"),
        ("sector_not_weak", "板块不弱"),
    ]
    reasons = [label for field, label in labels if row.get(field)]
    count = int(row.get("main_wave_confirmation_count", 0))
    return f"主升浪启动({count}/7)-" + "+".join(reasons)


def pick_first_limit_stocks(
    df: pd.DataFrame,
    hist_df: pd.DataFrame | None = None,
    core_inflow_sectors: list[str] | None = None,
    history_stats: pd.DataFrame | None = None,
):
    """主升浪启动池：7 项确认中至少满足 5 项。"""
    df = clean_data(df)
    if "volume_ratio" not in df.columns:
        df["volume_ratio"] = 0

    base = pick_strong_base_candidates(df)
    if base.empty:
        return base.iloc[0:0].copy()

    if history_stats is not None:
        stats = history_stats
    elif hist_df is None or hist_df.empty:
        return base.iloc[0:0].copy()
    else:
        stats = _build_strong_history_stats(hist_df)
    if stats.empty:
        return base.iloc[0:0].copy()

    candidates = pd.merge(base, stats, on="ts_code", how="inner")
    if candidates.empty:
        return candidates

    candidates = _ensure_amount_yuan(candidates)
    candidates = _ensure_total_mv_yuan(candidates)
    candidates = _attach_sector_context(candidates, df)
    core_inflow_set = {str(item).strip() for item in (core_inflow_sectors or []) if str(item).strip()}
    candidates["core_inflow_sector"] = _matches_core_inflow_sector(candidates, core_inflow_set)
    candidates["limit_threshold"] = candidates["ts_code"].map(_limit_threshold_for_code)
    candidates["today_limit_up"] = candidates["pct_chg"] >= candidates["limit_threshold"]
    candidates["amount_active"] = candidates["amount_yuan"] > 200_000_000
    candidates["leader_market_cap_ok"] = candidates["total_mv_yuan"] >= 5_000_000_000
    sector_group = candidates["industry"].fillna("__UNKNOWN__")
    candidates["core_sector_amount_rank"] = (
        candidates.groupby(sector_group, dropna=False)["amount_yuan"]
        .rank(method="min", ascending=False)
    )
    candidates["core_sector_mv_rank"] = (
        candidates.groupby(sector_group, dropna=False)["total_mv_yuan"]
        .rank(method="min", ascending=False)
    )
    candidates["sector_leader_rank_ok"] = (
        (candidates["relative_strength_rank"].notna() & (candidates["relative_strength_rank"] <= 5)) |
        (candidates["core_sector_amount_rank"].notna() & (candidates["core_sector_amount_rank"] <= 10)) |
        (candidates["core_sector_mv_rank"].notna() & (candidates["core_sector_mv_rank"] <= 10))
    )
    candidates["turnover_active"] = candidates["turnover_rate"] >= 5
    candidates["volume_ratio_active"] = candidates["volume_ratio"] >= 1.5
    candidates["ma_up_confirmed"] = (
        candidates["ma5"].notna() &
        candidates["ma10"].notna() &
        candidates["ma20"].notna() &
        (candidates["ma5"] > candidates["ma10"]) &
        (candidates["ma10"] > candidates["ma20"]) &
        candidates["ma5_upward"].fillna(False) &
        candidates["ma10_upward"].fillna(False) &
        candidates["ma20_upward"].fillna(False)
    )
    candidates["pullback_key_support"] = (
        (candidates["close"] > candidates["ma20"]) &
        candidates["pullback_not_break_key"].fillna(False)
    )
    candidates["volume_price_confirmed"] = (
        (candidates["volume_expand_rate"] >= 1.2) &
        candidates["pullback_volume_contracting"].fillna(False)
    )
    candidates["main_cost_rising_confirmed"] = (
        candidates["main_cost_upward"].fillna(False) &
        candidates["price_above_main_cost"].fillna(False)
    )
    candidates["sector_not_weak"] = (
        candidates["sector_avg_pct_chg"].fillna(0) >= 0
    )
    candidates["kdj_after_golden_cross"] = (
        candidates["kdj_k"].notna() &
        candidates["kdj_d"].notna() &
        (candidates["kdj_k"] > candidates["kdj_d"])
    )
    candidates["macd_after_golden_cross"] = candidates["macd_golden_cross"].fillna(False)
    candidates["trend_upward"] = (
        candidates["ma_up_confirmed"] &
        candidates["ma30_not_fast_down"].fillna(False) &
        (candidates["close"] > candidates["ma20"])
    )
    candidates["core_leader_technical_start"] = (
        candidates["core_inflow_sector"] &
        candidates["sector_leader_rank_ok"] &
        candidates["leader_market_cap_ok"] &
        candidates["kdj_after_golden_cross"] &
        candidates["macd_after_golden_cross"] &
        candidates["trend_upward"]
    )
    confirmation_fields = [
        "ma_up_confirmed",
        "pullback_key_support",
        "breakout_hold",
        "volume_price_confirmed",
        "higher_lows_5d",
        "main_cost_rising_confirmed",
        "sector_not_weak",
    ]
    candidates["main_wave_confirmation_count"] = candidates[confirmation_fields].astype(int).sum(axis=1)
    candidates["main_wave_start"] = (
        (candidates["main_wave_confirmation_count"] >= 5) |
        candidates["core_leader_technical_start"]
    )
    candidates["first_limit"] = candidates["main_wave_start"]

    result = candidates[
        candidates["main_wave_start"] &
        candidates["amount_active"] &
        candidates["turnover_active"] &
        candidates["core_inflow_sector"] &
        candidates["sector_leader_rank_ok"] &
        candidates["leader_market_cap_ok"] &
        candidates["kdj_after_golden_cross"] &
        candidates["macd_after_golden_cross"] &
        candidates["trend_upward"]
    ].copy()
    if result.empty:
        return result

    score_weights = {
        "ma_up_confirmed": 16,
        "pullback_key_support": 14,
        "breakout_hold": 14,
        "volume_price_confirmed": 14,
        "higher_lows_5d": 14,
        "main_cost_rising_confirmed": 14,
        "sector_not_weak": 8,
        "core_inflow_sector": 8,
        "sector_leader_rank_ok": 8,
        "leader_market_cap_ok": 8,
        "kdj_after_golden_cross": 8,
        "macd_after_golden_cross": 8,
        "trend_upward": 8,
        "amount_active": 3,
        "turnover_active": 3,
    }
    score_columns = []
    for field, weight in score_weights.items():
        score_column = f"{field}_score"
        result[score_column] = result[field].astype(int) * weight
        score_columns.append(score_column)
    result["first_limit_score"] = result[score_columns].sum(axis=1)
    result["score"] = result["first_limit_score"]
    result["trend_stage"] = "S4"
    result["stage_label"] = "二次启动阶段"
    result["stage_action"] = "加仓"
    result["stage_reason"] = "主升浪启动确认，等待放量延续"
    result["first_limit_reason"] = result.apply(_build_first_limit_reason, axis=1)

    return result.sort_values(
        ["first_limit_score", "main_wave_confirmation_count", "amount_yuan", "turnover_rate", "volume_ratio"],
        ascending=[False, False, False, False, False],
    )


def select_stock_pools(
    df: pd.DataFrame,
    hist_df: pd.DataFrame | None = None,
    core_inflow_sectors: list[str] | None = None,
):
    base_codes = set()
    base = pick_strong_base_candidates(clean_data(df))
    if not base.empty and "ts_code" in base.columns:
        base_codes = set(base["ts_code"].astype(str))

    filtered_hist = hist_df
    if base_codes and hist_df is not None and not hist_df.empty and "ts_code" in hist_df.columns:
        filtered_hist = hist_df[hist_df["ts_code"].astype(str).isin(base_codes)].copy()

    history_stats = None
    if filtered_hist is not None and not filtered_hist.empty:
        history_stats = _build_strong_history_stats(filtered_hist)
    return {
        "reversal": pick_stocks(df, filtered_hist, history_stats=history_stats),
        "breakout": pick_breakout_stocks(df, filtered_hist, history_stats=history_stats),
        "first_limit": pick_first_limit_stocks(df, filtered_hist, core_inflow_sectors=core_inflow_sectors, history_stats=history_stats),
    }


def _build_dip_history_stats(hist_df: pd.DataFrame) -> pd.DataFrame:
    hist = hist_df.copy()
    numeric_cols = ["pct_chg", "vol", "close"]
    for col in numeric_cols:
        hist[col] = pd.to_numeric(hist[col], errors="coerce")
    hist = hist.dropna(subset=["ts_code", "trade_date", *numeric_cols])
    hist = hist.sort_values(["ts_code", "trade_date"])

    rows = []
    for ts_code, group in hist.groupby("ts_code"):
        if len(group) < 10:
            continue

        group = group.tail(40)
        close = group["close"]
        vol = group["vol"]
        pct_chg = group["pct_chg"]

        first_close = close.iloc[0]
        last_close = close.iloc[-1]
        ma20 = close.tail(20).mean() if len(close) >= 20 else close.mean()
        ma40 = close.tail(40).mean() if len(close) >= 40 else close.mean()
        recent_high = close.max()
        recent_low = close.min()
        recent_return = (last_close / first_close - 1) * 100 if first_close else 0
        high_drawdown = (last_close / recent_high - 1) * 100 if recent_high else 0

        last5_close = close.tail(5)
        last5_vol = vol.tail(5)
        prev20_vol = vol.tail(20)

        # 地量：最近成交量明显低于20日均量，且最近几天量能持续收缩。
        volume_shrink_rate = (
            last5_vol.iloc[-1] / prev20_vol.mean()
            if len(prev20_vol) >= 5 and prev20_vol.mean()
            else 1
        )
        decreasing_volume_days = int(last5_vol.diff().dropna().lt(0).sum())

        # 地价：价格处于近40日低位，且最近几天没有明显拉升。
        low_position_ratio = (last_close - recent_low) / (recent_high - recent_low) if recent_high > recent_low else 0
        close_down_days = int(last5_close.diff().dropna().lt(0).sum())

        ground_volume_price = (
            volume_shrink_rate <= 0.70 and
            decreasing_volume_days >= 3 and
            low_position_ratio <= 0.35 and
            close_down_days >= 2
        )

        # 前强/龙头：历史上出现过强势阳线或连续强势表现。
        former_strong = (
            pct_chg.max() >= 7 or
            pct_chg.tail(20).nlargest(min(3, len(pct_chg.tail(20)))).sum() >= 15
        )

        # 低位震荡守均线：从高点回撤明显，近5天围绕20/40日线但没有有效跌破。
        recent_support_ma20 = bool((last5_close >= ma20 * 0.98).all())
        recent_support_ma40 = bool((last5_close >= ma40 * 0.98).all())
        support_line = "MA20" if recent_support_ma20 else ("MA40" if recent_support_ma40 else "")
        recent_consolidation = (
            len(last5_close) >= 5 and
            ((last5_close.max() / last5_close.min() - 1) * 100 <= 8 if last5_close.min() else False)
        )
        former_strong_consolidation = (
            former_strong and
            high_drawdown <= -8 and
            recent_consolidation and
            (recent_support_ma20 or recent_support_ma40)
        )

        rows.append({
            "ts_code": ts_code,
            "recent_20_return": recent_return,
            "hist_days": len(group),
            "ma20": ma20,
            "ma40": ma40,
            "recent_low": recent_low,
            "recent_high": recent_high,
            "high_drawdown": high_drawdown,
            "low_position_ratio": low_position_ratio,
            "volume_shrink_rate": volume_shrink_rate,
            "decreasing_volume_days": decreasing_volume_days,
            "close_down_days": close_down_days,
            "ground_volume_price": ground_volume_price,
            "former_strong": former_strong,
            "recent_support_ma20": recent_support_ma20,
            "recent_support_ma40": recent_support_ma40,
            "support_line": support_line,
            "recent_consolidation": recent_consolidation,
            "former_strong_consolidation": former_strong_consolidation,
        })

    return pd.DataFrame(rows)


def _build_single_day_dip_fallback(df, condition_mv, condition_price, condition_st, reason):
    base = df[
        (df["pct_chg"] < -3) &
        (df["turnover_rate"] >= 2.5) &
        (df["vol"] >= 800_000) &
        condition_mv &
        condition_price &
        condition_st
    ].copy()
    result = base.copy()
    result["recent_20_return"] = result["pct_chg"]
    result["hist_days"] = 1
    result["ma20"] = result["close"]
    result["ma40"] = result["close"]
    result["recent_low"] = result["close"]
    result["recent_high"] = result["close"]
    result["high_drawdown"] = 0
    result["low_position_ratio"] = 0
    result["volume_shrink_rate"] = result["volume_ratio"]
    result["decreasing_volume_days"] = 0
    result["close_down_days"] = 1
    result["ground_volume_price"] = False
    result["former_strong"] = False
    result["recent_support_ma20"] = True
    result["recent_support_ma40"] = True
    result["support_line"] = "单日"
    result["recent_consolidation"] = False
    result["former_strong_consolidation"] = False
    result["fund_active"] = True
    result["dip_reason"] = reason
    result["dip_score"] = (
        result["pct_chg"].abs() * 0.4 +
        result["turnover_rate"] * 0.3 +
        result["volume_ratio"] * 0.3
    )
    return result.sort_values("dip_score", ascending=False)


def _build_dip_reason(row):
    reasons = []
    if row.get("ground_volume_price"):
        reasons.append("地量地价")
    if row.get("former_strong_pullback"):
        reasons.append("前强回调")
    if row.get("bottom_base"):
        reasons.append("低位缩量")
    if row.get("active_reversal"):
        reasons.append("资金回流")
    if row.get("near_recent_low"):
        reasons.append("近阶段低位")

    if not reasons:
        reasons.append("低位观察")

    support_line = row.get("support_line")
    if support_line:
        reasons[-1] = f"{reasons[-1]}({support_line})"

    return "+".join(reasons)


def pick_dip_stocks(df: pd.DataFrame, hist_df: pd.DataFrame | None = None):
    """
    抄底候选股：
    - 地量地价：多日缩量，价格处于低位并走低
    - 前强回调：之前有强势表现，现在低位震荡且不有效跌破20日均线
    - 非ST
    """
    df = clean_data(df)
    if "volume_ratio" not in df.columns:
        df["volume_ratio"] = 0

    condition_mv = df["total_mv"] < 3_000_000
    condition_price = df["close"] > 3
    condition_fund_active = (df["turnover_rate"] >= 1.2) & (df["vol"] >= 300_000)

    if "name" in df.columns:
        condition_st = ~df["name"].str.contains("ST", na=False)
    else:
        condition_st = True

    if hist_df is None or hist_df.empty:
        return _build_single_day_dip_fallback(
            df,
            condition_mv,
            condition_price,
            condition_st,
            "历史数据不足-单日回调候选",
        )

    stats = _build_dip_history_stats(hist_df)
    if stats.empty:
        return _build_single_day_dip_fallback(
            df,
            condition_mv,
            condition_price,
            condition_st,
            "历史统计不足-单日回调候选",
        )

    base = df[
        condition_mv &
        condition_price &
        condition_fund_active &
        condition_st
    ].copy()

    candidates = pd.merge(base, stats, on="ts_code", how="inner")

    if candidates.empty:
        return candidates

    candidates["near_recent_low"] = candidates["low_position_ratio"] <= 0.45
    candidates["meaningful_drawdown"] = candidates["high_drawdown"] <= -5
    candidates["volume_cooling"] = (
        (candidates["volume_shrink_rate"] <= 1.05) |
        (candidates["decreasing_volume_days"] >= 2)
    )
    candidates["support_or_low"] = (
        candidates["recent_support_ma20"] |
        candidates["recent_support_ma40"] |
        (
            candidates["near_recent_low"] &
            (candidates["close"] <= candidates["recent_low"] * 1.08)
        )
    )
    candidates["bottom_base"] = (
        candidates["near_recent_low"] &
        candidates["meaningful_drawdown"] &
        candidates["volume_cooling"]
    )
    candidates["former_strong_pullback"] = (
        candidates["former_strong"] &
        (candidates["high_drawdown"] <= -6) &
        (candidates["low_position_ratio"] <= 0.55)
    )
    candidates["active_reversal"] = (
        candidates["pct_chg"].between(-5, 4) &
        (candidates["turnover_rate"] >= 1.8) &
        (candidates["volume_ratio"] >= 0.7) &
        (candidates["high_drawdown"] <= -7) &
        (candidates["low_position_ratio"] <= 0.6)
    )
    candidates["fund_active"] = True
    candidates["dip_score"] = (
        candidates["high_drawdown"].abs().clip(upper=35) * 0.35 +
        (1 - candidates["low_position_ratio"]).clip(lower=0, upper=1) * 28 +
        (1 - candidates["volume_shrink_rate"]).clip(lower=-0.5, upper=1) * 16 +
        candidates["decreasing_volume_days"] * 2 +
        candidates["former_strong"].astype(int) * 7 +
        candidates["bottom_base"].astype(int) * 6 +
        candidates["active_reversal"].astype(int) * 5 +
        candidates["recent_support_ma20"].astype(int) * 8 +
        candidates["recent_support_ma40"].astype(int) * 5 +
        (candidates["turnover_rate"] / 5).clip(upper=4)
    )
    candidates["dip_reason"] = candidates.apply(_build_dip_reason, axis=1)

    result = candidates[
        candidates["support_or_low"] &
        (
            candidates["ground_volume_price"] |
            candidates["former_strong_consolidation"] |
            candidates["bottom_base"] |
            candidates["former_strong_pullback"] |
            candidates["active_reversal"]
        )
    ].copy()

    min_candidates = 20
    if len(result) < min_candidates:
        relaxed = candidates[
            candidates["support_or_low"] &
            candidates["meaningful_drawdown"] &
            (candidates["low_position_ratio"] <= 0.70) &
            (candidates["pct_chg"] <= 5)
        ].copy()
        relaxed = relaxed[~relaxed["ts_code"].isin(result["ts_code"])]
        result = pd.concat(
            [result, relaxed.sort_values("dip_score", ascending=False).head(min_candidates - len(result))],
            ignore_index=True,
        )

    return result.sort_values("dip_score", ascending=False)


def pick_dip_sectors(sector_df: pd.DataFrame, stock_merged: pd.DataFrame, top_n=5):
    """
    强势板块：
    - 板块平均涨幅靠前
    - 板块内股票数量 >= 3（避免单股拉偏）
    - 返回板块名 + 板块内涨幅靠前且换手较高的代表股
    """
    if sector_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    sector_df = sector_df.copy()
    sector_df["avg_pct_chg"] = pd.to_numeric(sector_df["avg_pct_chg"], errors="coerce")
    sector_df["stock_count"] = pd.to_numeric(sector_df["stock_count"], errors="coerce")
    if "max_pct_chg" in sector_df.columns:
        sector_df["max_pct_chg"] = pd.to_numeric(sector_df["max_pct_chg"], errors="coerce")
    else:
        sector_df["max_pct_chg"] = sector_df["avg_pct_chg"]
    if "up_ratio" not in sector_df.columns:
        sector_df["up_ratio"] = 0
    sector_df["up_ratio"] = pd.to_numeric(sector_df["up_ratio"], errors="coerce").fillna(0)
    sector_df["sector_score"] = (
        sector_df["avg_pct_chg"].fillna(0) * 18 +
        sector_df["max_pct_chg"].fillna(0).clip(upper=10) * 3 +
        sector_df["up_ratio"] * 20
    )

    strong_sectors = sector_df[
        (sector_df["avg_pct_chg"] > 0) &
        (sector_df["stock_count"] >= 3)
    ].sort_values(
        ["sector_score", "avg_pct_chg", "max_pct_chg"],
        ascending=[False, False, False],
    ).head(top_n).copy()
    strong_sectors["sector_rank"] = range(1, len(strong_sectors) + 1)

    if strong_sectors.empty:
        return strong_sectors, pd.DataFrame()

    # 从 stock_merged 中找各板块代表股（涨幅靠前 + 换手活跃）
    stock_merged = stock_merged.copy()
    stock_merged["pct_chg"] = pd.to_numeric(stock_merged["pct_chg"], errors="coerce")
    stock_merged["turnover_rate"] = pd.to_numeric(stock_merged.get("turnover_rate", pd.Series()), errors="coerce")

    rep_stocks = []
    for industry in strong_sectors["industry_name"]:
        subset = stock_merged[stock_merged["industry"] == industry].copy()
        subset = subset[subset["pct_chg"] > 0]
        if "turnover_rate" in subset.columns:
            subset = subset[(subset["turnover_rate"].isna()) | (subset["turnover_rate"] >= 3)]
        subset = subset.sort_values(["pct_chg", "turnover_rate"], ascending=[False, False]).head(3)
        rep_stocks.append(subset)

    if rep_stocks:
        rep_df = pd.concat(rep_stocks, ignore_index=True)
    else:
        rep_df = pd.DataFrame()

    return strong_sectors, rep_df


def pick_sector_tail_buy_stocks(
    market_df: pd.DataFrame,
    hist_df: pd.DataFrame | None,
    rep_stocks: pd.DataFrame,
    breakout_pool: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """从强势板块代表股中筛选符合尾盘买入模型的股票。"""
    if market_df.empty or rep_stocks.empty or hist_df is None or hist_df.empty:
        return pd.DataFrame()

    rep_codes = set(rep_stocks["ts_code"].astype(str)) if "ts_code" in rep_stocks.columns else set()
    if not rep_codes:
        return pd.DataFrame()

    breakout = breakout_pool if breakout_pool is not None else pick_breakout_stocks(market_df, hist_df)
    if breakout.empty:
        return breakout

    return breakout[breakout["ts_code"].astype(str).isin(rep_codes)].copy()


def format_for_ai(df: pd.DataFrame, label="优势股", limit=10):
    df = df.head(limit)
    wanted_cols = [
        "ts_code", "name", "close", "pct_chg", "turnover_rate", "vol",
        "volume_ratio", "total_mv", "total_mv_yuan", "industry", "concept", "strong_reason",
        "trend_stage", "stage_label", "stage_action", "stage_reason",
        "sector_avg_pct_chg", "rs_industry", "rs_industry_penalty",
        "relative_strength_rank",
        "sector_up_ratio", "sector_strong_count", "sector_rank", "hist_days", "ma5", "ma10",
        "ma20", "ma30", "ma40", "ma60", "previous_ma60", "ma60_slope",
        "ma60_20days_ago", "ma60_40days_ago", "ma60_slope_20", "ma60_slope_prev20",
        "ma60_trend", "ma60_decline_slowing", "ma60_improving",
        "recent_60_return", "recent_low_60", "recent_high_60", "high_drawdown_60",
        "bottom_position_60", "bottom_position_qualified", "rebound_volume_confirmed",
        "washout_volume_ratio", "washout_volume_shrink", "trend_repairing",
        "low20", "high20", "strength20", "strength20_score",
        "ret60", "amount_yuan", "previous_high_20", "vol_ma5",
        "volume_expand_rate", "macd_dif", "macd_dea", "macd", "previous_macd", "rsi6",
        "kdj_k", "kdj_d", "kdj_j", "previous_kdj_k", "previous_kdj_d", "previous_kdj_j",
        "reversal_indicator_count", "ma60_above", "ma60_up", "ma60_flat", "rsi6_below_75",
        "kdj_j_below_90", "kdj_golden_cross", "macd_above_zero", "macd_trending_up",
        "macd_trend_or_above_zero", "volume_above_ma5_1_5",
        "in_bottom_area", "ret60_oversold", "volume_price_rise", "turnover_active",
        "volume_ratio_active", "close_above_ma20", "macd_golden_cross", "hot_theme",
        "in_bottom_area_score", "ret60_oversold_score", "volume_price_rise_score",
        "turnover_active_score", "volume_ratio_active_score", "close_above_ma20_score",
        "macd_golden_cross_score", "hot_theme_score", "recent_20_return",
        "support_line", "high_drawdown", "volume_shrink_rate", "dip_reason",
        "breakout_reason", "breakout_score", "breakout_status", "trade_state",
        "breakout_new_high", "trend_position_strong",
        "close_near_high", "intraday_recover_strong", "moderate_gain",
        "not_volume_spike", "overnight_premium_score", "volume_breakout",
        "boll_width", "boll_width_expand", "close_near_boll_upper", "boll_breakout_ready",
        "weekly_trend_state", "weekly_trend_ok", "kdj_recent_golden_cross",
        "kdj_breakout_signal", "macd_zero_axis_ready", "macd_cross_ready",
        "breakout_confluence_count", "breakout_confluence_score",
        "sector_top10", "close_position_strong", "tail_pct_chg", "tail_price_rise",
        "tail_amount_ratio", "tail_amount_active", "volume_above_ma5_1_8",
        "ma_bullish", "macd_buy_signal", "tail_data_missing", "daily_proxy_qualified",
        "breakout_entry_threshold", "relative_strength_positive", "main_cost_low",
        "main_cost_high", "main_cost_vwap", "main_cost_days", "main_cost_range_pct",
        "main_cost_distance_pct", "main_cost_score", "main_cost_penalty", "main_cost_label",
        "pre_cost_breakout_score", "trend_breakout_score",
        "position_breakout_score", "volume_breakout_score", "kline_breakout_score",
        "tail_breakout_score", "fund_breakout_score", "sector_breakout_score",
        "risk_reject", "continuous_overheated", "long_upper_shadow", "surge_fade",
        "first_limit_reason",
        "first_limit_score", "main_wave_start", "main_wave_confirmation_count",
        "ma_up_confirmed", "pullback_key_support", "breakout_hold",
        "volume_price_confirmed", "higher_lows_5d", "main_cost_rising_confirmed",
        "sector_not_weak", "core_inflow_sector", "sector_leader_rank_ok",
        "core_sector_amount_rank", "core_sector_mv_rank",
        "leader_market_cap_ok", "kdj_after_golden_cross", "macd_after_golden_cross",
        "trend_upward", "core_leader_technical_start", "today_limit_up", "first_limit", "recent_limit_days",
        "amount_active", "score", "dip_score"
    ]
    cols = [c for c in wanted_cols if c in df.columns]
    header = f"【{label}】共 {len(df)} 只\n"
    return header + df[cols].to_string(index=False)


def format_sectors_for_ai(dip_sectors: pd.DataFrame, rep_stocks: pd.DataFrame):
    if dip_sectors.empty:
        return "【强势板块】无符合条件的板块。"

    wanted_sector_cols = ["sector_rank", "industry_name", "avg_pct_chg", "max_pct_chg", "stock_count", "sector_score"]
    sector_cols = [col for col in wanted_sector_cols if col in dip_sectors.columns]
    sector_str = dip_sectors[sector_cols].to_string(index=False)

    if rep_stocks.empty:
        stock_str = "（无符合尾盘买入模型的代表股）"
    else:
        wanted = ["ts_code", "name", "industry", "close", "pct_chg", "turnover_rate"]
        cols = [c for c in wanted if c in rep_stocks.columns]
        stock_str = rep_stocks[cols].to_string(index=False)

    return f"【强势板块】\n{sector_str}\n\n【板块尾盘候选股】\n{stock_str}"
