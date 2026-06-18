import pandas as pd


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
    return code.str.endswith((".SH", ".SZ")) & ~code.str.startswith("3")


def _build_sector_strength(df: pd.DataFrame) -> pd.DataFrame:
    data = clean_data(df)
    if "industry" not in data.columns:
        return pd.DataFrame()

    data = data[_is_mainboard_a_stock(data["ts_code"])].copy()
    data = data.dropna(subset=["industry", "pct_chg"])
    if data.empty:
        return pd.DataFrame()

    grouped = (
        data.groupby("industry")
        .agg(
            avg_pct_chg=("pct_chg", "mean"),
            stock_count=("ts_code", "count"),
            up_count=("pct_chg", lambda value: int((value > 0).sum())),
            strong_count=("pct_chg", lambda value: int((value >= 3).sum())),
            max_pct_chg=("pct_chg", "max"),
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
    for ts_code, group in hist.groupby("ts_code"):
        if len(group) < 61:
            continue

        group = group.tail(61)
        close_full = group["close"]
        current_window = group.tail(60)
        close = current_window["close"]
        vol = current_window["vol"]
        high = current_window["high"].fillna(close)
        low = current_window["low"].fillna(close)

        last_close = close.iloc[-1]
        ma20 = close.tail(20).mean()
        recent_low_60 = low.min()
        low20 = low.tail(20).min()
        high20 = high.tail(20).max()
        strength20_range = high20 - low20
        strength20 = (last_close - low20) / strength20_range if strength20_range > 0 else 0
        strength20 = max(0, min(float(strength20), 1))
        previous_high_20 = high.iloc[-21:-1].max()
        vol_ma5 = vol.iloc[-6:-1].mean()
        volume_expand_rate = vol.iloc[-1] / vol_ma5 if pd.notna(vol_ma5) and vol_ma5 > 0 else 0
        ret60 = (last_close / close_full.iloc[0] - 1) * 100 if close_full.iloc[0] else 0

        ema12 = close_full.ewm(span=12, adjust=False).mean()
        ema26 = close_full.ewm(span=26, adjust=False).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9, adjust=False).mean()
        macd = (dif - dea) * 2

        rows.append({
            "ts_code": ts_code,
            "hist_days": len(group),
            "ma20": ma20,
            "recent_low_60": recent_low_60,
            "low20": low20,
            "high20": high20,
            "strength20": strength20,
            "ret60": ret60,
            "previous_high_20": previous_high_20,
            "vol_ma5": vol_ma5,
            "volume_expand_rate": volume_expand_rate,
            "macd_dif": dif.iloc[-1],
            "macd_dea": dea.iloc[-1],
            "macd": macd.iloc[-1],
            "previous_macd": macd.iloc[-2],
            "macd_golden_cross": bool(dif.iloc[-1] > dea.iloc[-1]),
        })

    return pd.DataFrame(rows)


def _build_strong_reason(row):
    labels = [
        ("in_bottom_area", "底部区域"),
        ("ret60_oversold", "60日跌幅超30%"),
        ("volume_price_rise", "放量上涨"),
        ("turnover_active", "换手率>8%"),
        ("volume_ratio_active", "量比>2"),
        ("close_above_ma20", "站上20日线"),
        ("macd_golden_cross", "MACD金叉"),
        ("hot_theme", "热点行业"),
    ]
    reasons = [label for field, label in labels if row.get(field)]
    return "+".join(reasons) if reasons else "暂无评分项命中"


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


def pick_stocks(df: pd.DataFrame, hist_df: pd.DataFrame | None = None):
    """按超跌反转 100 分模型计算优势股评分。"""
    df = clean_data(df)
    if "volume_ratio" not in df.columns:
        df["volume_ratio"] = 0

    base = pick_strong_base_candidates(df)
    if base.empty:
        return base

    if hist_df is None or hist_df.empty:
        return base.iloc[0:0].copy()
    else:
        stats = _build_strong_history_stats(hist_df)
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
        (candidates["ret60"] < -20) &
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

    candidates["in_bottom_area"] = candidates["close"] <= candidates["recent_low_60"] * 1.20
    candidates["ret60_oversold"] = candidates["ret60"] < -30
    candidates["volume_price_rise"] = (
        (candidates["pct_chg"] > 0) &
        (candidates["volume_expand_rate"] >= 1.5)
    )
    candidates["turnover_active"] = candidates["turnover_rate"] > 8
    candidates["volume_ratio_active"] = candidates["volume_ratio"] > 2
    candidates["close_above_ma20"] = candidates["close"] > candidates["ma20"]
    candidates["volume_platform_breakout"] = (
        (candidates["close"] > candidates["previous_high_20"]) &
        (candidates["volume_expand_rate"] >= 1.5)
    )
    candidates["hot_theme"] = candidates["industry"].isin(hot_industries)

    score_weights = {
        "in_bottom_area": 25,
        "ret60_oversold": 20,
        "volume_price_rise": 15,
        "turnover_active": 10,
        "volume_ratio_active": 10,
        "close_above_ma20": 10,
        "macd_golden_cross": 5,
        "hot_theme": 5,
    }
    score_columns = []
    for field, weight in score_weights.items():
        score_column = f"{field}_score"
        candidates[score_column] = candidates[field].astype(int) * weight
        score_columns.append(score_column)
    candidates["strength20_score"] = (candidates["strength20"].fillna(0).clip(lower=0, upper=1) * 10).round().astype(int)
    candidates["score"] = (
        candidates[score_columns].sum(axis=1) +
        candidates["strength20_score"] +
        candidates["rs_industry_penalty"]
    )
    candidates["strong_reason"] = candidates.apply(_build_strong_reason, axis=1)

    sort_columns = [
        "score",
        "in_bottom_area",
        "ret60_oversold",
        "volume_price_rise",
        "strength20_score",
        "turnover_rate",
        "volume_ratio",
        "volume_expand_rate",
        "relative_strength_rank",
    ]
    return candidates.sort_values(
        sort_columns,
        ascending=[False, False, False, False, False, False, False, False, True],
    )


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
    抄底板块：
    - 板块平均跌幅 < -2%
    - 板块内股票数量 >= 3（避免单股拉偏）
    - 返回板块名 + 板块内跌幅最深且换手较高的代表股
    """
    if sector_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    sector_df = sector_df.copy()
    sector_df["avg_pct_chg"] = pd.to_numeric(sector_df["avg_pct_chg"], errors="coerce")

    dip_sectors = sector_df[
        (sector_df["avg_pct_chg"] < -2) &
        (sector_df["stock_count"] >= 3)
    ].sort_values("avg_pct_chg").head(top_n)

    if dip_sectors.empty:
        return dip_sectors, pd.DataFrame()

    # 从 stock_merged 中找各板块代表股（跌幅最深 + 换手 > 2%）
    stock_merged = stock_merged.copy()
    stock_merged["pct_chg"] = pd.to_numeric(stock_merged["pct_chg"], errors="coerce")
    stock_merged["turnover_rate"] = pd.to_numeric(stock_merged.get("turnover_rate", pd.Series()), errors="coerce")

    rep_stocks = []
    for industry in dip_sectors["industry_name"]:
        subset = stock_merged[stock_merged["industry"] == industry].copy()
        subset = subset[subset["pct_chg"] < 0]
        if "turnover_rate" in subset.columns:
            subset = subset[subset["turnover_rate"] > 2]
        subset = subset.sort_values("pct_chg").head(3)
        rep_stocks.append(subset)

    if rep_stocks:
        rep_df = pd.concat(rep_stocks, ignore_index=True)
    else:
        rep_df = pd.DataFrame()

    return dip_sectors, rep_df


def format_for_ai(df: pd.DataFrame, label="优势股", limit=10):
    df = df.head(limit)
    wanted_cols = [
        "ts_code", "name", "close", "pct_chg", "turnover_rate", "vol",
        "volume_ratio", "total_mv", "industry", "strong_reason",
        "sector_avg_pct_chg", "rs_industry", "rs_industry_penalty",
        "relative_strength_rank",
        "sector_up_ratio", "sector_strong_count", "hist_days", "ma5", "ma10",
        "ma20", "ma30", "ma40", "ma60", "recent_60_return", "recent_low_60",
        "low20", "high20", "strength20", "strength20_score",
        "ret60", "amount_yuan", "previous_high_20", "vol_ma5",
        "volume_expand_rate", "macd_dif", "macd_dea", "macd",
        "in_bottom_area", "ret60_oversold", "volume_price_rise", "turnover_active",
        "volume_ratio_active", "close_above_ma20", "macd_golden_cross", "hot_theme",
        "in_bottom_area_score", "ret60_oversold_score", "volume_price_rise_score",
        "turnover_active_score", "volume_ratio_active_score", "close_above_ma20_score",
        "macd_golden_cross_score", "hot_theme_score", "recent_20_return",
        "support_line", "high_drawdown", "volume_shrink_rate", "dip_reason",
        "score", "dip_score"
    ]
    cols = [c for c in wanted_cols if c in df.columns]
    header = f"【{label}】共 {len(df)} 只\n"
    return header + df[cols].to_string(index=False)


def format_sectors_for_ai(dip_sectors: pd.DataFrame, rep_stocks: pd.DataFrame):
    if dip_sectors.empty:
        return "【抄底板块】无符合条件的板块。"

    sector_str = dip_sectors[["industry_name", "avg_pct_chg", "stock_count"]].to_string(index=False)

    if rep_stocks.empty:
        stock_str = "（无代表股数据）"
    else:
        wanted = ["ts_code", "name", "industry", "close", "pct_chg", "turnover_rate"]
        cols = [c for c in wanted if c in rep_stocks.columns]
        stock_str = rep_stocks[cols].to_string(index=False)

    return f"【抄底板块】\n{sector_str}\n\n【板块代表股】\n{stock_str}"
