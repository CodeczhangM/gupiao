from __future__ import annotations

from typing import Any
import math

import numpy as np
import pandas as pd

from indicator_settings import (
    calculate_macd,
    load_macd_settings,
    macd_parameter_key,
    macd_provenance,
)


BASE_SCORE_VERSION = "tail-premium-v1"
MODULE_WEIGHTS = {
    "tail": 35.0,
    "limit": 20.0,
    "sector": 15.0,
    "trend": 10.0,
    "volume": 10.0,
    "position": 10.0,
}
MIN_AVERAGE_AMOUNT_YUAN = 50_000_000.0
TAIL_VOLUME_HARD_REJECT = 5.0
TAIL_VOLUME_SOFT_RISK = 3.0
TURNOVER_HARD_REJECT = 18.0
TURNOVER_SOFT_RISK = 12.0
RETURN20_HARD_REJECT = 50.0
RETURN20_SOFT_RISK = 35.0
HIGH_POSITION_HARD_REJECT = 0.98
HIGH_POSITION_SOFT_RISK = 0.95


def current_score_version(settings: dict[str, Any] | None = None) -> str:
    return f"{BASE_SCORE_VERSION}-{macd_parameter_key(settings)}"


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def normalize_amount_yuan(
    value: Any,
    *,
    unit: str | None = None,
    source: str | None = None,
) -> float | None:
    number = _number(value)
    if number is None:
        return None
    normalized_unit = str(unit or "").strip().lower()
    normalized_source = str(source or "").strip().lower()
    if normalized_unit in {
        "thousand_yuan",
        "thousand-yuan",
        "千元",
        "k_yuan",
    }:
        return number * 1_000
    if normalized_unit in {"yuan", "元", "cny"}:
        return number
    if normalized_source in {"tushare_daily", "tushare"}:
        return number * 1_000
    return number


def _row_amount_yuan(row: pd.Series, *, history: bool) -> float | None:
    default_source = "tushare_daily" if history else None
    return normalize_amount_yuan(
        row.get("amount"),
        unit=row.get("amount_unit"),
        source=row.get("amount_source") or default_source,
    )


def _series_number(data: pd.DataFrame, column: str) -> pd.Series:
    if column not in data:
        return pd.Series(np.nan, index=data.index, dtype="float64")
    return pd.to_numeric(data[column], errors="coerce")


def _last(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    return float(clean.iloc[-1]) if not clean.empty else None


def _pct_return(close: pd.Series, periods: int) -> float | None:
    clean = pd.to_numeric(close, errors="coerce").dropna()
    if len(clean) <= periods or not clean.iloc[-periods - 1]:
        return None
    return float((clean.iloc[-1] / clean.iloc[-periods - 1] - 1) * 100)


def _limit_up_flag(row: pd.Series) -> bool:
    explicit = row.get("limit_flag")
    if explicit is not None and not pd.isna(explicit):
        text = str(explicit).strip().lower()
        return text in {"1", "true", "u", "up", "涨停", "yes"}
    pct = _number(row.get("pct_chg"))
    code = str(row.get("ts_code") or "")
    if pct is None:
        return False
    threshold = 19.5 if code.startswith(("300", "301", "688")) else 9.5
    return pct >= threshold


def _daily_metrics(
    current: pd.Series,
    history: pd.DataFrame,
    trade_date: str,
    macd_settings: dict[str, Any],
) -> dict[str, Any]:
    data = history.copy()
    if not data.empty:
        data["trade_date"] = data.get(
            "trade_date",
            pd.Series("", index=data.index),
        ).astype(str)
        data = data.sort_values("trade_date")
    current_date = str(trade_date)
    if data.empty or current_date not in set(data["trade_date"].tolist()):
        appended = {
            key: current.get(key)
            for key in (
                "ts_code", "open", "high", "low", "close", "pre_close",
                "vol", "amount", "amount_unit", "amount_source", "pct_chg",
                "limit_flag", "limit_time", "limit_open_count",
                "continuous_limit_days",
            )
        }
        appended["trade_date"] = current_date
        data = pd.concat([data, pd.DataFrame([appended])], ignore_index=True)
    else:
        mask = data["trade_date"].eq(current_date)
        for column in (
            "open", "high", "low", "close", "pre_close", "vol", "amount",
            "amount_unit", "amount_source", "pct_chg", "limit_flag",
            "limit_time", "limit_open_count", "continuous_limit_days",
        ):
            value = current.get(column)
            if value is not None and not pd.isna(value):
                data.loc[mask, column] = value

    data = data.sort_values("trade_date").tail(180).reset_index(drop=True)
    close = _series_number(data, "close")
    high = _series_number(data, "high").fillna(close)
    low = _series_number(data, "low").fillna(close)
    volume = _series_number(data, "vol")
    pct = _series_number(data, "pct_chg")
    amounts = data.apply(
        lambda row: _row_amount_yuan(row, history=True),
        axis=1,
    )
    amounts = pd.to_numeric(amounts, errors="coerce")
    metrics: dict[str, Any] = {}
    ma_series: dict[int, pd.Series] = {}
    for window in (5, 10, 20, 60):
        ma_series[window] = close.rolling(window, min_periods=window).mean()
        metrics[f"ma{window}"] = _last(ma_series[window])
    metrics["previous_ma60"] = (
        _number(ma_series[60].iloc[-2])
        if len(ma_series[60]) >= 2 and pd.notna(ma_series[60].iloc[-2])
        else None
    )
    metrics["ma60_declining"] = bool(
        metrics["ma60"] is not None
        and metrics["previous_ma60"] is not None
        and metrics["ma60"] < metrics["previous_ma60"]
    )
    metrics["return20"] = _pct_return(close, 20)
    recent60 = high.tail(60).dropna()
    high60 = float(recent60.max()) if not recent60.empty else None
    last_close = _last(close)
    metrics["high_60"] = high60
    metrics["high_position_60"] = (
        last_close / high60 if last_close is not None and high60 else None
    )
    metrics["average_amount_5d_yuan"] = (
        float(amounts.tail(5).mean())
        if amounts.tail(5).notna().any()
        else None
    )
    latest_volume = _last(volume)
    prior_volume = _number(volume.iloc[-2]) if len(volume) >= 2 else None
    latest_pct = _last(pct)
    prior_pct = _number(pct.iloc[-2]) if len(pct) >= 2 else None
    metrics["price_volume_confirmed"] = bool(
        latest_volume is not None
        and prior_volume is not None
        and latest_pct is not None
        and prior_pct is not None
        and latest_volume > prior_volume
        and latest_pct > prior_pct
    )
    metrics["price_volume_stagnation"] = bool(
        latest_volume is not None
        and prior_volume is not None
        and latest_pct is not None
        and prior_pct is not None
        and latest_volume > prior_volume
        and latest_pct < prior_pct
    )

    if close.notna().sum() >= 2:
        dif, dea, histogram = calculate_macd(
            close,
            macd_settings,
            min_periods=False,
        )
        metrics["macd_dif"] = _last(dif)
        metrics["macd_dea"] = _last(dea)
        metrics["macd_histogram"] = _last(histogram)
        metrics["macd_golden_cross"] = bool(
            len(dif) >= 2
            and pd.notna(dif.iloc[-1])
            and pd.notna(dea.iloc[-1])
            and pd.notna(dif.iloc[-2])
            and pd.notna(dea.iloc[-2])
            and dif.iloc[-1] > dea.iloc[-1]
            and dif.iloc[-2] <= dea.iloc[-2]
        )
        metrics["macd_above_zero"] = bool(
            metrics["macd_dif"] is not None
            and metrics["macd_dea"] is not None
            and metrics["macd_dif"] > 0
            and metrics["macd_dea"] > 0
        )
    else:
        metrics.update({
            "macd_dif": None,
            "macd_dea": None,
            "macd_histogram": None,
            "macd_golden_cross": False,
            "macd_above_zero": False,
        })

    limit_flags = data.apply(_limit_up_flag, axis=1)
    recent20 = data.tail(20).copy()
    recent20["_limit_up"] = limit_flags.tail(20).to_numpy()
    limit_rows = recent20[recent20["_limit_up"]]
    metrics["limit_count_20d"] = int(len(limit_rows))
    recent5_flags = limit_flags.tail(5)
    metrics["limit_in_5d"] = bool(recent5_flags.any())
    metrics["latest_limit_date"] = (
        str(limit_rows.iloc[-1]["trade_date"]) if not limit_rows.empty else None
    )
    continuous = _number(current.get("continuous_limit_days"))
    if continuous is None:
        count = 0
        for flag in reversed(limit_flags.tolist()):
            if not flag:
                break
            count += 1
        continuous = count
    metrics["continuous_limit_days"] = int(continuous or 0)
    metrics["limit_sealed"] = bool(
        _limit_up_flag(data.iloc[-1]) and not (
            (_number(current.get("limit_open_count"), 0) or 0) > 0
        )
    )
    metrics["limit_open_count"] = int(
        _number(current.get("limit_open_count"), 0) or 0
    )
    metrics["history_days"] = int(close.notna().sum())
    return metrics


def build_daily_factor_frame(
    market: pd.DataFrame,
    history: pd.DataFrame,
    trade_date: str,
    macd_settings: dict[str, Any] | None = None,
) -> pd.DataFrame:
    if market is None or market.empty:
        return pd.DataFrame()
    settings = dict(macd_settings or load_macd_settings())
    historical = history.copy() if isinstance(history, pd.DataFrame) else pd.DataFrame()
    groups = (
        {
            str(code): group.copy()
            for code, group in historical.groupby(
                historical.get(
                    "ts_code",
                    pd.Series("", index=historical.index),
                ).astype(str)
            )
        }
        if not historical.empty and "ts_code" in historical
        else {}
    )
    rows: list[dict[str, Any]] = []
    basis = macd_provenance(settings)
    for record in market.to_dict("records"):
        current = pd.Series(record)
        code = str(record.get("ts_code") or "")
        metrics = _daily_metrics(
            current,
            groups.get(code, pd.DataFrame()),
            str(trade_date),
            settings,
        )
        amount_yuan = normalize_amount_yuan(
            record.get("amount"),
            unit=record.get("amount_unit"),
            source=record.get("amount_source"),
        )
        history_quality = (
            "complete" if metrics["history_days"] >= 60 else "insufficient"
        )
        warnings: list[str] = []
        if history_quality == "insufficient":
            warnings.append("历史不足60日")
        reasons: list[str] = []
        name = str(record.get("name") or "")
        if any(marker in name.upper() for marker in ("ST", "退市")):
            reasons.append("风险股票")
        if (_number(record.get("vol"), 0) or 0) <= 0:
            reasons.append("停牌或无成交")
        pct_chg = _number(record.get("pct_chg"), 0) or 0
        if pct_chg > 7:
            reasons.append("当日涨幅超过7%")
        tail_volume = _number(record.get("tail_volume_ratio"))
        if (
            tail_volume is not None
            and tail_volume > TAIL_VOLUME_HARD_REJECT
        ):
            reasons.append("尾盘量能过热")
        turnover = _number(record.get("turnover_rate"))
        if (
            turnover is not None
            and turnover > TURNOVER_HARD_REJECT
        ):
            reasons.append("换手过高隔日兑现风险")
        return20 = _number(metrics.get("return20"))
        if (
            return20 is not None
            and return20 > RETURN20_HARD_REJECT
        ):
            reasons.append("近20日涨幅过热")
        high_position = _number(metrics.get("high_position_60"))
        if (
            high_position is not None
            and high_position > HIGH_POSITION_HARD_REJECT
            and (
                (
                    return20 is not None
                    and return20 > RETURN20_SOFT_RISK
                )
                or (
                    turnover is not None
                    and turnover > TURNOVER_SOFT_RISK
                )
            )
        ):
            reasons.append("接近60日高位兑现风险")
        if int(metrics.get("limit_count_20d") or 0) < 1:
            reasons.append("近20日无涨停基因")
        if metrics.get("limit_sealed"):
            reasons.append("当日封板买入受限")
        if int(metrics.get("continuous_limit_days") or 0) >= 2:
            reasons.append("连续涨停后隔日兑现风险高")
        average_amount = metrics.get("average_amount_5d_yuan")
        if (
            average_amount is not None
            and average_amount < MIN_AVERAGE_AMOUNT_YUAN
        ):
            reasons.append("近5日日均成交额低于5000万元")
        close_value = _number(record.get("close"))
        if (
            metrics.get("ma60") is not None
            and close_value is not None
            and close_value < metrics["ma60"]
            and metrics.get("ma60_declining")
        ):
            reasons.append("MA60下降且股价位于MA60下方")
        rows.append({
            **record,
            **metrics,
            **basis,
            "score_version": current_score_version(settings),
            "amount_yuan": amount_yuan,
            "history_quality": history_quality,
            "data_quality_warnings": warnings,
            "eligible_tail_premium": not reasons,
            "exclusion_reasons": reasons,
        })
    return pd.DataFrame(rows)


def eligible_tail_universe(factors: pd.DataFrame) -> pd.DataFrame:
    if factors is None or factors.empty:
        return pd.DataFrame()
    mask = factors.get(
        "eligible_tail_premium",
        pd.Series(False, index=factors.index),
    ).fillna(False).astype(bool)
    return factors[mask].copy().reset_index(drop=True)


def _tail_return_points(value: float) -> float:
    if 0.5 < value <= 1.1:
        return 20
    if value == 0.5 or 1.1 < value <= 1.3:
        return 15
    if value > 1.3:
        return 3
    if value >= 0.3:
        return 15
    if value >= 0.1:
        return 10
    if value >= 0:
        return 5
    return -10


def _auction_points(value: float) -> float:
    if value >= 0.3:
        return 15
    if value >= 0.1:
        return 10
    if value >= -0.1:
        return 5
    return -10


def _close_position_points(value: float) -> float:
    if value >= 0.9:
        return 20
    if value >= 0.75:
        return 15
    if value >= 0.5:
        return 5
    return -10


def _tail_volume_points(ratio: float, tail_return: float) -> float:
    if ratio > 3 and tail_return < 0:
        return -10
    if ratio > TAIL_VOLUME_HARD_REJECT:
        return -15
    if TAIL_VOLUME_SOFT_RISK < ratio <= TAIL_VOLUME_HARD_REJECT:
        return -5
    if 1.2 <= ratio <= TAIL_VOLUME_SOFT_RISK:
        return 10
    if 1 <= ratio < 1.2:
        return 5
    return 0


def _opening_auction_return(row: dict[str, Any]) -> float:
    provided = _number(row.get("opening_auction_return"))
    if provided is not None:
        return provided
    open_price = _number(row.get("open"))
    pre_close = _number(row.get("pre_close")) or _number(
        row.get("previous_close")
    )
    if open_price is None or not pre_close:
        return 0.0
    return (open_price / pre_close - 1) * 100


def _limit_score(row: dict[str, Any]) -> float:
    count = int(_number(row.get("limit_count_20d"), 0) or 0)
    if bool(row.get("limit_in_5d")):
        base = 5
    elif count >= 1:
        base = 3
    else:
        base = 0
    return min(5.0, max(0.0, float(base)))


def _sector_score(row: dict[str, Any]) -> float:
    explicit = _number(row.get("sector_score"))
    if explicit is not None and 0 <= explicit <= 15:
        return explicit
    change = _number(
        row.get("sector_change"),
        _number(row.get("sector_avg_pct_chg"), 0),
    ) or 0
    rank = _number(row.get("sector_rank"))
    limit_count = _number(
        row.get("sector_limit_count"),
        _number(row.get("sector_limit_up_count"), 0),
    ) or 0
    up_ratio = _number(row.get("sector_up_ratio"), 0) or 0
    if up_ratio > 1:
        up_ratio /= 100
    points = min(6.0, max(0.0, change) * 2)
    if rank is not None:
        points += 4 if rank <= 5 else 3 if rank <= 10 else 1 if rank <= 20 else 0
    points += min(3.0, limit_count)
    points += 2 if up_ratio >= 0.7 else 1 if up_ratio >= 0.5 else 0
    return min(15.0, max(0.0, points))


def _trend_score(row: dict[str, Any]) -> float:
    if str(row.get("history_quality") or "") == "insufficient":
        history_cap = 5.0
    else:
        history_cap = 10.0
    ma5 = _number(row.get("ma5"))
    ma10 = _number(row.get("ma10"))
    ma20 = _number(row.get("ma20"))
    close = _number(row.get("close"))
    points = 0.0
    if None not in (ma5, ma10, ma20) and ma5 > ma10 > ma20:
        points += 4
    if close is not None and ma20 is not None and close > ma20:
        points += 2
    if row.get("macd_golden_cross"):
        points += 2
    if row.get("macd_above_zero"):
        points += 2
    return min(history_cap, points)


def _volume_score(row: dict[str, Any]) -> float:
    ratio = _number(row.get("volume_ratio"), 0) or 0
    if 1.5 <= ratio <= 3:
        points = 7.0
    elif 1 <= ratio < 1.5:
        points = 4.0
    elif ratio > 5:
        points = -5.0
    else:
        points = 0.0
    if row.get("price_volume_confirmed"):
        points += 3
    if ratio > 5 and (_number(row.get("pct_chg"), 0) or 0) < 2:
        points -= 3
    return min(10.0, max(0.0, points))


def _position_score(row: dict[str, Any]) -> float:
    return20 = _number(row.get("return20"), 0) or 0
    high_position = _number(row.get("high_position_60"))
    if 10 <= return20 <= 40:
        points = 8.0
    elif 40 < return20 <= 80:
        points = 4.0
    elif return20 > 80:
        points = -5.0
    else:
        points = 3.0
    if high_position is not None:
        if high_position >= HIGH_POSITION_HARD_REJECT:
            points -= 6
        elif high_position >= HIGH_POSITION_SOFT_RISK:
            points -= 3
        elif 0.6 <= high_position <= 0.9:
            points += 2
    return min(10.0, max(0.0, points))


def _risk(row: dict[str, Any]) -> tuple[float, list[str]]:
    risks: list[str] = []
    score = 0.0
    return20 = _number(row.get("return20"), 0) or 0
    volume_ratio = _number(row.get("volume_ratio"), 0) or 0
    open_price = _number(row.get("open"), 0) or 0
    close = _number(row.get("close"), 0) or 0
    high = _number(row.get("high"), close) or close
    low = _number(row.get("low"), close) or close
    position = _number(row.get("high_position_60"), 0) or 0
    turnover = _number(row.get("turnover_rate"), 0) or 0
    if return20 > 50 and volume_ratio > 3 and close < open_price:
        risks.append("高位巨量阴线")
        score += 15
    if row.get("price_volume_stagnation"):
        risks.append("高位放量滞涨")
        score += 8
    shadow = (high - close) / (high - low) if high > low else 0
    if shadow >= 0.45:
        risks.append("长上影线")
        score += 8
    if return20 > RETURN20_HARD_REJECT:
        risks.append("近20日涨幅过热")
        score += 12
    elif return20 > RETURN20_SOFT_RISK:
        risks.append("短线涨幅偏高")
        score += 6
    if position > HIGH_POSITION_HARD_REJECT:
        risks.append("接近60日高位兑现风险")
        score += 12
    elif position >= HIGH_POSITION_SOFT_RISK:
        risks.append("60日高位附近")
        score += 6
    if turnover > TURNOVER_HARD_REJECT:
        risks.append("换手过高隔日兑现风险")
        score += 12
    elif turnover > TURNOVER_SOFT_RISK:
        risks.append("换手偏高")
        score += 6
    if position >= 0.9 and turnover > 15:
        risks.append("高位高换手")
        score += 8
    tail_return = _number(row.get("tail_return_after_1430"), 0) or 0
    tail_volume = _number(row.get("tail_volume_ratio"), 0) or 0
    sector_score = _sector_score(row)
    if tail_return > 1.8 or tail_volume > TAIL_VOLUME_HARD_REJECT:
        risks.append("尾盘或量能过热")
        score += 15
    elif tail_return > 1.3 or tail_volume > TAIL_VOLUME_SOFT_RISK:
        risks.append("尾盘或量能过热")
        score += 8
    if sector_score < 9 and tail_return <= 0.1 and 0 < tail_volume < 1.0:
        risks.append("弱板块尾盘缩量无推动")
        score += 10
    return min(40.0, score), list(dict.fromkeys(risks))


def score_tail_premium_row(row: dict[str, Any] | pd.Series) -> dict[str, Any]:
    source = dict(row)
    tail_return = _number(source.get("tail_return_after_1430"), 0) or 0
    auction_return = _opening_auction_return(source)
    close_position = _number(source.get("tail_close_position"), 0) or 0
    tail_volume = _number(source.get("tail_volume_ratio"), 0) or 0
    tail_raw = (
        _tail_return_points(tail_return)
        + _auction_points(auction_return)
        + _close_position_points(close_position)
        + _tail_volume_points(tail_volume, tail_return)
    )
    tail_score = min(
        MODULE_WEIGHTS["tail"],
        max(0.0, tail_raw) / 65.0 * MODULE_WEIGHTS["tail"],
    )
    limit_score = _limit_score(source)
    sector_score = _sector_score(source)
    trend_score = _trend_score(source)
    volume_score = _volume_score(source)
    position_score = _position_score(source)
    risk_score, risk_items = _risk(source)
    gross = (
        tail_score
        + limit_score
        + sector_score
        + trend_score
        + volume_score
        + position_score
    )
    premium_score = min(100.0, max(0.0, gross - risk_score))
    reasons: list[str] = []
    if tail_score >= 20:
        reasons.append("尾盘承接与强度较好")
    if sector_score >= 9:
        reasons.append("所属板块强势")
    if limit_score >= 3:
        reasons.append("具备近期涨停基因")
    if trend_score >= 7:
        reasons.append("均线与MACD趋势占优")
    if volume_score >= 7:
        reasons.append("成交量处于健康区间")
    if not reasons:
        reasons.append("综合信号仍需盘末确认")
    risk_level = (
        "高" if risk_score >= 18 else "中" if risk_score >= 8 else "低"
    )
    return {
        **source,
        "opening_auction_return": round(auction_return, 6),
        "tail_auction_return": round(auction_return, 6),
        "tail_raw_score": round(tail_raw, 2),
        "tail_score": round(tail_score, 2),
        "limit_score": round(limit_score, 2),
        "sector_score": round(sector_score, 2),
        "trend_score": round(trend_score, 2),
        "volume_score": round(volume_score, 2),
        "position_score": round(position_score, 2),
        "risk_score": round(risk_score, 2),
        "premium_score": round(premium_score, 2),
        "overnight_candidate_score": round(premium_score, 2),
        "risk_level": risk_level,
        "buy_reasons": reasons,
        "risk_items": risk_items,
        "next_day_plan": (
            "目标收益+3%，止损-3%；高开3%以上先兑现一半，"
            "冲高不封板或跌破分时均价逐步退出"
        ),
        "next_morning_sell_plan": (
            "目标收益+3%，止损-3%；高开3%以上先兑现一半，"
            "冲高不封板或跌破分时均价逐步退出"
        ),
    }


def rank_tail_premium_candidates(
    frame: pd.DataFrame,
    limit: int = 20,
) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    result = frame.copy()
    for column in (
        "premium_score", "tail_score", "sector_score", "amount_yuan",
    ):
        result[column] = pd.to_numeric(
            result.get(column, 0),
            errors="coerce",
        ).fillna(0)
    result["ts_code"] = result.get(
        "ts_code",
        pd.Series("", index=result.index),
    ).astype(str)
    return (
        result.sort_values(
            [
                "premium_score", "tail_score", "sector_score",
                "amount_yuan", "ts_code",
            ],
            ascending=[False, False, False, False, True],
            kind="mergesort",
        )
        .head(max(1, min(int(limit), 100)))
        .reset_index(drop=True)
    )
