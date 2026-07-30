package com.codec.quantserver.service;

import com.codec.quantserver.dto.QuantBacktestRequest;
import com.codec.quantserver.dto.QuantScanRequest;
import com.codec.quantserver.dto.TradeReviewRequest;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;

import java.util.Map;

@Service
public class QuantPythonClient {

    private final RestClient restClient;

    public QuantPythonClient(RestClient quantRestClient) {
        this.restClient = quantRestClient;
    }

    public Map<String, Object> health() {
        return getMap("/health");
    }

    public Map<String, Object> databaseHealth() {
        return getMap("/health/db");
    }

    public Map<String, Object> cacheStatus() {
        return getMap("/api/cache/status");
    }

    public Map<String, Object> syncCache(boolean forceCurrent) {
        return restClient.post()
                .uri(uriBuilder -> uriBuilder.path("/api/cache/sync")
                        .queryParam("force_current", forceCurrent).build())
                .retrieve().body(mapType());
    }

    public Map<String, Object> runScan(QuantScanRequest request) {
        int limit = Math.max(1, Math.min(request.getLimit(), 100));
        return restClient.post()
                .uri(uriBuilder -> uriBuilder
                        .path("/api/scan/run")
                        .queryParam("include_ai", request.isIncludeAi())
                        .queryParam("limit", limit)
                        .build())
                .retrieve()
                .body(mapType());
    }

    public Map<String, Object> runBacktest(QuantBacktestRequest request) {
        int lookbackDays = Math.max(1, Math.min(request.getLookbackDays(), 120));
        int holdDays = Math.max(1, Math.min(request.getHoldDays(), 20));
        int limit = Math.max(1, Math.min(request.getLimit(), 100));
        return restClient.post()
                .uri(uriBuilder -> uriBuilder
                        .path("/api/backtest/run")
                        .queryParam("lookback_days", lookbackDays)
                        .queryParam("hold_days", holdDays)
                        .queryParam("limit", limit)
                        .build())
                .retrieve()
                .body(mapType());
    }

    public Map<String, Object> evaluateAi(int holdDays, int reportLimit, int stockLimit) {
        int safeHoldDays = Math.max(1, Math.min(holdDays, 20));
        int safeReportLimit = Math.max(1, Math.min(reportLimit, 200));
        int safeStockLimit = Math.max(1, Math.min(stockLimit, 100));
        return restClient.get()
                .uri(uriBuilder -> uriBuilder
                        .path("/api/evaluation/ai")
                        .queryParam("hold_days", safeHoldDays)
                        .queryParam("report_limit", safeReportLimit)
                        .queryParam("stock_limit", safeStockLimit)
                        .build())
                .retrieve()
                .body(mapType());
    }

    public Map<String, Object> reviewTrade(TradeReviewRequest request) {
        return restClient.post()
                .uri("/api/trade-review/analyze")
                .body(request)
                .retrieve()
                .body(mapType());
    }

    public Object listReports(int limit) {
        int safeLimit = Math.max(1, Math.min(limit, 100));
        return restClient.get()
                .uri(uriBuilder -> uriBuilder
                        .path("/api/reports")
                        .queryParam("limit", safeLimit)
                        .build())
                .retrieve()
                .body(Object.class);
    }

    public Map<String, Object> latestReport() {
        return getMap("/api/reports/latest");
    }

    public Map<String, Object> reportDetail(long reportId) {
        return restClient.get()
                .uri("/api/reports/{reportId}", reportId)
                .retrieve()
                .body(mapType());
    }

    public Map<String, Object> latestStrong() {
        return getMap("/api/scan/latest/strong");
    }

    public Map<String, Object> latestDip() {
        return getMap("/api/scan/latest/dip");
    }

    public Map<String, Object> intradayMonitor(boolean forceRefresh) {
        return restClient.get()
                .uri(uriBuilder -> uriBuilder
                        .path("/api/intraday-monitor")
                        .queryParam("force_refresh", forceRefresh)
                        .build())
                .retrieve()
                .body(mapType());
    }

    public Map<String, Object> overnightMonitor(int limit) {
        int safeLimit = Math.max(1, Math.min(limit, 100));
        return restClient.get()
                .uri(uriBuilder -> uriBuilder
                        .path("/api/overnight-monitor")
                        .queryParam("limit", safeLimit)
                        .build())
                .retrieve()
                .body(mapType());
    }

    public Map<String, Object> morningFollowMonitor(int limit) {
        int safeLimit = Math.max(1, Math.min(limit, 100));
        return restClient.get()
                .uri(uriBuilder -> uriBuilder
                        .path("/api/morning-follow-monitor")
                        .queryParam("limit", safeLimit)
                        .build())
                .retrieve()
                .body(mapType());
    }

    public Map<String, Object> realtimeInfo(int limit, boolean forceRefresh) {
        int safeLimit = Math.max(1, Math.min(limit, 100));
        return restClient.get()
                .uri(uriBuilder -> uriBuilder
                        .path("/api/realtime-info")
                        .queryParam("limit", safeLimit)
                        .queryParam("force_refresh", forceRefresh)
                        .build())
                .retrieve()
                .body(mapType());
    }

    private Map<String, Object> getMap(String uri) {
        return restClient.get()
                .uri(uri)
                .retrieve()
                .body(mapType());
    }

    @SuppressWarnings("unchecked")
    private Class<Map<String, Object>> mapType() {
        return (Class<Map<String, Object>>) (Class<?>) Map.class;
    }
}
