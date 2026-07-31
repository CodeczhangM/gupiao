from __future__ import annotations

import csv
import io
import threading
from datetime import datetime
from typing import Any

import pandas as pd

from data_service import _query_tushare
from financial_cache import (
    load_financial_as_of,
    sync_financial_indicators,
)
from free_review_repository import (
    load_review_export_rows,
    load_review_meta,
    load_review_sectors,
    load_build_status,
    query_review_snapshot,
    replace_review_snapshot,
    save_build_status,
)
from free_review_models import FreeReviewQuery
from free_review_scoring import (
    build_review_snapshot,
    current_score_version,
)
from market_cache import (
    get_complete_dates,
    load_market_snapshot,
    load_recent_daily,
)


_build_lock = threading.Lock()


def _status(
    trade_date: str,
    status: str,
    stage: str,
    **values: Any,
) -> dict[str, Any]:
    return {
        "trade_date": str(trade_date),
        "score_version": current_score_version(),
        "status": status,
        "stage": stage,
        **values,
    }


def _financial_coverage(snapshot: pd.DataFrame) -> float:
    if snapshot is None or snapshot.empty:
        return 0.0
    if "financial_end_date" not in snapshot:
        return 0.0
    return round(
        float(snapshot["financial_end_date"].notna().mean()),
        4,
    )


def build_free_review_snapshot(
    trade_date: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    current = str(trade_date) if trade_date else None
    if current is None:
        dates = get_complete_dates(1)
        if not dates:
            raise LookupError("行情缓存中没有完整交易日")
        current = str(dates[0])
    started_at = datetime.now()
    score_version = current_score_version()
    common = {
        "score_version": score_version,
        "started_at": started_at,
        "total_count": 0,
        "processed_count": 0,
        "failed_count": 0,
        "warnings": [],
    }
    try:
        save_build_status(_status(
            current, "running", "cache", **common
        ))
        market = load_market_snapshot(current)
        history = load_recent_daily(current, 100)
        if market is None or market.empty:
            raise LookupError(f"{current} 市场快照未就绪")
        if history is None or history.empty:
            raise LookupError(f"{current} 历史行情未就绪")

        save_build_status(_status(
            current, "running", "financial", **common
        ))
        sync_result = sync_financial_indicators(
            _query_tushare,
            current,
            quarters=8,
        )
        warnings = [
            f"{item.get('period')}: {item.get('error')}"
            for item in sync_result.get("failed_periods", [])
        ]
        financial = load_financial_as_of(current, periods=8)

        save_build_status(_status(
            current,
            "running",
            "scoring",
            **{**common, "warnings": warnings},
        ))
        snapshot = build_review_snapshot(
            market,
            history,
            financial,
            current,
        )
        total_count = 0 if snapshot is None else len(snapshot)
        coverage = _financial_coverage(snapshot)

        save_build_status(_status(
            current,
            "running",
            "persisting",
            **{
                **common,
                "total_count": total_count,
                "processed_count": total_count,
                "financial_coverage": coverage,
                "warnings": warnings,
            },
        ))
        replace_review_snapshot(
            current,
            score_version,
            snapshot if snapshot is not None else pd.DataFrame(),
        )
        result = _status(
            current,
            "success",
            "complete",
            total_count=total_count,
            processed_count=total_count,
            failed_count=0,
            financial_coverage=coverage,
            started_at=started_at,
            completed_at=datetime.now(),
            warnings=warnings,
        )
        save_build_status(result)
        return result
    except Exception as exc:
        save_build_status(_status(
            current,
            "failed",
            "failed",
            **common,
            completed_at=datetime.now(),
            error_message=str(exc)[:2000],
        ))
        raise


def start_free_review_build(force: bool = False) -> dict[str, Any]:
    dates = get_complete_dates(1)
    if not dates:
        raise LookupError("行情缓存中没有完整交易日")
    trade_date = str(dates[0])
    score_version = current_score_version()
    with _build_lock:
        existing = load_build_status(trade_date, score_version)
        if existing and existing.get("status") in {"pending", "running"}:
            return existing
        if existing and existing.get("status") == "success" and not force:
            return existing
        queued = _status(trade_date, "pending", "queued")
        save_build_status(queued)
        worker = threading.Thread(
            target=build_free_review_snapshot,
            args=(trade_date, force),
            name=f"free-review-{trade_date}",
            daemon=True,
        )
        worker.start()
        return queued


def query_free_review(request: FreeReviewQuery) -> dict[str, Any]:
    return query_review_snapshot(request)


def free_review_sectors(
    trade_date: str | None = None,
) -> dict[str, Any]:
    metadata = load_review_meta(trade_date)
    current = str(metadata["trade_date"])
    rows = load_review_sectors(current)
    return {
        "trade_date": current,
        "score_version": current_score_version(),
        "items": rows,
    }


def free_review_meta(
    trade_date: str | None = None,
) -> dict[str, Any]:
    return load_review_meta(trade_date)


def export_free_review_csv(
    request: FreeReviewQuery,
) -> tuple[str, bytes]:
    trade_date, rows = load_review_export_rows(request, limit=10000)
    preferred = [
        "trade_date", "ts_code", "name", "industry", "area", "market",
        "close", "pct_chg", "amount", "turnover_rate",
        "turnover_rate_f", "volume_ratio", "pe_ttm", "pb", "ps_ttm",
        "dv_ttm", "total_mv", "circ_mv",
        "ret_5", "ret_10", "ret_20", "ret_60",
        "ma20_slope", "ma60_slope", "macd_hist",
        "rsi6", "rsi12", "rsi24", "atr_pct",
        "roe", "roe_dt", "roa", "roic",
        "grossprofit_margin", "netprofit_margin",
        "current_ratio", "debt_to_assets", "ocf_to_or",
        "tr_yoy", "netprofit_yoy", "dt_netprofit_yoy", "ocf_yoy",
        "trend_score", "volume_price_score", "momentum_score",
        "valuation_score", "financial_quality_score",
        "financial_growth_score", "risk_penalty", "total_score",
        "data_completeness", "score_reasons", "risk_flags",
        "missing_fields",
    ]
    extra = sorted({
        key for row in rows for key in row
        if key not in preferred and key not in {"score_version", "generated_at"}
    })
    columns = preferred + extra
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=columns,
        extrasaction="ignore",
    )
    writer.writeheader()
    for row in rows:
        output = dict(row)
        for column in ("score_reasons", "risk_flags", "missing_fields"):
            value = output.get(column)
            if isinstance(value, list):
                output[column] = "；".join(str(item) for item in value)
        writer.writerow(output)
    content = b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")
    return f"free-review-{trade_date}.csv", content
