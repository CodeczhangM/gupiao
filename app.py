import logging

from fastapi import FastAPI, HTTPException, Query, Response

from ai_evaluation_service import evaluate_ai_recommendations
from backtest_service import run_backtest
from database import get_latest_report, get_report, init_db, list_reports, save_report
from quant_service import run_quant_scan
from realtime_info_service import build_realtime_info
from stock_detail_service import get_stock_technical_detail
from trade_review_service import review_trade
from data_service import get_trade_dates, sync_cached_market_data
from intraday_monitor_service import build_intraday_monitor
from market_cache import get_cache_status
from morning_follow_service import build_morning_follow_monitor
from overnight_monitor_service import build_overnight_monitor
from free_review_models import FreeReviewQuery
from indicator_settings import (
    load_macd_settings,
    macd_parameter_key,
    save_macd_settings_and_recalculate,
)
from indicator_settings_models import MacdSettingsUpdate
from free_review_repository import (
    load_build_status as load_free_review_build_status,
)
from free_review_service import (
    export_free_review_csv,
    free_review_meta,
    free_review_sectors,
    query_free_review,
    start_free_review_build,
)


logger = logging.getLogger(__name__)

app = FastAPI(
    title="量化选股分析后端",
    description="基于 Tushare 行情、规则策略和可选本地 AI 的半量化选股分析 API。",
    version="0.1.0",
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/db")
def database_health():
    try:
        init_db()
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/cache/sync")
def cache_sync(force_current: bool = Query(False)):
    try:
        return sync_cached_market_data(force_current=force_current)
    except Exception as exc:
        logger.exception("同步行情缓存失败")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/cache/status")
def cache_status():
    try:
        return get_cache_status(trade_date_loader=get_trade_dates)
    except Exception as exc:
        logger.exception("读取行情缓存状态失败")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/indicator-settings/macd")
def get_macd_indicator_settings():
    try:
        settings = load_macd_settings()
        return {
            **settings,
            "macd_parameter_key": macd_parameter_key(settings),
        }
    except Exception as exc:
        logger.exception("读取 MACD 全局设置失败")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.put("/api/indicator-settings/macd")
def put_macd_indicator_settings(request: MacdSettingsUpdate):
    try:
        return save_macd_settings_and_recalculate(
            request.fast_period,
            request.slow_period,
            request.signal_period,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("保存 MACD 全局设置失败")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/scan/run")
def run_scan(
    include_ai: bool = Query(False, description="是否调用 AI 生成分析"),
    limit: int = Query(20, ge=1, le=100, description="每类结果最多返回多少条"),
):
    try:
        report = run_quant_scan(include_ai=include_ai, limit=limit)
        report_id = save_report(report)
        report["id"] = report_id
        report["created_at"] = report["created_at"].isoformat(sep=" ")
        return report
    except Exception as exc:
        logger.exception("运行选股扫描失败")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/backtest/run")
def backtest(
    lookback_days: int = Query(30, ge=1, le=120, description="回测最近多少个可交易选股日"),
    hold_days: int = Query(3, ge=1, le=20, description="持有多少个交易日后卖出"),
    limit: int = Query(20, ge=1, le=100, description="每个交易日每个策略最多选多少只"),
):
    try:
        return run_backtest(lookback_days=lookback_days, hold_days=hold_days, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/evaluation/ai")
def ai_evaluation(
    hold_days: int = Query(3, ge=1, le=20, description="按 AI 推荐后持有多少个交易日评估"),
    report_limit: int = Query(50, ge=1, le=200, description="最多评估多少份历史 AI 报告"),
    stock_limit: int = Query(20, ge=1, le=100, description="每份报告每个策略最多评估多少只股票"),
):
    try:
        return evaluate_ai_recommendations(
            hold_days=hold_days,
            report_limit=report_limit,
            stock_limit=stock_limit,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/reports")
def reports(limit: int = Query(20, ge=1, le=100)):
    return list_reports(limit=limit)


@app.get("/api/reports/latest")
def latest_report():
    report = get_latest_report()
    if not report:
        raise HTTPException(status_code=404, detail="还没有选股报告，请先调用 /api/scan/run")
    return report


@app.get("/api/reports/{report_id}")
def report_detail(report_id: int):
    report = get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    return report


@app.get("/api/scan/latest/strong")
def latest_strong():
    report = get_latest_report()
    if not report:
        raise HTTPException(status_code=404, detail="还没有选股报告")
    return {
        "id": report["id"],
        "trade_date": report["trade_date"],
        "strong": report["strong"],
    }


@app.get("/api/scan/latest/dip")
def latest_dip():
    report = get_latest_report()
    if not report:
        raise HTTPException(status_code=404, detail="还没有选股报告")
    return {
        "id": report["id"],
        "trade_date": report["trade_date"],
        "dip": report["dip"],
        "sectors": report["sectors"],
        "rep_stocks": report["rep_stocks"],
    }


@app.get("/api/intraday-monitor")
def intraday_monitor(force_refresh: bool = False):
    try:
        return build_intraday_monitor(force_refresh=force_refresh)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("获取实时共振监控失败")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/overnight-monitor")
def overnight_monitor(limit: int = Query(10, ge=1, le=100)):
    try:
        return build_overnight_monitor(limit=limit)
    except Exception as exc:
        logger.exception("获取隔夜溢价监控失败")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/morning-follow-monitor")
def morning_follow_monitor(limit: int = Query(10, ge=1, le=100)):
    try:
        return build_morning_follow_monitor(limit=limit)
    except Exception as exc:
        logger.exception("获取次日早盘跟进失败")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/realtime-info")
def realtime_info(
    limit: int = Query(10, ge=1, le=100),
    force_refresh: bool = False,
):
    try:
        return build_realtime_info(
            limit=limit,
            force_refresh=force_refresh,
        )
    except Exception as exc:
        logger.exception("获取实时信息失败")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/free-review/build")
def free_review_build(force: bool = Query(False)):
    try:
        return start_free_review_build(force=force)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("启动自由复盘选股构建失败")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/free-review/build-status")
def free_review_build_status(
    trade_date: str | None = Query(None, pattern=r"^\d{8}$"),
):
    try:
        result = load_free_review_build_status(trade_date)
        if not result:
            raise LookupError("自由复盘选股尚无构建记录")
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("读取自由复盘选股构建状态失败")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/free-review/meta")
def free_review_metadata(
    trade_date: str | None = Query(None, pattern=r"^\d{8}$"),
):
    try:
        return {
            "ready": True,
            **free_review_meta(trade_date),
        }
    except LookupError as exc:
        return {
            "ready": False,
            "trade_date": trade_date,
            "score_version": "free-review-v1",
            "generated_at": None,
            "stock_count": 0,
            "sector_count": 0,
            "financial_coverage": 0,
            "available_filters": [],
            "data_warnings": [],
            "message": str(exc),
        }
    except Exception as exc:
        logger.exception("读取自由复盘选股元数据失败")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/free-review/query")
def free_review_query(request: FreeReviewQuery):
    try:
        return query_free_review(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("查询自由复盘选股失败")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/free-review/sectors")
def free_review_sector_summary(
    trade_date: str | None = Query(None, pattern=r"^\d{8}$"),
):
    try:
        return free_review_sectors(trade_date)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("读取自由复盘板块汇总失败")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/free-review/export")
def free_review_export(request: FreeReviewQuery):
    try:
        filename, content = export_free_review_csv(request)
        return Response(
            content=content,
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("导出自由复盘选股失败")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/stocks/{ts_code}/technical")
def stock_technical_detail(
    ts_code: str,
    trade_date: str = Query(..., pattern=r"^\d{8}$"),
    report_id: int | None = Query(None, ge=1),
):
    try:
        return get_stock_technical_detail(ts_code, trade_date, report_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("获取个股技术面失败")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/trade-review/analyze")
def trade_review(payload: dict):
    try:
        return review_trade(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("运行交易复盘失败")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
