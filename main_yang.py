"""
杨永兴 14:30 隔夜套利战法 — 主入口
运行方式：python main_yang.py
支持参数：--date YYYYMMDD --time HH:MM --force（跳过时间窗口，用于调试）
"""

import argparse
from datetime import datetime

from data_service import (
    get_trade_dates,
    get_today_daily_basic,
    get_industry_mapping,
)
from strategy_yang import run_yang_strategy
from ai_agent import analyze_stocks


def parse_args():
    parser = argparse.ArgumentParser(description="杨永兴 14:30 隔夜套利战法")
    parser.add_argument("--date",  type=str, default=None, help="指定交易日 YYYYMMDD（默认取前一交易日）")
    parser.add_argument("--time",  type=str, default=None, help="指定当前时间 HH:MM（默认取系统时间）")
    parser.add_argument("--force", action="store_true",    help="跳过 14:30 时间窗口限制（调试用）")
    return parser.parse_args()


def main():
    args = parse_args()

    print("\n========== 杨永兴 14:30 隔夜套利战法 ==========\n")

    # ── 确定交易日 ─────────────────────────────────────────────
    if args.date:
        trade_date = args.date
        print(f"使用指定交易日: {trade_date}")
    else:
        dates = get_trade_dates(n=2)
        trade_date = dates[1]   # 前一交易日
        print(f"使用前一交易日: {trade_date}")

    current_time = args.time or datetime.now().strftime("%H:%M")
    print(f"当前时间: {current_time}\n")

    # ── 预加载全市场数据（只拉一次，后续各步骤复用）──────────
    print("📊 加载全市场日线数据...")
    all_daily = get_today_daily_basic(trade_date)
    if all_daily.empty:
        print("❌ 全市场日线数据为空，退出")
        return

    print(f"   共 {len(all_daily)} 条\n")

    print("📊 加载行业映射...")
    industry_map = get_industry_mapping()
    print(f"   共 {len(industry_map)} 只股票有行业标签\n")

    # ── 执行策略 ───────────────────────────────────────────────
    print("⚙️  执行选股策略...\n")
    empty_position, reason, result = run_yang_strategy(
        trade_date=trade_date,
        all_daily=all_daily,
        industry_map=industry_map,
        current_time=current_time,
        force_run=args.force,
    )

    print()

    # ── 输出结果 ───────────────────────────────────────────────
    if empty_position:
        print(f"\n🚫 全盘空仓 — {reason}\n")
        return

    if result.empty:
        print(f"\n📭 {reason}，但无候选股通过全部过滤条件\n")
        return

    print(f"\n========== 候选股（共 {len(result)} 只）==========\n")
    display_cols = [c for c in [
        "ts_code", "name", "close", "pct_chg",
        "turnover_rate", "volume_ratio", "total_mv",
        "industry", "signal_detail"
    ] if c in result.columns]
    print(result[display_cols].to_string(index=False))

    # ── AI 分析 ────────────────────────────────────────────────
    print("\n🤖 AI 分析中...\n")

    strong_text = _format_for_ai(result)
    dip_text = "（本策略为隔夜套利，无抄底板块分析）"
    ai_result = analyze_stocks(strong_text, dip_text, trade_date)

    print("\n========== AI 分析结果 ==========\n")
    print(ai_result)


def _format_for_ai(df):
    wanted = ["ts_code", "name", "close", "pct_chg", "turnover_rate", "volume_ratio", "industry", "signal_detail"]
    cols = [c for c in wanted if c in df.columns]
    header = f"【杨永兴14:30战法候选股】共 {len(df)} 只\n"
    return header + df[cols].to_string(index=False)


if __name__ == "__main__":
    main()
