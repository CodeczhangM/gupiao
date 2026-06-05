package com.codec.quantserver.controller;

import org.springframework.http.HttpStatusCode;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.client.RestClientResponseException;

import java.util.LinkedHashMap;
import java.util.Map;

@RestControllerAdvice
public class ApiExceptionHandler {

    @ExceptionHandler(RestClientResponseException.class)
    public ResponseEntity<Map<String, Object>> handleRestClientResponse(RestClientResponseException exception) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", exception.getStatusCode().value());
        body.put("error", "Python quant service error");
        body.put("detail", exception.getResponseBodyAsString());
        return ResponseEntity.status(exception.getStatusCode()).body(body);
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<Map<String, Object>> handleException(Exception exception) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", 500);
        body.put("error", exception.getClass().getSimpleName());
        body.put("detail", exception.getMessage());
        return ResponseEntity.status(HttpStatusCode.valueOf(500)).body(body);
    }
}

