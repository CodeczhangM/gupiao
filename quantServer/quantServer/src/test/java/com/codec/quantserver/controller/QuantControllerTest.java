package com.codec.quantserver.controller;

import com.codec.quantserver.dto.QuantBacktestRequest;
import com.codec.quantserver.dto.TradeReviewRequest;
import com.codec.quantserver.service.QuantPythonClient;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
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
        when(quantPythonClient.intradayMonitor()).thenReturn(Map.of("market_phase", "盘中监控"));
        MockMvc mockMvc = MockMvcBuilders.standaloneSetup(new QuantController(quantPythonClient)).build();

        mockMvc.perform(get("/api/quant/intraday-monitor"))
                .andExpect(status().isOk());

        verify(quantPythonClient).intradayMonitor();
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
}
