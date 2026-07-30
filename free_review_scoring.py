from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd


SCORE_VERSION = "free-review-v1"
FINANCIAL_COLUMNS = [
    "roe", "roe_dt", "roa", "roic", "grossprofit_margin",
    "netprofit_margin", "current_ratio", "debt_to_assets",
    "ocf_to_or", "cfps", "tr_yoy", "netprofit_yoy",
    "dt_netprofit_yoy", "ocf_yoy", "basic_eps_yoy",
]


def _numeric(data: pd.DataFrame, column: str) -> pd.Series:
    if column not in data:
        return pd.Series(np.nan, index=data.index, dtype="float64")
    return pd.to_numeric(data[column], errors="coerce")


def eligible_universe(
    market: pd.DataFrame,
    trade_date: str,
) -> pd.DataFrame:
    if market is None or market.empty:
        return pd.DataFrame()
    data = market.copy()
    code = data.get("ts_code", pd.Series("", index=data.index)).astype(str)
    name = data.get("name", pd.Series("", index=data.index)).fillna("").astype(str)
    status = data.get(
        "list_status",
        pd.Series("L", index=data.index),
    ).fillna("L").astype(str)
    list_date = pd.to_datetime(
        data.get("list_date", pd.Series(None, index=data.index)),
        format="%Y%m%d",
        errors="coerce",
    )
    review_day = pd.Timestamp(
        datetime.strptime(str(trade_date), "%Y%m%d")
    )
    listed_days = (review_day - list_date).dt.days
    data["listed_days"] = listed_days
    valid_exchange = code.str.endswith((".SH", ".SZ", ".BJ"))
    risk_name = name.str.upper().str.contains(
        r"(?:\*?ST|退市)",
        regex=True,
    )
    active = (_numeric(data, "vol") > 0) & (_numeric(data, "amount") > 0)
    old_enough = list_date.notna() & (listed_days >= 60)
    result = data[
        valid_exchange
        & ~risk_name
        & status.eq("L")
        & active
        & old_enough
    ].copy()
    pe_ttm = _numeric(result, "pe_ttm")
    result["profit_state"] = np.where(pe_ttm < 0, "loss", "profit")
    return result.reset_index(drop=True)


def _last_number(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    return float(clean.iloc[-1]) if not clean.empty else None


def _pct_return(close: pd.Series, periods: int) -> float | None:
    if len(close) <= periods or close.iloc[-periods - 1] == 0:
        return None
    return float((close.iloc[-1] / close.iloc[-periods - 1] - 1) * 100)


def _rsi(close: pd.Series, periods: int) -> float | None:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(periods, min_periods=periods).mean()
    loss = (-delta.clip(upper=0)).rolling(periods, min_periods=periods).mean()
    denominator = gain + loss
    value = 100 * gain / denominator.replace(0, np.nan)
    if not value.dropna().empty:
        return float(value.dropna().iloc[-1])
    if len(close) > periods and float(gain.iloc[-1] or 0) > 0:
        return 100.0
    return None


def _history_metrics(group: pd.DataFrame) -> dict[str, Any]:
    data = group.sort_values("trade_date").copy()
    close = _numeric(data, "close")
    high = _numeric(data, "high").fillna(close)
    low = _numeric(data, "low").fillna(close)
    volume = _numeric(data, "vol")
    result: dict[str, Any] = {}
    for window in (5, 10, 20, 30, 60):
        result[f"ma{window}"] = _last_number(
            close.rolling(window, min_periods=window).mean()
        )
    for window in (5, 10, 20, 60):
        result[f"ret_{window}"] = _pct_return(close, window)
    last_close = _last_number(close)
    for window in (20, 60):
        recent = close.tail(window)
        recent_high = float(recent.max()) if not recent.empty else None
        recent_low = float(recent.min()) if not recent.empty else None
        result[f"drawdown_{window}"] = (
            (last_close / recent_high - 1) * 100
            if last_close is not None and recent_high
            else None
        )
        if window == 60:
            width = (recent_high - recent_low) if (
                recent_high is not None and recent_low is not None
            ) else 0
            result["position_60"] = (
                (last_close - recent_low) / width
                if last_close is not None and width
                else None
            )
    for window in (5, 10, 20):
        average = _last_number(
            volume.rolling(window, min_periods=window).mean()
        )
        latest_volume = _last_number(volume)
        result[f"vol_ratio_ma{window}"] = (
            latest_volume / average if latest_volume is not None and average else None
        )
    ma20 = close.rolling(20, min_periods=20).mean()
    ma60 = close.rolling(60, min_periods=60).mean()
    result["ma20_slope"] = (
        float((ma20.iloc[-1] / ma20.iloc[-6] - 1) * 100)
        if len(ma20) >= 6 and pd.notna(ma20.iloc[-6]) and ma20.iloc[-6]
        else None
    )
    result["ma60_slope"] = (
        float((ma60.iloc[-1] / ma60.iloc[-6] - 1) * 100)
        if len(ma60) >= 6 and pd.notna(ma60.iloc[-6]) and ma60.iloc[-6]
        else None
    )
    ema12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False, min_periods=9).mean()
    result["macd_dif"] = _last_number(dif)
    result["macd_dea"] = _last_number(dea)
    result["macd_hist"] = _last_number((dif - dea) * 2)
    low9 = low.rolling(9, min_periods=9).min()
    high9 = high.rolling(9, min_periods=9).max()
    rsv = (close - low9) / (high9 - low9).replace(0, np.nan) * 100
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    result["kdj_k"] = _last_number(k)
    result["kdj_d"] = _last_number(d)
    result["kdj_j"] = (
        3 * result["kdj_k"] - 2 * result["kdj_d"]
        if result["kdj_k"] is not None and result["kdj_d"] is not None
        else None
    )
    for window in (6, 12, 24):
        result[f"rsi{window}"] = _rsi(close, window)
    middle = close.rolling(20, min_periods=20).mean()
    std = close.rolling(20, min_periods=20).std(ddof=0)
    upper, lower = middle + 2 * std, middle - 2 * std
    band_width = upper - lower
    result["boll_position"] = (
        float((close.iloc[-1] - lower.iloc[-1]) / band_width.iloc[-1])
        if len(close) and pd.notna(band_width.iloc[-1]) and band_width.iloc[-1]
        else None
    )
    result["boll_width"] = (
        float(band_width.iloc[-1] / middle.iloc[-1])
        if len(close) and pd.notna(middle.iloc[-1]) and middle.iloc[-1]
        else None
    )
    previous = close.shift(1)
    true_range = pd.concat(
        [(high - low), (high - previous).abs(), (low - previous).abs()],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(14, min_periods=14).mean()
    result["atr_pct"] = (
        float(atr.iloc[-1] / close.iloc[-1] * 100)
        if len(close) and pd.notna(atr.iloc[-1]) and close.iloc[-1]
        else None
    )
    return result


def _financial_latest(
    financial: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, int]]:
    if financial is None or financial.empty:
        return pd.DataFrame(), {}
    data = financial.sort_values(["ts_code", "end_date", "ann_date"]).copy()
    latest = data.groupby("ts_code", as_index=False).tail(1)
    improvements: dict[str, int] = {}
    for code, group in data.groupby("ts_code"):
        roe = _numeric(group.sort_values("end_date"), "roe").dropna()
        improvements[str(code)] = int((roe.diff().dropna() > 0).sum())
    return latest, improvements


def _clip_score(value: float, maximum: float) -> float:
    return round(float(max(0, min(value, maximum))), 2)


def build_review_snapshot(
    market: pd.DataFrame,
    history: pd.DataFrame,
    financial: pd.DataFrame,
    trade_date: str,
) -> pd.DataFrame:
    universe = eligible_universe(market, trade_date)
    if universe.empty:
        return universe
    metric_rows = []
    history_groups = (
        {str(code): group for code, group in history.groupby("ts_code")}
        if history is not None and not history.empty
        else {}
    )
    for code in universe["ts_code"].astype(str):
        group = history_groups.get(code, pd.DataFrame())
        metric_rows.append({
            "ts_code": code,
            **(_history_metrics(group) if not group.empty else {}),
        })
    result = universe.merge(pd.DataFrame(metric_rows), on="ts_code", how="left")
    latest_financial, improvements = _financial_latest(financial)
    if not latest_financial.empty:
        financial_fields = [
            "ts_code", "end_date", "ann_date", *FINANCIAL_COLUMNS
        ]
        available = [field for field in financial_fields if field in latest_financial]
        latest_financial = latest_financial[available].rename(columns={
            "end_date": "financial_end_date",
            "ann_date": "financial_ann_date",
        })
        result = result.merge(latest_financial, on="ts_code", how="left")
    else:
        result["financial_end_date"] = None
        result["financial_ann_date"] = None
    result["financial_improvement_count"] = (
        result["ts_code"].astype(str).map(improvements).fillna(0).astype(int)
    )

    score_inputs = [
        "ma20_slope", "ma60_slope", "ret_20", "position_60",
        "volume_ratio", "turnover_rate", "vol_ratio_ma5", "amount",
        "macd_hist", "kdj_k", "rsi12", "boll_position",
        "pe_ttm", "pb", "ps_ttm", "dv_ttm",
        *FINANCIAL_COLUMNS,
    ]
    rows = []
    for row in result.to_dict("records"):
        reasons: list[str] = []
        risks: list[str] = []
        ma20_slope = float(row.get("ma20_slope") or 0)
        ma60_slope = float(row.get("ma60_slope") or 0)
        ret20 = float(row.get("ret_20") or 0)
        trend = (
            (6 if ma20_slope > 0 else 0)
            + (5 if ma60_slope > 0 else 0)
            + (5 if ret20 > 0 else 0)
            + (4 if float(row.get("position_60") or 0) >= 0.5 else 0)
        )
        volume_score = (
            min(max(float(row.get("volume_ratio") or 0), 0), 3) / 3 * 7
            + min(max(float(row.get("turnover_rate") or 0), 0), 10) / 10 * 5
            + min(max(float(row.get("vol_ratio_ma5") or 0), 0), 2) / 2 * 5
            + (3 if float(row.get("amount") or 0) > 100000 else 0)
        )
        momentum = (
            (5 if float(row.get("macd_hist") or 0) > 0 else 0)
            + (4 if float(row.get("kdj_k") or 0) >= float(row.get("kdj_d") or 0) else 0)
            + (3 if 40 <= float(row.get("rsi12") or 0) <= 75 else 0)
            + (3 if float(row.get("boll_position") or 0) >= 0.5 else 0)
        )
        pe_ttm = row.get("pe_ttm")
        loss = pe_ttm is not None and pd.notna(pe_ttm) and float(pe_ttm) < 0
        valuation = 0 if loss else (
            (6 if pe_ttm is not None and pd.notna(pe_ttm) and 0 < float(pe_ttm) <= 40 else 0)
            + (4 if 0 < float(row.get("pb") or 0) <= 5 else 0)
            + (3 if 0 < float(row.get("ps_ttm") or 0) <= 8 else 0)
            + (2 if float(row.get("dv_ttm") or 0) > 0 else 0)
        )
        quality = (
            (6 if float(row.get("roe") or 0) >= 10 else 0)
            + (4 if float(row.get("roic") or 0) >= 8 else 0)
            + (3 if float(row.get("grossprofit_margin") or 0) >= 20 else 0)
            + (3 if float(row.get("netprofit_margin") or 0) >= 5 else 0)
            + (2 if float(row.get("ocf_to_or") or 0) > 0 else 0)
            + (2 if float(row.get("debt_to_assets") or 100) <= 60 else 0)
        )
        growth = (
            (3 if float(row.get("tr_yoy") or 0) > 0 else 0)
            + (3 if float(row.get("netprofit_yoy") or 0) > 0 else 0)
            + (2 if float(row.get("dt_netprofit_yoy") or 0) > 0 else 0)
            + (1 if float(row.get("ocf_yoy") or 0) > 0 else 0)
            + (1 if int(row.get("financial_improvement_count") or 0) >= 3 else 0)
        )
        risk = 0
        if loss:
            risk += 5
            risks.append("亏损")
        if float(row.get("ocf_to_or") or 0) < 0:
            risk += 4
            risks.append("经营现金流为负")
        if float(row.get("atr_pct") or 0) > 6:
            risk += 4
            risks.append("波动较高")
        if float(row.get("ret_60") or 0) > 40:
            risk += 4
            risks.append("阶段涨幅较大")
        if float(row.get("debt_to_assets") or 0) > 75 and str(row.get("industry") or "") not in {
            "银行", "保险", "证券",
        }:
            risk += 3
            risks.append("负债率较高")
        scores = {
            "trend_score": _clip_score(trend, 20),
            "volume_price_score": _clip_score(volume_score, 20),
            "momentum_score": _clip_score(momentum, 15),
            "valuation_score": _clip_score(valuation, 15),
            "financial_quality_score": _clip_score(quality, 20),
            "financial_growth_score": _clip_score(growth, 10),
            "risk_penalty": _clip_score(risk, 20),
        }
        if scores["trend_score"] >= 12:
            reasons.append("趋势向上")
        if scores["volume_price_score"] >= 10:
            reasons.append("量价活跃")
        if scores["financial_quality_score"] >= 12:
            reasons.append("财务质量较好")
        missing = sorted(
            field for field in score_inputs
            if row.get(field) is None or pd.isna(row.get(field))
        )
        completeness = (
            (len(score_inputs) - len(missing)) / len(score_inputs) * 100
        )
        total = sum(
            scores[key] for key in (
                "trend_score", "volume_price_score", "momentum_score",
                "valuation_score", "financial_quality_score",
                "financial_growth_score",
            )
        ) - scores["risk_penalty"]
        rows.append({
            **row,
            **scores,
            "total_score": _clip_score(total, 100),
            "profit_state": "loss" if loss else "profit",
            "data_completeness": round(completeness, 2),
            "score_reasons": reasons,
            "risk_flags": risks,
            "missing_fields": missing,
            "score_version": SCORE_VERSION,
            "trade_date": str(trade_date),
        })
    return pd.DataFrame(rows)
