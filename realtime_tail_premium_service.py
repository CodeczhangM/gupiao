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
    rank_sector_potential,
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
        try:
            sectors = rank_sector_potential(
                market,
                history,
                limit=50,
                leaders_per_sector=5,
            )
        except Exception:
            sectors = pd.DataFrame()
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


def _latest_bar_time(frame: pd.DataFrame) -> str | None:
    if frame is None or frame.empty or "trade_time" not in frame:
        return None
    times = pd.to_datetime(frame["trade_time"], errors="coerce").dropna()
    if times.empty:
        return None
    return times.max().isoformat(sep=" ", timespec="seconds")


def _load_and_score(
    stock: dict[str, Any],
    trade_date: str,
    now: datetime,
    minute_loader: Callable[..., MinuteLoadResult] | None,
    sector: dict[str, Any],
    selection_state: str,
) -> tuple[dict[str, Any], str | None, list[str]]:
    warnings: list[str] = []
    tail_bars = pd.DataFrame()
    minute_source = "unavailable"
    if minute_loader is not None:
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
                loaded_tail.source if not tail_bars.empty else minute_source
            )
            warnings.extend(loaded_tail.warnings)
        except Exception as exc:
            warnings.append(f"尾盘1分钟数据失败: {str(exc)[:120]}")

    tail_signal = _tail_next_day_bias(
        tail_bars,
        _finite(stock.get("pct_chg"), 0),
    )
    scored = score_tail_premium_row({
        **stock,
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
    return result, latest, warnings


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
    factors = build_daily_factor_frame(market, history, trade_date)
    eligible = _prefilter(
        eligible_tail_universe(factors),
        max_fetch=max_fetch,
    )
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
    latest_times = [item[1] for item in loaded if item[1]]
    warnings = list(dict.fromkeys(
        warning
        for item in loaded
        for warning in item[2]
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
