from __future__ import annotations

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
    load_build_status,
    replace_review_snapshot,
    save_build_status,
)
from free_review_scoring import SCORE_VERSION, build_review_snapshot
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
        "score_version": SCORE_VERSION,
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
    common = {
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
            SCORE_VERSION,
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
    with _build_lock:
        existing = load_build_status(trade_date, SCORE_VERSION)
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
