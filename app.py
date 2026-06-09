import logging

from fastapi import FastAPI, HTTPException, Query

from ai_evaluation_service import evaluate_ai_recommendations
from backtest_service import run_backtest
from database import get_latest_report, get_report, init_db, list_reports, save_report
from quant_service import run_quant_scan


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


@app.post("/api/scan/run")
def run_scan(
    include_ai: bool = Query(False, description="是否调用 Ollama 生成 AI 分析"),
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
