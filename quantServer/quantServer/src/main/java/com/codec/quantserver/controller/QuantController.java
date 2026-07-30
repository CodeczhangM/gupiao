package com.codec.quantserver.controller;

import com.codec.quantserver.dto.QuantBacktestRequest;
import com.codec.quantserver.dto.QuantScanRequest;
import com.codec.quantserver.dto.TradeReviewRequest;
import com.codec.quantserver.service.QuantPythonClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
@RequestMapping("/api/quant")
public class QuantController {

    private final QuantPythonClient quantPythonClient;

    public QuantController(QuantPythonClient quantPythonClient) {
        this.quantPythonClient = quantPythonClient;
    }

    @GetMapping("/health")
    public Map<String, Object> health() {
        return quantPythonClient.health();
    }

    @GetMapping("/health/db")
    public Map<String, Object> databaseHealth() {
        return quantPythonClient.databaseHealth();
    }

    @GetMapping("/cache/status")
    public Map<String, Object> cacheStatus() {
        return quantPythonClient.cacheStatus();
    }

    @PostMapping("/cache/sync")
    public Map<String, Object> syncCache(
            @RequestParam(name = "forceCurrent", defaultValue = "false") boolean forceCurrent) {
        return quantPythonClient.syncCache(forceCurrent);
    }

    @PostMapping("/scan/run")
    public Map<String, Object> runScan(@RequestBody(required = false) QuantScanRequest request) {
        return quantPythonClient.runScan(request == null ? new QuantScanRequest() : request);
    }

    @PostMapping("/backtest/run")
    public Map<String, Object> runBacktest(@RequestBody(required = false) QuantBacktestRequest request) {
        return quantPythonClient.runBacktest(request == null ? new QuantBacktestRequest() : request);
    }

    @GetMapping("/backtest/run")
    public Map<String, Object> runBacktest(
            @RequestParam(defaultValue = "30") int lookbackDays,
            @RequestParam(defaultValue = "3") int holdDays,
            @RequestParam(defaultValue = "20") int limit
    ) {
        QuantBacktestRequest request = new QuantBacktestRequest();
        request.setLookbackDays(lookbackDays);
        request.setHoldDays(holdDays);
        request.setLimit(limit);
        return quantPythonClient.runBacktest(request);
    }

    @GetMapping("/evaluation/ai")
    public Map<String, Object> evaluateAi(
            @RequestParam(defaultValue = "3") int holdDays,
            @RequestParam(defaultValue = "50") int reportLimit,
            @RequestParam(defaultValue = "20") int stockLimit
    ) {
        return quantPythonClient.evaluateAi(holdDays, reportLimit, stockLimit);
    }

    @PostMapping("/trade-review/analyze")
    public Map<String, Object> reviewTrade(@RequestBody TradeReviewRequest request) {
        return quantPythonClient.reviewTrade(request);
    }

    @GetMapping("/reports")
    public Object reports(@RequestParam(defaultValue = "20") int limit) {
        return quantPythonClient.listReports(limit);
    }

    @GetMapping("/reports/latest")
    public Map<String, Object> latestReport() {
        return quantPythonClient.latestReport();
    }

    @GetMapping("/reports/{reportId}")
    public Map<String, Object> reportDetail(@PathVariable long reportId) {
        return quantPythonClient.reportDetail(reportId);
    }

    @GetMapping("/scan/latest/strong")
    public Map<String, Object> latestStrong() {
        return quantPythonClient.latestStrong();
    }

    @GetMapping("/scan/latest/dip")
    public Map<String, Object> latestDip() {
        return quantPythonClient.latestDip();
    }

    @GetMapping("/intraday-monitor")
    public Map<String, Object> intradayMonitor(
            @RequestParam(name = "force_refresh", defaultValue = "false") boolean forceRefresh) {
        return quantPythonClient.intradayMonitor(forceRefresh);
    }

    @GetMapping("/overnight-monitor")
    public Map<String, Object> overnightMonitor(@RequestParam(defaultValue = "30") int limit) {
        return quantPythonClient.overnightMonitor(limit);
    }

    @GetMapping("/morning-follow-monitor")
    public Map<String, Object> morningFollowMonitor(@RequestParam(defaultValue = "10") int limit) {
        return quantPythonClient.morningFollowMonitor(limit);
    }

    @GetMapping("/realtime-info")
    public Map<String, Object> realtimeInfo(
            @RequestParam(defaultValue = "10") int limit,
            @RequestParam(name = "force_refresh", defaultValue = "false") boolean forceRefresh) {
        return quantPythonClient.realtimeInfo(limit, forceRefresh);
    }
}
