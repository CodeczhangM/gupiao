from fastapi import FastAPI, HTTPException, Query

from database import get_latest_report, get_report, init_db, list_reports, save_report
from quant_service import run_quant_scan


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
