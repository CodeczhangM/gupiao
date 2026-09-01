package com.codec.quantserver.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;
import org.springframework.web.cors.CorsConfiguration;

import java.util.List;

@Configuration
public class CorsConfig {

    static CorsConfiguration quantCorsConfiguration() {
        CorsConfiguration configuration = new CorsConfiguration();
        configuration.setAllowedOriginPatterns(List.of("*"));
        configuration.setAllowedMethods(List.of("GET", "POST", "PATCH", "DELETE", "OPTIONS"));
        configuration.setAllowedHeaders(List.of("*"));
        configuration.setAllowCredentials(false);
        configuration.setMaxAge(3600L);
        return configuration;
    }

    @Bean
    WebMvcConfigurer quantCorsConfigurer() {
        CorsConfiguration configuration = quantCorsConfiguration();
        return new WebMvcConfigurer() {
            @Override
            public void addCorsMappings(CorsRegistry registry) {
                registry.addMapping("/api/quant/**")
                        .allowedOriginPatterns(configuration.getAllowedOriginPatterns().toArray(String[]::new))
                        .allowedMethods(configuration.getAllowedMethods().toArray(String[]::new))
                        .allowedHeaders(configuration.getAllowedHeaders().toArray(String[]::new))
                        .allowCredentials(Boolean.TRUE.equals(configuration.getAllowCredentials()))
                        .maxAge(configuration.getMaxAge());
            }
        };
    }
}
