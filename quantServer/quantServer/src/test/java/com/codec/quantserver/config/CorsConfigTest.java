package com.codec.quantserver.config;

import org.junit.jupiter.api.Test;
import org.springframework.http.HttpMethod;
import org.springframework.web.cors.CorsConfiguration;

import static org.junit.jupiter.api.Assertions.assertNotNull;

class CorsConfigTest {

    @Test
    void quantApiAllowsDeleteAndPatchPreflightMethods() {
        CorsConfiguration configuration = CorsConfig.quantCorsConfiguration();

        assertNotNull(configuration.checkHttpMethod(HttpMethod.DELETE));
        assertNotNull(configuration.checkHttpMethod(HttpMethod.PATCH));
    }
}
