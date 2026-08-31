package com.codec.quantserver.controller;

import com.codec.quantserver.dto.QuantBacktestRequest;
import com.codec.quantserver.dto.FreeReviewQueryRequest;
import com.codec.quantserver.dto.MacdSettingsRequest;
import com.codec.quantserver.dto.QuantScanRequest;
import com.codec.quantserver.dto.TradeReviewRequest;
import com.codec.quantserver.dto.CycleWatchCreateRequest;
import com.codec.quantserver.dto.CycleWatchUpdateRequest;
import com.codec.quantserver.service.QuantPythonClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.http.HttpHeaders;
import org.springframework.http.ResponseEntity;

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

    @GetMapping("/cycle-watchlist")
    public Map<String, Object> cycleWatchlist() {
        return quantPythonClient.cycleWatchlist();
    }

    @PostMapping("/cycle-watchlist")
    public Map<String, Object> createCycleWatch(
            @RequestBody CycleWatchCreateRequest request) {
        return quantPythonClient.createCycleWatch(request);
    }

    @PostMapping("/cycle-watchlist/check")
    public Map<String, Object> checkCycleWatch(
            @RequestBody(required = false) Map<String, String> request) {
        Map<String, String> body = request == null ? Map.of() : request;
        return quantPythonClient.checkCycleWatch(
                body.get("ts_code"), body.get("schedule_slot"));
    }

    @PostMapping("/cycle-watchlist/alerts/read")
    public Map<String, Object> readCycleWatchAlerts(
            @RequestBody(required = false) Map<String, String> request) {
        Map<String, String> body = request == null ? Map.of() : request;
        return quantPythonClient.readCycleWatchAlerts(body.get("trade_date"));
    }

    @PatchMapping("/cycle-watchlist/{tsCode}")
    public Map<String, Object> updateCycleWatch(
            @PathVariable String tsCode,
            @RequestBody CycleWatchUpdateRequest request) {
        return quantPythonClient.updateCycleWatch(tsCode, request);
    }

    @DeleteMapping("/cycle-watchlist/{tsCode}")
    public ResponseEntity<Void> deleteCycleWatch(@PathVariable String tsCode) {
        quantPythonClient.deleteCycleWatch(tsCode);
        return ResponseEntity.noContent().build();
    }

    @GetMapping("/cycle-watchlist/{tsCode}/history")
    public Map<String, Object> cycleWatchHistory(
            @PathVariable String tsCode,
            @RequestParam(defaultValue = "50") int limit) {
        return quantPythonClient.cycleWatchHistory(tsCode, Math.max(1, Math.min(limit, 200)));
    }

    @GetMapping("/cache/status")
    public Map<String, Object> cacheStatus() {
        return quantPythonClient.cacheStatus();
    }

    @GetMapping("/indicator-settings/macd")
    public Map<String, Object> macdSettings() {
        return quantPythonClient.macdSettings();
    }

    @PutMapping("/indicator-settings/macd")
    public Map<String, Object> updateMacdSettings(
            @RequestBody MacdSettingsRequest request) {
        return quantPythonClient.updateMacdSettings(request);
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
            @RequestParam(defaultValue = "20") int limit,
            @RequestParam(name = "force_refresh", defaultValue = "false") boolean forceRefresh) {
        return quantPythonClient.realtimeInfo(limit, forceRefresh);
    }

    @GetMapping("/realtime-info/position-candidates")
    public Map<String, Object> dailyPositionCandidates(
            @RequestParam(defaultValue = "10") int limit,
            @RequestParam(name = "force_refresh", defaultValue = "false") boolean forceRefresh,
            @RequestParam(name = "debug", defaultValue = "false") boolean debug) {
        return quantPythonClient.dailyPositionCandidates(limit, forceRefresh, debug);
    }

    @GetMapping("/realtime-info/tail-premium")
    public Map<String, Object> realtimeTailPremium(
            @RequestParam(defaultValue = "20") int limit,
            @RequestParam(name = "force_refresh", defaultValue = "false") boolean forceRefresh,
            @RequestParam(name = "debug", defaultValue = "false") boolean debug) {
        return quantPythonClient.realtimeTailPremium(limit, forceRefresh, debug);
    }

    @GetMapping("/market-news-summary")
    public Map<String, Object> marketNewsSummary(
            @RequestParam(defaultValue = "all") String market,
            @RequestParam(defaultValue = "8") int limit,
            @RequestParam(name = "force_refresh", defaultValue = "false") boolean forceRefresh,
            @RequestParam(name = "use_ai", defaultValue = "true") boolean useAi) {
        return quantPythonClient.marketNewsSummary(
                market, limit, forceRefresh, useAi);
    }

    @GetMapping("/stocks/{tsCode}/trend-box-target")
    public Map<String, Object> trendBoxTarget(
            @PathVariable String tsCode,
            @RequestParam(name = "end_trade_date") String endTradeDate,
            @RequestParam(name = "lookback_days", defaultValue = "120")
            int lookbackDays,
            @RequestParam(name = "auto_detect", defaultValue = "true")
            boolean autoDetect,
            @RequestParam(name = "box_start", required = false)
            String boxStart,
            @RequestParam(name = "box_end", required = false)
            String boxEnd) {
        return quantPythonClient.trendBoxTarget(
                tsCode, endTradeDate, lookbackDays, autoDetect, boxStart, boxEnd);
    }

    @GetMapping("/sector-rotation/tomorrow")
    public Map<String, Object> sectorRotationTomorrow(
            @RequestParam(name = "trade_date", required = false)
            String tradeDate,
            @RequestParam(defaultValue = "10") int limit,
            @RequestParam(name = "stocks_per_sector", defaultValue = "5")
            int stocksPerSector) {
        return quantPythonClient.sectorRotationTomorrow(
                tradeDate, limit, stocksPerSector);
    }

    @PostMapping("/free-review/build")
    public Map<String, Object> freeReviewBuild(
            @RequestParam(defaultValue = "false") boolean force) {
        return quantPythonClient.startFreeReviewBuild(force);
    }

    @GetMapping("/free-review/build-status")
    public Map<String, Object> freeReviewBuildStatus(
            @RequestParam(name = "trade_date", required = false)
            String tradeDate) {
        return quantPythonClient.freeReviewBuildStatus(tradeDate);
    }

    @GetMapping("/free-review/meta")
    public Map<String, Object> freeReviewMeta(
            @RequestParam(name = "trade_date", required = false)
            String tradeDate) {
        return quantPythonClient.freeReviewMeta(tradeDate);
    }

    @GetMapping("/free-review/sectors")
    public Map<String, Object> freeReviewSectors(
            @RequestParam(name = "trade_date", required = false)
            String tradeDate) {
        return quantPythonClient.freeReviewSectors(tradeDate);
    }

    @PostMapping("/free-review/query")
    public Map<String, Object> freeReviewQuery(
            @RequestBody(required = false) FreeReviewQueryRequest request) {
        return quantPythonClient.queryFreeReview(
                request == null ? new FreeReviewQueryRequest() : request);
    }

    @PostMapping("/free-review/export")
    public ResponseEntity<byte[]> freeReviewExport(
            @RequestBody(required = false) FreeReviewQueryRequest request) {
        ResponseEntity<byte[]> upstream = quantPythonClient.exportFreeReview(
                request == null ? new FreeReviewQueryRequest() : request);
        HttpHeaders headers = new HttpHeaders();
        if (upstream.getHeaders().getContentType() != null) {
            headers.setContentType(upstream.getHeaders().getContentType());
        }
        String disposition = upstream.getHeaders().getFirst(
                HttpHeaders.CONTENT_DISPOSITION);
        if (disposition != null) {
            headers.set(HttpHeaders.CONTENT_DISPOSITION, disposition);
        }
        return ResponseEntity.status(upstream.getStatusCode())
                .headers(headers)
                .body(upstream.getBody());
    }
}
