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
        if len(group) < 2:
            continue

        first_close = group["close"].iloc[0]
        last_close = group["close"].iloc[-1]

        recent_return = (last_close / first_close - 1) * 100 if first_close else 0

        rows.append({
            "ts_code": ts_code,
            "recent_20_return": recent_return,
            "hist_days": len(group),
        })

    return pd.DataFrame(rows)


def pick_dip_stocks(df: pd.DataFrame, hist_df: pd.DataFrame | None = None):
    """
    抄底候选股：
    - 近20日跌幅 > 20%
    - 今日跌幅 > 5%
    - 换手率 > 10%
    - 量比 > 2
    - 市值 < 100 亿
    - 非ST
    """
    df = clean_data(df)
    if "volume_ratio" not in df.columns:
        df["volume_ratio"] = 0

    condition_today_drop = df["pct_chg"] < -5
    condition_turnover = df["turnover_rate"] > 10
    condition_volume_ratio = df["volume_ratio"] > 2
    condition_mv = df["total_mv"] < 1_000_000
    condition_price = df["close"] > 3

    if "name" in df.columns:
        condition_st = ~df["name"].str.contains("ST", na=False)
    else:
        condition_st = True

    base = df[
        condition_today_drop &
        condition_turnover &
        condition_volume_ratio &
        condition_mv &
        condition_price &
        condition_st
    ].copy()

    print(f"选出：${base}")
    if hist_df is None or hist_df.empty:
        result = base.copy()
        result["recent_20_return"] = result["pct_chg"]
        result["hist_days"] = 1
        result["dip_score"] = (
            result["pct_chg"].abs() * 0.4 +
            result["turnover_rate"] * 0.3 +
            result["volume_ratio"] * 0.3
        )
        return result.sort_values("dip_score", ascending=False)

    stats = _build_dip_history_stats(hist_df)
    if stats.empty:
        return pd.DataFrame()

    result = pd.merge(base, stats, on="ts_code", how="inner")

    print(f"过滤之前：${result}")

    result = result[result["recent_20_return"] < -5].copy()

    if result.empty:
        return result

    result["dip_score"] = (
        result["recent_20_return"].abs() * 0.4 +
        result["turnover_rate"] * 0.3 +
        result["volume_ratio"] * 0.3
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
        "volume_ratio", "total_mv", "recent_20_return", "hist_days", "dip_score"
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
