import pandas as pd


def clean_data(df: pd.DataFrame):
    df = df.copy()
    numeric_cols = ["pct_chg", "turnover_rate", "volume_ratio", "vol", "total_mv", "close"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    required_cols = ["pct_chg", "turnover_rate", "vol", "total_mv", "close"]
    df = df.dropna(subset=required_cols)
    return df


def pick_stocks(df: pd.DataFrame):
    """推荐强势股：涨幅3~9%、高换手、中小市值、非ST"""
    df = clean_data(df)

    condition_pct = df["pct_chg"].between(3, 9)
    condition_turnover = df["turnover_rate"] > 5
    condition_vol = df["vol"] > 1e6
    condition_mv = df["total_mv"] < 5_000_000
    condition_price = df["close"] > 5

    if "name" in df.columns:
        condition_st = ~df["name"].str.contains("ST", na=False)
    else:
        condition_st = True

    result = df[
        condition_pct &
        condition_turnover &
        condition_vol &
        condition_mv &
        condition_price &
        condition_st
    ].copy()

    result["score"] = (
        result["pct_chg"] * 0.4 +
        result["turnover_rate"] * 0.3 +
        (result["vol"] / 1e6) * 0.3
    )

    return result.sort_values("score", ascending=False)


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

    condition_mv = df["total_mv"] < 2_000_000
    condition_price = df["close"] > 3
    condition_fund_active = (df["turnover_rate"] >= 2.5) & (df["vol"] >= 800_000)

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

    result = pd.merge(base, stats, on="ts_code", how="inner")

    result = result[
        (
            result["ground_volume_price"] |
            result["former_strong_consolidation"]
        ) &
        (
            result["recent_support_ma20"] |
            result["recent_support_ma40"]
        )
    ].copy()

    if result.empty:
        return result

    result["dip_reason"] = ""
    result.loc[result["ground_volume_price"], "dip_reason"] = "地量地价"
    result.loc[result["former_strong_consolidation"], "dip_reason"] = result.loc[
        result["former_strong_consolidation"], "dip_reason"
    ].apply(lambda value: "前强回调守均线" if not value else f"{value}+前强回调守均线")
    result["dip_reason"] = result.apply(
        lambda row: f"{row['dip_reason']}({row['support_line']})" if row.get("support_line") else row["dip_reason"],
        axis=1,
    )
    result["fund_active"] = True

    result["dip_score"] = (
        result["high_drawdown"].abs() * 0.25 +
        (1 - result["low_position_ratio"]).clip(lower=0) * 25 +
        (1 - result["volume_shrink_rate"]).clip(lower=0) * 20 +
        result["decreasing_volume_days"] * 2 +
        result["former_strong"].astype(int) * 8 +
        result["recent_support_ma20"].astype(int) * 8 +
        result["recent_support_ma40"].astype(int) * 5 +
        (result["turnover_rate"] / 5).clip(upper=4)
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


def format_for_ai(df: pd.DataFrame, label="强势股", limit=10):
    df = df.head(limit)
    wanted_cols = [
        "ts_code", "name", "close", "pct_chg", "turnover_rate", "vol",
        "volume_ratio", "total_mv", "recent_20_return", "hist_days", "ma20", "ma40",
        "support_line", "high_drawdown", "volume_shrink_rate", "dip_reason", "dip_score"
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
