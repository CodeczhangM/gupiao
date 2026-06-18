package com.codec.quantserver.controller;

import com.codec.quantserver.dto.QuantBacktestRequest;
import com.codec.quantserver.dto.QuantScanRequest;
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
}
