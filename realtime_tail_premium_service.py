from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import math
from typing import Any, Callable

import pandas as pd

from indicator_settings import macd_provenance
from overnight_monitor_service import (
    _datetime_window,
    _json_safe,
)
from realtime_market_source import MinuteLoadResult
from strategy import (
    _tail_next_day_bias,
)
from tail_premium_scoring import (
    build_daily_factor_frame,
    current_score_version,
    eligible_tail_universe,
    rank_tail_premium_candidates,
    score_tail_premium_row,
)


MAX_MINUTE_WORKERS = 4
DEFAULT_MAX_FETCH = 60


def _selection_state(now: datetime) -> tuple[str, str]:
    clock = now.strftime("%H:%M:%S")
    if clock < "14:50:00":
        return "waiting_tail_window", "14:50前预观察"
    if clock < "15:00:00":
        return "live_tail_window", "盘末动态候选"
    return "closed_final", "收盘最终结果"


def _sector_map(
    market: pd.DataFrame,
    history: pd.DataFrame,
    override: pd.DataFrame | None,
) -> dict[str, dict[str, Any]]:
    if override is None:
        if market is None or market.empty:
            sectors = pd.DataFrame()
        else:
            industry_column = (
                "industry_name" if "industry_name" in market else "industry"
            )
            if industry_column not in market:
                sectors = pd.DataFrame()
            else:
                data = market.copy()
                data["pct_chg"] = pd.to_numeric(
                    data.get("pct_chg", 0),
                    errors="coerce",
                ).fillna(0)
                sectors = (
                    data.groupby(industry_column, dropna=False)
                    .agg(
                        avg_pct_chg=("pct_chg", "mean"),
                        stock_count=("ts_code", "count"),
                    )
                    .reset_index()
                    .sort_values(
                        ["avg_pct_chg", "stock_count"],
                        ascending=[False, False],
                        kind="mergesort",
                    )
                    .head(50)
                    .reset_index(drop=True)
                )
                sectors["rank"] = sectors.index + 1
                sectors["limit_up_count"] = 0
                sectors["up_ratio"] = 0
                sectors["strong_count"] = sectors["stock_count"]
                sectors["potential_score"] = sectors["avg_pct_chg"].clip(
                    lower=0,
                    upper=10,
                ) * 10
    else:
        sectors = override.copy()
    if sectors is None or sectors.empty:
        return {}
    industry_column = (
        "industry_name" if "industry_name" in sectors else "industry"
    )
    if industry_column not in sectors:
        return {}
    result: dict[str, dict[str, Any]] = {}
    ordered = sectors.reset_index(drop=True)
    for index, row in ordered.iterrows():
        industry = str(row.get(industry_column) or "")
        if not industry:
            continue
        result[industry] = {
            "sector_change": _finite(
                row.get("avg_pct_chg"),
                row.get("sector_change"),
            ),
            "sector_rank": int(
                _finite(
                    row.get("rank"),
                    row.get("sector_rank"),
                    index + 1,
                )
                or index + 1
            ),
            "sector_limit_count": int(
                _finite(
                    row.get("limit_up_count"),
                    row.get("sector_limit_count"),
                    0,
                )
                or 0
            ),
            "sector_up_ratio": _finite(row.get("up_ratio"), 0),
            "sector_strong_count": int(
                _finite(row.get("strong_count"), 0) or 0
            ),
            "sector_potential_score": _finite(
                row.get("potential_score"),
            ),
        }
    return result


def _finite(*values: Any) -> float | None:
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            return number
    return None


def _resolve_previous_close_for_pct(stock: dict[str, Any]) -> Any:
    """Resolve previous close for realtime pct calculation.

    When pre_close/previous_close are missing, fall back to deriving from
    close and pct_chg instead of using the current close directly (which
    would zero out the computed pct_chg).
    """
    direct = _finite(stock.get("pre_close"), stock.get("previous_close"))
    if direct is not None:
        return direct
    close = _finite(stock.get("close"))
    pct_chg = _finite(stock.get("pct_chg"))
    if close and pct_chg is not None and pct_chg > -99:
        return close / (1 + pct_chg / 100)
    return close


def _prefilter(factors: pd.DataFrame, max_fetch: int) -> pd.DataFrame:
    if factors.empty:
        return factors
    data = factors.copy()
    for column in ("pct_chg", "volume_ratio", "turnover_rate", "amount_yuan"):
        data[column] = pd.to_numeric(
            data.get(column, 0),
            errors="coerce",
        ).fillna(0)
    data["_tail_prefilter_score"] = (
        data["pct_chg"].clip(-3, 9.5) * 2.5
        + data["volume_ratio"].clip(0, 4) * 5
        + data["turnover_rate"].clip(0, 20) * 0.4
        + (data["amount_yuan"] / 100_000_000).clip(0, 10)
    )
    return (
        data.sort_values(
            ["_tail_prefilter_score", "amount_yuan", "ts_code"],
            ascending=[False, False, True],
            kind="mergesort",
        )
        .head(max(20, min(int(max_fetch), 100)))
        .drop(columns=["_tail_prefilter_score"])
        .reset_index(drop=True)
    )


def _raw_tail_prefilter_market(
    market: pd.DataFrame,
    max_fetch: int,
    *,
    current_first: bool = False,
) -> pd.DataFrame:
    if market is None or market.empty or "ts_code" not in market:
        return pd.DataFrame()
    data = market.copy()
    data = data[
        ~data["ts_code"].astype(str).str.startswith(("688", "689"))
    ].copy()
    if data.empty:
        return pd.DataFrame()
    for column in ("pct_chg", "volume_ratio", "turnover_rate", "amount"):
        data[column] = pd.to_numeric(
            data.get(column, 0),
            errors="coerce",
        ).fillna(0)
    amount_unit = data.get("amount_unit")
    if amount_unit is not None:
        amount_yuan = data["amount"].where(
            amount_unit.astype(str).eq("yuan"),
            data["amount"] * 1000,
        )
    else:
        amount_yuan = data["amount"]
    if current_first:
        data["_raw_tail_prefilter_score"] = (
            (amount_yuan / 100_000_000).clip(0, 20) * 2.0
            + data["turnover_rate"].clip(0, 25) * 0.35
        )
    else:
        data["_raw_tail_prefilter_score"] = (
            data["pct_chg"].clip(-3, 10) * 3.0
            + data["volume_ratio"].clip(0, 5) * 5.0
            + data["turnover_rate"].clip(0, 25) * 0.35
            + (amount_yuan / 100_000_000).clip(0, 12)
        )
    return (
        data.sort_values(
            ["_raw_tail_prefilter_score", "amount", "ts_code"],
            ascending=[False, False, True],
            kind="mergesort",
        )
        .head(max(20, min(int(max_fetch), len(data))))
        .drop(columns=["_raw_tail_prefilter_score"])
        .reset_index(drop=True)
    )


def _latest_bar_time(frame: pd.DataFrame) -> str | None:
    if frame is None or frame.empty or "trade_time" not in frame:
        return None
    times = pd.to_datetime(frame["trade_time"], errors="coerce").dropna()
    if times.empty:
        return None
    return times.max().isoformat(sep=" ", timespec="seconds")


def _session_progress(now: datetime) -> float:
    minutes = now.hour * 60 + now.minute + now.second / 60
    morning_start = 9 * 60 + 30
    morning_end = 11 * 60 + 30
    afternoon_start = 13 * 60
    afternoon_end = 15 * 60
    if minutes <= morning_start:
        return 0.0
    if minutes < morning_end:
        return (minutes - morning_start) / 240
    if minutes < afternoon_start:
        return 0.5
    if minutes < afternoon_end:
        return (120 + minutes - afternoon_start) / 240
    return 1.0


def _current_day_minute_window(trade_date: str, now: datetime) -> tuple[str, str]:
    start, session_end = _datetime_window(trade_date, "09:30:00", "15:00:00")
    end = min(
        pd.Timestamp(session_end),
        pd.Timestamp(now.replace(second=0, microsecond=0)),
    )
    if end < pd.Timestamp(start):
        end = pd.Timestamp(start)
    return start, end.strftime("%Y-%m-%d %H:%M:%S")


def _minute_price_snapshot(
    bars: pd.DataFrame,
    trade_date: str,
    previous_close: Any,
    previous_volume: Any = None,
    previous_amount: Any = None,
    previous_amount_unit: Any = None,
    progress: float | None = None,
) -> dict[str, Any]:
    if bars is None or bars.empty or "trade_time" not in bars:
        return {}
    day_text = datetime.strptime(str(trade_date), "%Y%m%d").strftime(
        "%Y-%m-%d"
    )
    data = bars[bars["trade_time"].astype(str).str.startswith(day_text)].copy()
    if data.empty:
        return {}
    data["trade_time"] = pd.to_datetime(data["trade_time"], errors="coerce")
    data = data.dropna(subset=["trade_time"]).sort_values("trade_time")
    if data.empty:
        return {}
    for column in ("open", "high", "low", "close", "vol", "amount"):
        data[column] = (
            pd.to_numeric(data[column], errors="coerce")
            if column in data
            else None
        )
    closes = data["close"].dropna() if "close" in data else pd.Series(dtype=float)
    if closes.empty:
        return {}
    latest_close = float(closes.iloc[-1])
    snapshot: dict[str, Any] = {
        "close": latest_close,
        "high": (
            float(data["high"].max())
            if "high" in data and data["high"].notna().any()
            else latest_close
        ),
        "low": (
            float(data["low"].min())
            if "low" in data and data["low"].notna().any()
            else latest_close
        ),
        "amount": (
            float(data["amount"].sum())
            if "amount" in data and data["amount"].notna().any()
            else None
        ),
        "vol": (
            float(data["vol"].sum())
            if "vol" in data and data["vol"].notna().any()
            else None
        ),
    }
    previous = _finite(previous_close)
    if previous:
        snapshot["pct_chg"] = round((latest_close / previous - 1) * 100, 6)
    prior_amount = _finite(previous_amount)
    if prior_amount and str(previous_amount_unit or "").lower() not in {
        "yuan",
        "元",
        "cny",
    }:
        prior_amount *= 1000
    current_amount = snapshot.get("amount")
    if prior_amount and current_amount and progress and progress > 0:
        snapshot["volume_ratio"] = round(
            float(current_amount) / prior_amount / progress,
            6,
        )
        return snapshot
    prior_volume = _finite(previous_volume)
    current_volume = snapshot.get("vol")
    if prior_volume and current_volume and progress and progress > 0:
        snapshot["volume_ratio"] = round(
            float(current_volume) / prior_volume / progress,
            6,
        )
    return snapshot


def _warning_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if value is None:
        return []
    try:
        if pd.isna(value):
            return []
    except (TypeError, ValueError):
        pass
    return [str(value)] if str(value) else []


def _refresh_waiting_market_with_current_minutes(
    market: pd.DataFrame,
    trade_date: str,
    now: datetime,
    minute_loader: Callable[..., MinuteLoadResult] | None,
) -> tuple[pd.DataFrame, list[str], list[str]]:
    if market is None or market.empty or minute_loader is None:
        return market, [], []
    price_start, price_end = _current_day_minute_window(trade_date, now)

    def load(record: dict[str, Any]) -> dict[str, Any]:
        code = str(record.get("ts_code") or "")
        warnings: list[str] = []
        source = "unavailable"
        bars = pd.DataFrame()
        try:
            loaded = minute_loader(
                code,
                price_start,
                price_end,
                "1min",
                trade_date,
            )
            bars = loaded.bars
            source = loaded.source if not bars.empty else source
            warnings.extend(loaded.warnings)
        except Exception as exc:
            warnings.append(f"实时1分钟数据失败: {str(exc)[:120]}")
        snapshot = _minute_price_snapshot(
            bars,
            trade_date,
            _resolve_previous_close_for_pct(record),
            record.get("vol"),
            record.get("amount"),
            record.get("amount_unit"),
            _session_progress(now),
        )
        latest = _latest_bar_time(bars)
        result = {**record, **snapshot}
        if snapshot:
            result["amount_unit"] = "yuan"
            result["amount_source"] = source
        result["minute_data_source"] = source
        result["minute_data_warnings"] = list(dict.fromkeys(warnings))
        result["data_as_of"] = latest
        return result

    records = market.to_dict("records")
    if len(records) <= 1:
        refreshed = [load(record) for record in records]
    else:
        with ThreadPoolExecutor(
            max_workers=min(MAX_MINUTE_WORKERS, len(records))
        ) as executor:
            refreshed = list(executor.map(load, records))
    warnings = list(dict.fromkeys(
        warning
        for row in refreshed
        for warning in _warning_list(row.get("minute_data_warnings"))
    ))
    latest_times = [
        str(row.get("data_as_of"))
        for row in refreshed
        if row.get("data_as_of")
    ]
    return pd.DataFrame(refreshed), latest_times, warnings


def _filter_waiting_realtime_candidates(factors: pd.DataFrame) -> pd.DataFrame:
    if factors is None or factors.empty:
        return pd.DataFrame()
    data = factors.copy()
    pct = pd.to_numeric(data.get("pct_chg", 0), errors="coerce").fillna(0)
    volume_ratio = pd.to_numeric(
        data.get("volume_ratio", 0),
        errors="coerce",
    ).fillna(0)
    amount = pd.to_numeric(data.get("amount", 0), errors="coerce").fillna(0)
    current_minutes = data.get("data_as_of", pd.Series("", index=data.index))
    mask = (
        pct.ge(0)
        & volume_ratio.ge(1.0)
        & amount.ge(30_000_000)
        & current_minutes.astype(str).ne("")
    )
    return data[mask].copy().reset_index(drop=True)


def _load_and_score(
    stock: dict[str, Any],
    trade_date: str,
    now: datetime,
    minute_loader: Callable[..., MinuteLoadResult] | None,
    sector: dict[str, Any],
    selection_state: str,
    refresh_current_price: bool = False,
) -> tuple[dict[str, Any], str | None, list[str]]:
    warnings: list[str] = _warning_list(stock.get("minute_data_warnings"))
    tail_bars = pd.DataFrame()
    price_bars = pd.DataFrame()
    minute_source = str(stock.get("minute_data_source") or "unavailable")
    if minute_loader is not None:
        if refresh_current_price and selection_state == "waiting_tail_window":
            price_start, price_end = _current_day_minute_window(
                trade_date,
                now,
            )
            try:
                loaded_price = minute_loader(
                    str(stock.get("ts_code") or ""),
                    price_start,
                    price_end,
                    "1min",
                    trade_date,
                )
                price_bars = loaded_price.bars
                minute_source = (
                    loaded_price.source
                    if not price_bars.empty
                    else minute_source
                )
                warnings.extend(loaded_price.warnings)
            except Exception as exc:
                warnings.append(f"实时1分钟数据失败: {str(exc)[:120]}")
        if selection_state != "waiting_tail_window":
            tail_start, tail_end = _datetime_window(
                trade_date,
                "14:25:00",
                "15:00:00",
            )
            try:
                loaded_tail = minute_loader(
                    str(stock.get("ts_code") or ""),
                    tail_start,
                    min(
                        pd.Timestamp(tail_end),
                        pd.Timestamp(now.replace(second=0, microsecond=0)),
                    ).strftime("%Y-%m-%d %H:%M:%S"),
                    "1min",
                    trade_date,
                )
                tail_bars = loaded_tail.bars
                minute_source = (
                    loaded_tail.source
                    if not tail_bars.empty
                    else minute_source
                )
                warnings.extend(loaded_tail.warnings)
            except Exception as exc:
                warnings.append(f"尾盘1分钟数据失败: {str(exc)[:120]}")

    snapshot_bars = (
        price_bars
        if selection_state == "waiting_tail_window"
        else tail_bars
    )
    previous_close = (
        _resolve_previous_close_for_pct(stock)
        if refresh_current_price
        else stock.get("pre_close")
    )
    price_snapshot = (
        _minute_price_snapshot(
            snapshot_bars,
            trade_date,
            previous_close,
            stock.get("vol"),
            stock.get("amount"),
            stock.get("amount_unit"),
            _session_progress(now),
        )
        if refresh_current_price or selection_state != "waiting_tail_window"
        else {}
    )
    scored_input = {**stock, **price_snapshot}
    tail_signal = _tail_next_day_bias(
        tail_bars if selection_state != "waiting_tail_window" else pd.DataFrame(),
        _finite(scored_input.get("pct_chg"), 0),
    )
    scored = score_tail_premium_row({
        **scored_input,
        **sector,
        **tail_signal,
    })
    score = float(scored.get("premium_score") or 0)
    if selection_state == "waiting_tail_window":
        action = "等待14:50"
        bias = "盘末窗口未到"
    elif score >= 80 and not scored.get("risk_items"):
        action = "尾盘可买"
        bias = "隔夜高开优先"
    elif score >= 65:
        action = "轻仓观察"
        bias = "次日冲高套利"
    else:
        action = "观察"
        bias = "信号待确认"
    latest = _latest_bar_time(tail_bars)
    result = {
        **scored,
        "buyable_tail_signal": action,
        "overnight_bias": bias,
        "overnight_reason": "；".join(scored.get("buy_reasons") or []),
        "minute_data_source": minute_source,
        "minute_data_warnings": list(dict.fromkeys(warnings)),
        "tail_after_1430_available": bool(
            scored.get("tail_return_after_1430") is not None
            and tail_bars is not None
            and not tail_bars.empty
        ),
        "tail_auction_available": bool(
            scored.get("opening_auction_return") is not None
        ),
    }
    price_latest = _latest_bar_time(price_bars)
    preloaded_latest = stock.get("data_as_of")
    data_as_of = max(
        [
            str(value)
            for value in (latest, price_latest, preloaded_latest)
            if value
        ],
        default=None,
    )
    result["data_as_of"] = data_as_of
    return result, data_as_of, warnings


def build_realtime_tail_premium_monitor(
    limit: int = 20,
    max_fetch: int = DEFAULT_MAX_FETCH,
    max_leaders: int | None = None,
    now: datetime | None = None,
    *,
    market_override: pd.DataFrame | None = None,
    history_override: pd.DataFrame | None = None,
    trade_date_override: str | None = None,
    minute_loader: Callable[..., MinuteLoadResult] | None = None,
    source_metadata: dict[str, Any] | None = None,
    leader_codes_override: dict[str, dict[str, Any]] | None = None,
    sector_potential_override: pd.DataFrame | None = None,
) -> dict[str, Any]:
    del max_leaders, leader_codes_override
    current = now or datetime.now()
    trade_date = str(
        trade_date_override or current.strftime("%Y%m%d")
    )
    market = (
        market_override.copy()
        if isinstance(market_override, pd.DataFrame)
        else pd.DataFrame()
    )
    history = (
        history_override.copy()
        if isinstance(history_override, pd.DataFrame)
        else pd.DataFrame()
    )
    metadata = dict(source_metadata or {})
    if not market.empty and "amount_unit" not in market:
        market["amount_unit"] = (
            "yuan"
            if str(metadata.get("data_source") or "").startswith(
                "eastmoney"
            )
            else "thousand_yuan"
        )
    state, state_label = _selection_state(current)
    stale_waiting_refresh = state == "waiting_tail_window" and not bool(
        metadata.get("data_current", True)
    )
    raw_fetch = (
        max(1, min(int(max_fetch), 100))
        if stale_waiting_refresh
        else max(
            max_fetch * 4,
            max(1, int(limit)) * 4,
            120,
        )
    )
    factor_market = _raw_tail_prefilter_market(
        market,
        raw_fetch,
        current_first=stale_waiting_refresh,
    )
    refreshed_latest_times: list[str] = []
    refreshed_warnings: list[str] = []
    if stale_waiting_refresh:
        (
            factor_market,
            refreshed_latest_times,
            refreshed_warnings,
        ) = _refresh_waiting_market_with_current_minutes(
            factor_market,
            trade_date,
            current,
            minute_loader,
        )
    if not factor_market.empty and not history.empty and "ts_code" in history:
        factor_codes = set(factor_market["ts_code"].astype(str))
        factor_history = history[
            history["ts_code"].astype(str).isin(factor_codes)
        ].copy()
    else:
        factor_history = history
    factors = build_daily_factor_frame(factor_market, factor_history, trade_date)
    eligible = eligible_tail_universe(factors)
    if stale_waiting_refresh:
        eligible = _filter_waiting_realtime_candidates(eligible)
    eligible = _prefilter(eligible, max_fetch=max_fetch)
    sectors = _sector_map(
        market,
        history,
        sector_potential_override,
    )

    def worker(record: dict[str, Any]):
        return _load_and_score(
            record,
            trade_date,
            current,
            minute_loader,
            sectors.get(str(record.get("industry") or ""), {}),
            state,
            refresh_current_price=(
                not stale_waiting_refresh
                and not bool(metadata.get("data_current", True))
            ),
        )

    records = eligible.to_dict("records")
    if len(records) <= 1:
        loaded = [worker(record) for record in records]
    else:
        with ThreadPoolExecutor(
            max_workers=min(MAX_MINUTE_WORKERS, len(records))
        ) as executor:
            loaded = list(executor.map(worker, records))
    rows = [item[0] for item in loaded]
    latest_times = refreshed_latest_times + [
        item[1] for item in loaded if item[1]
    ]
    warnings = list(dict.fromkeys(
        list(refreshed_warnings)
        + [
            warning
            for item in loaded
            for warning in item[2]
        ]
    ))
    ranked = rank_tail_premium_candidates(
        pd.DataFrame(rows),
        limit=limit,
    )
    stocks = [_json_safe(row) for row in ranked.to_dict("records")]
    return _json_safe({
        "trade_date": trade_date,
        "data_trade_date": trade_date,
        "latest_trade_date": metadata.get(
            "latest_trade_date",
            trade_date,
        ),
        "data_current": metadata.get("data_current", True),
        "data_source": metadata.get(
            "data_source",
            "realtime_tail_premium",
        ),
        "data_as_of": max(latest_times) if latest_times else None,
        "selection_state": state,
        "selection_state_label": state_label,
        "market_phase": state_label,
        "auto_refresh_enabled": state != "closed_final",
        "refresh_interval_seconds": 30,
        "updated_at": current.isoformat(sep=" ", timespec="seconds"),
        "candidate_count": len(stocks),
        "eligible_count": int(len(eligible)),
        "screened_count": int(len(factors)),
        "failed_count": sum(
            1 for row in stocks if row.get("minute_data_warnings")
        ),
        "score_version": current_score_version(),
        "warnings": warnings[:20],
        "stocks": stocks,
        **macd_provenance(),
    })
