package com.codec.quantserver.service;

import com.codec.quantserver.dto.QuantScanRequest;
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

