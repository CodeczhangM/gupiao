import pandas as pd
from data_service import get_market_data, get_recent_daily_data, get_sector_data
from strategy import (
    pick_dip_sectors,
    format_for_ai,
    format_sectors_for_ai,
    pick_sector_tail_buy_stocks,
    select_stock_pools,
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
        hist_df = get_recent_daily_data(trade_date, n=100)
        if hist_df.empty:
            print("历史数据为空，优势股将无结果，抄底股退回单日逻辑")
        else:
            hist_days = hist_df["trade_date"].nunique()
            print(f"已加载最近 {hist_days} 个交易日历史数据\n")
            if hist_days < 100:
                print("历史数据不足 100 个交易日，超跌反转池无法计算 MA60 的 20 日斜率")
    except Exception as e:
        print(f"获取历史数据失败: {e}")
        hist_df = pd.DataFrame()

    # ── 3. 三池筛选 ────────────────────────────────────────────
    print("⚙️  筛选三池候选...")
    pools = select_stock_pools(df, hist_df)
    reversal = pools["reversal"]
    breakout = pools["breakout"]
    first_limit = pools["first_limit"]

    if breakout.empty:
        print("❌ 没有符合条件的趋势突破候选")
        breakout_text = "【趋势突破】无符合条件的股票。"
    else:
        print(f"选出 {len(breakout)} 只趋势突破候选")
        breakout_text = format_for_ai(breakout, label="趋势突破", limit=10)

    if reversal.empty:
        print("❌ 没有符合条件的超跌反转候选")
    else:
        print(f"选出 {len(reversal)} 只超跌反转候选")

    first_limit_text = (
        format_for_ai(first_limit, label="主升浪启动", limit=10)
        if not first_limit.empty
        else "【主升浪启动】无符合条件的股票。"
    )
    if first_limit.empty:
        print("❌ 没有符合条件的主升浪启动候选")
    else:
        print(f"选出 {len(first_limit)} 只主升浪启动候选")

    # ── 4. 强势板块分析 ────────────────────────────────────────
    print("\n⚙️  分析强势板块...")
    sector_result = get_sector_data(trade_date)
    if isinstance(sector_result, tuple):
        sector_df, stock_merged = sector_result
        dip_sectors, rep_stocks = pick_dip_sectors(sector_df, stock_merged)
        rep_stocks = pick_sector_tail_buy_stocks(df, hist_df, rep_stocks)
        if not dip_sectors.empty:
            print(f"发现 {len(dip_sectors)} 个强势板块")
        else:
            print("❌ 没有符合条件的强势板块")
    else:
        dip_sectors, rep_stocks = pd.DataFrame(), pd.DataFrame()

    dip_text = format_sectors_for_ai(dip_sectors, rep_stocks)
    if not reversal.empty:
        dip_text += "\n\n" + format_for_ai(reversal, label="超跌反转", limit=10)

    # ── 5. AI 分析 ─────────────────────────────────────────────
    print("\n🤖 本地 AI 分析中...\n")
    result = analyze_stocks(breakout_text, dip_text, trade_date, first_limit_text)

    # ── 6. 输出结果 ────────────────────────────────────────────
    print("\n========== 超跌反转（Top 10）==========\n")
    if not reversal.empty:
        print(reversal.head(10).to_string(index=False))
    else:
        print("无")

    print("\n========== 趋势突破（Top 10）==========\n")
    if not breakout.empty:
        print(breakout.head(10).to_string(index=False))
    else:
        print("无")

    print("\n========== 主升浪启动（Top 10）==========\n")
    if not first_limit.empty:
        print(first_limit.head(10).to_string(index=False))
    else:
        print("无")

    print("\n========== 强势板块（Top）==========\n")
    if not dip_sectors.empty:
        print(dip_sectors.to_string(index=False))
    else:
        print("无")

    print("\n========== 抄底候选（个股 Top 10）==========\n")
    if not reversal.empty:
        print(reversal.head(10).to_string(index=False))
    else:
        print("无")

    print("\n========== AI 分析结果 ==========\n")
    print(result)


if __name__ == "__main__":
    main()
