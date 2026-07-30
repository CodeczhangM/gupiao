package com.codec.quantserver.controller;

import com.codec.quantserver.dto.QuantBacktestRequest;
import com.codec.quantserver.dto.FreeReviewQueryRequest;
import com.codec.quantserver.dto.TradeReviewRequest;
import com.codec.quantserver.service.QuantPythonClient;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;

class QuantControllerTest {

    @Test
    void cacheSyncForwardsForceCurrent() throws Exception {
        QuantPythonClient client = mock(QuantPythonClient.class);
        when(client.syncCache(true)).thenReturn(Map.of("cache_updated", true));
        MockMvc mockMvc = MockMvcBuilders.standaloneSetup(new QuantController(client)).build();

        mockMvc.perform(post("/api/quant/cache/sync").param("forceCurrent", "true"))
                .andExpect(status().isOk());

        verify(client).syncCache(true);
    }

    @Test
    void tradeReviewForwardsRequestBody() throws Exception {
        QuantPythonClient quantPythonClient = mock(QuantPythonClient.class);
        when(quantPythonClient.reviewTrade(any(TradeReviewRequest.class))).thenReturn(Map.of("ai_summary", "ok"));
        MockMvc mockMvc = MockMvcBuilders.standaloneSetup(new QuantController(quantPythonClient))
                .setControllerAdvice(new ApiExceptionHandler())
                .build();

        mockMvc.perform(post("/api/quant/trade-review/analyze")
                        .contentType("application/json")
                        .content("{\"tsCode\":\"600001.SH\",\"buyDate\":\"20260601\",\"buyPrice\":10}"))
                .andExpect(status().isOk());

        ArgumentCaptor<TradeReviewRequest> requestCaptor = ArgumentCaptor.forClass(TradeReviewRequest.class);
        verify(quantPythonClient).reviewTrade(requestCaptor.capture());
        assertThat(requestCaptor.getValue().getTsCode()).isEqualTo("600001.SH");
    }

    @Test
    void getBacktestRunAcceptsQueryParameters() throws Exception {
        QuantPythonClient quantPythonClient = mock(QuantPythonClient.class);
        when(quantPythonClient.runBacktest(any(QuantBacktestRequest.class)))
                .thenReturn(Map.of("status", "ok"));
        MockMvc mockMvc = MockMvcBuilders
                .standaloneSetup(new QuantController(quantPythonClient))
                .setControllerAdvice(new ApiExceptionHandler())
                .build();

        mockMvc.perform(get("/api/quant/backtest/run")
                        .param("lookbackDays", "5")
                        .param("holdDays", "1")
                        .param("limit", "3"))
                .andExpect(status().isOk());

        ArgumentCaptor<QuantBacktestRequest> requestCaptor = ArgumentCaptor.forClass(QuantBacktestRequest.class);
        verify(quantPythonClient).runBacktest(requestCaptor.capture());
        QuantBacktestRequest request = requestCaptor.getValue();
        assertThat(request.getLookbackDays()).isEqualTo(5);
        assertThat(request.getHoldDays()).isEqualTo(1);
        assertThat(request.getLimit()).isEqualTo(3);
    }

    @Test
    void intradayMonitorForwardsToPythonClient() throws Exception {
        QuantPythonClient quantPythonClient = mock(QuantPythonClient.class);
        when(quantPythonClient.intradayMonitor(true)).thenReturn(Map.of("market_phase", "盘中监控"));
        MockMvc mockMvc = MockMvcBuilders.standaloneSetup(new QuantController(quantPythonClient)).build();

        mockMvc.perform(get("/api/quant/intraday-monitor")
                        .param("force_refresh", "true"))
                .andExpect(status().isOk());

        verify(quantPythonClient).intradayMonitor(true);
    }

    @Test
    void overnightMonitorForwardsLimitToPythonClient() throws Exception {
        QuantPythonClient quantPythonClient = mock(QuantPythonClient.class);
        when(quantPythonClient.overnightMonitor(15)).thenReturn(Map.of("market_phase", "尾盘盯盘"));
        MockMvc mockMvc = MockMvcBuilders.standaloneSetup(new QuantController(quantPythonClient)).build();

        mockMvc.perform(get("/api/quant/overnight-monitor").param("limit", "15"))
                .andExpect(status().isOk());

        verify(quantPythonClient).overnightMonitor(15);
    }

    @Test
    void morningFollowMonitorForwardsLimitToPythonClient() throws Exception {
        QuantPythonClient client = mock(QuantPythonClient.class);
        when(client.morningFollowMonitor(10)).thenReturn(Map.of("market_phase", "早盘确认"));
        MockMvc mockMvc = MockMvcBuilders.standaloneSetup(new QuantController(client)).build();

        mockMvc.perform(get("/api/quant/morning-follow-monitor").param("limit", "10"))
                .andExpect(status().isOk());

        verify(client).morningFollowMonitor(10);
    }

    @Test
    void realtimeInfoForwardsLimitAndForceRefreshToPythonClient() throws Exception {
        QuantPythonClient quantPythonClient = mock(QuantPythonClient.class);
        when(quantPythonClient.realtimeInfo(10, true)).thenReturn(Map.of("trade_date", "20260729"));
        MockMvc mockMvc = MockMvcBuilders.standaloneSetup(new QuantController(quantPythonClient)).build();

        mockMvc.perform(get("/api/quant/realtime-info")
                        .param("limit", "10")
                        .param("force_refresh", "true"))
                .andExpect(status().isOk());

        verify(quantPythonClient).realtimeInfo(10, true);
    }

    @Test
    void freeReviewBuildForwardsForce() throws Exception {
        QuantPythonClient client = mock(QuantPythonClient.class);
        when(client.startFreeReviewBuild(true))
                .thenReturn(Map.of("status", "pending"));
        MockMvc mockMvc = MockMvcBuilders
                .standaloneSetup(new QuantController(client)).build();

        mockMvc.perform(post("/api/quant/free-review/build")
                        .param("force", "true"))
                .andExpect(status().isOk());

        verify(client).startFreeReviewBuild(true);
    }

    @Test
    void freeReviewGetEndpointsForwardToClient() throws Exception {
        QuantPythonClient client = mock(QuantPythonClient.class);
        when(client.freeReviewBuildStatus("20260730"))
                .thenReturn(Map.of("status", "running"));
        when(client.freeReviewMeta("20260730"))
                .thenReturn(Map.of("stock_count", 5000));
        when(client.freeReviewSectors("20260730"))
                .thenReturn(Map.of("items", java.util.List.of()));
        MockMvc mockMvc = MockMvcBuilders
                .standaloneSetup(new QuantController(client)).build();

        mockMvc.perform(get("/api/quant/free-review/build-status")
                        .param("trade_date", "20260730"))
                .andExpect(status().isOk());
        mockMvc.perform(get("/api/quant/free-review/meta")
                        .param("trade_date", "20260730"))
                .andExpect(status().isOk());
        mockMvc.perform(get("/api/quant/free-review/sectors")
                        .param("trade_date", "20260730"))
                .andExpect(status().isOk());

        verify(client).freeReviewBuildStatus("20260730");
        verify(client).freeReviewMeta("20260730");
        verify(client).freeReviewSectors("20260730");
    }

    @Test
    void freeReviewQueryForwardsSnakeCaseRequestBody() throws Exception {
        QuantPythonClient client = mock(QuantPythonClient.class);
        when(client.queryFreeReview(any(FreeReviewQueryRequest.class)))
                .thenReturn(Map.of("total", 1));
        MockMvc mockMvc = MockMvcBuilders
                .standaloneSetup(new QuantController(client)).build();

        mockMvc.perform(post("/api/quant/free-review/query")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "trade_date":"20260730",
                                  "sort_by":"total_score",
                                  "sort_direction":"desc",
                                  "page_size":100
                                }
                                """))
                .andExpect(status().isOk());

        ArgumentCaptor<FreeReviewQueryRequest> captor =
                ArgumentCaptor.forClass(FreeReviewQueryRequest.class);
        verify(client).queryFreeReview(captor.capture());
        assertThat(captor.getValue().getTradeDate()).isEqualTo("20260730");
        assertThat(captor.getValue().getSortBy()).isEqualTo("total_score");
        assertThat(captor.getValue().getPageSize()).isEqualTo(100);
    }

    @Test
    void freeReviewExportPreservesCsvHeadersAndBytes() throws Exception {
        QuantPythonClient client = mock(QuantPythonClient.class);
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.parseMediaType(
                "text/csv;charset=UTF-8"));
        headers.set(
                HttpHeaders.CONTENT_DISPOSITION,
                "attachment; filename=\"free-review-20260730.csv\"");
        byte[] payload = new byte[]{(byte) 0xEF, (byte) 0xBB, (byte) 0xBF, 'a'};
        when(client.exportFreeReview(any(FreeReviewQueryRequest.class)))
                .thenReturn(ResponseEntity.ok().headers(headers).body(payload));
        MockMvc mockMvc = MockMvcBuilders
                .standaloneSetup(new QuantController(client)).build();

        mockMvc.perform(post("/api/quant/free-review/export")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isOk())
                .andExpect(content().bytes(payload))
                .andExpect(header().string(
                        HttpHeaders.CONTENT_DISPOSITION,
                        "attachment; filename=\"free-review-20260730.csv\""))
                .andExpect(header().string(
                        HttpHeaders.CONTENT_TYPE,
                        "text/csv;charset=UTF-8"));
    }
}
