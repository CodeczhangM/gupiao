package com.codec.quantserver.dto;

public class QuantScanRequest {

    private boolean includeAi = false;
    private int limit = 20;

    public boolean isIncludeAi() {
        return includeAi;
    }

    public void setIncludeAi(boolean includeAi) {
        this.includeAi = includeAi;
    }

    public int getLimit() {
        return limit;
    }

    public void setLimit(int limit) {
        this.limit = limit;
    }
}

