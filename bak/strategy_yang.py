"""
杨永兴 14:30 隔夜套利战法 — 选股策略
======================================
7 条过滤逻辑按"最快淘汰"顺序排列，越早能排除大量股票的条件越靠前，
减少后续昂贵的逐股分时数据拉取。

过滤顺序：
  1. 时间窗口（14:30~15:00）
  2. 大盘一票否决
  3. 基础池（主板 + 涨幅 + 市值）
  4. 题材共振（板块 Top 10%）
  5. 量能筹码（量比 + 换手率）
  6. 股性激活（20 日内有涨停）
  7. 分时临界点（VWAP 回踩）
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Optional

from data_service import (
    get_history_daily,
    get_minute_bars,
    get_index_daily,
)

# ── 策略参数（集中管理，方便调参）──────────────────────────────
PCT_LOW          = 3.0      # 日线涨幅下限 %
PCT_HIGH         = 5.0      # 日线涨幅上限 %
MV_LOW           = 500_000  # 总市值下限（万元）= 50 亿
MV_HIGH          = 2_000_000 # 总市值上限（万元）= 200 亿
TURNOVER_LOW     = 5.0      # 换手率下限 %（自由流通口径）
TURNOVER_HIGH    = 10.0     # 换手率上限 %
VOLUME_RATIO_MIN = 1.0      # 量比最小值
LIMIT_UP_THRESH  = 9.9      # 涨停判定阈值 %
HISTORY_DAYS     = 20       # 回溯交易日数
SECTOR_TOP_PCT   = 0.10     # 题材共振：板块涨幅 Top 10%
INDEX_DROP_LIMIT = -1.5     # 大盘日内跌幅一票否决线 %
MA5_BREAK        = True     # 是否启用大盘跌破 5MA 否决

# 分时回踩参数
VWAP_TOLERANCE   = 0.005    # 回踩均线容忍带：均线上方 0.5% 以内视为"贴近"
PULLBACK_BARS    = 3        # 连续 N 根 1 分钟 K 线收盘价不跌破 VWAP
HIGH_PROXIMITY   = 0.02     # 当前价距全天最高价在 2% 以内视为"高位附近"

# 主板市场标识（排除创业板 300xxx、科创板 688xxx、北交所 8xxxxx/4xxxxx）
def _is_mainboard(ts_code: str) -> bool:
    code = ts_code.split(".")[0]
    if ts_code.endswith(".SH"):
        return not code.startswith("688")
    if ts_code.endswith(".SZ"):
        return not code.startswith("3")
    return False


# ═══════════════════════════════════════════════════════════════
#  Step 1 — 时间窗口检查
# ═══════════════════════════════════════════════════════════════

def check_time_window() -> bool:
    """仅在 14:30~15:00 返回 True。"""
    now = datetime.now().time()
    return datetime.strptime("14:30", "%H:%M").time() <= now <= datetime.strptime("15:00", "%H:%M").time()


# ═══════════════════════════════════════════════════════════════
#  Step 2 — 大盘一票否决
# ═══════════════════════════════════════════════════════════════

def check_market_ok(trade_date: str) -> tuple[bool, str]:
    """
    返回 (通过, 原因)。
    条件：大盘日内跌幅 > -1.5%，且收盘价 >= 5 日均线。
    """
    df = get_index_daily(trade_date, n_days=6)
    if df.empty or len(df) < 2:
        return False, "大盘数据不足，保守空仓"

    today_row = df.iloc[0]
    today_pct = float(today_row["pct_chg"])

    if today_pct < INDEX_DROP_LIMIT:
        return False, f"大盘日内跌幅 {today_pct:.2f}% < {INDEX_DROP_LIMIT}%，一票否决"

    if MA5_BREAK and len(df) >= 6:
        ma5 = df["close"].iloc[:5].mean()   # 前 5 日（不含今日）均值
        today_close = float(today_row["close"])
        if today_close < ma5:
            return False, f"大盘收盘 {today_close:.2f} 跌破 5MA {ma5:.2f}，一票否决"

    return True, "大盘环境正常"


# ═══════════════════════════════════════════════════════════════
#  Step 3 — 基础池筛选（向量化，无循环）
# ═══════════════════════════════════════════════════════════════

def filter_basic_pool(df: pd.DataFrame) -> pd.DataFrame:
    """
    主板 + 涨幅 [3%, 5%] + 市值 [50亿, 200亿] + 非ST。
    df 需包含列：ts_code, pct_chg, total_mv, name（可选）
    """
    d = df.copy()
    d["pct_chg"]  = pd.to_numeric(d["pct_chg"],  errors="coerce")
    d["total_mv"] = pd.to_numeric(d["total_mv"],  errors="coerce")
    d = d.dropna(subset=["pct_chg", "total_mv"])

    mask_board    = d["ts_code"].apply(_is_mainboard)
    mask_pct      = d["pct_chg"].between(PCT_LOW, PCT_HIGH)
    mask_mv       = d["total_mv"].between(MV_LOW, MV_HIGH)
    mask_st       = ~d["name"].str.contains("ST", na=False) if "name" in d.columns else pd.Series(True, index=d.index)

    return d[mask_board & mask_pct & mask_mv & mask_st].copy()


# ═══════════════════════════════════════════════════════════════
#  Step 4 — 题材共振（板块 Top 10%）
# ═══════════════════════════════════════════════════════════════

def filter_sector_resonance(
    pool: pd.DataFrame,
    all_daily: pd.DataFrame,
    industry_map: pd.DataFrame,
) -> pd.DataFrame:
    """
    计算全市场各申万二级行业平均涨幅，保留个股所属板块位于 Top 10% 的股票。

    pool         : 已通过基础池筛选的候选股 DataFrame（含 ts_code）
    all_daily    : 当日全市场日线（含 ts_code, pct_chg）
    industry_map : ts_code → industry 映射（来自 get_industry_mapping()）
    """
    if industry_map.empty or all_daily.empty:
        return pool  # 数据缺失时放行，不因数据问题误杀

    # 计算各行业平均涨幅
    merged = pd.merge(
        all_daily[["ts_code", "pct_chg"]],
        industry_map[["ts_code", "industry"]],
        on="ts_code", how="inner"
    )
    merged["pct_chg"] = pd.to_numeric(merged["pct_chg"], errors="coerce")
    sector_avg = merged.groupby("industry")["pct_chg"].mean()

    # Top 10% 阈值
    threshold = sector_avg.quantile(1 - SECTOR_TOP_PCT)

    hot_sectors = set(sector_avg[sector_avg >= threshold].index)

    # 给候选池打上行业标签
    pool = pd.merge(pool, industry_map[["ts_code", "industry"]], on="ts_code", how="left")
    pool["in_hot_sector"] = pool["industry"].isin(hot_sectors)

    return pool[pool["in_hot_sector"]].drop(columns=["in_hot_sector"]).copy()


# ═══════════════════════════════════════════════════════════════
#  Step 5 — 量能筹码（量比 + 换手率）
# ═══════════════════════════════════════════════════════════════

def filter_volume(pool: pd.DataFrame) -> pd.DataFrame:
    """
    量比 > 1，换手率（自由流通口径）在 [5%, 10%]。
    需要列：volume_ratio, turnover_rate
    """
    d = pool.copy()
    d["volume_ratio"]  = pd.to_numeric(d.get("volume_ratio",  pd.Series(dtype=float)), errors="coerce")
    d["turnover_rate"] = pd.to_numeric(d.get("turnover_rate", pd.Series(dtype=float)), errors="coerce")

    mask_vr = d["volume_ratio"] > VOLUME_RATIO_MIN
    mask_tr = d["turnover_rate"].between(TURNOVER_LOW, TURNOVER_HIGH)

    return d[mask_vr & mask_tr].copy()


# ═══════════════════════════════════════════════════════════════
#  Step 6 — 股性激活（20 日内有涨停）
# ═══════════════════════════════════════════════════════════════

def _has_limit_up(ts_code: str, trade_date: str) -> bool:
    """单股：过去 20 交易日是否有涨停（pct_chg >= 9.9%）。"""
    try:
        hist = get_history_daily(ts_code, trade_date, n_days=HISTORY_DAYS)
        if hist.empty:
            return False
        return bool((hist["pct_chg"] >= LIMIT_UP_THRESH).any())
    except Exception:
        return False


def _print_stage_detail(label: str, df: pd.DataFrame) -> None:
    """打印某阶段候选股明细，供调试和人工复核。"""
    if df.empty:
        print(f"\n  ── {label}：无数据 ──")
        return
    wanted = ["ts_code", "name", "close", "pct_chg", "turnover_rate", "volume_ratio", "total_mv", "industry", "limit_up_flag"]
    cols = [c for c in wanted if c in df.columns]
    print(f"\n  ── {label}（{len(df)} 只）──")
    print(df[cols].to_string(index=False))
    print()


def filter_limit_up_history(pool: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    """
    逐股检查 20 日内是否有涨停，结果写入 limit_up_flag 列。
    - True  : 20 日内有涨停，强烈推荐
    - False : 无涨停记录或数据缺失（新股/停牌），降级保留，输出时标注
    不做硬淘汰，避免因数据缺失误杀候选股。
    """
    if pool.empty:
        return pool
    d = pool.copy()
    d["limit_up_flag"] = d["ts_code"].apply(lambda code: _has_limit_up(code, trade_date))
    return d


# ═══════════════════════════════════════════════════════════════
#  Step 7 — 分时临界点（VWAP 回踩，向量化）
# ═══════════════════════════════════════════════════════════════

def _calc_vwap_series(minute_df: pd.DataFrame) -> pd.Series:
    """
    计算每根 K 线截止当前的累计 VWAP（当日成交额 / 当日成交量）。
    完全向量化，无 for 循环。
    """
    cum_amount = minute_df["amount"].cumsum()
    cum_vol    = minute_df["vol"].cumsum()
    # 防止除零（开盘前无成交）
    vwap = cum_amount / cum_vol.replace(0, np.nan)
    return vwap


def _check_pullback_signal(minute_df: pd.DataFrame, current_time: str) -> tuple[bool, str]:
    """
    判断分时临界点：
      A. 当前价 > VWAP（价格在均线上方）
      B. 当前价距全天最高价在 HIGH_PROXIMITY 以内（高位附近）
      C. 最近 PULLBACK_BARS 根 K 线收盘价均 >= VWAP * (1 - VWAP_TOLERANCE)
         （回踩均线但未跌破，容忍带 0.5%）

    current_time: "HH:MM"，用于截取当日已完成的分时数据，避免未来函数。
    """
    if minute_df.empty:
        return False, "分时数据为空"

    # 严格截取到 current_time（含），防止 look-ahead bias
    df = minute_df[minute_df["time_str"] <= current_time].copy()
    if len(df) < PULLBACK_BARS + 1:
        return False, f"分时数据不足 {PULLBACK_BARS + 1} 根"

    df = df.reset_index(drop=True)
    df["vwap"] = _calc_vwap_series(df)
    df = df.dropna(subset=["vwap"])

    if df.empty:
        return False, "VWAP 计算后数据为空"

    current_close = df["close"].iloc[-1]
    day_high      = df["high"].max()
    current_vwap  = df["vwap"].iloc[-1]

    # 条件 A：当前价在 VWAP 上方
    if current_close <= current_vwap:
        return False, f"当前价 {current_close:.3f} <= VWAP {current_vwap:.3f}"

    # 条件 B：当前价在全天最高价附近（2% 以内）
    if (day_high - current_close) / day_high > HIGH_PROXIMITY:
        return False, f"当前价 {current_close:.3f} 距最高价 {day_high:.3f} 超过 {HIGH_PROXIMITY*100:.1f}%"

    # 条件 C：最近 PULLBACK_BARS 根 K 线收盘价均不跌破 VWAP 容忍带
    recent = df.tail(PULLBACK_BARS)
    lower_bound = recent["vwap"] * (1 - VWAP_TOLERANCE)
    if not (recent["close"] >= lower_bound).all():
        return False, f"最近 {PULLBACK_BARS} 根 K 线中有跌破 VWAP 容忍带"

    return True, (
        f"✓ 回踩信号成立 | 当前价={current_close:.3f} "
        f"VWAP={current_vwap:.3f} 最高={day_high:.3f}"
    )


def filter_intraday_signal(pool: pd.DataFrame, trade_date: str, current_time: str) -> pd.DataFrame:
    """
    对候选池逐股拉取分时数据，判断 VWAP 回踩临界点。
    返回通过的股票，并附加 signal_detail 列。
    """
    if pool.empty:
        return pool

    passed = []
    for _, row in pool.iterrows():
        ts_code = row["ts_code"]
        try:
            minute_df = get_minute_bars(ts_code, trade_date)
            ok, detail = _check_pullback_signal(minute_df, current_time)
            if ok:
                r = row.copy()
                r["signal_detail"] = detail
                passed.append(r)
        except Exception as e:
            print(f"[filter_intraday_signal] {ts_code} 异常: {e}")
            continue

    if not passed:
        return pd.DataFrame(columns=list(pool.columns) + ["signal_detail"])
    return pd.DataFrame(passed).reset_index(drop=True)


# ═══════════════════════════════════════════════════════════════
#  主入口：串联全部 7 步
# ═══════════════════════════════════════════════════════════════

def run_yang_strategy(
    trade_date: str,
    all_daily: pd.DataFrame,
    industry_map: pd.DataFrame,
    current_time: Optional[str] = None,
    force_run: bool = False,
) -> tuple[bool, str, pd.DataFrame]:
    """
    执行杨永兴 14:30 战法完整选股流程。

    参数
    ----
    trade_date   : 当日交易日 YYYYMMDD
    all_daily    : 当日全市场日线 + 基本面合并表（来自 get_today_daily_basic）
    industry_map : ts_code → industry 映射（来自 get_industry_mapping）
    current_time : 当前时间 "HH:MM"，None 时自动取系统时间
    force_run    : True 时跳过时间窗口检查（用于回测/调试）

    返回
    ----
    (空仓信号: bool, 原因说明: str, 候选股 DataFrame)
    空仓信号为 True 时 DataFrame 为空，表示全盘空仓。
    """
    if current_time is None:
        current_time = datetime.now().strftime("%H:%M")

    # Step 1 — 时间窗口
    if not force_run and not check_time_window():
        return True, f"当前时间 {current_time} 不在 14:30~15:00 窗口内", pd.DataFrame()

    # Step 2 — 大盘一票否决
    market_ok, market_reason = check_market_ok(trade_date)
    if not market_ok:
        return True, market_reason, pd.DataFrame()

    print(f"  [大盘] {market_reason}")

    # Step 3 — 基础池
    pool = filter_basic_pool(all_daily)
    snap_basic = pool.copy()
    print(f"  [基础池] {len(pool)} 只（主板 + 涨幅 {PCT_LOW}~{PCT_HIGH}% + 市值 {MV_LOW//10000}~{MV_HIGH//10000}亿）")
    if pool.empty:
        _print_stage_detail("基础池", snap_basic)
        return False, "基础池为空", pd.DataFrame()

    # Step 4 — 题材共振
    pool = filter_sector_resonance(pool, all_daily, industry_map)
    snap_sector = pool.copy()
    print(f"  [题材共振] {len(pool)} 只（板块 Top {int(SECTOR_TOP_PCT*100)}%）")
    if pool.empty:
        _print_stage_detail("基础池（题材共振前）", snap_basic)
        return False, "题材共振后无候选股", pd.DataFrame()

    # Step 5 — 量能筹码
    pool = filter_volume(pool)
    snap_volume = pool.copy()
    print(f"  [量能筹码] {len(pool)} 只（量比>{VOLUME_RATIO_MIN} 换手{TURNOVER_LOW}~{TURNOVER_HIGH}%）")
    if pool.empty:
        _print_stage_detail("题材共振后（量能过滤前）", snap_sector)
        return False, "量能筹码过滤后无候选股", pd.DataFrame()

    # Step 6 — 股性激活（软过滤，不淘汰，仅标记）
    pool = filter_limit_up_history(pool, trade_date)
    has_lu = pool["limit_up_flag"].sum()
    print(f"  [股性激活] 共 {len(pool)} 只，其中 {has_lu} 只 20日内有涨停（其余降级保留）")

    # Step 7 — 分时临界点（最贵，最后执行）
    pool = filter_intraday_signal(pool, trade_date, current_time)
    print(f"  [分时临界] {len(pool)} 只（VWAP 回踩信号）")

    # 无论结果如何，打印各阶段明细供参考
    _print_stage_detail("量能筹码后候选股（含股性标记）", snap_volume)

    return False, "选股完成", pool
