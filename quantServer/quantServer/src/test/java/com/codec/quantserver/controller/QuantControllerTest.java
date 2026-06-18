package com.codec.quantserver.controller;

import com.codec.quantserver.dto.QuantBacktestRequest;
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
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

class QuantControllerTest {

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
}
