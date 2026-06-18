import pandas as pd
from data_service import get_market_data, get_recent_daily_data, get_sector_data
from strategy import (
    pick_stocks,
    pick_dip_stocks,
    pick_dip_sectors,
    format_for_ai,
    format_sectors_for_ai,
)
from ai_agent import analyze_stocks


def main():
    print("\n========== AI 量化选股系统启动 ==========\n")

    # ── 1. 获取前一交易日行情数据 ──────────────────────────────
    print("📊 获取市场数据（前一交易日）...")
    df, trade_date = get_market_data()
    print(f"数据获取完成：{len(df)} 条，交易日：{trade_date}\n")

    # ── 2. 获取历史数据 ────────────────────────────────────────
    try:
        hist_df = get_recent_daily_data(trade_date, n=61)
        if hist_df.empty:
            print("历史数据为空，优势股将无结果，抄底股退回单日逻辑")
        else:
            hist_days = hist_df["trade_date"].nunique()
            print(f"已加载最近 {hist_days} 个交易日历史数据\n")
            if hist_days < 61:
                print("历史数据不足 61 个交易日，优势股仅保留历史完整的股票")
    except Exception as e:
        print(f"获取历史数据失败: {e}")
        hist_df = pd.DataFrame()

    # ── 3. 优势股筛选 ──────────────────────────────────────────
    print("⚙️  筛选优势股...")
    strong = pick_stocks(df, hist_df)
    if strong.empty:
        print("❌ 没有可评分的优势股")
        strong_text = "【优势股】无符合条件的股票。"
    else:
        print(f"选出 {len(strong)} 只优势股")
        strong_text = format_for_ai(strong, label="优势股", limit=10)

    # ── 4. 抄底股筛选 ──────────────────────────────────────────
    print("\n⚙️  筛选抄底候选股...")
    dip = pick_dip_stocks(df, hist_df)
    if dip.empty:
        print("❌ 没有符合条件的抄底候选股")
    else:
        print(f"选出 {len(dip)} 只抄底候选股")

    # ── 5. 抄底板块分析 ────────────────────────────────────────
    print("\n⚙️  分析抄底板块...")
    sector_result = get_sector_data(trade_date)
    if isinstance(sector_result, tuple):
        sector_df, stock_merged = sector_result
        dip_sectors, rep_stocks = pick_dip_sectors(sector_df, stock_merged)
        if not dip_sectors.empty:
            print(f"发现 {len(dip_sectors)} 个抄底板块")
        else:
            print("❌ 没有符合条件的抄底板块")
    else:
        dip_sectors, rep_stocks = pd.DataFrame(), pd.DataFrame()

    dip_text = format_sectors_for_ai(dip_sectors, rep_stocks)
    if not dip.empty:
        dip_text += "\n\n" + format_for_ai(dip, label="抄底候选个股", limit=10)

    # ── 6. AI 分析 ─────────────────────────────────────────────
    print("\n🤖 本地 AI 分析中...\n")
    result = analyze_stocks(strong_text, dip_text, trade_date)

    # ── 7. 输出结果 ────────────────────────────────────────────
    print("\n========== 优势股（Top 10）==========\n")
    if not strong.empty:
        print(strong.head(10).to_string(index=False))

    print("\n========== 抄底候选（板块）==========\n")
    if not dip_sectors.empty:
        print(dip_sectors.to_string(index=False))
    else:
        print("无")

    print("\n========== 抄底候选（个股 Top 10）==========\n")
    if not dip.empty:
        print(dip.head(10).to_string(index=False))
    else:
        print("无")

    print("\n========== AI 分析结果 ==========\n")
    print(result)


if __name__ == "__main__":
    main()
